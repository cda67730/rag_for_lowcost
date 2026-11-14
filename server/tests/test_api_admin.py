"""後台 API 第一批：登入與 token、權限（四種角色＋外人）、機器人、設定、知識庫掛載、文件上傳到審核、背景任務。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.routes.admin as A
import app.routes.chat as C
from app import auth
from app.main import app
from db import models as m
from db.session import get_session
from test_ingest import FakeLLM, md

PW = "correct-horse-1"


@pytest.fixture
def env(db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    ws = m.Workspace(slug="w", name="w"); other = m.Workspace(slug="o", name="o"); db.add_all([ws, other]); db.flush()
    bot = m.Bot(workspace_id=ws.id, slug="bot-a", name="A"); bot2 = m.Bot(workspace_id=ws.id, slug="bot-b", name="B"); db.add_all([bot, bot2]); db.flush()
    db.add(m.Flow(bot_id=bot.id, name="預設", is_default=True))
    users = {}
    for login, role, b, w in [("owner", "owner", None, ws), ("wsadmin", "admin", None, ws), ("botadmin", "admin", bot, ws), ("rev", "reviewer", None, ws),
                              ("ana", "analyst", bot, ws), ("outsider", "owner", None, other)]:
        u = m.User(login=login, display_name=login, pw_hash=auth.hash_password(PW)); db.add(u); db.flush()
        db.add(m.Membership(user_id=u.id, workspace_id=w.id, bot_id=b.id if b else None, role=role))
        users[login] = u
    db.flush()
    llm = FakeLLM()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[C.get_llm] = lambda: llm
    app.dependency_overrides[A.get_session_factory] = lambda: (lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint"))
    c = TestClient(app)
    tokens = {}

    def as_(who):
        if who not in tokens:
            r = c.post("/api/admin/auth/login", json={"login": who, "password": PW})
            assert r.status_code == 200, r.text
            tokens[who] = r.json()["token"]
        return {"Authorization": "Bearer " + tokens[who]}

    yield c, db, ws, bot, bot2, users, as_
    app.dependency_overrides.clear()


def test_login_lock_logout_and_password(env):
    c, db, ws, bot, bot2, users, as_ = env
    assert c.post("/api/admin/auth/login", json={"login": "nobody", "password": PW}).status_code == 401
    for _ in range(5):
        assert c.post("/api/admin/auth/login", json={"login": "rev", "password": "wrong"}).json()["code"] == "bad_login"
    r = c.post("/api/admin/auth/login", json={"login": "rev", "password": PW})
    assert r.status_code == 429 and r.json()["code"] == "locked"
    h = as_("owner")
    me = c.get("/api/admin/me", headers=h).json()
    assert me["login"] == "owner" and me["workspaces"][0]["roles"] == ["owner"]
    assert c.get("/api/admin/me").status_code == 401 and c.get("/api/admin/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    new = c.post("/api/admin/auth/refresh", headers=h).json()["token"]
    assert c.get("/api/admin/me", headers={"Authorization": "Bearer " + new}).status_code == 200
    assert c.post("/api/admin/me/password", headers=h, json={"old": "x", "new": "y" * 12}).status_code == 422
    assert c.post("/api/admin/me/password", headers=h, json={"old": PW, "new": "short"}).json()["code"] == "weak_password"
    t2 = c.post("/api/admin/me/password", headers=h, json={"old": PW, "new": "another-long-pw"}).json()["token"]
    assert c.get("/api/admin/me", headers=h).status_code == 401                         # 舊 token 失效
    h2 = {"Authorization": "Bearer " + t2}
    assert c.post("/api/admin/auth/logout", headers=h2).json() == {"ok": True}
    assert c.get("/api/admin/me", headers=h2).status_code == 401
    users["wsadmin"].disabled = True; db.flush()
    assert c.post("/api/admin/auth/login", json={"login": "wsadmin", "password": PW}).status_code == 401


def test_expired_token(env, monkeypatch):
    c, db, ws, bot, bot2, users, as_ = env
    monkeypatch.setattr(auth, "TOKEN_HOURS", -1)
    t = auth.make_token(users["owner"])
    assert c.get("/api/admin/me", headers={"Authorization": "Bearer " + t}).status_code == 401


def test_bot_visibility_and_roles(env):
    c, db, ws, bot, bot2, users, as_ = env
    W = f"/api/admin/workspaces/{ws.id}"
    assert {b["slug"] for b in c.get(W + "/bots", headers=as_("owner")).json()["items"]} == {"bot-a", "bot-b"}
    assert [b["slug"] for b in c.get(W + "/bots", headers=as_("botadmin")).json()["items"]] == ["bot-a"]
    assert c.get(W + "/bots", headers=as_("outsider")).status_code == 404
    assert c.get(f"/api/admin/bots/{bot2.id}", headers=as_("botadmin")).status_code == 404     # 看不到別的 Bot
    assert c.get(f"/api/admin/bots/{bot.id}", headers=as_("ana")).status_code == 200
    assert c.patch(f"/api/admin/bots/{bot.id}", headers=as_("ana"), json={"name": "x"}).status_code == 403
    assert c.patch(f"/api/admin/bots/{bot.id}", headers=as_("botadmin"), json={"name": "新名字"}).json()["name"] == "新名字"
    # 建 Bot：只有 owner 與 Workspace 層 admin
    assert c.post(W + "/bots", headers=as_("botadmin"), json={"slug": "new-bot", "name": "N"}).status_code == 403
    r = c.post(W + "/bots", headers=as_("wsadmin"), json={"slug": "new-bot", "name": "N"})
    assert r.status_code == 201 and db.query(m.Flow).filter_by(bot_id=r.json()["id"], is_default=True).count() == 1
    assert c.post(W + "/bots", headers=as_("owner"), json={"slug": "new-bot", "name": "N"}).status_code == 409
    assert c.post(W + "/bots", headers=as_("owner"), json={"slug": "X!", "name": ""}).json()["fields"].keys() == {"slug", "name"}
    assert c.get("/api/admin/bots/not-a-uuid", headers=as_("owner")).status_code == 404
    assert db.query(m.AuditLog).filter_by(action="bot.create").count() == 1


def test_settings(env):
    c, db, ws, bot, bot2, users, as_ = env
    S = f"/api/admin/bots/{bot.id}/settings"
    g = c.get(S, headers=as_("botadmin")).json()
    item = {i["key"]: i for i in g["items"]}
    assert item["min_score"]["is_default"] and item["min_score"]["note"] and "basic" in g["tiers"] and g["schema"]
    assert "answer_schema" in item and item["answer_schema"]["text"]
    assert c.get(S, headers=as_("ana")).status_code == 403
    assert c.patch(S, headers=as_("botadmin"), json={"items": [{"key": "min_score", "value": 0.5}]}).json()["changed"] == ["min_score"]
    item = {i["key"]: i for i in c.get(S, headers=as_("botadmin")).json()["items"]}
    assert item["min_score"]["value"] == "0.5" and not item["min_score"]["is_default"]
    r = c.patch(S, headers=as_("botadmin"), json={"items": [{"key": "nope", "value": 1}]})
    assert r.status_code == 422 and r.json()["code"] == "unknown_key"
    assert c.patch(S, headers=as_("botadmin"), json={"items": [{"key": "min_score", "value": "abc"}]}).json()["fields"].keys() == {"min_score"}
    assert c.post(f"/api/admin/bots/{bot.id}/tier", headers=as_("botadmin"), json={"tier": "full"}).status_code == 200
    assert c.post(f"/api/admin/bots/{bot.id}/tier", headers=as_("botadmin"), json={"tier": "zzz"}).status_code == 422


def test_kbs_and_mounting(env):
    c, db, ws, bot, bot2, users, as_ = env
    W = f"/api/admin/workspaces/{ws.id}"
    mk = lambda who, **kw: c.post(W + "/kbs", headers=as_(who), json={"slug": kw.pop("slug"), "name": "庫", **kw})
    assert mk("botadmin", slug="k1").status_code == 403
    k1 = mk("wsadmin", slug="k1").json()
    k2 = mk("owner", slug="k2", embed_model="text-embedding-3-small", embed_dim=1536).json()
    k3 = mk("owner", slug="k3", visibility="restricted").json()
    bad = mk("owner", slug="k4", embed_dim=999, chunk_size=10, visibility="x")
    assert bad.status_code == 422 and bad.json()["fields"].keys() == {"embed_dim", "chunk_size", "chunk_overlap", "visibility"}
    assert mk("owner", slug="k1").status_code == 409
    K = f"/api/admin/bots/{bot.id}/knowledge"
    put = lambda items: c.put(K, headers=as_("botadmin"), json={"items": items})
    assert put([{"kb_id": k1["id"]}, {"kb_id": k2["id"]}]).json()["fields"]["items"].startswith("同一個機器人")
    assert "沒有開放" in str(put([{"kb_id": k3["id"]}]).json()["fields"])
    db.add(m.KbAllowedBot(kb_id=k3["id"], bot_id=bot.id)); db.flush()
    r = put([{"kb_id": k1["id"], "label": "手冊", "min_hits": 2}, {"kb_id": k3["id"], "weight": 1.5}])
    assert r.status_code == 200 and [(i["label"], i["min_hits"], i["weight"]) for i in r.json()["items"]] == [("手冊", 2, 1.0), ("庫", 0, 1.5)]
    assert put([{"kb_id": k1["id"], "weight": 9}]).status_code == 422
    # Bot 層的人只看得到自己 Bot 掛的庫
    assert {k["slug"] for k in c.get(W + "/kbs", headers=as_("ana")).json()["items"]} == {"k1", "k3"}
    assert c.get(f"/api/admin/kbs/{k2['id']}", headers=as_("ana")).status_code == 404
    assert c.get(f"/api/admin/kbs/{k2['id']}", headers=as_("rev")).status_code == 200      # Workspace 層的人都看得到
    assert mk("outsider", slug="zz").status_code == 404                                    # 外人：不洩漏存在與否
    assert c.get(f"/api/admin/kbs/{k1['id']}", headers=as_("ana")).json()["bots"][0]["name"] == "A"


def test_document_upload_review_flow(env):
    c, db, ws, bot, bot2, users, as_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768, chunk_size=200, chunk_overlap=20)
    db.add(k); db.flush()
    D = f"/api/admin/kbs/{k.id}/documents"
    assert c.post(D, headers=as_("botadmin"), files={"file": ("a.md", md(), "text/markdown")}).status_code == 403
    r = c.post(D, headers=as_("rev"), files={"file": ("手冊.md", md(), "text/markdown")}, data={"source_note": "出處"})
    assert r.status_code == 202, r.text
    doc_id, job_id = r.json()["doc_id"], r.json()["job_id"]
    db.expire_all()
    j = c.get(f"/api/admin/jobs/{job_id}", headers=as_("rev")).json()
    assert j["status"] == "done" and j["result"]["chunks"] > 0                             # 背景任務在回應後跑完
    d = c.get(f"/api/admin/documents/{doc_id}", headers=as_("rev")).json()
    assert d["status"] == "pending" and d["source_note"] == "出處" and d["job"]["status"] == "done"
    ch = c.get(f"/api/admin/documents/{doc_id}/chunks", headers=as_("rev")).json()["items"]
    assert ch and all(x["has_vector"] and x["type"] == "具體做法" for x in ch)
    assert c.post(D, headers=as_("rev"), files={"file": ("again.md", md(), "text/markdown")}).status_code == 409
    assert c.post(D, headers=as_("rev"), files={"file": ("a.exe", b"MZ", "application/octet-stream")}).json()["code"] == "bad_file"
    busy = m.Document(kb_id=k.id, title="處理中", status="processing"); db.add(busy); db.flush()
    assert c.patch(f"/api/admin/documents/{busy.id}", headers=as_("rev"), json={"status": "approved"}).status_code == 409
    p = c.post(D, headers=as_("wsadmin"), json={"title": "貼上", "text": "一段文字\n\n第二段"})
    assert p.status_code == 202
    # 審核：核可後段落才進檢索
    assert c.patch(f"/api/admin/documents/{doc_id}", headers=as_("rev"), json={"status": "deleted"}).status_code == 422
    r = c.patch(f"/api/admin/documents/{doc_id}", headers=as_("rev"), json={"status": "approved", "title": "新標題"})
    assert r.json()["status"] == "approved" and r.json()["title"] == "新標題"
    db.expire_all()
    assert all(x.active for x in db.query(m.Chunk).filter_by(doc_id=doc_id))
    lst = c.get(D + "?status=approved", headers=as_("rev")).json()
    assert [x["id"] for x in lst["items"]] == [doc_id] and lst["next"] is None
    f = c.get(f"/api/admin/documents/{doc_id}/file", headers=as_("rev"))
    assert f.content == md() and "filename*=UTF-8''" in f.headers["content-disposition"]
    assert c.get(f"/api/admin/documents/{doc_id}", headers=as_("ana")).status_code == 403
    assert c.get(f"/api/admin/jobs/{job_id}", headers=as_("outsider")).status_code == 404


def test_pagination_and_repair(env):
    c, db, ws, bot, bot2, users, as_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768); db.add(k); db.flush()
    t0 = datetime.now(timezone.utc)
    for i in range(5):
        db.add(m.Document(kb_id=k.id, title=f"d{i}", status="pending", uploaded_at=t0 - timedelta(minutes=i)))
    db.flush()
    D = f"/api/admin/kbs/{k.id}/documents"
    p1 = c.get(D + "?limit=2", headers=as_("owner")).json()
    p2 = c.get(D + f"?limit=2&cursor={p1['next']}", headers=as_("owner")).json()
    p3 = c.get(D + f"?limit=2&cursor={p2['next']}", headers=as_("owner")).json()
    assert [x["title"] for x in p1["items"] + p2["items"] + p3["items"]] == ["d0", "d1", "d2", "d3", "d4"] and p3["next"] is None
    assert c.post(f"/api/admin/kbs/{k.id}/repair", headers=as_("rev")).status_code == 403
    assert c.post(f"/api/admin/kbs/{k.id}/repair", headers=as_("wsadmin")).json() == {"fixed": 0}


def test_stale_jobs_marked_failed(env):
    c, db, ws, *_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768); db.add(k); db.flush()
    d = m.Document(kb_id=k.id, title="d", status="processing"); db.add(d); db.flush()
    db.add(m.Job(workspace_id=ws.id, kind="ingest", target=str(d.id), status="running")); db.flush()
    assert A.fail_stale_jobs(db) == 1 and d.status == "failed" and "重新啟動" in d.error
