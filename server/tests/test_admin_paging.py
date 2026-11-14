"""紀錄列表翻頁（從上一頁最後一筆接著讀）、只讀當頁回饋；圖譜總覽的查詢次數不隨候選與文件數量增加。"""
import json
from contextlib import contextmanager

from sqlalchemy import event

from db import models as m
from services import kg_admin
from test_api_admin import env  # noqa: F401
from test_api_admin_kg import GRAPH, setup_kb


@contextmanager
def sql_log(db):
    stmts = []
    conn = db.connection()
    fn = lambda conn_, cur, stmt, params, ctx, many: stmts.append(stmt)
    event.listen(conn, "before_cursor_execute", fn)
    try:
        yield stmts
    finally:
        event.remove(conn, "before_cursor_execute", fn)


def test_logs_keyset_paging_and_page_feedback(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    # 同一個交易寫入：created_at 全部一樣，翻頁要靠 id 分先後，不能漏也不能重複
    for i in range(23):
        db.add(m.Conversation(bot_id=bot.id, session=f"s{i % 5}", turn=i, user_text=f"q{i}", rtype="answer", hits=1, cited=1, ms=10, trace=[{"step": "大"}]))
    for i in (0, 7, 22):
        db.add(m.Feedback(bot_id=bot.id, session=f"s{i % 5}", turn=i, kind="有幫助"))
    db.add(m.Feedback(bot_id=bot.id, session="s1", turn=1, kind="沒幫助"))
    db.add(m.Feedback(bot_id=bot2.id, session="s0", turn=0, kind="沒幫助"))     # 別的 bot 的回饋不算
    db.flush()
    L = f"/api/admin/bots/{bot.id}/logs"
    full = c.get(L + "?limit=200", headers=as_("ana")).json()
    assert full["next"] is None and len(full["items"]) == 23
    got, cur, pages = [], None, 0
    while True:
        with sql_log(db) as stmts:
            d = c.get(L + "?limit=5" + (f"&cursor={cur}" if cur else ""), headers=as_("ana")).json()
        pages += 1
        fbq = [s for s in stmts if "FROM feedback" in s]
        assert len(fbq) == 1 and " IN " in fbq[0]                                    # 只查這一頁的回饋
        assert not any("conversations.trace" in s for s in stmts if "FROM conversations" in s)   # 列表不讀流程紀錄
        got += d["items"]
        cur = d["next"]
        if not cur:
            break
    assert pages == 5 and [x["id"] for x in got] == [x["id"] for x in full["items"]]
    assert {x["turn"]: x["fb"] for x in got if x["fb"]} == {0: "up", 7: "up", 22: "up", 1: "down"}
    assert c.get(L + "?cursor=abc", headers=as_("ana")).json()["code"] == "bad_cursor"
    assert c.get(L + "?cursor=MTA=", headers=as_("ana")).json()["code"] == "bad_cursor"          # 舊格式（跳過 N 筆）
    import base64
    naive = base64.urlsafe_b64encode(b"2026-01-01T00:00:00|5").decode()
    assert c.get(L + f"?cursor={naive}", headers=as_("ana")).json()["code"] == "bad_cursor"                # 沒有時區
    one = next(x for x in got if x["turn"] == 7)
    d = c.get(f"/api/admin/logs/{one['id']}", headers=as_("ana")).json()
    assert d["trace"] == [{"step": "大"}] and d["fb"] == "up"


def test_kg_overview_query_count_is_constant(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k, d, chs = setup_kb(db, ws, bot)
    c.post(f"/api/admin/kbs/{k.id}/kg/import", headers=as_("owner"), content=json.dumps(GRAPH))
    n1 = db.query(m.KgNode).filter_by(kb_id=k.id, node_key="n1").one()

    def add(n):
        docs = []
        for i in range(n):
            doc = m.Document(kb_id=k.id, title=f"文件{i}", status=["approved", "pending", "disabled"][i % 3]); db.add(doc); db.flush()
            cs = [m.Chunk(kb_id=k.id, doc_id=doc.id, seq=j, text=f"段{i}-{j}" * 30, ctype="具體做法", embed_dim=768, embedding=[1.0] + [0.0] * 767) for j in range(3)]
            db.add_all(cs); db.flush()
            if i % 2:
                db.add(m.KgLink(chunk_id=cs[0].id, node_id=n1.id, tag="primary"))
            if i % 4 == 0:
                db.add_all([m.KgLink(chunk_id=x.id, node_id=n1.id, tag="secondary") for x in cs])      # 全部標過 → 不在待標註
            db.add(m.KgCandidate(kb_id=k.id, name=f"候選{i}", chunk_id=cs[1].id, doc_id=doc.id, hits=i))
            docs.append((doc, cs))
        db.add(m.KgCandidate(kb_id=k.id, name="沒出處"))
        db.flush()
        return docs

    add(2)
    with sql_log(db) as small:
        kg_admin.overview(db, k.id)
    docs = add(30)
    k2 = m.KnowledgeBase(workspace_id=ws.id, slug="k2", name="k2", embed_model="gemini-embedding-001", embed_dim=768); db.add(k2); db.flush()
    other = m.KgNode(kb_id=k2.id, node_key="x", name="別的圖譜", ntype="概念"); db.add(other); db.flush()
    db.add(m.KgLink(chunk_id=docs[6][1][2].id, node_id=other.id, tag="primary")); db.flush()     # 掛在別的知識庫的節點上：仍算未標註
    with sql_log(db) as big:
        ov = kg_admin.overview(db, k.id)
    assert len(big) == len(small) < 15
    # 結果和逐筆計算一致
    done = kg_admin.done_chunks(db, k.id)
    want = [{"id": str(x.id), "title": x.title, "status": x.status, "todo": sum(1 for ch in db.query(m.Chunk).filter_by(doc_id=x.id) if ch.id not in done)}
            for x in db.query(m.Document).filter(m.Document.kb_id == k.id, m.Document.status.notin_(("disabled", "failed", "processing"))).order_by(m.Document.uploaded_at, m.Document.id)]
    assert ov["todo"] == [w for w in want if w["todo"]] and ov["todo"]
    assert next(x for x in ov["todo"] if x["title"] == "文件6")["todo"] == 3
    by = {x["name"]: x for x in ov["cands"]}
    doc5, cs5 = docs[5]
    assert by["候選5"]["doc"] == "文件5" and by["候選5"]["snippet"] == kg_admin._u16cut(cs5[1].text, 160)
    assert by["沒出處"]["doc"] == "" and by["沒出處"]["snippet"] == ""
