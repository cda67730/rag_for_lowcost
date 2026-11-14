"""知識圖譜後台：標註、接法提醒、產生圖譜、Ontology 與 Apps Script 比對；匯入、儲存標註、總覽、候選審核用資料庫測。"""
import asyncio
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from db import models as m
from services import kg_admin as K

HERE = Path(__file__).parent
CFG = {"domain_desc": "學前融合", "role_user": "老師"}
DEFS_ROWS = [{"種類": "節點類型", "ID": "hub", "名稱": "主軸", "定義": "最上層"}, {"種類": "節點類型", "ID": "concept", "名稱": "概念", "定義": "名詞"},
             {"種類": "節點類型", "ID": "practice", "名稱": "實務策略", "定義": "做法"}, {"種類": "節點類型", "ID": "sub", "名稱": "子分類", "定義": "分類"},
             {"種類": "關係", "ID": "hasPart", "名稱": "包含", "定義": "階層"}, {"種類": "關係", "ID": "hasStrategy", "名稱": "可用策略", "定義": "策略"},
             {"種類": "關係", "ID": "relatedTo", "名稱": "相關", "定義": "一般"}, {"種類": "讀者對象", "ID": "a1", "名稱": "老師", "定義": ""}, {"種類": "讀者對象", "ID": "a2", "名稱": "家長", "定義": ""}]
NODES = [("n1", "情緒調節", "概念", "自我調節、情緒管理", "主軸一 > 情緒"), ("n2", "轉銜預告", "實務策略", "", "主軸二 > 轉銜"), ("n3", "主軸一", "主軸", "", ""), ("n4", "環境調整", "實務策略", "", "")]
NODE_ROWS = [{"節點ID": i, "名稱": n, "類型": t, "同義說法": a, "路徑": p, "狀態": "啟用"} for i, n, t, a, p in NODES]
EDGE_ROWS = [{"主詞": "n1", "關係": "hasStrategy", "受詞": "n2"}, {"主詞": "n1", "關係": "hasStrategy", "受詞": "n4"}, {"主詞": "n3", "關係": "hasPart", "受詞": "n1"}]


def defs_of(rows):
    pick = lambda kind, key: [{"id": r["ID"], "label": r["名稱"], "def": r["定義"]} for r in rows if r["種類"] == kind] or [{"id": i, "label": l, "def": d} for i, l, d in K.DEFAULT_DEFS[key]]
    return {"types": pick("節點類型", "types"), "rels": pick("關係", "rels"), "audiences": pick("讀者對象", "audiences")}


PY_NODES = [{"id": i, "name": n, "aliases": a, "type": t, "path": p} for i, n, t, a, p in NODES]
CHUNKS = [{"id": f"k{i}", "text": f"段落{i} " + "孩子收拾時情緒激動" * (i * 30)} for i in range(1, 15)]
MAP_RESP = [
    {"items": [{"chunk": 1, "main": ["n1", "n1", "zz", "n2", "n4"], "sub": ["n1", "n2", "n4"], "audience": ["老師", "路人"], "age": " 3–4歲" + "x" * 40, "context": ["收拾", "", "午睡", "a", "b", "c"],
                "new": [{"name": "等待策略", "definition": "d", "parent": "n1", "type": "實務策略", "relation": {"type": "hasStrategy", "target": "n2"}}, {"name": ""}, "x"]},
               {"chunk": "2", "nodes": ["n2"]}, {"chunk": 99, "main": ["n1"]}, {"chunk": 0}, "壞", {"chunk": 3, "main": ["n4"], "audience": "家長", "context": "戶外", "new": [{"name": "單一"}]}]},
    {"items": [{"chunk": 2, "main": ["n1"]}]},
]
GEN_CHUNKS = [{"id": f"c{i}", "docId": f"D{i % 3}", "title": f"文件{i % 3}", "text": "內容" * (i * 200)} for i in range(20)]
GEN_RESP = [{"concepts": [{"name": " 情緒調節 ", "type": "概念", "definition": "調整情緒", "aliases": ["自我調節", " ", 3], "tips": ["深呼吸", "", "數數", "多"],
                           "related": [{"rel": "hasStrategy", "target": "轉銜預告"}, {"rel": "hasPart", "target": "x"}, {"rel": "bad", "target": "y"}, {"rel": "relatedTo"}], "chunks": [1, "2", 99, 1]},
                          {"name": "轉銜預告", "type": "子分類", "chunks": 3}, {"definition": "沒名字"}, "s", {"name": "環境", "type": "實務策略", "aliases": "空間"}]}]
ITEMS = [
    {"name": "情緒調節", "type": "概念", "def": "調整情緒", "aliases": ["自我調節"], "tips": ["深呼吸"], "related": [{"rel": "hasStrategy", "target": "轉銜預告"}], "docs": ["D0", "D1"]},
    {"name": "自我調節", "type": "概念", "def": "較長的定義文字", "aliases": [], "tips": ["數數", "深呼吸"], "related": [], "docs": ["D2"]},
    {"name": "轉銜預告", "type": "實務策略", "def": "先說", "aliases": ["預告"], "tips": [], "related": [{"rel": "relatedTo", "target": "環境（調整）"}, {"rel": "relatedTo", "target": "不存在"}], "docs": ["D1"]},
    {"name": "環境調整", "type": "實務策略", "def": "", "aliases": [], "tips": [], "related": [{"rel": "hasStrategy", "target": "情緒調節"}], "docs": []},
    {"name": "計時器", "type": "實務策略", "def": "看時間", "aliases": ["預告器", "提示"], "tips": [], "related": [], "docs": ["D0"]},
    {"name": "提示卡", "type": "實務策略", "def": "", "aliases": ["提示"], "tips": [], "related": [{"rel": "relatedTo", "target": "提示"}], "docs": []},
    {"name": "等待", "type": "概念", "def": "", "aliases": [], "tips": [], "related": [], "docs": []},
]
HIER = [{"hubs": [{"id": "h1", "label": "情緒"}, {"id": "h2", "label": "環境"}, {"id": "h3"}], "subs": [{"id": "s1", "label": "調節", "hub": "h1", "definition": "d"},
         {"id": "s2", "label": "空間", "hub": "h2"}, {"id": "s3", "label": "孤兒", "hub": "h9"}],
         "place": [{"id": "c1", "parent": "s1"}, {"id": "c2", "parent": "c1"}, {"id": "c3", "parent": "c4"}, {"id": "c4", "parent": "c3"}, {"id": "c5", "parent": "s9"}, {"id": "c1", "parent": "s2"}],
         "merge": [{"from": "c5", "into": "c4"}, {"from": "c4", "into": "c1"}, {"from": "c9", "into": "c1"}, {"from": "c2", "into": "c2"}]}, {},
        {"hubs": [{"id": f"h{i}", "label": f"軸{i}"} for i in range(1, 9)], "subs": [{"id": "s8", "label": "第八", "hub": "h8"}], "place": [{"id": "c1", "parent": "s8"}]}]
DOCS = [{"文件ID": "D0", "標題": "手冊", "出處": "出版社", "證據等級": "A"}, {"文件ID": "D1", "標題": "指引", "出處": "", "證據等級": ""}]

CASES = []
for defs_rows in (DEFS_ROWS, []):
    sheets = {"kgDefs": defs_rows, "kgNodes": NODE_ROWS, "kgEdges": EDGE_ROWS, "docs": DOCS}
    CASES.append({"fn": "map", "cfg": CFG, "sheets": sheets, "title": "收拾手冊", "chunks": CHUNKS, "resp": MAP_RESP})
    CASES.append({"fn": "map", "cfg": CFG, "sheets": sheets, "title": "t", "chunks": CHUNKS[:2], "resp": [{"__throw": "壞"}]})
    CASES.append({"fn": "warn", "cfg": CFG, "sheets": sheets, "resp": [], "args": [["hasStrategy", "概念", "實務策略"], ["hasStrategy", "實務策略", "概念"], ["relatedTo", "a", "b"],
                                                                                   ["hasPart", "主軸", "概念"], ["regulates", "x", "y"]]})
    for i in (0, 1, 5):
        CASES.append({"fn": "gen", "cfg": CFG, "sheets": sheets, "genChunks": GEN_CHUNKS, "i": i, "resp": GEN_RESP})
    for h in HIER:
        CASES.append({"fn": "finish", "cfg": CFG, "sheets": sheets, "items": ITEMS, "resp": [h]})


class L:
    def __init__(self, resp):
        self.resp, self.prompts = list(resp), []

    async def generate_json(self, model, prompt, **kw):
        self.prompts.append(prompt)
        r = self.resp[len(self.prompts) - 1]
        if isinstance(r, dict) and "__throw" in r:
            raise RuntimeError(r["__throw"])
        return copy.deepcopy(r), None


def run_py(c):
    defs = defs_of(c["sheets"]["kgDefs"])
    llm = L(c["resp"])
    if c["fn"] == "map":
        r = asyncio.run(K.map_chunks(llm, "m", defs, "學前融合", PY_NODES, c["title"], c["chunks"]))
        out = {"links": [[a, b, "主要" if t == "primary" else "次要"] for a, b, t in r["links"]], "cands": r["cands"], "meta": r["meta"]}
    elif c["fn"] == "warn":
        types = {r["節點ID"]: r["類型"] for r in NODE_ROWS}
        pairs = K.pairs_of(types, [(e["主詞"], e["關係"], e["受詞"]) for e in EDGE_ROWS])
        out = [K.rel_warn(pairs, defs, *a) for a in c["args"]]
    elif c["fn"] == "gen":
        group = GEN_CHUNKS[c["i"] * K.GEN_BATCH:(c["i"] + 1) * K.GEN_BATCH]
        if not group:
            return {"out": {"items": []}, "prompts": []}
        r = asyncio.run(llm.generate_json("m", K.gen_batch_prompt(defs, "學前融合", "老師", group)))[0]
        out = {"items": K.gen_batch_items(defs, r, group)}
    else:
        keep, lst = K.gen_merge(copy.deepcopy(c["items"]))
        llm.prompts.append(K.hierarchy_prompt("學前融合", keep))
        docs = {d["文件ID"]: {"title": d["標題"], "publisher": d["出處"], "scope": d["證據等級"]} for d in DOCS}
        nodes, edges, sources, hubs = K.gen_build(defs, keep, copy.deepcopy(c["resp"][0]), docs, "g2611141200_")
        out = {"nodes": nodes, "edges": edges, "sources": sources, "groups": [{"id": "g2611141200_" + x["id"], "label": x["label"]} for x in hubs],
               "ttl": K.ontology_ttl(defs, "2026.11.14-1200"), "merged": len(lst), "kept": sum(1 for n in nodes if n["typeLabel"] not in ("主軸", "子分類"))}
    return {"out": out, "prompts": llm.prompts}


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(HERE / "parity" / "run_kgadmin_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


@pytest.mark.parametrize("i", range(len(CASES)))
def test_same_as_apps_script(gas, i):
    c, want, got = CASES[i], gas[i], run_py(CASES[i])
    if c["fn"] == "finish":
        j = want["out"]["json"]
        want = {"out": {"nodes": j["nodes"], "edges": j["edges"], "sources": j["sources"], "groups": j["groups"], "ttl": want["out"]["ttl"],
                        "merged": want["out"]["merged"], "kept": want["out"]["kept"]}, "prompts": want["prompts"]}
    assert got["prompts"] == want["prompts"], c["fn"]
    assert got["out"] == want["out"], c["fn"]


def test_lenient_where_apps_script_breaks():
    """原版把字串 main 拆成單一字元、new 給物件時整批中斷；這裡當成一個元素"""
    llm = L([{"items": [{"chunk": 1, "main": "n4", "new": {"name": "單一"}}, {"chunk": 2, "main": ["n1"]}]}])
    r = asyncio.run(K.map_chunks(llm, "m", defs_of([]), "d", PY_NODES, "t", CHUNKS[:2]))
    assert r["links"] == [("k1", "n4", "primary"), ("k2", "n1", "primary")] and r["cands"] == [{"chunkId": "k1", "name": "單一"}]
