"""對話紀錄 → 專家修正問答 → 存成知識庫的新文件（切段、分類、算向量；可選處理完直接核可）。"""
from sqlalchemy.orm import Session

import app.routes.admin as A
import app.routes.chat as C
from app.main import app
from db import models as m
from test_api_admin import env  # noqa: F401
from test_ingest import FakeLLM


def setup(c, db, ws, bot, bot2):
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="手冊庫", embed_model="gemini-embedding-001", embed_dim=768); db.add(k)
    other = m.KnowledgeBase(workspace_id=ws.id, slug="k2", name="沒掛的庫", embed_model="gemini-embedding-001", embed_dim=768); db.add(other)
    db.flush()
    db.add(m.BotKnowledge(bot_id=bot.id, kb_id=k.id, label="手冊"))
    db.add(m.BotKnowledge(bot_id=bot2.id, kb_id=other.id, label="別的機器人的庫"))     # 掛在別的機器人：不能選
    conv = m.Conversation(bot_id=bot.id, session="s1", turn=1, user_text="孩子A收玩具時一直丟積木", rtype="answer", answer={"text": "原本的回答"})
    db.add(conv); db.flush()
    llm = FakeLLM()
    app.dependency_overrides[C.get_llm] = lambda: llm
    app.dependency_overrides[A.get_session_factory] = lambda: (lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False))
    db.commit()
    return k, other, conv, llm


def test_expert_correction_creates_document(env):  # noqa: F811
    """專家＝看得到這個機器人的紀錄（analyst）又能管文件（工作區 reviewer）的人"""
    c, db, ws, bot, bot2, users, as_ = env
    k, other, conv, llm = setup(c, db, ws, bot, bot2)
    assert c.get(f"/api/admin/logs/{conv.id}", headers=as_("rev")).status_code == 403          # 只有 reviewer：看不到對話原文
    db.add(m.Membership(user_id=users["rev"].id, workspace_id=ws.id, bot_id=bot.id, role="analyst")); db.commit()   # 工作區 reviewer＋這個機器人的 analyst
    d = c.get(f"/api/admin/logs/{conv.id}", headers=as_("rev")).json()
    assert d["kb_targets"] == [{"id": str(k.id), "name": "手冊庫", "label": "手冊", "chunk_size": k.chunk_size}] and d["corrected_doc"] is None
    assert c.get(f"/api/admin/logs/{conv.id}", headers=as_("ana")).json()["kb_targets"] == []     # 分析者看得到紀錄，但不能改知識庫
    assert c.get(f"/api/admin/logs/{conv.id}", headers=as_("ana")).json()["correct_block"] == "no_permission"
    assert d["correct_block"] is None
    body = {"kb_id": str(k.id), "question": "孩子A收玩具時丟積木怎麼辦？", "answer": "先蹲下來、用簡短的話說明期待，再示範一次。", "approve": True}
    assert c.post(f"/api/admin/logs/{conv.id}/to-kb", headers=as_("ana"), json=body).status_code == 403
    assert c.post(f"/api/admin/logs/{conv.id}/to-kb", headers=as_("rev"), json={**body, "kb_id": str(other.id)}).json()["code"] == "kb_not_allowed"
    assert c.post(f"/api/admin/logs/{conv.id}/to-kb", headers=as_("rev"), json={**body, "answer": " "}).json()["fields"] == {"answer": "必填"}
    r = c.post(f"/api/admin/logs/{conv.id}/to-kb", headers=as_("rev"), json=body)
    assert r.status_code == 202 and r.json()["approve"] is True
    db.expire_all()
    doc = db.get(m.Document, __import__("uuid").UUID(r.json()["doc_id"]))
    assert doc.status == "approved" and doc.reviewed_by == users["rev"].id and doc.title == "專家修正：孩子A收玩具時丟積木怎麼辦？"
    assert "對話紀錄 #" in doc.source_note and doc.chunk_count == 1
    (ch,) = db.query(m.Chunk).filter_by(doc_id=doc.id).all()
    assert ch.text.startswith("問：孩子A收玩具時丟積木怎麼辦？") and "答：先蹲下來" in ch.text and ch.embedding is not None and ch.active
    assert db.get(m.Conversation, conv.id).corrected_doc_id == doc.id
    d = c.get(f"/api/admin/logs/{conv.id}", headers=as_("ana")).json()
    assert d["corrected"] is True and d["corrected_doc"]["status"] == "approved"
    assert c.get(f"/api/admin/bots/{bot.id}/logs", headers=as_("ana")).json()["items"][0]["corrected"] is True
    assert c.post(f"/api/admin/logs/{conv.id}/to-kb", headers=as_("rev"), json=body).json()["code"] == "duplicate"     # 一模一樣的問答不重複建
    log = db.query(m.AuditLog).filter_by(action="log.to_kb").one()
    assert log.detail["log_id"] == conv.id and log.bot_id == bot.id


def test_correction_without_approve_waits_for_review(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k, other, conv, llm = setup(c, db, ws, bot, bot2)
    r = c.post(f"/api/admin/logs/{conv.id}/to-kb", headers=as_("wsadmin"), json={"kb_id": str(k.id), "question": "問題", "answer": "回答", "title": "自訂標題"}).json()
    db.expire_all()
    doc = db.get(m.Document, __import__("uuid").UUID(r["doc_id"]))
    assert doc.status == "pending" and doc.title == "自訂標題" and doc.reviewed_by is None
    job = db.get(m.Job, __import__("uuid").UUID(r["job_id"]))
    assert job.status == "done" and job.result["status"] == "pending" and "auto_approve_by" not in job.result


def test_correct_block_no_kb(env):  # noqa: F811
    """機器人還沒掛知識庫：告訴管理者原因，而不是按鈕不見"""
    c, db, ws, bot, bot2, users, as_ = env
    conv = m.Conversation(bot_id=bot2.id, session="s", turn=1, user_text="q", rtype="answer"); db.add(conv); db.flush()
    d = c.get(f"/api/admin/logs/{conv.id}", headers=as_("owner")).json()
    assert d["kb_targets"] == [] and d["correct_block"] == "no_kb"
