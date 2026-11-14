"""驗證資料表行為與 docs/data-model.md 一致：唯一性、檢查約束、刪除連帶、向量索引查詢。"""
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError

from db import models as m


def mk_ws(db, slug="ws1"):
    ws = m.Workspace(slug=slug, name="工作區")
    db.add(ws); db.flush()
    return ws


def mk_bot(db, ws, slug="bot1"):
    bot = m.Bot(workspace_id=ws.id, slug=slug, name="機器人")
    db.add(bot); db.flush()
    db.add(m.Flow(bot_id=bot.id, name="正式", is_default=True)); db.flush()
    return bot


def mk_kb(db, ws, slug="kb1", dim=768):
    kb = m.KnowledgeBase(workspace_id=ws.id, slug=slug, name="知識庫", embed_model="gemini-embedding-001", embed_dim=dim)
    db.add(kb); db.flush()
    return kb


def mk_doc(db, kb, title="文件"):
    d = m.Document(kb_id=kb.id, title=title, status="approved")
    db.add(d); db.flush()
    return d


def expect_integrity(db, fn):
    sp = db.begin_nested()
    with pytest.raises(IntegrityError):
        fn(); db.flush()
    sp.rollback()


# ── 唯一性與檢查約束 ──

def test_one_default_flow_per_bot(db):
    bot = mk_bot(db, mk_ws(db))
    db.add(m.Flow(bot_id=bot.id, name="試用", is_default=False)); db.flush()   # 非預設可以多條
    expect_integrity(db, lambda: db.add(m.Flow(bot_id=bot.id, name="另一條預設", is_default=True)))


def test_bot_slug_unique_across_workspaces(db):
    mk_bot(db, mk_ws(db, "a"), "same")
    expect_integrity(db, lambda: db.add(m.Bot(workspace_id=mk_ws(db, "b").id, slug="same", name="x")))


def test_membership_rules(db):
    ws = mk_ws(db); bot = mk_bot(db, ws)
    u = m.User(login="alice", pw_hash="x"); db.add(u); db.flush()
    db.add(m.Membership(user_id=u.id, workspace_id=ws.id, role="admin")); db.flush()
    # Workspace 層（bot_id 為 NULL）不能重複
    expect_integrity(db, lambda: db.add(m.Membership(user_id=u.id, workspace_id=ws.id, role="analyst")))
    # owner 只能在 Workspace 層
    expect_integrity(db, lambda: db.add(m.Membership(user_id=u.id, workspace_id=ws.id, bot_id=bot.id, role="owner")))
    # 未知角色
    expect_integrity(db, lambda: db.add(m.Membership(user_id=u.id, workspace_id=ws.id, bot_id=bot.id, role="god")))
    # 同一人可以在某 Bot 另有角色
    db.add(m.Membership(user_id=u.id, workspace_id=ws.id, bot_id=bot.id, role="reviewer")); db.flush()


def test_document_status_check(db):
    kb = mk_kb(db, mk_ws(db))
    expect_integrity(db, lambda: db.add(m.Document(kb_id=kb.id, title="x", status="published")))


def test_kb_visibility_check(db):
    ws = mk_ws(db)
    expect_integrity(db, lambda: db.add(m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="e", embed_dim=768, visibility="public")))


# ── 刪除連帶 ──

def test_kb_delete_restricted_while_mounted(db):
    ws = mk_ws(db); bot = mk_bot(db, ws); kb = mk_kb(db, ws)
    db.add(m.BotKnowledge(bot_id=bot.id, kb_id=kb.id, label="發展", min_hits=1)); db.flush()
    expect_integrity(db, lambda: db.execute(delete(m.KnowledgeBase).where(m.KnowledgeBase.id == kb.id)))
    # 卸載後可以刪，文件與段落一起刪
    doc = mk_doc(db, kb)
    db.add(m.Chunk(kb_id=kb.id, doc_id=doc.id, seq=0, text="段落", embed_dim=768)); db.flush()
    db.execute(delete(m.BotKnowledge).where(m.BotKnowledge.kb_id == kb.id))
    db.execute(delete(m.KnowledgeBase).where(m.KnowledgeBase.id == kb.id)); db.flush()
    assert db.scalar(select(text("count(*)")).select_from(m.Chunk)) == 0
    assert db.scalar(select(text("count(*)")).select_from(m.Document)) == 0


def test_bot_delete_cascades_but_keeps_kb(db):
    ws = mk_ws(db); bot = mk_bot(db, ws); kb = mk_kb(db, ws)
    db.add_all([
        m.BotKnowledge(bot_id=bot.id, kb_id=kb.id),
        m.BotSetting(bot_id=bot.id, key="role_user", value="老師"),
        m.Skill(bot_id=bot.id, skill_key="S01", name="理解"),
        m.InviteCode(bot_id=bot.id, code="ABC"),
        m.Conversation(bot_id=bot.id, session="s", turn=1, user_text="q", rtype="answer"),
    ]); db.flush()
    db.execute(delete(m.Bot).where(m.Bot.id == bot.id)); db.flush()
    for model in (m.Flow, m.BotSetting, m.Skill, m.InviteCode, m.Conversation, m.BotKnowledge):
        assert db.scalar(select(text("count(*)")).select_from(model)) == 0, model.__tablename__
    assert db.get(m.KnowledgeBase, kb.id) is not None


def test_user_delete_sets_null(db):
    kb = mk_kb(db, mk_ws(db))
    u = m.User(login="bob", pw_hash="x"); db.add(u); db.flush()
    d = m.Document(kb_id=kb.id, title="t", uploaded_by=u.id); db.add(d); db.flush()
    db.execute(delete(m.User).where(m.User.id == u.id)); db.flush()
    db.expire_all()
    assert db.get(m.Document, d.id).uploaded_by is None


# ── 產生欄位、計數 ──

def test_tsv_generated(db):
    kb = mk_kb(db, mk_ws(db)); doc = mk_doc(db, kb)
    c = m.Chunk(kb_id=kb.id, doc_id=doc.id, seq=0, text="ABC routine 收玩具", embed_dim=768); db.add(c); db.flush()
    assert db.scalar(text("select tsv @@ to_tsquery('simple','routine') from chunks where id=:i"), {"i": c.id}) is True


def test_usage_counter_upsert(db):
    sql = text("""INSERT INTO usage_counters (bucket, count, expires_at) VALUES (:b, 1, :e)
                  ON CONFLICT (bucket) DO UPDATE SET count = usage_counters.count + 1 RETURNING count""")
    e = datetime.now(timezone.utc) + timedelta(days=1)
    got = [db.scalar(sql, {"b": "day:x:20261114", "e": e}) for _ in range(3)]
    assert got == [1, 2, 3]


# ── 向量：文件裡的查詢寫法要能用到 HNSW 索引，且不同維度可以並存 ──

VECTOR_SQL = """
SELECT c.id, 1 - (c.embedding::vector(768) <=> CAST(:q AS vector(768))) AS score
FROM chunks c
WHERE c.embed_dim = 768 AND c.active AND c.kb_id = ANY(:kbs)
ORDER BY c.embedding::vector(768) <=> CAST(:q AS vector(768))
LIMIT :k
"""


def unit(v):
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


def test_active_follows_document_status(db):
    kb = mk_kb(db, mk_ws(db))
    d = m.Document(kb_id=kb.id, title="t", status="processing"); db.add(d); db.flush()
    c = m.Chunk(kb_id=kb.id, doc_id=d.id, seq=0, text="x", embed_dim=768); db.add(c); db.flush()
    active = lambda: db.scalar(text("select active from chunks where id=:i"), {"i": c.id})
    assert active() is False                              # 處理中
    d.status = "approved"; db.flush(); assert active() is True
    d.status = "disabled"; db.flush(); assert active() is False
    # 已核可的文件後來補段落（修復向量）：插入時就跟著文件狀態
    d.status = "approved"; db.flush()
    c2 = m.Chunk(kb_id=kb.id, doc_id=d.id, seq=1, text="y", embed_dim=768); db.add(c2); db.flush()
    assert db.scalar(text("select active from chunks where id=:i"), {"i": c2.id}) is True


def test_vector_query_correct_and_mixed_dims(db):
    rnd = random.Random(7)
    ws = mk_ws(db); kb = mk_kb(db, ws); doc = mk_doc(db, kb)
    vecs = [unit([rnd.gauss(0, 1) for _ in range(768)]) for _ in range(300)]
    rows = [m.Chunk(kb_id=kb.id, doc_id=doc.id, seq=i, text=f"段落{i}", embed_dim=768, embedding=v) for i, v in enumerate(vecs)]
    kb3 = mk_kb(db, ws, "kb3", dim=3); doc3 = mk_doc(db, kb3)
    rows.append(m.Chunk(kb_id=kb3.id, doc_id=doc3.id, seq=0, text="三維", embed_dim=3, embedding=[1, 0, 0]))
    pend = m.Document(kb_id=kb.id, title="待審", status="pending"); db.add(pend); db.flush()
    rows.append(m.Chunk(kb_id=kb.id, doc_id=pend.id, seq=0, text="待審段落", embed_dim=768, embedding=vecs[42]))
    db.add_all(rows); db.flush()
    res = db.execute(text(VECTOR_SQL), {"q": str(vecs[42]), "kbs": [kb.id, kb3.id], "k": 5}).all()
    assert len(res) == 5
    assert res[0].id == rows[42].id and res[0].score > 0.999
    assert all(r.id not in (rows[-1].id, rows[-2].id) for r in res)   # 3 維與待審的都沒進來


def test_vector_index_is_usable(db):
    """寫法要讓規劃器「可以」選 HNSW：資料少時規劃器會選精確掃描（也是對的），這裡關掉其他路徑確認索引可用"""
    rnd = random.Random(1)
    ws = mk_ws(db); kb = mk_kb(db, ws); doc = mk_doc(db, kb)
    db.add_all([m.Chunk(kb_id=kb.id, doc_id=doc.id, seq=i, text="x", embed_dim=768, embedding=unit([rnd.gauss(0, 1) for _ in range(768)])) for i in range(200)])
    db.flush(); db.execute(text("ANALYZE chunks"))
    for opt in ("enable_seqscan", "enable_bitmapscan", "enable_sort"):
        db.execute(text(f"SET LOCAL {opt} = off"))
    q = str(unit([rnd.gauss(0, 1) for _ in range(768)]))
    plan = "\n".join(r[0] for r in db.execute(text("EXPLAIN " + VECTOR_SQL), {"q": q, "kbs": [kb.id], "k": 5}))
    assert "chunks_emb_768" in plan, plan
