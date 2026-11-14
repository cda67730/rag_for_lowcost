"""知識圖譜後台 API：匯入（重匯保留標註與同義說法）、文件標註、總覽、候選審核、產生草稿、匯出。"""
import json

from sqlalchemy.orm import Session

import app.routes.admin as A
import app.routes.chat as C
from app.main import app
from db import models as m
from test_api_admin import env  # noqa: F401

GRAPH = {
    "meta": {"version": "1"},
    "nodeTypes": [{"id": "hub", "label": "主軸"}, {"id": "concept", "label": "概念", "definition": "名詞"}, {"id": "practice", "label": "實務策略"}],
    "relations": [{"id": "hasPart", "label": "包含"}, {"id": "hasStrategy", "label": "可用策略"}, {"id": "relatedTo", "label": "相關"}],
    "audiences": ["老師", {"id": "p", "label": "家長"}],
    "nodes": [{"id": "h1", "label": "情緒", "typeLabel": "主軸"}, {"id": "n1", "label": "情緒調節", "typeLabel": "概念", "parent": "h1", "path": ["情緒"], "practiceTips": ["深呼吸"]},
              {"id": "n2", "label": "轉銜預告", "typeLabel": "實務策略", "parent": "h1", "sources": ["S1"]}, {"id": "n2", "label": "重複"}, {"label": "沒 ID"}],
    "edges": [{"source": "h1", "target": "n1", "relation": "hasPart"}, {"source": "n1", "target": "n2", "relation": "hasStrategy"}, {"source": "n1", "target": "zz"},
              {"source": "n1", "target": "n2", "relation": "hasStrategy"}],
    "sources": [{"id": "S1", "title": "手冊"}, {"id": "S1", "title": "重複"}],
}


class KgLLM:
    def __init__(self):
        self.calls = []

    async def generate_json(self, model, prompt, **kw):
        self.calls.append(prompt)
        if "做標註" in prompt:
            n = prompt.count("【段落")
            items = [{"chunk": 1, "main": ["n1"], "sub": ["n1", "n2"], "audience": ["老師"], "age": "3–4歲", "context": ["收拾"],
                      "new": [{"name": "等待策略", "definition": "d", "parent": "n1", "type": "實務策略", "relation": {"type": "hasStrategy", "target": "n2"}},
                              {"name": "自我調節"}]}]
            if n > 1:
                items.append({"chunk": 2, "main": ["n2"], "new": [{"name": "等待策略"}]})
            return {"items": items}, None
        if "建立知識圖譜" in prompt:
            return {"concepts": [{"name": "情緒調節", "type": "概念", "chunks": [1]}, {"name": "預告", "type": "實務策略", "related": [{"rel": "hasStrategy", "target": "情緒調節"}], "chunks": [2]}]}, None
        if "整理" in prompt and "階層" in prompt:
            return {"hubs": [{"id": "h1", "label": "情緒"}], "subs": [{"id": "s1", "label": "調節", "hub": "h1"}], "place": [{"id": "c1", "parent": "s1"}]}, None
        raise AssertionError(prompt[:50])


def setup_kb(db, ws, bot):
    k = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768); db.add(k); db.flush()
    d = m.Document(kb_id=k.id, title="收拾手冊", status="approved", source_note="出版社"); db.add(d); db.flush()
    chs = [m.Chunk(kb_id=k.id, doc_id=d.id, seq=i, text=f"段落{i} 收拾", ctype="具體做法", embed_dim=768, embedding=[1.0] + [0.0] * 767) for i in (1, 2)]
    db.add_all(chs); db.flush()
    db.add(m.BotKnowledge(bot_id=bot.id, kb_id=k.id)); db.flush()
    return k, d, chs


def test_import_reimport_and_export(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k, d, chs = setup_kb(db, ws, bot)
    I = f"/api/admin/kbs/{k.id}/kg/import"
    assert c.post(I, headers=as_("rev"), content=json.dumps(GRAPH)).status_code == 403
    assert c.post(I, headers=as_("wsadmin"), content=b"{bad").json()["error"] == "檔案不是有效的 JSON"
    assert c.post(I, headers=as_("wsadmin"), content=b'{"nodes":1}').json()["code"] == "bad_graph"
    r = c.post(I, headers=as_("wsadmin"), content=("﻿" + json.dumps(GRAPH, ensure_ascii=False)).encode()).json()
    assert r == {"nodes": 3, "kept": 0, "edges": 2, "sources": 1, "defs": 8}
    n1 = db.query(m.KgNode).filter_by(kb_id=k.id, node_key="n1").one()
    h1 = db.query(m.KgNode).filter_by(kb_id=k.id, node_key="h1").one()
    assert n1.parent_id == h1.id and n1.path == "情緒" and n1.tips == "深呼吸" and n1.origin == "import"
    # 人工加的同義說法、停用、標註、候選核可的節點，重新匯入後都要留著
    n1.aliases, n1.status = ["自我調節"], "disabled"
    db.add(m.KgLink(chunk_id=chs[0].id, node_id=n1.id, tag="primary", method="manual"))
    db.add(m.KgNode(kb_id=k.id, node_key="n_cand", name="候選來的", ntype="概念", origin="candidate")); db.flush()
    g2 = dict(GRAPH, nodes=[x for x in GRAPH["nodes"] if x.get("id") != "n2"], edges=GRAPH["edges"][:1], relations=[])
    r2 = c.post(I, headers=as_("owner"), content=json.dumps(g2)).json()
    assert r2["nodes"] == 2 and r2["kept"] == 1 and r2["edges"] == 1
    db.expire_all()
    n1 = db.query(m.KgNode).filter_by(kb_id=k.id, node_key="n1").one()
    assert n1.aliases == ["自我調節"] and n1.status == "disabled" and db.query(m.KgLink).filter_by(node_id=n1.id).count() == 1
    assert db.query(m.KgNode).filter_by(kb_id=k.id, node_key="n2").count() == 0
    assert db.query(m.KgDef).filter_by(kb_id=k.id, kind="relation").count() == 3          # 檔案沒給關係定義：保留原本的
    ex = c.get(f"/api/admin/kbs/{k.id}/kg/export", headers=as_("rev")).json()
    assert {n["id"] for n in ex["nodes"]} == {"h1", "n1", "n_cand"} and ex["nodes"][1]["aliases"] == ["自我調節"]
    assert [a["label"] for a in ex["audiences"]] == ["老師", "家長"]
    # 匯出的檔案可以直接再匯入
    assert c.post(I, headers=as_("owner"), content=json.dumps(ex, ensure_ascii=False).encode()).status_code == 200
    ttl = c.get(f"/api/admin/kbs/{k.id}/kg/ontology.ttl", headers=as_("rev"))
    assert ttl.headers["content-type"].startswith("text/turtle") and "kg:hasStrategy a owl:ObjectProperty" in ttl.text


def test_tag_overview_and_candidates(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k, d, chs = setup_kb(db, ws, bot)
    llm = KgLLM()
    app.dependency_overrides[C.get_llm] = lambda: llm
    T = f"/api/admin/documents/{d.id}/kg-tag"
    assert c.post(T, headers=as_("wsadmin")).json() == {"skipped": "尚未匯入知識圖譜"}
    c.post(f"/api/admin/kbs/{k.id}/kg/import", headers=as_("owner"), content=json.dumps(GRAPH))
    n1 = db.query(m.KgNode).filter_by(kb_id=k.id, node_key="n1").one()
    n1.aliases = ["自我調節"]; db.flush()
    r = c.post(T, headers=as_("wsadmin")).json()
    # 段落1：n1 主要（同時被標次要→以主要為準）、n2 次要、「自我調節」對到 n1 的同義說法；段落2：n2 主要
    assert r == {"links": 3, "main": 2, "candidates": 1, "candidateHits": 1}
    links = {(l.chunk_id, l.node_id): l.tag for l in db.query(m.KgLink)}
    n2 = db.query(m.KgNode).filter_by(kb_id=k.id, node_key="n2").one()
    assert links == {(chs[0].id, n1.id): "primary", (chs[0].id, n2.id): "secondary", (chs[1].id, n2.id): "primary"}
    db.expire_all()
    assert db.get(m.Chunk, chs[0].id).audience == ["老師"] and db.get(m.Chunk, chs[0].id).age_range == "3–4歲"
    cand = db.query(m.KgCandidate).one()
    assert (cand.name, cand.hits, cand.parent_hint, json.loads(cand.rel_hint)) == ("等待策略", 2, "n1", {"type": "hasStrategy", "target": "n2"})
    assert c.post(T, headers=as_("wsadmin")).json()["links"] == 0                     # 已標過的段落不重標
    ov = c.get(f"/api/admin/kbs/{k.id}/kg", headers=as_("rev")).json()
    by = {n["key"]: n for n in ov["nodes"]}
    assert (by["n1"]["all"], by["n2"]["all"], by["n2"]["sub"]) == (1, 1, 1) and ov["stats"]["pending"] == 1 and ov["todo"] == []
    assert ov["cands"][0]["warns"] and "實務策略" in ov["cands"][0]["warns"][0]      # 概念 → 包含 → 實務策略 在圖譜裡沒出現過
    # 審核：核可（改名、指定上層）
    P = f"/api/admin/kg/candidates/{cand.id}"
    assert c.post(P, headers=as_("rev"), json={"action": "approve", "opts": {"type": "主軸"}}).json()["code"] == "invalid"
    key = c.post(P, headers=as_("rev"), json={"action": "approve", "opts": {"name": "等待"}}).json()["result"]
    new = db.query(m.KgNode).filter_by(kb_id=k.id, node_key=key).one()
    assert new.name == "等待" and new.origin == "candidate" and new.path == "情緒 > 情緒調節"
    assert {(e.rel, e.obj_id) for e in db.query(m.KgEdge).filter_by(subj_id=new.id)} == {("hasStrategy", n2.id)}
    assert db.query(m.KgLink).filter_by(node_id=new.id).count() == 1
    assert c.post(P, headers=as_("rev"), json={"action": "skip"}).json()["error"] == "這個候選已處理過"
    # 合併到既有節點：名稱變成同義說法
    c2 = m.KgCandidate(kb_id=k.id, name="情緒管理", chunk_id=chs[1].id); c3 = m.KgCandidate(kb_id=k.id, name="雜訊"); db.add_all([c2, c3]); db.flush()
    assert c.post(f"/api/admin/kg/candidates/{c2.id}", headers=as_("rev"), json={"action": "merge", "opts": {"target": "n1"}}).json()["result"] == "n1"
    db.expire_all()
    assert db.get(m.KgNode, n1.id).aliases == ["自我調節", "情緒管理"]
    assert c.post(f"/api/admin/kg/candidates/{c3.id}", headers=as_("rev"), json={"action": "skip"}).json() == {"ok": True, "result": ""}
    assert db.get(m.KgCandidate, c3.id).status == "rejected"
    assert c.post(f"/api/admin/kg/candidates/{c3.id}", headers=as_("ana"), json={"action": "skip"}).status_code == 403


def test_generate_job(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    k, d, chs = setup_kb(db, ws, bot)
    app.dependency_overrides[C.get_llm] = lambda: KgLLM()
    app.dependency_overrides[A.get_session_factory] = lambda: (lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint"))
    j = c.post(f"/api/admin/kbs/{k.id}/kg/generate", headers=as_("wsadmin")).json()["job_id"]
    db.expire_all()
    r = c.get(f"/api/admin/jobs/{j}", headers=as_("wsadmin")).json()
    assert r["status"] == "done", r
    g = r["result"]["json"]
    labels = {n["label"]: n for n in g["nodes"]}
    assert set(labels) == {"情緒", "調節", "情緒調節", "預告"} and labels["情緒調節"]["path"] == ["情緒", "調節"]
    assert g["sources"] == [{"id": str(d.id), "title": "收拾手冊", "publisher": "出版社", "url": "", "scope": ""}]
    assert any(e["relation"] == "hasStrategy" for e in g["edges"]) and "owl:Ontology" in r["result"]["ttl"]
    # 產生的草稿可以直接匯入
    assert c.post(f"/api/admin/kbs/{k.id}/kg/import", headers=as_("owner"), content=json.dumps(g, ensure_ascii=False).encode()).json()["nodes"] == 4
