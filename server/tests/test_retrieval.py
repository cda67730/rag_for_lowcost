"""檢索：同義詞與關鍵字（與 Apps Script 比對）、多知識庫向量檢索（資料庫測試）。"""
import dataclasses
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from sqlalchemy import text as sql_text

from db import models as m
from services.retrieval import expand_query, keywords, mounts, search
from settings.loader import build_config

HERE = Path(__file__).parent
SYN = [{"word": "丟", "concept": "攻擊行為"}, {"word": "推", "concept": "攻擊行為"}, {"word": "收拾", "concept": "轉銜時段"}, {"word": "不存在", "concept": "X"}]
EXP = [{"syn": SYN, "text": t} for t in ["他收拾時一直丟積木還推人", "沒有任何同義詞 abc 123", "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥" * 2, ""]]


def test_expand_and_keywords_same_as_apps_script():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    want = json.loads(subprocess.run(["node", str(HERE / "parity" / "run_expand_gas.js")], input=json.dumps(EXP, ensure_ascii=False), capture_output=True, text=True, check=True).stdout)
    for c, w in zip(EXP, want):
        e = expand_query(c["text"], [(s["word"], s["concept"]) for s in c["syn"]])
        assert e == w["e"] and keywords(c["text"], e["concepts"]) == w["keys"]


# ── 資料庫：多知識庫 ──

def unit(v):
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


def at(q, sim, rnd):
    """與單位向量 q 的餘弦相似度恰為 sim 的向量"""
    r = [rnd.gauss(0, 1) for _ in q]
    d = sum(a * b for a, b in zip(r, q))
    r = unit([a - d * b for a, b in zip(r, q)])
    return [sim * a + (1 - sim * sim) ** 0.5 * b for a, b in zip(q, r)]


@pytest.fixture
def world(db):
    rnd = random.Random(11)
    ws = m.Workspace(slug="w", name="w"); db.add(ws); db.flush()
    bot = m.Bot(workspace_id=ws.id, slug="b", name="b"); db.add(bot); db.flush()
    q = unit([rnd.gauss(0, 1) for _ in range(768)])
    kbs = {}
    for slug, n, top in [("dev", 6, 0.95), ("cls", 6, 0.80), ("off", 3, 0.99)]:
        kb = m.KnowledgeBase(workspace_id=ws.id, slug=slug, name=slug, embed_model="gemini-embedding-001", embed_dim=768); db.add(kb); db.flush()
        doc = m.Document(kb_id=kb.id, title=f"{slug}文件", status="approved", source_note="出處"); db.add(doc); db.flush()
        for i in range(n):
            db.add(m.Chunk(kb_id=kb.id, doc_id=doc.id, seq=i, text=f"{slug} 段落{i}" + ("收玩具的做法" if i == 0 else ""), page=str(i + 1),
                           ctype="具體做法", embed_dim=768, embedding=at(q, top - 0.02 * i, rnd)))
        kbs[slug] = kb
    db.flush()
    return db, bot, kbs, q


def mount(db, bot, kb, **kw):
    db.add(m.BotKnowledge(bot_id=bot.id, kb_id=kb.id, **kw)); db.flush()


def test_only_mounted_and_enabled_kbs(world):
    db, bot, kbs, q = world
    mount(db, bot, kbs["dev"], label="發展"); mount(db, bot, kbs["off"], label="停用", enabled=False)
    r = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "x", [], pool=20)
    assert {h["label"] for h in r.hits} == {"發展"} and len(r.hits) == 6
    assert [h["ref"] for h in r.hits] == [f"K{i}" for i in range(1, 7)]
    assert r.hits[0]["title"] == "dev文件" and r.hits[0]["source"] == "出處" and r.hits[0]["page"] == "1"


def test_min_hits_guarantees_each_kb(world):
    db, bot, kbs, q = world
    mount(db, bot, kbs["dev"], label="發展", sort=0); mount(db, bot, kbs["cls"], label="班級", min_hits=2, sort=1)
    r = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "x", [], pool=4)
    labels = [h["label"] for h in r.hits]
    assert len(labels) == 4 and labels.count("班級") == 2      # dev 比較像，不保底的話 4 段都是 dev
    assert [h["score"] for h in r.hits] == sorted([h["score"] for h in r.hits], reverse=True)
    ms = mounts(db, bot.id)
    r0 = search(db, build_config({"min_score": 0.1}), [ms[0], dataclasses.replace(ms[1], min_hits=0)], q, "x", [], pool=4)
    assert [h["label"] for h in r0.hits].count("班級") == 0


def test_weight_and_threshold(world):
    db, bot, kbs, q = world
    mount(db, bot, kbs["dev"], label="發展", weight=0.5); mount(db, bot, kbs["cls"], label="班級")
    r = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "x", [], pool=3)
    assert all(h["label"] == "班級" for h in r.hits)           # 權重 0.5 讓 dev 全部掉到後面
    r = search(db, build_config({"min_score": 0.999}), mounts(db, bot.id), q, "x", [], pool=3)
    assert r.hits == [] and r.cands and not any(c["pass"] for c in r.cands)


def test_keyword_boost_changes_order(world):
    db, bot, kbs, q = world
    mount(db, bot, kbs["cls"], label="班級")
    plain = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "", [], pool=6)
    boosted = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "收玩具的做法", ["收玩具的做法"], pool=6)
    top = lambda r: next(c for c in r.cands if "收玩具" in c["text"])
    assert top(boosted)["kw"] > 0 and top(boosted)["score"] > top(plain)["score"]


def test_inactive_documents_excluded(world):
    db, bot, kbs, q = world
    mount(db, bot, kbs["dev"], label="發展")
    db.query(m.Document).filter(m.Document.kb_id == kbs["dev"].id).update({"status": "disabled"}); db.flush()
    assert search(db, build_config({"min_score": 0.01}), mounts(db, bot.id), q, "x", [], pool=10).hits == []


def test_kg_only_and_hybrid(world):
    db, bot, kbs, q = world
    mount(db, bot, kbs["cls"], label="班級")
    ids = [str(c.id) for c in db.query(m.Chunk).filter(m.Chunk.kb_id == kbs["cls"].id).order_by(m.Chunk.seq)]
    kg = {"nodes": [], "chunks": {ids[5]: "direct"}}
    only = search(db, build_config({"min_score": 0.01, "kg_mode": "only"}), mounts(db, bot.id), q, "x", [], pool=10, kg=kg)
    assert [h["id"] for h in only.hits] == [ids[5]] and only.kg_mode == "only"
    hyb = search(db, build_config({"min_score": 0.01, "kg_mode": "hybrid", "kg_boost": 1.0}), mounts(db, bot.id), q, "x", [], pool=10, kg=kg)
    assert hyb.hits[0]["id"] == ids[5] and len(hyb.hits) == 6     # 加分把最不像的那段推到第一


def test_dimension_mismatch_skipped_with_note(world):
    db, bot, kbs, q = world
    kb3 = m.KnowledgeBase(workspace_id=bot.workspace_id, slug="small", name="s", embed_model="x", embed_dim=1536); db.add(kb3); db.flush()
    mount(db, bot, kbs["dev"]); mount(db, bot, kb3)
    r = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "x", [], pool=5)
    assert len(r.hits) == 5 and any("1536" in n for n in r.notes)


def test_min_hits_from_several_kbs_resorted(world):
    db, bot, kbs, q = world
    rnd = random.Random(5)
    low = m.KnowledgeBase(workspace_id=bot.workspace_id, slug="low", name="low", embed_model="gemini-embedding-001", embed_dim=768); db.add(low); db.flush()
    doc = m.Document(kb_id=low.id, title="low", status="approved"); db.add(doc); db.flush()
    db.add(m.Chunk(kb_id=low.id, doc_id=doc.id, seq=0, text="low", embed_dim=768, embedding=at(q, 0.70, rnd))); db.flush()
    mount(db, bot, low, label="低", min_hits=1, sort=0); mount(db, bot, kbs["cls"], label="班級", min_hits=1, sort=1); mount(db, bot, kbs["dev"], label="發展", sort=2)
    r = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "x", [], pool=3)
    assert [h["label"] for h in r.hits] == ["發展", "班級", "低"] and [h["ref"] for h in r.hits] == ["K1", "K2", "K3"]


def test_small_kb_not_crowded_out_by_big_kb(world):
    """HNSW 先取近鄰再過濾 kb_id：大庫的段落比較近時，小庫仍要拿到自己的段落"""
    db, bot, kbs, q = world
    rnd = random.Random(9)
    big = m.KnowledgeBase(workspace_id=bot.workspace_id, slug="big", name="big", embed_model="gemini-embedding-001", embed_dim=768); db.add(big); db.flush()
    doc = m.Document(kb_id=big.id, title="big", status="approved"); db.add(doc); db.flush()
    db.add_all([m.Chunk(kb_id=big.id, doc_id=doc.id, seq=i, text=f"b{i}", embed_dim=768, embedding=at(q, 0.99 - i * 0.0001, rnd)) for i in range(300)]); db.flush()
    db.execute(sql_text("ANALYZE chunks"))
    # 資料量小時規劃器不會用 HNSW；在這個測試的交易裡拿掉其他索引、關掉循序掃描，強迫走 HNSW 才測得到（測完回滾）
    for (ix,) in db.execute(sql_text("SELECT indexname FROM pg_indexes WHERE tablename = 'chunks' AND indexname NOT LIKE 'chunks_emb_%' AND indexname NOT LIKE 'pk_%' AND indexname NOT LIKE 'uq_%'")).all():
        db.execute(sql_text(f'DROP INDEX "{ix}"'))
    for k in ("enable_seqscan", "enable_bitmapscan"):
        db.execute(sql_text(f"SELECT set_config('{k}', 'off', true)"))
    plan = "\n".join(r[0] for r in db.execute(sql_text("EXPLAIN SELECT id FROM chunks WHERE embed_dim = 768 AND active AND kb_id = :kb ORDER BY embedding::vector(768) <=> CAST(:q AS vector(768)) LIMIT 6"), {"kb": kbs["cls"].id, "q": str(q)}))
    assert "chunks_emb_768" in plan
    mount(db, bot, kbs["cls"], label="班級")
    r = search(db, build_config({"min_score": 0.1}), mounts(db, bot.id), q, "x", [], pool=6)
    assert len(r.hits) == 6 and {h["label"] for h in r.hits} == {"班級"}
