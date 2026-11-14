"""專案範本：一次匯入／匯出一個領域的設定、Skill、同義詞、禁止用語、轉介資源、圖譜定義。

格式與 Apps Script 版相同（templates/*.json）：settings、skills、synonyms、forbidden、referral、kgDefs。
- 設定值：合法的寫入（覆蓋），不合法或屬於知識庫層級的略過並回報，不會讓整份匯入失敗。
- 清單：預設只加沒有的；replace=True 先清掉這個 Bot 的該類清單再寫。
- kgDefs 屬於知識庫，要指定 kb_id 才會匯入（給了的種類整組換掉）。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import AuditLog, Bot, BotSetting, ForbiddenRule, KgDef, KnowledgeBase, Referral, Skill, SkillVersion, Synonym
from settings.loader import js_str
from settings.store import normalize, save_bot_settings

KG_KINDS = {"types": "type", "rels": "relation", "relations": "relation", "audiences": "audience"}
KG_KINDS_OUT = {"type": "types", "relation": "rels", "audience": "audiences"}


def _topics(v: Any) -> list[str]:
    if isinstance(v, list):
        return [js_str(x).strip() for x in v if js_str(x).strip()]
    return [t.strip() for t in re.split(r"[,，、]", js_str(v)) if t.strip()]


def apply_template(session: Session, bot_id: uuid.UUID, data: dict | str, *, replace: bool = False,
                   kb_id: uuid.UUID | None = None, user_id: uuid.UUID | None = None) -> dict:
    if isinstance(data, str):
        try:
            data = json.loads(data.lstrip("﻿"))
        except json.JSONDecodeError:
            raise ValueError("檔案不是有效的 JSON") from None
    if not isinstance(data, dict) or not (data.get("settings") or data.get("skills")):
        raise ValueError("找不到 settings 或 skills，請上傳專案範本 JSON")
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise LookupError("找不到這個機器人")
    out = {"settings": 0, "skills": 0, "synonyms": 0, "forbidden": 0, "referral": 0, "kgDefs": 0, "skipped": []}
    now = datetime.now(timezone.utc)

    # 設定
    good = {}
    for k, v in (data.get("settings") or {}).items():
        try:
            normalize(k, v)
            good[k] = v
        except ValueError as e:
            out["skipped"].append(f"設定 {k}：{e}")
    if good:
        save_bot_settings(session, bot_id, good, user_id)
    out["settings"] = len(good)

    def existing(model, col) -> set[str]:
        return set(session.scalars(select(col).where(model.bot_id == bot_id)))

    # Skill
    skills = [s for s in (data.get("skills") or []) if isinstance(s, dict) and js_str(s.get("id")).strip() and js_str(s.get("name")).strip()]
    if skills:
        if replace:
            session.execute(delete(Skill).where(Skill.bot_id == bot_id))
        keys, names = existing(Skill, Skill.skill_key), existing(Skill, Skill.name)
        for s in skills:
            key, name = js_str(s["id"]).strip(), js_str(s["name"]).strip()
            if key in keys or name in names:
                out["skipped"].append(f"skills {key}（已存在）")
                continue
            row = Skill(bot_id=bot_id, skill_key=key, name=name, topics=_topics(s.get("tags")), always_on=bool(s.get("always")),
                        content=js_str(s.get("content")), updated_by=user_id, updated_at=now)
            session.add(row); session.flush()
            session.add(SkillVersion(skill_id=row.id, version=1, name=row.name, topics=row.topics, always_on=row.always_on,
                                     content=row.content, updated_by=user_id, updated_at=now))
            keys.add(key); names.add(name); out["skills"] += 1

    # 清單類（第一欄不重複才加）
    def fill(model, rows: list[dict], first: str, counter: str):
        if not rows:
            return
        if replace:
            session.execute(delete(model).where(model.bot_id == bot_id))
        have = existing(model, getattr(model, first)) if model is not Synonym else {
            (w, c) for w, c in session.execute(select(Synonym.word, Synonym.concept).where(Synonym.bot_id == bot_id))}
        for r in rows:
            ident = (r["word"], r["concept"]) if model is Synonym else r[first]
            if not r[first] or ident in have:
                if r[first]:
                    out["skipped"].append(f"{counter} {r[first]}（已存在）")
                continue
            session.add(model(bot_id=bot_id, **r)); have.add(ident); out[counter] += 1

    fill(Synonym, [{"word": js_str(x.get("word")).strip(), "concept": js_str(x.get("concept")).strip()} for x in data.get("synonyms") or [] if isinstance(x, dict)], "word", "synonyms")
    fill(ForbiddenRule, [{"rule": js_str(x.get("rule")).strip(), "kind": "regex" if x.get("type") in ("正規式", "regex") else "word", "note": js_str(x.get("note"))}
                         for x in data.get("forbidden") or [] if isinstance(x, dict)], "rule", "forbidden")
    fill(Referral, [{"category": js_str(x.get("category")).strip(), "who": js_str(x.get("who")), "contact": js_str(x.get("contact")),
                     "now_action": js_str(x.get("now")), "sort": i} for i, x in enumerate(data.get("referral") or []) if isinstance(x, dict)], "category", "referral")

    # 圖譜定義（屬於知識庫）
    kg = data.get("kgDefs") or {}
    if kg:
        if kb_id is None:
            out["skipped"].append("kgDefs：圖譜定義屬於知識庫，匯入時要指定知識庫")
        else:
            kb = session.get(KnowledgeBase, kb_id)
            if kb is None or kb.workspace_id != bot.workspace_id:
                raise LookupError("找不到這個知識庫")
            for name, kind in KG_KINDS.items():
                items = [x for x in kg.get(name) or [] if isinstance(x, dict) and js_str(x.get("label")).strip()]
                if not items:
                    continue
                session.execute(delete(KgDef).where(KgDef.kb_id == kb_id, KgDef.kind == kind))
                for i, x in enumerate(items):
                    session.add(KgDef(kb_id=kb_id, kind=kind, def_key=js_str(x.get("id")).strip() or f"{kind[0]}{i + 1}",
                                      label=js_str(x["label"]).strip(), definition=js_str(x.get("def") or x.get("definition"))))
                    out["kgDefs"] += 1

    session.add(AuditLog(workspace_id=bot.workspace_id, bot_id=bot_id, kb_id=kb_id, user_id=user_id, action="template.apply",
                         target=js_str(data.get("name"))[:200], detail={k: v for k, v in out.items() if k != "skipped"} | {"replace": replace}))
    session.flush()
    return out


def export_template(session: Session, bot_id: uuid.UUID, kb_id: uuid.UUID | None = None) -> dict:
    """匯出成範本：只含明確設定過的值；不含知識庫內容、紀錄、邀請碼"""
    bot = session.get(Bot, bot_id)
    if bot is None:
        raise LookupError("找不到這個機器人")
    q = lambda model, *order: list(session.scalars(select(model).where(model.bot_id == bot_id).order_by(*order)))
    out = {
        "name": bot.name, "description": "", "version": 1,
        "settings": {r.key: r.value for r in q(BotSetting, BotSetting.key) if r.value != ""},
        "skills": [{"id": s.skill_key, "name": s.name, "tags": "、".join(s.topics), "always": s.always_on, "content": s.content} for s in q(Skill, Skill.skill_key)],
        "synonyms": [{"word": s.word, "concept": s.concept} for s in q(Synonym, Synonym.word, Synonym.concept)],
        "forbidden": [{"rule": f.rule, "type": "正規式" if f.kind == "regex" else "詞", "note": f.note} for f in q(ForbiddenRule, ForbiddenRule.rule)],
        "referral": [{"category": r.category, "who": r.who, "contact": r.contact, "now": r.now_action} for r in q(Referral, Referral.sort, Referral.category)],
    }
    if kb_id:
        defs = list(session.scalars(select(KgDef).where(KgDef.kb_id == kb_id).order_by(KgDef.kind, KgDef.def_key)))
        out["kgDefs"] = {}
        for d in defs:
            out["kgDefs"].setdefault(KG_KINDS_OUT[d.kind], []).append({"id": d.def_key, "label": d.label, "def": d.definition})
    return out
