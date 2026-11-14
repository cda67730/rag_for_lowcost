"""知識圖譜比對：與 Apps Script kgMatch_ 比對（node），以及從資料庫讀圖譜。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from db import models as m
from services.kg import kg_match, match
from settings.loader import build_config

HERE = Path(__file__).parent
N = lambda i, name, t="概念", al=(), d="", tips="": {"id": i, "name": name, "type": t, "aliases": list(al), "def": d, "tips": tips}
NODES = [N("a", "情緒", al=["心情"]), N("b", "情緒調節", al=["自我調節", "x"], d="調整情緒" * 80, tips="降低刺激" * 100), N("c", "轉銜", al=["收拾時間"]),
         N("d", "預告", "策略"), N("e", "計時器", "策略"), N("f", "主軸一", "主軸", al=["情緒"]), N("g", "同儕", al=["朋友"]), N("h", "環境"), N("i", "😀表情", al=["😀"])]
EDGES = [{"subj": "c", "rel": "hasStrategy", "obj": "d"}, {"subj": "e", "rel": "appliedIn", "obj": "c"}, {"subj": "c", "rel": "hasPart", "obj": "h"},
         {"subj": "c", "rel": "relatedTo", "obj": "g"}, {"subj": "c", "rel": "unknownRel", "obj": "a"}, {"subj": "b", "rel": "supports", "obj": "d"},
         {"subj": "c", "rel": "influences", "obj": "d"}, {"subj": "c", "rel": "supports", "obj": "zz"}]
LINKS = [{"chunk": "k1", "node": "c", "tag": "primary"}, {"chunk": "k2", "node": "c", "tag": "secondary"}, {"chunk": "k2", "node": "d", "tag": "primary"},
         {"chunk": "k3", "node": "d", "tag": "primary"}, {"chunk": "k4", "node": "b", "tag": "secondary"}, {"chunk": "k4", "node": "b", "tag": "primary"},
         {"chunk": "k5", "node": "h", "tag": "primary"}, {"chunk": "k3", "node": "c", "tag": "secondary"}]
CASES = []
for text, concepts in [("收拾時間他情緒調節不好", []), ("沒有相關", []), ("", ["轉銜", "同儕"]), ("情緒 心情 😀 x", ["計時器"]), ("自我調節 預告 計時器 轉銜 同儕 環境", [])]:
    for mx in [3, 1, 0, 10, "", "2"]:
        CASES.append({"nodes": NODES, "edges": EDGES, "links": LINKS, "text": text, "concepts": concepts, "max": mx})


def py_max(v):
    return int(v) if str(v).strip() not in ("", "0") else 3


def test_same_as_apps_script():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    want = json.loads(subprocess.run(["node", str(HERE / "parity" / "run_kg_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True, check=True).stdout)
    for c, w in zip(CASES, want):
        assert match(c["nodes"], c["edges"], c["links"], c["text"], c["concepts"], py_max(c["max"])) == w, (c["text"], c["max"])


def test_from_database_across_kbs(db):
    ws = m.Workspace(slug="w", name="w"); db.add(ws); db.flush()
    kbs = [m.KnowledgeBase(workspace_id=ws.id, slug=s, name=s, embed_model="gemini-embedding-001", embed_dim=768) for s in ("a", "b", "c")]
    db.add_all(kbs); db.flush()
    doc = m.Document(kb_id=kbs[1].id, title="t", status="approved"); db.add(doc); db.flush()
    ch = m.Chunk(kb_id=kbs[1].id, doc_id=doc.id, seq=0, text="x", embed_dim=768, embedding=[1.0] + [0.0] * 767); db.add(ch); db.flush()
    n1 = m.KgNode(kb_id=kbs[0].id, node_key="n1", name="轉銜", ntype="概念", aliases=["收拾時間"])
    n2 = m.KgNode(kb_id=kbs[1].id, node_key="n2", name="預告", ntype="策略", definition="先說")
    n3 = m.KgNode(kb_id=kbs[1].id, node_key="n3", name="停用的", ntype="概念", status="disabled")
    n4 = m.KgNode(kb_id=kbs[2].id, node_key="n4", name="收拾", ntype="概念")
    db.add_all([n1, n2, n3, n4]); db.flush()
    db.add(m.KgEdge(kb_id=kbs[1].id, subj_id=n2.id, rel="hasStrategy", obj_id=n1.id))
    db.add(m.KgLink(chunk_id=ch.id, node_id=n2.id, tag="primary")); db.flush()
    cfg = build_config({})
    r = kg_match(db, cfg, [kbs[0].id, kbs[1].id], "收拾時間 停用的", [])
    assert [(n["name"], n["hop"]) for n in r["nodes"]] == [("轉銜", 0), ("預告", 1)] and r["chunks"] == {str(ch.id): "hop"}
    assert kg_match(db, cfg, [kbs[0].id], "預告", [])["note"] == "問題裡沒有對到任何節點"      # 沒掛 b，預告不會命中
    assert kg_match(db, cfg, [], "x", [])["note"] == "尚未匯入圖譜"


def test_max_nodes_at_least_one():
    for mx in (0, -2):
        r = match(NODES, [], [], "情緒調節 轉銜", [], mx)
        assert [n["id"] for n in r["nodes"]] == ["b"]
