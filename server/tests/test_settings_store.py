import pytest
from sqlalchemy import select

from db import models as m
from settings.loader import TIERS, load_config
from settings.store import SettingsError, apply_tier, normalize, save_bot_settings, save_flow_settings


@pytest.mark.parametrize("key,value,want", [
    ("enable_kg", True, "TRUE"), ("enable_kg", "false", "FALSE"), ("enable_kg", "是", "TRUE"), ("enable_kg", "", ""),
    ("top_k", "8", "8"), ("top_k", 8.0, "8"), ("min_score", "0.5", "0.5"), ("kg_mode", " only ", "only"),
    ("answer_schema", "[]", "[]"), ("welcome", "  你好  ", "  你好  "),
])
def test_normalize_ok(key, value, want):
    assert normalize(key, value) == want


@pytest.mark.parametrize("key,value,msg", [
    ("nope", "1", "沒有這個設定"), ("embed_dim", "768", "知識庫"), ("enable_kg", "maybe", "開或關"),
    ("top_k", "abc", "數字"), ("top_k", "1.5", "整數"), ("daily_limit", "-1", "負數"),
    ("kg_mode", "all", "只能是"), ("tier", "pro", "只能是"), ("answer_schema", "{x", "JSON 格式錯誤"), ("answer_schema", "{}", "陣列"),
])
def test_normalize_rejects(key, value, msg):
    with pytest.raises(ValueError, match=msg):
        normalize(key, value)


def mk(db):
    ws = m.Workspace(slug="w", name="w"); db.add(ws); db.flush()
    bot = m.Bot(workspace_id=ws.id, slug="b", name="b"); db.add(bot); db.flush()
    flow = m.Flow(bot_id=bot.id, name="試用", is_default=True); db.add(flow); db.flush()
    return ws, bot, flow


def audits(db, bot):
    return list(db.scalars(select(m.AuditLog).where(m.AuditLog.bot_id == bot.id).order_by(m.AuditLog.id)))


def test_save_atomic_and_reports_all_errors(db):
    _, bot, _ = mk(db)
    with pytest.raises(SettingsError) as e:
        save_bot_settings(db, bot.id, {"top_k": "x", "embed_dim": "1", "role_user": "老師"})
    assert set(e.value.fields) == {"top_k", "embed_dim"}
    assert db.scalar(select(m.BotSetting).where(m.BotSetting.bot_id == bot.id)) is None   # 一項都沒寫


def test_save_then_load_and_audit_only_changes(db):
    _, bot, _ = mk(db)
    assert set(save_bot_settings(db, bot.id, [{"key": "role_user", "value": "老師"}, {"key": "top_k", "value": 7}])) == {"role_user", "top_k"}
    c = load_config(db, bot.id)
    assert c.roles()["user"] == "老師" and c.int("top_k") == 7
    assert save_bot_settings(db, bot.id, {"role_user": "老師", "top_k": "7"}) == []        # 沒變就不寫、不記
    assert save_bot_settings(db, bot.id, {"top_k": "9"}) == ["top_k"]
    a = audits(db, bot)
    assert len(a) == 2 and a[1].detail == {"top_k": {"before": "7", "after": "9"}}


def test_flow_overrides(db):
    _, bot, flow = mk(db)
    with pytest.raises(SettingsError, match="流程層"):
        save_flow_settings(db, flow.id, {"bot_name": "x"})
    save_flow_settings(db, flow.id, {"top_k": "3"})
    assert load_config(db, bot.id, flow.id).int("top_k") == 3
    assert save_flow_settings(db, flow.id, {"top_k": None}) == ["top_k"]                  # 移除覆蓋
    assert load_config(db, bot.id, flow.id).int("top_k") == load_config(db, bot.id).int("top_k")


@pytest.mark.parametrize("tier", list(TIERS))
def test_apply_tier(db, tier):
    _, bot, flow = mk(db)
    apply_tier(db, bot.id, tier)
    c = load_config(db, bot.id)
    assert c.str("tier") == tier
    for k, v in TIERS[tier]["set"].items():
        assert (c.flag(k) if isinstance(v, bool) else c.int(k)) == v, k
    # 流程層套用另一個版本，只影響該流程
    other = "full" if tier != "full" else "basic"
    apply_tier(db, bot.id, other, flow_id=flow.id)
    assert load_config(db, bot.id, flow.id).str("tier") == other and load_config(db, bot.id).str("tier") == tier


def test_apply_unknown_tier(db):
    _, bot, _ = mk(db)
    with pytest.raises(SettingsError):
        apply_tier(db, bot.id, "pro")
