"""專案範本：repo 內兩份範本都要能完整匯入；重複匯入不重複；replace；kgDefs 要指定知識庫；匯出再匯入結果一致。"""
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from db import models as m
from services.templates import apply_template, export_template
from settings.loader import KB_LEVEL, load_config

TPL = Path(__file__).resolve().parents[2] / "templates"


def mk(db, slug="b"):
    ws = db.scalar(select(m.Workspace).where(m.Workspace.slug == "w")) or m.Workspace(slug="w", name="w")
    db.add(ws); db.flush()
    bot = m.Bot(workspace_id=ws.id, slug=slug, name=slug); db.add(bot); db.flush()
    return ws, bot


def count(db, model, bot):
    return db.scalar(select(func.count()).select_from(model).where(model.bot_id == bot.id))


@pytest.mark.parametrize("name", ["preschool-inclusion.json", "internal-helpdesk.json"])
def test_repo_templates_import_fully(db, name):
    t = json.loads((TPL / name).read_text(encoding="utf-8"))
    _, bot = mk(db)
    r = apply_template(db, bot.id, t)
    kb_keys = [k for k in t["settings"] if k in KB_LEVEL]
    assert r["settings"] == len(t["settings"]) - len(kb_keys)
    assert [s for s in r["skipped"] if not s.startswith("kgDefs")] == [f for f in r["skipped"] if any(k in f for k in kb_keys)]
    assert (r["skills"], r["synonyms"], r["forbidden"], r["referral"]) == (len(t["skills"]), len(t["synonyms"]), len(t["forbidden"]), len(t["referral"]))
    assert count(db, m.Skill, bot) == len(t["skills"]) and count(db, m.Referral, bot) == len(t["referral"])
    # 每個 Skill 都有第 1 版歷史
    assert db.scalar(select(func.count()).select_from(m.SkillVersion).join(m.Skill).where(m.Skill.bot_id == bot.id)) == len(t["skills"])
    c = load_config(db, bot.id)
    assert c.roles()["user"] == t["settings"]["role_user"]
    assert c.str("bot_name") == t["settings"]["bot_name"]
    assert c.schema() and not c.notes   # 範本的回答欄位能被解析、沒有設定錯誤


def test_reimport_adds_nothing_and_replace_rewrites(db):
    t = json.loads((TPL / "preschool-inclusion.json").read_text(encoding="utf-8"))
    _, bot = mk(db)
    apply_template(db, bot.id, t)
    r = apply_template(db, bot.id, t)
    assert (r["skills"], r["synonyms"], r["forbidden"], r["referral"]) == (0, 0, 0, 0)
    assert sum("已存在" in s for s in r["skipped"]) == len(t["skills"]) + len(t["synonyms"]) + len(t["forbidden"]) + len(t["referral"])
    t2 = dict(t, skills=t["skills"][:1], synonyms=[{"word": "新詞", "concept": "新概念"}])
    r = apply_template(db, bot.id, t2, replace=True)
    assert count(db, m.Skill, bot) == 1 and count(db, m.Synonym, bot) == 1
    assert count(db, m.ForbiddenRule, bot) == len(t["forbidden"])   # 範本有給才清，沒影響其他清單


def test_bad_values_skipped_not_fatal(db):
    _, bot = mk(db)
    r = apply_template(db, bot.id, {"settings": {"top_k": "abc", "role_user": "同仁", "nope": 1, "embed_dim": 3}})
    assert r["settings"] == 1 and len(r["skipped"]) == 3
    assert load_config(db, bot.id).roles()["user"] == "同仁"


def test_invalid_file(db):
    _, bot = mk(db)
    with pytest.raises(ValueError, match="JSON"):
        apply_template(db, bot.id, "{not json")
    with pytest.raises(ValueError, match="settings 或 skills"):
        apply_template(db, bot.id, {"foo": 1})


def test_kgdefs_need_kb(db):
    t = json.loads((TPL / "internal-helpdesk.json").read_text(encoding="utf-8"))
    ws, bot = mk(db)
    r = apply_template(db, bot.id, t)
    assert r["kgDefs"] == 0 and any(s.startswith("kgDefs") for s in r["skipped"])
    kb = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="e", embed_dim=768); db.add(kb); db.flush()
    r = apply_template(db, bot.id, t, kb_id=kb.id)
    assert r["kgDefs"] == len(t["kgDefs"]["types"]) + len(t["kgDefs"]["audiences"])
    kinds = dict(db.execute(select(m.KgDef.kind, func.count()).where(m.KgDef.kb_id == kb.id).group_by(m.KgDef.kind)).all())
    assert kinds == {"type": len(t["kgDefs"]["types"]), "audience": len(t["kgDefs"]["audiences"])}


def test_export_roundtrip(db):
    t = json.loads((TPL / "preschool-inclusion.json").read_text(encoding="utf-8"))
    _, a = mk(db, "a"); _, b = mk(db, "b")
    apply_template(db, a.id, t)
    ea = export_template(db, a.id)
    apply_template(db, b.id, ea)
    eb = export_template(db, b.id)
    ea.pop("name"); eb.pop("name")
    assert ea == eb
    assert len(ea["skills"]) == len(t["skills"]) and ea["forbidden"][0]["type"] == "詞"


def test_export_preserves_original_content(db):
    """和原始範本比，不是匯出跟匯出比（否則匯出時漏掉的欄位兩邊都漏，照樣相等）"""
    t = json.loads((TPL / "preschool-inclusion.json").read_text(encoding="utf-8"))
    t["forbidden"].append({"rule": r"\d{4}-\d{3}", "type": "正規式", "note": "電話"})
    _, bot = mk(db)
    apply_template(db, bot.id, t)
    e = export_template(db, bot.id)
    norm_tags = lambda s: [x.strip() for x in s.replace("，", "、").replace(",", "、").split("、") if x.strip()]
    assert {s["id"]: (s["name"], norm_tags(s["tags"]), s["always"], s["content"]) for s in e["skills"]} == \
           {s["id"]: (s["name"], norm_tags(s["tags"]), s["always"], s["content"]) for s in t["skills"]}
    assert sorted((f["rule"], f["type"], f["note"]) for f in e["forbidden"]) == sorted((f["rule"], f["type"], f["note"]) for f in t["forbidden"])
    assert e["referral"] == t["referral"]
    assert sorted((s["word"], s["concept"]) for s in e["synonyms"]) == sorted((s["word"], s["concept"]) for s in t["synonyms"])
    for k, v in t["settings"].items():
        assert load_config(db, bot.id).raw(k) == (("TRUE" if v else "FALSE") if isinstance(v, bool) else str(v)), k


def test_regex_forbidden_kind(db):
    _, bot = mk(db)
    apply_template(db, bot.id, {"skills": [], "settings": {"role_user": "x"}, "forbidden": [{"rule": "a+", "type": "正規式"}, {"rule": "b", "type": "詞"}]})
    kinds = dict(db.execute(select(m.ForbiddenRule.rule, m.ForbiddenRule.kind).where(m.ForbiddenRule.bot_id == bot.id)).all())
    assert kinds == {"a+": "regex", "b": "word"}


def test_kgdefs_replace_not_append(db):
    ws, bot = mk(db)
    kb = m.KnowledgeBase(workspace_id=ws.id, slug="k2", name="k", embed_model="e", embed_dim=768); db.add(kb); db.flush()
    base = {"settings": {"role_user": "x"}}
    apply_template(db, bot.id, dict(base, kgDefs={"audiences": [{"id": "a1", "label": "甲"}, {"id": "a2", "label": "乙"}], "types": [{"id": "t", "label": "類"}]}), kb_id=kb.id)
    apply_template(db, bot.id, dict(base, kgDefs={"audiences": [{"id": "z", "label": "丙"}]}), kb_id=kb.id)
    rows = sorted(db.execute(select(m.KgDef.kind, m.KgDef.label).where(m.KgDef.kb_id == kb.id)).all())
    assert rows == [("audience", "丙"), ("type", "類")]   # 給了的種類整組換掉，沒給的保留
