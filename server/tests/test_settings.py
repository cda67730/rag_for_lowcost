"""設定載入：合併順序、Flow 只能覆蓋允許的 key、知識庫層級 key 不進 Bot 設定、型別轉換、預設值同步。"""
import shutil
import subprocess
from pathlib import Path

import pytest

from db import models as m
from settings.loader import FLOW_OVERRIDABLE, KB_LEVEL, SETTING_DEFAULTS, build_config, load_config

ROOT = Path(__file__).resolve().parents[2]


def test_defaults_json_in_sync_with_setup_gs():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(ROOT / "tools" / "export-defaults.js"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_meta_sane():
    assert KB_LEVEL == {"model_embed", "embed_dim", "chunk_size", "chunk_overlap", "enable_node_mapping"}
    assert not (KB_LEVEL & FLOW_OVERRIDABLE)
    assert FLOW_OVERRIDABLE <= set(SETTING_DEFAULTS)


def test_types_and_blanks():
    c = build_config({"top_k": "8", "min_score": "abc", "enable_kg": "true", "max_followups": ""})
    assert c.int("top_k") == 8
    assert c.num("min_score") == SETTING_DEFAULTS["min_score"] and c.notes[0][0] == "設定錯誤"
    assert c.flag("enable_kg") is True
    assert c.int("max_followups") == SETTING_DEFAULTS["max_followups"]   # 空白＝預設
    assert c.flag("show_sources") is True                                 # 沒設＝預設（開）


def test_kb_level_keys_ignored_in_bot_layer():
    c = build_config({"embed_dim": "3072", "chunk_size": "9"})
    assert c.int("embed_dim") == SETTING_DEFAULTS["embed_dim"] and c.int("chunk_size") == SETTING_DEFAULTS["chunk_size"]


def mk(db):
    ws = m.Workspace(slug="w", name="w"); db.add(ws); db.flush()
    bot = m.Bot(workspace_id=ws.id, slug="b", name="b"); db.add(bot); db.flush()
    flow = m.Flow(bot_id=bot.id, name="試用", is_default=True); db.add(flow); db.flush()
    return bot, flow


def test_load_order_bot_flow_override(db):
    bot, flow = mk(db)
    db.add_all([
        m.BotSetting(bot_id=bot.id, key="role_user", value="老師"),
        m.BotSetting(bot_id=bot.id, key="top_k", value="5"),
        m.BotSetting(bot_id=bot.id, key="bot_name", value="正式名"),
        m.FlowSetting(flow_id=flow.id, key="top_k", value="9"),          # 可覆蓋
        m.FlowSetting(flow_id=flow.id, key="bot_name", value="試用名"),   # 不可覆蓋：要被忽略
        m.FlowSetting(flow_id=flow.id, key="enable_rerank", value="FALSE"),
    ]); db.flush()
    c = load_config(db, bot.id)
    assert (c.roles()["user"], c.int("top_k"), c.str("bot_name")) == ("老師", 5, "正式名")
    c = load_config(db, bot.id, flow.id)
    assert (c.int("top_k"), c.str("bot_name"), c.flag("enable_rerank")) == (9, "正式名", False)
    c = load_config(db, bot.id, flow.id, override={"top_k": 3, "enable_rerank": True})
    assert (c.int("top_k"), c.flag("enable_rerank")) == (3, True)
    assert c.raw("enable_rerank") == "TRUE"   # override 的布林轉成與試算表一致的字串


def test_tpl_and_text_fallback(db):
    bot, _ = mk(db)
    db.add_all([m.BotSetting(bot_id=bot.id, key="role_escalate", value="園長"),
                m.BotSetting(bot_id=bot.id, key="msg_ai_fallback", value="")]); db.flush()
    c = load_config(db, bot.id)
    assert "園長" in c.text("msg_ai_fallback")   # 空白→內建預設，且 {escalate} 被替換


def test_enable_roles_switch():
    """自訂角色用詞可以關掉：關掉時提問者／談論對象／轉介對象改用通用稱呼，領域描述照常；模板替換也跟著變"""
    vals = {"domain_desc": "學前融合教育", "role_user": "老師", "role_subject": "孩子", "role_escalate": "園長"}
    on = build_config(vals)
    assert on.roles() == {"domain": "學前融合教育", "user": "老師", "subject": "孩子", "escalate": "園長"}
    off = build_config({**vals, "enable_roles": "FALSE"})
    assert off.roles() == {"domain": "學前融合教育", "user": "使用者", "subject": "對方", "escalate": "主管"}
    assert off.tpl("{user}問{subject}的事，找{escalate}；{domain}") == "使用者問對方的事，找主管；學前融合教育"
    assert build_config({**vals, "enable_roles": ""}).roles()["user"] == "老師"          # 空白＝預設（開）


def test_answer_schema_field_enabled_toggle():
    import json as _j
    fields = [{"key": "reply", "type": "text", "modes": "both", "required": True},
              {"key": "steps", "type": "items", "modes": "both", "count": "1-3"},
              {"key": "notes", "type": "list", "modes": "both", "enabled": False},
              {"key": "draft", "type": "text", "modes": "full", "enabled": "FALSE"},
              {"key": "tip", "type": "text", "modes": "full", "enabled": True}]
    c = build_config({"answer_schema": _j.dumps(fields)})
    assert [f.key for f in c.schema()] == ["reply", "steps", "tip"]
    assert '"notes"' not in c.schema_format("full") and '"draft"' not in c.schema_format("full")
    assert not c.notes
    # 全部關閉 → 用預設欄位並記一筆
    c = build_config({"answer_schema": _j.dumps([dict(f, enabled=False) for f in fields])})
    assert [f.key for f in c.schema()] == ["reply", "steps", "notes"]
    assert any("所有欄位都關閉" in n[1] for n in c.notes)
    # 出處開著卻把 items 欄位關掉 → 照舊補參考做法欄
    c = build_config({"answer_schema": _j.dumps([fields[0], dict(fields[1], enabled=False)])})
    assert any(f.type == "items" for f in c.schema())


def test_groups_put_switch_next_to_its_content():
    import json as _j
    d = _j.loads((Path(__file__).resolve().parents[1] / "settings" / "defaults.json").read_text(encoding="utf-8"))
    g = {x["title"]: x["keys"] for x in d["groups"]}
    titles = [x["title"] for x in d["groups"]]
    assert titles[0] == "版本與基本" and "提示詞與回答格式" not in titles
    assert {"system_prompt", "answer_rules", "enable_answer_schema", "answer_schema", "fallback_rules"} <= set(g["版本與基本"])
    for sw, dep in [("enable_answer_schema", "answer_schema"), ("enable_triage", "triage_rules"), ("enable_case_flow", "continue_prompt"),
                    ("enable_kg", "kg_mode"), ("enable_deid", "deid_mode"), ("enable_llm_check", "check_items"), ("enable_roles", "role_user")]:
        grp = next(k for k, v in g.items() if sw in v)
        assert dep in g[grp] and g[grp].index(sw) < g[grp].index(dep), sw


def test_prompt_settings_blank_means_default():
    from settings.loader import TEXT_DEFAULTS
    c = build_config({"system_prompt": "  ", "answer_rules": "", "fallback_rules": "", "answer_schema": ""})
    assert c.str("system_prompt") == SETTING_DEFAULTS["system_prompt"]
    assert c.text("answer_rules") == c.tpl(TEXT_DEFAULTS["answer_rules"]) and c.text("fallback_rules")
    assert [f.key for f in c.schema()] == ["reply", "steps", "notes"] and not c.notes
    assert build_config({"system_prompt": "自訂"}).str("system_prompt") == "自訂"
    assert build_config({"welcome": ""}).str("welcome") == ""       # 其他設定的空白不受影響
