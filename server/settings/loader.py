"""Bot 設定：預設值 → bot_settings → flow_settings → 評測 override，合併成一份快照（BotConfig）。

解析邏輯逐一對應 apps-script 的同名函式（text_、roles_、tpl_、intents_、checkItems_、deidLabels_、uiConfig_、
schema_、parseSchema_、schemaFormat_…），測試會用 node 跑 Apps Script 版比對同樣輸入的結果。
預設值來自 settings/defaults.json（由 tools/export-defaults.js 從 Setup.gs 產生，不要手改）。
"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_D = json.loads((Path(__file__).with_name("defaults.json")).read_text(encoding="utf-8"))
SETTING_DEFAULTS: dict[str, Any] = {s["key"]: s["value"] for s in _D["settings"]}
SETTING_NOTES: dict[str, str] = {s["key"]: s["note"] for s in _D["settings"]}
TEXT_DEFAULTS: dict[str, str] = _D["text_defaults"]
TIERS: dict[str, dict] = _D["tiers"]
ROLE_DEFAULTS: dict[str, str] = _D["role_defaults"]
INTENT_KEYS: list[str] = _D["intent_keys"]
FLOW_OVERRIDABLE: frozenset[str] = frozenset(_D["flow_overridable"])
KB_LEVEL: frozenset[str] = frozenset(_D["kb_level"])
GROUPS: list[dict] = _D["groups"]

SCHEMA_TYPES = ("text", "list", "items")
SCHEMA_RESERVED = ("refs", "knowledge_gap", "referral", "options", "notice")
DEID_CATS = ("subject", "family", "staff", "org", "other")
_ROLE_KEYS = {"domain": "domain_desc", "user": "role_user", "subject": "role_subject", "escalate": "role_escalate"}
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RANGE_RE = re.compile(r"^\s*(\d+)\s*[-–~]\s*(\d+)\s*$")
_SPLIT_EQ = re.compile(r"[＝=]")


# ───────── 與 JavaScript 行為一致的小工具（Apps Script 版的語意） ─────────

def js_str(v: Any) -> str:
    """對應 Chat.gs 的 str_()：null→''、陣列與物件攤平用「；」接、其他 String(v)"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
    if isinstance(v, list):
        return "；".join(x for x in (js_str(i) for i in v) if x)
    if isinstance(v, dict):
        return "；".join(x for x in (js_str(i) for i in v.values()) if x)
    return str(v)


def js_truthy(v: Any) -> bool:
    """JavaScript 的 !!v：只有 null／false／0／''／NaN 是假，空陣列與 'false' 字串都是真"""
    if v is None or v is False or v == "":
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return not (v == 0 or (isinstance(v, float) and math.isnan(v)))
    return True


def truthy(v: Any) -> bool:
    """對應 Store.gs 的 truthy_()：TRUE（不分大小寫）或「是」"""
    return v is True or js_str(v).upper() == "TRUE" or v == "是"


def to_setting_str(v: Any) -> str:
    """寫進 bot_settings 的字串形式（與試算表一致：布林寫 TRUE／FALSE）"""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return js_str(v)


def _blank(v: Any) -> bool:
    return v is None or js_str(v).strip() == ""


# ───────── 回答欄位 ─────────

@dataclass(frozen=True)
class Field:
    key: str
    type: str
    label: str
    label_continue: str
    hint: str
    modes: str
    required: bool
    summary: bool
    copy: bool
    count: tuple[int, int]
    count_continue: tuple[int, int]

    def to_public(self) -> dict:
        """回給前端的格式（與 Apps Script 回應的 schema 欄位名稱一致）"""
        return {"key": self.key, "type": self.type, "label": self.label, "labelContinue": self.label_continue, "hint": self.hint,
                "modes": self.modes, "required": self.required, "summary": self.summary, "copy": self.copy,
                "count": list(self.count), "countContinue": list(self.count_continue)}


def range_of(s: Any, default: tuple[int, int]) -> tuple[int, int]:
    m = _RANGE_RE.match(js_str(s))
    return (int(m.group(1)), int(m.group(2))) if m else default


def _free_field(d: dict) -> Field:
    return Field(key=d["key"], type=d["type"], label=d["label"], label_continue=d["label_continue"], hint=d["hint"], modes=d["modes"],
                 required=d["required"], summary=d["summary"], copy=d["copy"],
                 count=range_of(d["count"], (0, 3)), count_continue=range_of(d["count_continue"], (0, 2)))


SCHEMA_FREE: tuple[Field, ...] = tuple(_free_field(d) for d in _D["schema_free"])


# ───────── 設定快照 ─────────

@dataclass
class BotConfig:
    values: dict[str, Any]
    notes: list[tuple[str, str]] = field(default_factory=list)   # 穩定度紀錄：(類型, 說明)
    _schema: list[Field] | None = None

    # 基本讀取
    def raw(self, key: str) -> Any:
        return self.values.get(key)

    def flag(self, key: str) -> bool:
        """布林設定；空白用預設"""
        v = self.values.get(key)
        return truthy(SETTING_DEFAULTS.get(key, False) if _blank(v) else v)

    def num(self, key: str) -> float:
        v = self.values.get(key)
        d = SETTING_DEFAULTS.get(key, 0)
        if _blank(v):
            return float(d or 0)
        try:
            return float(js_str(v).strip())
        except ValueError:
            self.notes.append(("設定錯誤", f"{key} 不是數字：{js_str(v)[:20]}，改用預設 {d}"))
            return float(d or 0)

    def int(self, key: str) -> int:
        return int(self.num(key))

    def str(self, key: str) -> str:
        return js_str(self.values.get(key))

    # 角色用詞與文字
    def roles(self) -> dict[str, str]:
        out = {}
        for k, sk in _ROLE_KEYS.items():
            v = self.values.get(sk)
            out[k] = ROLE_DEFAULTS[k] if _blank(v) else js_str(v).strip()
        return out

    def tpl(self, s: Any) -> str:
        r = self.roles()
        return re.sub(r"\{(user|subject|escalate|domain)\}", lambda m: r[m.group(1)], js_str(s))

    def text(self, key: str) -> str:
        """對應 text_()：空白用 TEXT_DEFAULTS，再替換角色用詞"""
        v = self.values.get(key)
        return self.tpl(TEXT_DEFAULTS.get(key, "") if _blank(v) else js_str(v))

    # 意圖、檢查項目、代稱
    def intents(self) -> list[dict]:
        lines = [l.strip() for l in re.split(r"\n+", self.text("intents")) if l.strip()]
        default = TEXT_DEFAULTS["intents"].split("\n")
        out = []
        for i, k in enumerate(INTENT_KEYS):
            p = _SPLIT_EQ.split(lines[i] if i < len(lines) and lines[i] else default[i])
            out.append({"key": k, "name": p[0].strip(), "desc": "＝".join(p[1:]).strip()})
        return out

    def intent_name(self, key: str) -> str:
        return next((i["name"] for i in self.intents() if i["key"] == key), key)

    def intent_key(self, name: Any) -> str | None:
        n = js_str(name).strip()
        return next((i["key"] for i in self.intents() if i["name"] == n), None)

    def check_items(self) -> list[dict]:
        def parse(t: str) -> list[dict]:
            out = []
            for l in (x.strip() for x in re.split(r"\n+", t)):
                if not l:
                    continue
                p = _SPLIT_EQ.split(l)
                if p[0].strip():
                    out.append({"name": p[0].strip(), "desc": "＝".join(p[1:]).strip()})
            return out
        return parse(self.text("check_items")) or parse(self.tpl(TEXT_DEFAULTS["check_items"]))

    def deid_labels(self) -> dict[str, str]:
        r = self.roles()
        out = {"subject": r["subject"], "family": "家人", "staff": r["user"], "org": "某機構", "other": "某人"}
        for l in re.split(r"\n+", self.str("deid_labels")):
            p = _SPLIT_EQ.split(l)
            k, v = p[0].strip(), "＝".join(p[1:]).strip()
            if len(p) > 1 and k in DEID_CATS and v:
                out[k] = v
        return out

    def ui_config(self) -> dict[str, Any]:
        o: dict[str, Any] = {"botName": self.str("bot_name"), "welcome": self.str("welcome")}
        for k, v in self.values.items():
            if k.startswith("ui_") and not _blank(v):
                o[k[3:]] = js_str(v)
        o["show_sources"] = self.flag("show_sources")
        return o

    # 回答欄位
    def citation_on(self) -> bool:
        return self.flag("enable_citation")

    def _parse_schema(self, text: str) -> list[Field]:
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("不是陣列")
        seen, out = set(), []
        for f in raw:
            f = f if isinstance(f, dict) else {}
            key = js_str(f.get("key") or "").strip()
            fld = Field(
                key=key,
                type=f.get("type") if f.get("type") in SCHEMA_TYPES else "text",
                label=js_str(f.get("label")),
                label_continue=js_str(f.get("label")) if f.get("label_continue") is None else js_str(f.get("label_continue")),
                hint=self.tpl(js_str(f.get("hint"))),
                modes=f.get("modes") if f.get("modes") in ("continue", "both") else "full",
                required=js_truthy(f.get("required")), summary=js_truthy(f.get("summary")), copy=js_truthy(f.get("copy")),
                count=range_of(f.get("count"), (2, 3)), count_continue=range_of(f.get("count_continue"), (0, 2)),
            )
            if key and _KEY_RE.match(key) and key not in SCHEMA_RESERVED and key not in seen:
                seen.add(key)
                out.append(fld)
        return out

    def schema(self) -> list[Field]:
        """對應 schema_()：固定回答欄位關閉→自由作答；出處開著卻沒有 items→補參考做法欄；解析失敗或空→預設並記一筆"""
        if self._schema is not None:
            return self._schema
        if not self.flag("enable_answer_schema"):
            self._schema = list(SCHEMA_FREE)
            return self._schema
        out, bad = None, None
        try:
            out = self._parse_schema(self.text("answer_schema"))
        except (ValueError, json.JSONDecodeError) as e:
            bad = str(e)
        if out and self.citation_on() and not any(f.type == "items" for f in out):
            out.append(SCHEMA_FREE[1])
        if not out:
            out = self._parse_schema(TEXT_DEFAULTS["answer_schema"])
            if bad:
                self.notes.append(("設定錯誤", f"answer_schema 解析失敗：{bad}，改用預設欄位"))
        self._schema = out
        return out

    def schema_fields(self, mode: str) -> list[Field]:
        return [f for f in self.schema() if f.modes in ("both", mode)]

    def items_range(self, mode: str) -> tuple[int, int]:
        f = next((x for x in self.schema_fields(mode) if x.type == "items"), None)
        return (f.count if mode == "full" else f.count_continue) if f else (0, 3)

    def schema_format(self, mode: str, extra: list[str] | None = None) -> str:
        parts = []
        for f in self.schema_fields(mode):
            h = f.hint or f.label or f.key
            if f.type == "items":
                parts.append(f'"{f.key}":[{{"title":"標題","how":"{h}","refs":{"[\"K1\"]" if self.citation_on() else "[]"}}}]')
            elif f.type == "list":
                parts.append(f'"{f.key}":["{h}"]')
            else:
                parts.append(f'"{f.key}":"{h}"')
        return "{" + ",".join(parts + list(extra or [])) + "}"

    def answer_items(self, answer: dict | None) -> list:
        out: list = []
        for f in self.schema():
            if f.type == "items":
                v = (answer or {}).get(f.key)
                out.extend(v if isinstance(v, list) else ([] if v is None else [v]))
        return out

    def answer_summary(self, answer: dict | None, rtype: str) -> str:
        mode = "continue" if rtype == "continue" else "full"
        parts = []
        for f in self.schema_fields(mode):
            if not f.summary:
                continue
            v = (answer or {}).get(f.key)
            if v is None or (isinstance(v, list) and not v):
                continue
            if f.type == "items":
                parts.append("、".join(js_str(x.get("title") if isinstance(x, dict) else None) for x in v))
            elif isinstance(v, list):
                parts.append("；".join(js_str(x) for x in v))
            else:
                parts.append(js_str(v))
        return "｜".join(p for p in parts if p)


def build_config(*layers: dict[str, Any] | None, flow_layer_index: int | None = None) -> BotConfig:
    """合併多層設定（後面的蓋前面的）。flow_layer_index 指出哪一層是 Flow，只有可覆蓋的 key 會生效"""
    values: dict[str, Any] = dict(SETTING_DEFAULTS)
    for i, layer in enumerate(layers):
        for k, v in (layer or {}).items():
            if k in KB_LEVEL:
                continue
            if i == flow_layer_index and k not in FLOW_OVERRIDABLE:
                continue
            values[k] = v
    return BotConfig(values=values)


def load_config(session, bot_id: uuid.UUID, flow_id: uuid.UUID | None = None, override: dict[str, Any] | None = None) -> BotConfig:
    """從資料庫載入一個 Bot（＋流程）的設定快照；override 是評測用的試用設定"""
    from sqlalchemy import select

    from db.models import BotSetting, FlowSetting

    bot = {r.key: r.value for r in session.scalars(select(BotSetting).where(BotSetting.bot_id == bot_id))}
    flow = {r.key: r.value for r in session.scalars(select(FlowSetting).where(FlowSetting.flow_id == flow_id))} if flow_id else {}
    ov = {k: to_setting_str(v) for k, v in (override or {}).items()}
    return build_config(bot, flow, ov, flow_layer_index=1)
