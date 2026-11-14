"""寫入設定：驗證 key 與型別、Flow 只收可覆蓋的 key、知識庫層級的 key 拒寫、套用版本、寫稽核紀錄。

全部驗證通過才寫入（任何一項錯就一項都不寫），錯誤一次回報所有欄位。
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.models import AuditLog, Bot, BotSetting, Flow, FlowSetting
from settings.loader import FLOW_OVERRIDABLE, KB_LEVEL, SETTING_DEFAULTS, TIERS, js_str, to_setting_str

MAX_LEN = 20000
CHOICES = {"tier": tuple(TIERS), "kg_mode": ("hybrid", "only"), "deid_mode": ("ai", "rule")}
JSON_ARRAY_KEYS = ("answer_schema",)
_TRUE = {"true", "是", "1", "yes", "on"}
_FALSE = {"false", "否", "0", "no", "off"}


class SettingsError(ValueError):
    def __init__(self, fields: dict[str, str]):
        self.fields = fields
        super().__init__("；".join(f"{k}：{v}" for k, v in fields.items()))


def normalize(key: str, value: Any) -> str:
    """把一個設定值轉成存進資料庫的字串；不合法丟 ValueError（訊息給人看）。空字串＝用預設，一律接受"""
    if key not in SETTING_DEFAULTS:
        raise ValueError("沒有這個設定")
    if key in KB_LEVEL:
        raise ValueError("這是知識庫的設定，請到知識庫修改")
    default = SETTING_DEFAULTS[key]
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return ""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return to_setting_str(value)
        s = js_str(value).strip().lower()
        if s in _TRUE:
            return "TRUE"
        if s in _FALSE:
            return "FALSE"
        raise ValueError("要是開或關")
    if isinstance(default, (int, float)):
        try:
            n = float(js_str(value).strip())
        except ValueError:
            raise ValueError("要是數字") from None
        if n < 0:
            raise ValueError("不能是負數")
        if isinstance(default, int):
            if not n.is_integer():
                raise ValueError("要是整數")
            return str(int(n))
        return js_str(n)
    s = js_str(value)
    if len(s) > MAX_LEN:
        raise ValueError(f"太長（上限 {MAX_LEN} 字）")
    if key in CHOICES and s.strip() not in CHOICES[key]:
        raise ValueError("只能是 " + "、".join(CHOICES[key]))
    if key in JSON_ARRAY_KEYS:
        try:
            if not isinstance(json.loads(s), list):
                raise ValueError("要是 JSON 陣列")
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 格式錯誤（第 {e.lineno} 行第 {e.colno} 字）") from None
    return s.strip() if key in CHOICES else s


def _items(items: dict[str, Any] | Iterable[dict]) -> list[tuple[str, Any]]:
    if isinstance(items, dict):
        return list(items.items())
    return [(i["key"], i.get("value")) for i in items]


def _audit(session: Session, workspace_id, bot_id, user_id, action: str, target: str, changes: dict):
    clip = lambda v: v if v is None or len(v) <= 200 else v[:200] + "…"
    session.add(AuditLog(workspace_id=workspace_id, bot_id=bot_id, user_id=user_id, action=action, target=target,
                         detail={k: {"before": clip(b), "after": clip(a)} for k, (b, a) in changes.items()}))


def save_bot_settings(session: Session, bot_id: uuid.UUID, items, user_id: uuid.UUID | None = None) -> list[str]:
    """回傳實際有改變的 key"""
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise LookupError("找不到這個機器人")
    clean, errors = {}, {}
    for k, v in _items(items):
        try:
            clean[k] = normalize(k, v)
        except ValueError as e:
            errors[k] = str(e)
    if errors:
        raise SettingsError(errors)
    cur = {r.key: r.value for r in session.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id, BotSetting.key.in_(list(clean))))}
    changes = {k: (cur.get(k), v) for k, v in clean.items() if cur.get(k) != v}
    for k, (_, v) in changes.items():
        stmt = insert(BotSetting).values(bot_id=bot_id, key=k, value=v, updated_by=user_id)
        session.execute(stmt.on_conflict_do_update(index_elements=["bot_id", "key"],
                                                   set_={"value": v, "updated_by": user_id, "updated_at": stmt.excluded.updated_at}))
    if changes:
        _audit(session, bot.workspace_id, bot_id, user_id, "settings.update", "bot", changes)
    session.flush()
    return list(changes)


def save_flow_settings(session: Session, flow_id: uuid.UUID, items, user_id: uuid.UUID | None = None) -> list[str]:
    """Flow 覆蓋：只收可覆蓋的 key；value 為 None 表示移除覆蓋（回到 Bot 設定）"""
    flow = session.get(Flow, flow_id)
    if flow is None:
        raise LookupError("找不到這個流程")
    bot = session.get(Bot, flow.bot_id)
    clean, errors = {}, {}
    for k, v in _items(items):
        if k in SETTING_DEFAULTS and k not in KB_LEVEL and k not in FLOW_OVERRIDABLE:
            errors[k] = "這個設定不能在流程層覆蓋"
            continue
        try:
            clean[k] = None if v is None else normalize(k, v)
        except ValueError as e:
            errors[k] = str(e)
    if errors:
        raise SettingsError(errors)
    cur = {r.key: r.value for r in session.scalars(select(FlowSetting).where(FlowSetting.flow_id == flow_id, FlowSetting.key.in_(list(clean))))}
    changes = {k: (cur.get(k), v) for k, v in clean.items() if cur.get(k) != v}
    for k, (_, v) in changes.items():
        if v is None:
            session.execute(delete(FlowSetting).where(FlowSetting.flow_id == flow_id, FlowSetting.key == k))
        else:
            stmt = insert(FlowSetting).values(flow_id=flow_id, key=k, value=v, updated_by=user_id)
            session.execute(stmt.on_conflict_do_update(index_elements=["flow_id", "key"],
                                                       set_={"value": v, "updated_by": user_id, "updated_at": stmt.excluded.updated_at}))
    if changes:
        _audit(session, bot.workspace_id, bot.id, user_id, "settings.update", f"flow:{flow_id}", changes)
    session.flush()
    return list(changes)


def apply_tier(session: Session, bot_id: uuid.UUID, tier: str, user_id: uuid.UUID | None = None, flow_id: uuid.UUID | None = None) -> list[str]:
    """套用版本＝寫入該版本的一組開關（＋tier 本身）；有 flow_id 就寫在流程層"""
    if tier not in TIERS:
        raise SettingsError({"tier": "只能是 " + "、".join(TIERS)})
    items = dict(TIERS[tier]["set"], tier=tier)
    return save_flow_settings(session, flow_id, items, user_id) if flow_id else save_bot_settings(session, bot_id, items, user_id)
