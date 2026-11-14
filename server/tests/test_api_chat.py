"""聊天 API：bot 解析、邀請碼、用量上限、回饋、錯誤不外洩。模型用假的。"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.routes.chat as C
from app.main import app
from db import models as m
from db.session import get_session
from settings.store import save_bot_settings
from test_chat_graph import FakeLLM, bot  # noqa: F401


@pytest.fixture
def api(bot, monkeypatch):  # noqa: F811
    db, b = bot
    save_bot_settings(db, b.id, {"bot_name": "小幫手", "welcome": "嗨", "ui_accent": "#123456", "enable_invite": True, "daily_limit": 3, "hourly_limit": 100})
    db.add(m.InviteCode(bot_id=b.id, code="ABC123", unit="甲園", trace=True))
    db.add(m.InviteCode(bot_id=b.id, code="OFF1", enabled=False))
    db.add(m.InviteCode(bot_id=b.id, code=" low9 "))
    db.add(m.InviteCode(bot_id=b.id, code="OLD1", expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    db.flush()
    llm = FakeLLM()
    monkeypatch.setattr(C, "FAIL_DELAY", 0)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[C.get_llm] = lambda: llm
    yield TestClient(app), db, b, llm
    app.dependency_overrides.clear()


def post(c, action, **body):
    return c.post(f"/api/{action}", json={"bot": "b", **body}).json()


def test_ui_without_code_and_text_plain(api):
    c, db, b, _ = api
    r = c.post("/api/ui", content=json.dumps({"bot": "b"}), headers={"Content-Type": "text/plain"}).json()
    assert r["ok"] and r["invite"] is True
    assert {k: r["ui"][k] for k in ("botName", "welcome", "accent", "show_sources")} == {"botName": "小幫手", "welcome": "嗨", "accent": "#123456", "show_sources": True}
    assert all(not k.startswith("ui_") for k in r["ui"])
    assert post(c, "ui", bot="nope") == {"ok": False, "error": "服務暫停"}
    b.disabled = True; db.flush()
    assert post(c, "ui")["error"] == "服務暫停"


def test_check_codes(api):
    c, *_ = api
    r = post(c, "check", code=" abc123 ")
    assert r["ok"] and r["botName"] == "小幫手" and r["trace"] is True and r["ui"]["accent"] == "#123456"
    assert post(c, "check", code="LOW9")["ok"]           # 大小寫與前後空白不計
    for code in ["OFF1", "OLD1", "", None, "zzz"]:
        assert post(c, "check", code=code) == {"ok": False, "error": "邀請碼不正確或已停用"}


def test_code_lock_after_many_failures(api, monkeypatch):
    c, *_ = api
    monkeypatch.setattr(C.limits, "CODE_FAIL_MAX", 3)
    assert post(c, "check", code="ABC123")["ok"]          # 先成功一次：鎖定期間這組仍可用
    for _ in range(3):
        post(c, "check", code="bad")
    assert post(c, "check", code="bad")["error"] == "嘗試次數過多，請 15 分鐘後再試"
    assert post(c, "check", code="ABC123")["ok"]


def test_chat_logs_unit_and_trace_only_when_allowed(api):
    c, db, b, llm = api
    r = post(c, "chat", code="ABC123", session="s9", message="小宇收玩具時丟積木", history=[])
    assert r["ok"] and r["type"] == "answer" and r["trace"]
    row = db.query(m.Conversation).filter_by(bot_id=b.id).one()
    assert row.unit == "甲園" and row.invite_code_id is not None and row.session == "s9"
    db.query(m.InviteCode).filter_by(code="ABC123").update({"trace": False}); db.flush()
    assert "trace" not in post(c, "chat", code="ABC123", session="s9", message="又一題", turn=2)
    assert post(c, "chat", code="ABC123", message="  ") == {"ok": False, "error": "請輸入內容"}


def test_daily_and_hourly_limits(api):
    c, db, b, _ = api
    for i in range(3):
        assert post(c, "chat", code="ABC123", session="s", message="題目", turn=i + 1)["ok"]
    assert post(c, "chat", code="ABC123", session="s", message="題目", turn=9) == {"ok": False, "error": "今日額度已用完，請明天再試"}
    assert post(c, "check", code="ABC123")["ok"]          # 每日上限只算問答
    save_bot_settings(db, b.id, {"hourly_limit": 1})
    assert post(c, "check", code="ABC123")["error"] == "使用次數過多，請稍後再試"


def test_open_bot_without_invite(api):
    c, db, b, _ = api
    save_bot_settings(db, b.id, {"enable_invite": False})
    assert post(c, "ui")["invite"] is False
    r = post(c, "check", code="")
    assert r["ok"] and r["trace"] is False
    assert post(c, "chat", session="x", message="題目")["ok"]
    assert db.query(m.Conversation).one().unit == "公開"


def test_feedback(api):
    c, db, b, _ = api
    assert post(c, "feedback", code="ABC123", session="s", turn="3", kind="有幫助", note="x" * 900) == {"ok": True}
    f = db.query(m.Feedback).one()
    assert (f.turn, f.kind, len(f.note)) == (3, "有幫助", 500)


def test_errors_do_not_leak(api):
    c, db, b, llm = api
    llm.resp["triage"] = RuntimeError("secret-key-xyz 連線失敗")
    r = post(c, "chat", code="ABC123", session="s", message="題目")
    assert r == {"ok": False, "error": "伺服器發生錯誤，請稍後再試"}
    assert "secret" not in json.dumps(r)


def test_flow_selection(api):
    c, db, b, _ = api
    f = m.Flow(bot_id=b.id, name="預設", is_default=True); db.add(f); db.flush()
    save_bot_settings(db, b.id, {"enable_invite": False})
    assert post(c, "check", flow=str(f.id))["ok"]
    assert post(c, "check", flow="not-a-uuid")["error"] == "服務暫停"
