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
