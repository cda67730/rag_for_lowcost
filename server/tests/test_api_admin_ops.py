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


def test_describe_endpoints(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env

    class L:
        async def generate_json(self, model, prompt, **kw):
            if "改寫成一段給一般人看" in prompt:
                return {"text": "目前會先回覆"}, None
            return {"intents": [{"name": n, "desc": "d"} for n in "甲乙丙丁"]}, None
    app.dependency_overrides[C.get_llm] = lambda: L()
    h = as_("botadmin")
    r = c.post(f"/api/admin/bots/{bot.id}/describe", headers=h, json={"kind": "intents", "text": "四種"}).json()
    assert r["text"] == "甲＝d\n乙＝d\n丙＝d\n丁＝d"
    assert c.post(f"/api/admin/bots/{bot.id}/describe", headers=h, json={"kind": "intents", "text": ""}).json()["error"] == "請先寫描述"
    assert c.post(f"/api/admin/bots/{bot.id}/describe-back", headers=h, json={"kind": "answer_schema"}).json() == {"text": "目前會先回覆"}
    assert c.post(f"/api/admin/bots/{bot.id}/describe-back", headers=as_("ana"), json={"kind": "intents"}).status_code == 403


def test_csv_export(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    db.add(m.Conversation(bot_id=bot.id, session="s", turn=1, unit="甲", user_text="=HYPERLINK(\"x\") 丟積木", rtype="answer", skills=["理解", "轉介"], rewritten=True))
    db.add(m.Conversation(bot_id=bot.id, session="admin-test-1", turn=1, user_text="測試", rtype="answer"))
    db.flush()
    r = c.get(f"/api/admin/bots/{bot.id}/logs/export.csv", headers=as_("ana"))
    assert r.headers["content-type"].startswith("text/csv") and r.text.startswith("﻿編號,時間")
    lines = r.text.strip().split("\n")
    assert len(lines) == 2 and "'=HYPERLINK" in lines[1] and "理解、轉介" in lines[1] and ",是," in lines[1]


def test_cancel_job(env):  # noqa: F811
    import asyncio
    from sqlalchemy.orm import Session
    from services import ingest
    c, db, ws, bot, bot2, users, as_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768); db.add(k); db.flush()
    doc = ingest.create_document(db, k, filename="a.md", data=md())
    j = m.Job(workspace_id=ws.id, kb_id=k.id, kind="ingest", target=str(doc.id)); db.add(j); db.flush()
    assert c.post(f"/api/admin/jobs/{j.id}/cancel", headers=as_("rev")).status_code == 403
    assert c.post(f"/api/admin/jobs/{j.id}/cancel", headers=as_("wsadmin")).json() == {"status": "cancelled"}
    assert c.post(f"/api/admin/jobs/{j.id}/cancel", headers=as_("wsadmin")).status_code == 409
    from test_ingest import FakeLLM
    asyncio.run(ingest.run_ingest_job(lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint"), FakeLLM(), j.id))
    db.expire_all()
    assert db.get(m.Job, j.id).status == "cancelled" and db.get(m.Document, doc.id).status == "failed" and "取消" in db.get(m.Document, doc.id).error


def test_cancel_while_running(env):  # noqa: F811
    import asyncio
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from services import ingest
    from test_ingest import FakeLLM
    c, db, ws, bot, bot2, users, as_ = env
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768); db.add(k); db.flush()
    doc = ingest.create_document(db, k, filename="a.md", data=md())
    j = m.Job(workspace_id=ws.id, kb_id=k.id, kind="ingest", target=str(doc.id)); db.add(j); db.flush()

    class CancelDuring(FakeLLM):
        async def generate_json(self, model, prompt, **kw):     # 分類進行中，有人按了取消
            db.execute(text("UPDATE jobs SET status = 'cancelled' WHERE id = :i"), {"i": j.id})
            return await super().generate_json(model, prompt, **kw)

    asyncio.run(ingest.run_ingest_job(lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint"), CancelDuring(), j.id))
    db.expire_all()
    assert db.get(m.Job, j.id).status == "cancelled" and db.get(m.Document, doc.id).status == "failed"
    assert db.query(m.Chunk).filter_by(doc_id=doc.id).count() == 0


def test_flow_stats_and_trial(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    tr = [{"step": "P03 去識別化", "ms": 5}, {"step": "L4 分流", "ms": 300}, {"step": "P06 檢索", "ms": 500}, {"step": "重新排序與配額", "detail": {"used": True}, "ms": 900},
          {"step": "A1 生成", "ms": 2900}, {"step": "檢查", "ms": 3500}, {"step": "A1 重寫", "ms": 5000}, {"step": "檢查", "ms": 5600}, {"step": "P10 組合", "ms": 5601}]
    db.add(m.Conversation(bot_id=bot.id, session="s", turn=1, user_text="x", rtype="answer", ms=5601, trace=tr, hits=3, top_score=0.8, rewritten=True,
                          quality={"gap": None, "l5": [{"issues": [{"severe": True}]}], "l5Final": None}, stability={"ev": [{"at": "A1", "kind": "缺欄位"}]}))
    db.add(m.Conversation(bot_id=bot.id, session="s", turn=2, user_text="y", rtype="conservative", ms=1000, hits=0, top_score=0.2,
                          trace=[{"step": "P06 檢索", "ms": 400}, {"step": "重新排序與配額", "detail": {"used": False, "why": "重排失敗"}, "ms": 600}, {"step": "P10 降級", "ms": 610}],
                          quality={"gap": {"kind": "沒有可用知識", "outcome": "保守回答"}, "l5": []}))
    db.add(m.Conversation(bot_id=bot.id, session="admin-test-1", turn=1, user_text="z", rtype="answer", trace=[]))
    db.add(m.Skill(bot_id=bot.id, skill_key="s1", name="a", always_on=True)); db.flush()
    r = c.get(f"/api/admin/bots/{bot.id}/flow-stats", headers=as_("ana")).json()
    assert r["turns"] == 2 and r["types"] == {"answer": 1, "conservative": 1} and r["avgMs"] == 3300
    assert r["steps"]["answer"] == {"runs": 1, "ms": 3500, "avg": 3500} and r["steps"]["check"]["runs"] == 1 and r["steps"]["check"]["ms"] == 1200
    assert r["retrieval"] == {"n": 2, "hit": 1, "top": 1.0, "topN": 2} and r["rerank"] == {"n": 2, "used": 1, "failed": 1, "off": 0}
    assert r["gap"]["byKind"] == {"沒有可用知識": 1} and r["answer"] == {"n": 1, "rewrite": 1, "format": 1} and r["check"] == {"n": 1, "l5": 1, "severe": 1, "conservative": 0}
    assert r["skills"] == 1 and r["skillsAlways"] == 1
    llm = ChatLLM()
    app.dependency_overrides[C.get_llm] = lambda: llm
    t = c.post(f"/api/admin/bots/{bot.id}/flow-trial", headers=as_("botadmin"), json={"message": "小宇丟積木"}).json()
    assert t["type"] in ("answer", "conservative") and t["steps"][0]["key"] in ("deid", "triage") and all(s["ms"] >= 0 for s in t["steps"])
    assert c.post(f"/api/admin/bots/{bot.id}/flow-trial", headers=as_("ana"), json={"message": "x"}).status_code == 403
