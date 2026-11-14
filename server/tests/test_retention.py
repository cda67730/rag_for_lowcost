"""TRACE_RETENTION_DAYS（過期流程紀錄清除）與 LOG_LEVEL。"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from app import cli, logsetup
from db import models as m
from services import retention


@pytest.mark.parametrize("raw,want", [(None, 90), ("", 90), ("30", 30), (" 7 ", 7), ("0", None), ("-1", 90), ("abc", 90), ("1.5", 90)])
def test_retention_days(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("TRACE_RETENTION_DAYS", raw)
    assert retention.retention_days() == want


def rows(db):
    ws = m.Workspace(slug="w", name="w"); db.add(ws); db.flush()
    b = m.Bot(workspace_id=ws.id, slug="b", name="b"); db.add(b); db.flush()
    now = datetime.now(timezone.utc)
    for i, age in enumerate([1, 29, 31, 40, 100, 200, 365]):
        db.add(m.Conversation(bot_id=b.id, session="s", turn=i, user_text=f"q{i}", rtype="answer", hits=2, trace=[{"step": "P06"}], answer={"a": i},
                              quality={"l5": []}, created_at=now - timedelta(days=age)))
    db.flush()
    return b


def state(db):
    db.expire_all()
    return [(c.turn, c.trace is not None, c.user_text, c.answer, c.hits) for c in db.query(m.Conversation).order_by(m.Conversation.turn)]


def test_purge_traces_keeps_conversations(db):
    rows(db)
    assert retention.purge_traces(db, 30, batch=2) == 5        # 分批：2＋2＋1
    assert state(db) == [(0, True, "q0", {"a": 0}, 2), (1, True, "q1", {"a": 1}, 2)] + [(i, False, f"q{i}", {"a": i}, 2) for i in range(2, 7)]
    assert retention.purge_traces(db, 30) == 0                  # 清過的不再清
    assert retention.purge_traces(db, 1) == 1 and state(db)[1][1] is False


def test_run_once_and_zero_disables(db, monkeypatch):
    rows(db)
    factory = lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "0")
    assert retention.run_once(factory) == 0 and all(x[1] for x in state(db))
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "100")
    assert retention.run_once(factory) == 2


def test_cli_purge(db, monkeypatch, capsys):
    rows(db)
    monkeypatch.setattr(cli, "session", lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint"))
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    cli.main(["purge-traces"])
    assert "已清除 3 筆超過 90 天" in capsys.readouterr().out
    cli.main(["purge-traces", "--days", "35"])
    assert "已清除 1 筆超過 35 天" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["purge-traces", "--days", "0"])


def test_retention_loop_survives_errors(monkeypatch):
    import app.main as M
    calls = []

    def once(factory):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("資料庫還沒好")
        return 0
    monkeypatch.setattr(M.retention, "run_once", once)

    async def go():
        t = asyncio.create_task(M.retention_loop(every=0.01))
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        t.cancel()
    asyncio.run(asyncio.wait_for(go(), 5))
    assert len(calls) >= 3


@pytest.mark.parametrize("raw,want", [(None, logging.INFO), ("debug", logging.DEBUG), ("WARNING", logging.WARNING), ("error", logging.ERROR), ("nope", logging.INFO)])
def test_log_level(monkeypatch, raw, want):
    root = logging.getLogger()
    old = root.level
    if raw is None:
        monkeypatch.delenv("LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("LOG_LEVEL", raw)
    try:
        assert logsetup.setup_logging() == want and root.level == want
        assert logging.getLogger("httpx").level == (logging.DEBUG if want == logging.DEBUG else logging.WARNING)
    finally:
        root.setLevel(old)
