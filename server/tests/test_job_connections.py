"""背景工作（文件處理、評測出題與執行、圖譜草稿）與圖譜標註：每次等模型時都不能握著資料庫交易（連線要先還回連線池）。"""
import asyncio

from sqlalchemy.orm import Session

import app.routes.admin as A
import app.routes.chat as C
import services.ingest as I
from app.main import app
from db import models as m
from test_api_admin import env  # noqa: F401
from test_api_admin_eval import EvalLLM, seed
from test_api_admin_kg import GRAPH, KgLLM, setup_kb
from test_ingest import FakeLLM, md


def spy(base, sessions):
    """模型呼叫時，記下所有 session 是否還在交易中"""
    class Spy(base):
        seen: list = []

        def _rec(self):
            Spy.seen.append(any(s.in_transaction() for s in sessions))

        async def generate_json(self, *a, **kw):
            self._rec()
            return await super().generate_json(*a, **kw)

        async def embed(self, *a, **kw):
            self._rec()
            return await super().embed(*a, **kw)
    Spy.seen = []
    return Spy


def wire(db, llm_cls):
    # 背景工作看它自己開的 session；請求用的 session 另外檢查（測試的 session 會在 commit 後重新讀物件而開新交易，正式環境不會）
    sessions = []

    def factory():
        s = Session(bind=db.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False)     # 同正式環境的 sessionmaker
        sessions.append(s)
        return s
    L = spy(llm_cls, sessions)
    llm = L()
    app.dependency_overrides[C.get_llm] = lambda: llm
    app.dependency_overrides[A.get_session_factory] = lambda: factory
    db.commit()
    return L, factory


def test_eval_generate_and_run_release(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    d, chs = seed(db, bot)
    L, _ = wire(db, EvalLLM)
    j = c.post(f"/api/admin/bots/{bot.id}/eval/generate", headers=as_("botadmin"), json={"max_chunks": 6, "special": 2}).json()["job_id"]
    db.expire_all()
    assert c.get(f"/api/admin/jobs/{j}", headers=as_("botadmin")).json()["result"]["added"] == 4
    assert len(L.seen) >= 2 and not any(L.seen)
    L.seen.clear()
    db.commit()
    r = c.post(f"/api/admin/bots/{bot.id}/eval/runs", headers=as_("botadmin"), json={"repeat": 2}).json()
    db.expire_all()
    assert c.get(f"/api/admin/eval/runs/{r['run_id']}", headers=as_("ana")).json()["status"] == "done"
    assert len(L.seen) >= 8 and not any(L.seen)             # 問答流程＋評審，每一次都沒握著交易


def test_kg_generate_and_tag_release(env):  # noqa: F811
    import json
    c, db, ws, bot, bot2, users, as_ = env
    k, d, chs = setup_kb(db, ws, bot)
    L, _ = wire(db, KgLLM)
    j = c.post(f"/api/admin/kbs/{k.id}/kg/generate", headers=as_("wsadmin")).json()["job_id"]
    db.expire_all()
    assert c.get(f"/api/admin/jobs/{j}", headers=as_("wsadmin")).json()["status"] == "done"
    assert len(L.seen) >= 2 and not any(L.seen)
    c.post(f"/api/admin/kbs/{k.id}/kg/import", headers=as_("owner"), content=json.dumps(GRAPH))
    L.seen.clear()
    db.commit()
    seen_db = []
    orig = L.generate_json

    async def gj(self, *a, **kw):
        seen_db.append(db.in_transaction())     # 圖譜標註在請求裡做：請求的 session 也不能握著交易
        return await orig(self, *a, **kw)
    L.generate_json = gj
    r = c.post(f"/api/admin/documents/{d.id}/kg-tag", headers=as_("wsadmin")).json()
    assert seen_db and not any(seen_db)
    assert r["links"] == 3 and L.seen and not any(L.seen)
    assert db.query(m.KgLink).count() == 3


class TagLLM(FakeLLM):
    async def generate_json(self, model, prompt, **kw):
        if "做標註" in prompt:
            return {"items": [{"chunk": 1, "main": ["n1"], "new": [{"name": "新概念"}]}]}, None
        return await super().generate_json(model, prompt, **kw)


def ingest_once(db, k, factory, L):
    doc = I.create_document(db, k, filename=f"a{len(L.seen)}.md", data=md(4 + len(L.seen) % 5))
    job = m.Job(workspace_id=k.workspace_id, kb_id=k.id, kind="ingest", target=str(doc.id)); db.add(job)
    db.commit()
    asyncio.run(I.run_ingest_job(factory, L(), job.id))
    db.expire_all()
    return db.get(m.Job, job.id), db.get(m.Document, doc.id)


def test_ingest_job_releases_and_tags(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768, auto_kg_tag=True); db.add(k); db.flush()
    db.add(m.KgNode(kb_id=k.id, node_key="n1", name="收拾", ntype="概念")); db.flush()
    L, factory = wire(db, TagLLM)
    job, doc = ingest_once(db, k, factory, L)
    assert job.status == "done" and doc.status == "pending" and "候選" in doc.error
    assert db.query(m.KgLink).count() >= 1 and db.query(m.KgCandidate).count() >= 1
    assert len(L.seen) >= 3 and not any(L.seen)              # 分類、向量、標註
    # 標註寫到一半失敗：段落照常寫入，不留半套標註
    import services.kg_admin as K
    orig = K.save_mapping

    def half(session, kb_id, doc_id, res):
        orig(session, kb_id, doc_id, res)
        session.flush()
        raise RuntimeError("寫到一半")
    K.save_mapping = half
    try:
        n_links, n_cands = db.query(m.KgLink).count(), db.query(m.KgCandidate).count()
        job, doc = ingest_once(db, k, factory, L)
        assert job.status == "done" and doc.status == "pending" and "圖譜標註失敗" in doc.error and doc.chunk_count > 0
        assert db.query(m.Chunk).filter_by(doc_id=doc.id).count() == doc.chunk_count
        assert (db.query(m.KgLink).count(), db.query(m.KgCandidate).count()) == (n_links, n_cands)
    finally:
        K.save_mapping = orig


def test_heartbeat_and_monitor(env, monkeypatch):  # noqa: F811
    """執行中每隔一段時間更新心跳；做完就停。定期檢查會把沒心跳的工作標失敗"""
    from datetime import datetime, timedelta, timezone
    from services import jobs
    c, db, ws, bot, bot2, users, as_ = env
    factory = lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False)
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    j = m.Job(workspace_id=ws.id, kind="eval_run", status="running", updated_at=old); db.add(j)
    gone = m.Job(workspace_id=ws.id, kind="eval_run", status="running", updated_at=old); db.add(gone)
    db.commit()
    monkeypatch.setattr(jobs, "BEAT_SECONDS", 0.02)
    ran = []

    async def work(x):
        await asyncio.sleep(0.15)
        ran.append(x)
    asyncio.run(jobs.run(factory, j.id, work, "ok"))
    db.expire_all()
    assert ran == ["ok"] and db.get(m.Job, j.id).updated_at > old and db.get(m.Job, gone.id).updated_at == old
    t1 = db.get(m.Job, j.id).updated_at
    asyncio.run(asyncio.sleep(0.1))
    db.expire_all()
    assert db.get(m.Job, j.id).updated_at == t1                     # 做完就不再更新

    async def mon():
        t = asyncio.create_task(jobs.monitor(factory, every=0.02))
        await asyncio.sleep(0.15)
        t.cancel()
    asyncio.run(mon())
    db.expire_all()
    assert db.get(m.Job, gone.id).status == "failed" and db.get(m.Job, j.id).status == "running"


def test_progress_updates_heartbeat(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    j = m.Job(workspace_id=ws.id, kind="eval_run", status="running"); db.add(j); db.commit()
    db.execute(__import__("sqlalchemy").text("UPDATE jobs SET updated_at = now() - interval '1 hour' WHERE id = :i"), {"i": j.id})
    db.expire_all()
    before = db.get(m.Job, j.id).updated_at
    db.get(m.Job, j.id).progress = 0.5
    db.flush(); db.expire_all()
    assert db.get(m.Job, j.id).updated_at > before


def test_instance_shared_by_spawned_workers():
    """uvicorn 多開用 spawn 起子行程：子行程算出的執行個體要和主行程相同"""
    import multiprocessing as mp
    from services import jobs
    ctx = mp.get_context("spawn")
    with ctx.Pool(1) as p:
        child = p.apply(_child_instance)
    assert child == jobs.instance_id() and child.split(":")[1] == str(__import__("os").getpid())


def _child_instance():
    from services import jobs
    return jobs.instance_id()
