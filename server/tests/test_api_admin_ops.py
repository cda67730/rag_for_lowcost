"""後台 API 第三批：成員與稽核、刪 Bot、模型、知識庫與文件管理、測試對話、紀錄與統計。"""
from db import models as m
from test_api_admin import env  # noqa: F401
from test_chat_graph import FakeLLM as ChatLLM
from test_ingest import md

import app.routes.chat as C
from app.main import app


def test_members(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    W = f"/api/admin/workspaces/{ws.id}"
    assert c.get(W, headers=as_("ana")).json()["bots"] == 2
    assert c.get(W, headers=as_("outsider")).status_code == 404
    assert c.get(W + "/members", headers=as_("wsadmin")).status_code == 403
    r = c.post(W + "/members", headers=as_("owner"), json={"login": "newbie", "role": "reviewer", "bot_id": str(bot.id)}).json()
    assert r["temp_password"] and r["role"] == "reviewer"
    nt = c.post("/api/admin/auth/login", json={"login": "newbie", "password": r["temp_password"]}).json()["token"]
    assert c.post(W + "/members", headers=as_("owner"), json={"login": "newbie", "role": "admin", "bot_id": str(bot.id)}).status_code == 409
    assert c.post(W + "/members", headers=as_("owner"), json={"login": "x", "role": "owner", "bot_id": str(bot.id)}).status_code == 422
    assert c.post(W + "/members", headers=as_("owner"), json={"login": "x", "role": "boss"}).status_code == 422
    assert c.post(W + "/members", headers=as_("owner"), json={"login": "y", "role": "admin", "password": "short"}).json()["code"] == "weak_password"
    own = next(x for x in c.get(W + "/members", headers=as_("owner")).json()["items"] if x["login"] == "owner")
    assert c.patch(f"{W}/members/{own['id']}", headers=as_("owner"), json={"role": "admin"}).json()["code"] == "last_owner"
    assert c.delete(f"{W}/members/{own['id']}", headers=as_("owner")).json()["code"] == "last_owner"
    assert c.patch(f"{W}/members/{r['id']}", headers=as_("owner"), json={"role": "analyst"}).json()["role"] == "analyst"
    tp = c.post(f"{W}/users/{r['user_id']}/reset-password", headers=as_("owner")).json()["temp_password"]
    assert c.get("/api/admin/me", headers={"Authorization": "Bearer " + nt}).status_code == 401      # 重設後舊登入失效
    assert c.post("/api/admin/auth/login", json={"login": "newbie", "password": r["temp_password"]}).status_code == 401
    assert c.post("/api/admin/auth/login", json={"login": "newbie", "password": tp}).status_code == 200
    assert c.post(f"{W}/users/{users['outsider'].id}/reset-password", headers=as_("owner")).status_code == 404   # 別的工作區的人
    assert c.delete(f"{W}/members/{r['id']}", headers=as_("owner")).json() == {"ok": True}
    a = c.get(W + "/audit?action=member", headers=as_("owner")).json()["items"]
    assert [x["action"] for x in a] == ["member.remove", "member.update", "member.add"] and a[0]["user"] == "owner"
    assert c.patch(W, headers=as_("owner"), json={"name": "新名"}).json()["name"] == "新名"


def test_delete_bot_needs_confirm(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    assert c.delete(f"/api/admin/bots/{bot2.id}", headers=as_("wsadmin")).status_code == 403
    assert c.delete(f"/api/admin/bots/{bot2.id}", headers=as_("owner")).json()["code"] == "confirm"
    assert c.delete(f"/api/admin/bots/{bot2.id}?confirm=bot-b", headers=as_("owner")).json() == {"ok": True}
    assert db.get(m.Bot, bot2.id) is None


def test_models_and_ping(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    llm = ChatLLM()
    llm.keys = {"gemini": "k", "openai": ""}
    app.dependency_overrides[C.get_llm] = lambda: llm
    r = c.get("/api/admin/models", headers=as_("botadmin")).json()
    assert r["providers"] == {"gemini": True, "openai": False}
    assert {e["id"]: e["available"] for e in r["embed"]}["text-embedding-3-small"] is False
    p = c.post("/api/admin/models/ping", headers=as_("botadmin"), json={"model": "gemini-x"}).json()
    assert p["ok"] is True and "ms" in p
    llm.resp["answer"] = RuntimeError("401 金鑰無效")
    bad = c.post("/api/admin/models/ping", headers=as_("botadmin"), json={"model": "gemini-x"}).json()
    assert bad["ok"] is False and "金鑰無效" in bad["error"]


def test_kb_management(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768, visibility="restricted")
    k2 = m.KnowledgeBase(workspace_id=ws.id, slug="k2", name="k2", embed_model="text-embedding-3-small", embed_dim=1536)
    db.add_all([k, k2]); db.flush()
    K = f"/api/admin/kbs/{k.id}"
    assert c.patch(K, headers=as_("wsadmin"), json={"embed_dim": 1536}).json()["fields"].keys() == {"embed_model"}
    assert c.patch(K, headers=as_("wsadmin"), json={"name": "新", "chunk_size": 800, "auto_kg_tag": True}).json()["chunk_size"] == 800
    av = c.get(f"/api/admin/bots/{bot.id}/knowledge/available", headers=as_("botadmin")).json()["items"]
    assert [x["slug"] for x in av] == ["k2"]                                     # restricted 的還沒開放
    assert c.put(K + "/allowed-bots", headers=as_("wsadmin"), json={"bot_ids": [str(bot.id), str(bot.id)]}).json()["bot_ids"] == [str(bot.id)]
    c.put(f"/api/admin/bots/{bot.id}/knowledge", headers=as_("botadmin"), json={"items": [{"kb_id": str(k.id)}]})
    av = {x["slug"]: x["compatible"] for x in c.get(f"/api/admin/bots/{bot.id}/knowledge/available", headers=as_("botadmin")).json()["items"]}
    assert av == {"k": True, "k2": False}                                        # 向量模型不同的標成不相容
    assert c.delete(K, headers=as_("wsadmin")).status_code == 403
    assert c.delete(K, headers=as_("owner")).json()["code"] == "mounted"
    c.put(f"/api/admin/bots/{bot.id}/knowledge", headers=as_("botadmin"), json={"items": []})
    assert c.delete(K, headers=as_("owner")).json() == {"ok": True} and db.get(m.KnowledgeBase, k.id) is None


def test_document_management(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768, chunk_size=200, chunk_overlap=20)
    db.add(k); db.flush()
    doc = c.post(f"/api/admin/kbs/{k.id}/documents", headers=as_("rev"), files={"file": ("a.md", md(), "text/markdown")}).json()["doc_id"]
    ch = c.get(f"/api/admin/documents/{doc}/chunks", headers=as_("rev")).json()["items"]
    assert c.patch(f"/api/admin/chunks/{ch[0]['id']}", headers=as_("rev"), json={"type": "案例"}).json()["type"] == "案例"
    assert c.patch(f"/api/admin/chunks/{ch[0]['id']}", headers=as_("rev"), json={"type": "亂寫"}).status_code == 422
    assert c.post(f"/api/admin/documents/{doc}/classify", headers=as_("rev")).status_code == 403
    r = c.post(f"/api/admin/documents/{doc}/classify", headers=as_("wsadmin")).json()
    assert r["classified"] == len(ch) and db.get(m.Chunk, ch[0]["id"]).ctype == "具體做法"
    class Broken:
        async def generate_json(self, *a, **k):
            raise RuntimeError("壞了")
    app.dependency_overrides[C.get_llm] = lambda: Broken()
    c.patch(f"/api/admin/chunks/{ch[0]['id']}", headers=as_("rev"), json={"type": "案例"})
    r = c.post(f"/api/admin/documents/{doc}/classify", headers=as_("wsadmin")).json()
    db.expire_all()
    assert r["failed_batches"] == 1 and db.get(m.Chunk, ch[0]["id"]).ctype == "案例"          # 分類失敗保留原本的
    assert c.delete(f"/api/admin/documents/{doc}", headers=as_("rev")).status_code == 403
    assert c.delete(f"/api/admin/documents/{doc}", headers=as_("wsadmin")).json() == {"ok": True}
    assert db.query(m.Chunk).filter_by(doc_id=doc).count() == 0


def test_test_chat_logs_and_stats(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    llm = ChatLLM()
    app.dependency_overrides[C.get_llm] = lambda: llm
    h = as_("botadmin")
    r = c.post(f"/api/admin/bots/{bot.id}/test-chat", headers=h, json={"message": "小宇丟積木", "settings_override": {"enable_deid": False, "enable_triage": False}})
    assert r.status_code == 200 and r.json()["trace"] and r.json()["session"].startswith("admin-test-") and r.json()["deidText"] == "小宇丟積木"
    assert c.post(f"/api/admin/bots/{bot.id}/test-chat", headers=h, json={"message": "x", "settings_override": {"nope": 1}}).status_code == 422
    assert c.post(f"/api/admin/bots/{bot.id}/test-chat", headers=h, json={"message": " "}).status_code == 422
    # 一般使用者的紀錄＋回饋
    now_rows = [("s1", 1, "answer", 3, 2, 0.8, False, "甲"), ("s1", 2, "conservative", 0, 0, 0.3, False, "甲"), ("s2", 1, "answer", 2, 1, 0.7, True, "乙")]
    for sess, turn, t, hits, cited, top, rw, unit in now_rows:
        db.add(m.Conversation(bot_id=bot.id, session=sess, turn=turn, unit=unit, user_text=f"{sess}-{turn} 丟積木", rtype=t, hits=hits, cited=cited, top_score=top,
                              rewritten=rw, ms=1000, sources=[{"ref": "K1", "title": "手冊"}] if cited else [],
                              quality={"gap": {"kind": "沒有可用知識"}, "l5": []} if t == "conservative" else {"gap": None, "l5": [{"issues": [{"type": "語氣"}]}]},
                              stability={"ev": [{"kind": "缺欄位"}]}))
    db.add(m.Feedback(bot_id=bot.id, session="s1", turn=1, kind="有幫助")); db.add(m.Feedback(bot_id=bot.id, session="s2", turn=1, kind="複製：建議做法"))
    db.flush()
    L = f"/api/admin/bots/{bot.id}/logs"
    items = c.get(L, headers=as_("ana")).json()["items"]
    assert len(items) == 3 and all(not x["session"].startswith("admin-test") for x in items)
    assert len(c.get(L + "?include_test=true", headers=as_("ana")).json()["items"]) == 4
    assert [x["session"] for x in c.get(L + "?unit=乙", headers=h).json()["items"]] == ["s2"]
    assert [x["type"] for x in c.get(L + "?type=conservative", headers=h).json()["items"]] == ["conservative"]
    assert len(c.get(L + "?q=s1-", headers=h).json()["items"]) == 2 and len(c.get(L + "?rewritten=true", headers=h).json()["items"]) == 1
    one = next(x for x in items if x["session"] == "s1" and x["turn"] == 1)
    assert one["fb"] == "up"
    d = c.get(f"/api/admin/logs/{one['id']}", headers=as_("ana")).json()
    assert d["message"] == "s1-1 丟積木" and d["sources"][0]["title"] == "手冊"
    assert c.get(f"/api/admin/logs/{one['id']}", headers=as_("outsider")).status_code == 404
    st = c.get(f"/api/admin/bots/{bot.id}/stats", headers=as_("ana")).json()
    s = st["stats"]
    assert (s["turns"], s["sessions"], s["byType"]) == (3, 2, {"answer": 2, "conservative": 1})
    assert s["citeRate"] == 60.0 and s["rewriteRate"] == 50.0 and s["upRate"] == 100.0 and s["copied"] == 1 and s["hitRate"] == 66.7
    assert st["topDocs"] == [{"title": "手冊", "n": 2}] and st["quality"]["gapKinds"] == {"沒有可用知識": 1} and st["quality"]["l5"] == {"語氣": 2}
    assert [g["message"] for g in st["gaps"]] == ["s1-2 丟積木"] and st["units"] == ["乙", "甲"] and st["stability"] == {"缺欄位": 3}
    assert c.get(f"/api/admin/bots/{bot2.id}/stats", headers=as_("ana")).status_code == 404
