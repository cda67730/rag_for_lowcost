"""知識圖譜的後台功能：匯入、文件標註、總覽、候選審核、依文件產生圖譜、Ontology。對應 apps-script/Graph.gs。

prompt 與結果整理逐行對應 Apps Script（tests/test_kg_admin.py 比對）；資料改存在 kg_* 資料表，每個知識庫一套圖譜。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.models import Chunk, Document, KgCandidate, KgDef, KgEdge, KgLink, KgNode, KgSource
from db.session import release
from graph.prompts import js_json
from services.llm import LLMClient
from settings.loader import js_str

KG_TOP_TYPE = "主軸"
DEFAULT_DEFS = {
    "types": [("hub", "主軸", "知識圖譜最上層的幾個主軸。"), ("sub", "子分類", "主軸底下的主題分類。"), ("concept", "概念", "領域裡的概念、現象或名詞。"),
              ("practice", "實務策略", "可以照做的做法、工具、流程或服務。"), ("theory", "理論", "理論、模型、原則或經典研究。"),
              ("policy", "法規制度", "法律、規範、標準或制度及其構成要素。"), ("category", "類別", "需求、問題或對象的分類。")],
    "rels": [("hasPart", "包含", "上層分類包含下層項目（樹狀階層）。"), ("regulates", "規範", "法規或制度對某項實務、對象或程序的規範、授權或保障。"),
             ("theoreticalBasisFor", "理論基礎", "理論、研究或概念為某項實務或概念提供理論依據。"), ("appliedIn", "應用於", "概念、能力或策略在某情境中被應用或練習。"),
             ("hasStrategy", "可用策略", "某項需求、問題或目標可搭配的策略或調整方式。"), ("supports", "支持", "人員、資源或環境對某對象或能力的支持、評估或服務。"),
             ("correspondsTo", "對應", "兩者在內容上相互對應、屬於或呼應。"), ("influences", "影響", "一方會促進、阻礙、預測或觸發另一方。"),
             ("precedes", "後續為", "階段、流程步驟或演變上的先後關係。"), ("informs", "提供資訊", "評量、觀察或資料為另一項工作提供依據或材料。"),
             ("relatedTo", "相關", "無法歸入上述類型的一般關聯。")],
    "audiences": [("a1", "一般使用者", "直接面對狀況的人。"), ("a2", "專業人員", "提供專業判斷或服務的人。")],
}
KIND_OF = {"types": "type", "rels": "relation", "audiences": "audience"}
MAP_BATCH = 12
GEN_BATCH = 15
ACTIONABLE = ("具體做法", "案例", "溝通範例")


def _u16cut(s: str, n: int) -> str:
    return str(s).encode("utf-16-le")[: n * 2].decode("utf-16-le", errors="ignore")


def _concat(v) -> list:
    """[].concat(v || [])"""
    if isinstance(v, list):
        return v
    return [] if v is None or v is False or v == "" or v == 0 else [v]


# ───────── 定義 ─────────

def load_defs(session: Session, kb_id: uuid.UUID) -> dict[str, list[dict]]:
    """{types, rels, audiences}：[{id, label, def}]；表裡沒有該種類就用預設"""
    rows = session.scalars(select(KgDef).where(KgDef.kb_id == kb_id).order_by(KgDef.kind, KgDef.def_key)).all()
    out = {}
    for key, kind in KIND_OF.items():
        a = [{"id": r.def_key, "label": r.label, "def": r.definition} for r in rows if r.kind == kind]
        out[key] = a or [{"id": i, "label": l, "def": d} for i, l, d in DEFAULT_DEFS[key]]
    return out


def audiences(defs) -> list[str]:
    return [a["label"] for a in defs["audiences"]]


def rel_label(defs, rid: str) -> str:
    return next((r["label"] for r in defs["rels"] if r["id"] == rid), rid)


def new_types(defs) -> list[str]:
    return [t["label"] for t in defs["types"] if t["label"] != KG_TOP_TYPE]


# ───────── 匯入 ─────────

def import_graph(session: Session, kb_id: uuid.UUID, data: dict | str) -> dict:
    """匯入 knowledge-graph.json：只取代「匯入」來的節點與關係，候選核可新增的節點保留。
    同一個節點 ID（node_key）原地更新，所以段落標註、同義說法、停用狀態都留著。"""
    if isinstance(data, str):
        try:
            data = json.loads(data.lstrip("\ufeff"))
        except json.JSONDecodeError:
            raise ValueError("檔案不是有效的 JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list) or not isinstance(data.get("edges"), list):
        raise ValueError("找不到 nodes 或 edges，請上傳 knowledge-graph.json")
    # 定義：檔案給了的種類整組換掉
    defs: dict[str, list] = {"type": [], "relation": [], "audience": []}
    for t in data.get("nodeTypes") or []:
        if isinstance(t, dict) and t.get("label"):
            defs["type"].append((js_str(t.get("id")) or f"t{len(defs['type']) + 1}", t["label"], js_str(t.get("definition"))))
    for r in data.get("relations") or []:
        if isinstance(r, dict) and r.get("id"):
            defs["relation"].append((r["id"], js_str(r.get("label")) or r["id"], js_str(r.get("definition"))))
    for i, a in enumerate(data.get("audiences") or []):
        o = {"label": a} if isinstance(a, str) else a
        if isinstance(o, dict) and o.get("label"):
            defs["audience"].append((js_str(o.get("id")) or f"a{i + 1}", o["label"], js_str(o.get("definition"))))
    ndefs = 0
    for kind, rows in defs.items():
        if rows:
            session.execute(delete(KgDef).where(KgDef.kb_id == kb_id, KgDef.kind == kind))
            seen = set()
            for k, label, d in rows:
                if k in seen:
                    continue
                seen.add(k)
                session.add(KgDef(kb_id=kb_id, kind=kind, def_key=k, label=label, definition=d))
                ndefs += 1
    existing = {n.node_key: n for n in session.scalars(select(KgNode).where(KgNode.kb_id == kb_id))}
    incoming = [n for n in data["nodes"] if isinstance(n, dict) and n.get("id")]
    keys = set()
    nodes: dict[str, KgNode] = {}
    for n in incoming:
        k = js_str(n["id"])
        if k in keys:
            continue
        keys.add(k)
        row = existing.get(k)
        if row is None:
            row = KgNode(kb_id=kb_id, node_key=k, name="", ntype="", status="active")
            session.add(row)
        row.name, row.ntype = js_str(n.get("label")), js_str(n.get("typeLabel") or n.get("type"))
        row.path, row.definition = " > ".join(js_str(x) for x in _concat(n.get("path"))), js_str(n.get("definition"))
        row.tips, row.source_key, row.origin = " | ".join(js_str(x) for x in _concat(n.get("practiceTips"))), ";".join(js_str(x) for x in _concat(n.get("sources"))), "import"
        nodes[k] = row
    kept = 0
    for k, row in existing.items():
        if k not in keys:
            if row.origin == "import":
                session.delete(row)          # 新檔沒有的匯入節點刪掉；標註一併刪
            else:
                nodes[k] = row
                kept += 1
    session.flush()
    for n in incoming:
        k = js_str(n["id"])
        p = js_str(n.get("parent"))
        nodes[k].parent_id = nodes[p].id if p in nodes else None
    session.execute(delete(KgEdge).where(KgEdge.kb_id == kb_id, KgEdge.origin == "import"))
    session.flush()
    nedges, seen_e = 0, set()
    for e in data["edges"]:
        if not isinstance(e, dict):
            continue
        s, t = js_str(e.get("source")), js_str(e.get("target"))
        if s in nodes and t in nodes:
            key = (nodes[s].id, js_str(e.get("relation")) or "relatedTo", nodes[t].id)
            if key in seen_e or session.get(KgEdge, key):
                continue
            seen_e.add(key)
            session.add(KgEdge(kb_id=kb_id, subj_id=key[0], rel=key[1], obj_id=key[2], note=js_str(e.get("label")), origin="import"))
            nedges += 1
    session.execute(delete(KgSource).where(KgSource.kb_id == kb_id))
    nsrc, seen_s = 0, set()
    for s in data.get("sources") or []:
        if isinstance(s, dict) and s.get("id") and js_str(s["id"]) not in seen_s:
            seen_s.add(js_str(s["id"]))
            session.add(KgSource(kb_id=kb_id, source_key=js_str(s["id"]), title=js_str(s.get("title")), publisher=js_str(s.get("publisher")),
                                 url=js_str(s.get("url")), scope=js_str(s.get("scope"))))
            nsrc += 1
    session.flush()
    return {"nodes": len(keys), "kept": kept, "edges": nedges, "sources": nsrc, "defs": ndefs}


# ───────── 文件標註（對應 kgNodeList_、kgMapPrompt_、kgMapChunks_、kgSaveMapping_） ─────────

def node_list(nodes: list[dict]) -> str:
    """nodes：[{id, name, aliases(以「、」接的字串), type, path}]"""
    return "\n".join(f"{n['id']}｜{n['name']}" + (f"（{n['aliases']}）" if n["aliases"] else "") + f"｜{n['type']}｜" + str(n["path"] or "").split(" > ")[-1] for n in nodes)


def map_prompt(defs, domain: str, lst: str, title: str, group: list[dict]) -> str:
    types = new_types(defs)
    return (f"你在替{domain}的知識文件做標註，只輸出 JSON。\n\n"
            "【標註規格】\n"
            "- main（主要標籤）：這段「直接討論」的節點，1–3 個。\n"
            "- sub（次要標籤）：這段沒有直接點名、但隱含運用的理論、策略或解釋，0–3 個。只是順帶提到的不要標。\n"
            "- 標籤只能用節點清單裡的 ID。\n"
            "- audience（讀者對象）：這段是寫給誰看的，從 " + js_json(audiences(defs)) + " 選，可多選；看不出來就給空陣列。\n"
            "- age（適用年齡）：段落「明講」的年齡範圍，例如「3–4歲」；沒明講就給空字串（代表不限年齡，不要自己推測）。\n"
            "- context（情境）：段落明講的發生情境（場合、流程階段或時段）；沒有就給空陣列。\n"
            "- new（建議新增節點）：只有當這段的主要內容在清單裡找不到合適節點時才提出。欄位：name（簡短名稱）、definition（一句話）、"
            "parent（最適合的上層節點 ID）、type（節點類型名稱）、relation（與某個既有節點的關係，可省略：{\"type\":關係ID,\"target\":節點ID}，方向為「新節點 → 目標」）、reason（為什麼現有節點不夠）。\n\n"
            "【節點類型】（新節點的 type 只能從這些選）\n" + "\n".join(f"- {t['label']}：{t['def']}" for t in defs["types"] if t["label"] in types) + "\n\n"
            "【關係類型】（relation.type 用 ID）\n" + "\n".join(f"- {r['id']}（{r['label']}）：{r['def']}" for r in defs["rels"]) + "\n\n"
            "【節點清單】（ID｜名稱（同義說法）｜類型｜所屬分類）\n" + lst + "\n\n"
            '格式：{"items":[{"chunk":1,"main":["id"],"sub":[],"audience":[],"age":"","context":[],"new":[]}]}\n\n'
            "文件標題：" + title + "\n\n" +
            "\n\n".join(f"【段落{j + 1}】\n" + _u16cut(c["text"], 1500) for j, c in enumerate(group)))


def _uniq(a) -> list:
    out = []
    for x in a:
        k = json.dumps(x, ensure_ascii=False, sort_keys=True) if isinstance(x, (dict, list)) else x
        if not any((json.dumps(y, ensure_ascii=False, sort_keys=True) if isinstance(y, (dict, list)) else y) == k for y in out):
            out.append(x)
    return out


def _num(v):
    """JS Number(v)：字串數字、布林；不行回 None"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else 0
        except ValueError:
            return None
    if v is None:
        return 0
    return None


async def map_chunks(llm: LLMClient, model: str, defs, domain: str, nodes: list[dict], title: str, chunks: list[dict]) -> dict:
    """chunks：[{id, text}] → {links: [(段落ID, 節點ID, primary|secondary)], cands: [...], meta: {段落ID: {audience, age, context}}}"""
    auds = audiences(defs)
    valid = {n["id"] for n in nodes}
    lst = node_list(nodes)
    links, cands, meta = [], [], {}
    for i in range(0, len(chunks), MAP_BATCH):
        group = chunks[i:i + MAP_BATCH]
        try:
            r, _ = await llm.generate_json(model, map_prompt(defs, domain, lst, title, group), temperature=0, no_think=True)
            items = r.get("items") if isinstance(r, dict) else None
            for it in (items if isinstance(items, list) else _concat(items)):
                if not isinstance(it, dict):
                    continue
                n = _num(it.get("chunk"))
                idx = int(n) - 1 if n is not None and float(n).is_integer() else -1
                if not 0 <= idx < len(group):
                    continue
                c = group[idx]
                main = [x for x in _uniq(_concat(it.get("main") or it.get("nodes"))) if isinstance(x, str) and x in valid][:3]
                sub = [x for x in _uniq(_concat(it.get("sub"))) if isinstance(x, str) and x in valid and x not in main][:3]
                links += [(c["id"], x, "primary") for x in main] + [(c["id"], x, "secondary") for x in sub]
                meta[c["id"]] = {"audience": "、".join(a for a in _concat(it.get("audience")) if a in auds),
                                 "age": _u16cut(js_str(it.get("age") or "").strip(), 30),
                                 "context": "、".join([y for y in (js_str(x).strip() for x in _concat(it.get("context"))) if y][:4])}
                for nw in _concat(it.get("new")):
                    if isinstance(nw, dict) and nw.get("name"):
                        cands.append({"chunkId": c["id"], **nw})
        except Exception:      # 一批失敗不影響其他批
            pass
    return {"links": links, "cands": cands, "meta": meta}


def _graph_nodes(session: Session, kb_id, include_off=False) -> list[KgNode]:
    q = select(KgNode).where(KgNode.kb_id == kb_id)
    if not include_off:
        q = q.where(KgNode.status == "active")
    return session.scalars(q.order_by(KgNode.created_at, KgNode.node_key)).all()


def node_dicts(rows: list[KgNode]) -> list[dict]:
    """標註 prompt 用：ID 用 node_key（人看得懂、與匯入檔一致）"""
    return [{"id": n.node_key, "name": n.name, "aliases": "、".join(n.aliases or []), "type": n.ntype, "path": n.path} for n in rows]


def save_mapping(session: Session, kb_id: uuid.UUID, doc_id: uuid.UUID, res: dict) -> dict:
    """寫入標註與候選；候選名稱和既有節點（含同義說法）相同就直接掛上，和待審候選相同就累加次數"""
    defs = load_defs(session, kb_id)
    rel_ids = [r["id"] for r in defs["rels"]]
    types = new_types(defs)
    nodes = _graph_nodes(session, kb_id)
    by_key = {n.node_key: n for n in nodes}
    by_name: dict[str, KgNode] = {}
    for n in nodes:
        by_name[n.name.strip()] = n
        for a in n.aliases or []:
            if a.strip():
                by_name[a.strip()] = n
    links = [(c, by_key[k].id, tag) for c, k, tag in res["links"] if k in by_key]
    pending = {c.name.strip(): c for c in session.scalars(select(KgCandidate).where(KgCandidate.kb_id == kb_id, KgCandidate.status == "pending"))}
    added = merged = 0
    for nw in res["cands"]:
        name = _u16cut(js_str(nw.get("name")).strip(), 40)
        if name in by_name:
            links.append((nw["chunkId"], by_name[name].id, "primary"))
            continue
        if name in pending:
            pending[name].hits += 1
            merged += 1
            continue
        rel = nw.get("relation") if isinstance(nw.get("relation"), dict) else None
        rel_ok = rel and rel.get("type") in rel_ids and rel.get("type") != "hasPart" and rel.get("target") in by_key
        c = KgCandidate(kb_id=kb_id, name=name, definition=_u16cut(js_str(nw.get("definition")), 200),
                        ntype=nw.get("type") if nw.get("type") in types else "概念", parent_hint=nw.get("parent") if nw.get("parent") in by_key else "",
                        rel_hint=js_json({"type": rel["type"], "target": rel["target"]}) if rel_ok else "", doc_id=doc_id, chunk_id=uuid.UUID(str(nw["chunkId"])),
                        hits=1, reason=_u16cut(js_str(nw.get("reason")), 200), status="pending")
        session.add(c)
        pending[name] = c
        added += 1
    # 同一段同一節點只留一筆；同時被標主要與次要時以主要為準
    best: dict[tuple, str] = {}
    for c, nid, tag in links:
        k = (uuid.UUID(str(c)), nid)
        if k not in best or tag == "primary":
            best[k] = tag
    for (c, nid), tag in best.items():
        old = session.get(KgLink, (c, nid))
        if old is None:
            session.add(KgLink(chunk_id=c, node_id=nid, tag=tag, method="ai"))
        elif tag == "primary":
            old.tag = "primary"
    for cid, m in (res.get("meta") or {}).items():
        ch = session.get(Chunk, uuid.UUID(str(cid)))
        if ch is not None:
            ch.audience = [a for a in m["audience"].split("、") if a]
            ch.age_range, ch.context = m["age"], m["context"]
    session.flush()
    return {"links": len(best), "main": sum(1 for t in best.values() if t == "primary"), "candidates": added, "candidateHits": merged}


def done_chunks(session: Session, kb_id) -> set:
    """已標註：至少掛到一個「目前存在」的節點（換過圖譜後掛在舊節點上的會視為未標註）"""
    q = select(KgLink.chunk_id).join(KgNode, KgNode.id == KgLink.node_id).where(KgNode.kb_id == kb_id)
    return set(session.scalars(q))


async def map_document(session: Session, llm: LLMClient, model: str, domain: str, doc_id: uuid.UUID, release_db: bool = False) -> dict:
    """release_db=True：讀完資料先提交、把連線還回去再等模型（呼叫端沒有未提交、需要一起回滾的寫入時才用）"""
    doc = session.get(Document, doc_id)
    if not session.scalar(select(KgNode.id).where(KgNode.kb_id == doc.kb_id).limit(1)):
        return {"skipped": "尚未匯入知識圖譜"}
    done = done_chunks(session, doc.kb_id)
    chunks = [{"id": str(c.id), "text": c.text} for c in session.scalars(select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.seq)) if c.id not in done]
    if not chunks:
        return {"links": 0, "main": 0, "candidates": 0, "candidateHits": 0}
    defs = load_defs(session, doc.kb_id)
    nodes, title, kb_id = node_dicts(_graph_nodes(session, doc.kb_id)), doc.title, doc.kb_id
    if release_db:
        release(session)
    res = await map_chunks(llm, model, defs, domain, nodes, title, chunks)
    if release_db:
        with session.begin_nested():        # 寫入一半失敗就整批不寫
            return save_mapping(session, kb_id, doc_id, res)
    return save_mapping(session, doc.kb_id, doc_id, res)


# ───────── 總覽與候選（對應 adminGraphLoad、kgPairs_、kgRelWarn_、adminCandAction） ─────────

def pairs_of(types_by_node: dict, edges: list[tuple]) -> dict:
    """{關係: {'來源類型>目標類型': 次數}}；edges：[(主詞, 關係, 受詞)]"""
    p: dict[str, dict[str, int]] = {}
    for s, rel, o in edges:
        a, b = types_by_node.get(s), types_by_node.get(o)
        if not a or not b:
            continue
        m = p.setdefault(rel, {})
        m[f"{a}>{b}"] = m.get(f"{a}>{b}", 0) + 1
    return p


def rel_warn(pairs, defs, rel: str, src: str, tgt: str) -> str:
    if rel == "relatedTo":
        return ""
    m = pairs.get(rel) or {}
    if m.get(f"{src}>{tgt}", 0) > 0:
        return ""
    common = "、".join(k.replace(">", "→", 1) for k in sorted(m, key=lambda k: -m[k])[:3])
    return f"圖譜裡沒有「{src} →{rel_label(defs, rel)}→ {tgt}」這種接法" + (f"（常見：{common}）" if common else "") + "，請確認"


def overview(session: Session, kb_id: uuid.UUID) -> dict:
    nodes = _graph_nodes(session, kb_id, include_off=True)
    if not nodes:
        return {"empty": True}
    defs = load_defs(session, kb_id)
    by_id = {n.id: n for n in nodes}
    edges = session.scalars(select(KgEdge).where(KgEdge.kb_id == kb_id)).all()
    rows = session.execute(select(KgLink, Chunk.ctype, Chunk.active).join(Chunk, Chunk.id == KgLink.chunk_id).where(KgLink.node_id.in_(list(by_id)))).all()
    cov: dict = {}
    for l, ctype, active in rows:
        o = cov.setdefault(l.node_id, {"all": 0, "sub": 0, "ok": 0, "act": 0})
        if l.tag != "primary":
            o["sub"] += 1
            continue
        o["all"] += 1
        if active:
            o["ok"] += 1
            if ctype in ACTIONABLE:
                o["act"] += 1
    lst = [{"id": str(n.id), "key": n.node_key, "label": n.name, "type": n.ntype, "path": n.path, "status": n.status, "from": n.origin, "syn": "、".join(n.aliases or []),
            **cov.get(n.id, {"all": 0, "sub": 0, "ok": 0, "act": 0})} for n in nodes]
    types_by = {n.id: n.ntype for n in nodes}
    pairs = pairs_of(types_by, [(e.subj_id, e.rel, e.obj_id) for e in edges])
    key_to = {n.node_key: n for n in nodes}
    cands = []
    pend = session.scalars(select(KgCandidate).where(KgCandidate.kb_id == kb_id, KgCandidate.status == "pending")).all()
    # 候選對應的段落與文件一次查完，不要每個候選各查一次
    texts = dict(session.execute(select(Chunk.id, Chunk.text).where(Chunk.id.in_({c.chunk_id for c in pend if c.chunk_id}))).all()) if pend else {}
    titles = dict(session.execute(select(Document.id, Document.title).where(Document.id.in_({c.doc_id for c in pend if c.doc_id}))).all()) if pend else {}
    for c in pend:
        rel = json.loads(c.rel_hint) if c.rel_hint else None
        parent = key_to.get(c.parent_hint)
        warns = []
        if parent:
            w = rel_warn(pairs, defs, "hasPart", parent.ntype, c.ntype)
            if w:
                warns.append("上層：" + w)
        tgt = key_to.get(rel["target"]) if rel else None
        if tgt:
            w = rel_warn(pairs, defs, rel["type"], c.ntype, tgt.ntype)
            if w:
                warns.append("建議關係：" + w)
        text, title = texts.get(c.chunk_id), titles.get(c.doc_id)
        cands.append({"id": str(c.id), "name": c.name, "def": c.definition, "parent": c.parent_hint, "parentLabel": parent.name if parent else "", "type": c.ntype,
                      "rel": {"type": rel["type"], "label": rel_label(defs, rel["type"]), "target": rel["target"], "targetLabel": tgt.name if tgt else rel["target"]} if rel else None,
                      "warns": warns, "doc": title if title is not None else "", "count": c.hits, "reason": c.reason, "snippet": _u16cut(text, 160) if text is not None else ""})
    cands.sort(key=lambda x: -x["count"])
    # 每份文件還沒標註的段落數：一個查詢算完（已標註＝至少掛到一個目前存在的節點，同 done_chunks）
    done = select(KgLink.chunk_id).join(KgNode, KgNode.id == KgLink.node_id).where(KgNode.kb_id == kb_id, KgLink.chunk_id == Chunk.id)
    todo = [{"id": str(i), "title": t, "status": st, "todo": n} for i, t, st, n in session.execute(
        select(Document.id, Document.title, Document.status, func.count(Chunk.id))
        .join(Chunk, Chunk.doc_id == Document.id)
        .where(Document.kb_id == kb_id, Document.status.not_in(("disabled", "failed", "processing")), ~done.exists())
        .group_by(Document.id).order_by(Document.uploaded_at, Document.id))]
    srcs = session.scalars(select(KgSource.source_key).where(KgSource.kb_id == kb_id)).all()
    return {"stats": {"nodes": sum(1 for n in nodes if n.status == "active"), "edges": len(edges), "sources": len(srcs), "links": len(rows),
                      "covered": sum(1 for x in lst if x["ok"] > 0), "pending": len(cands)},
            "nodes": lst, "cands": cands, "types": new_types(defs), "pairs": {k: v for k, v in pairs.items()}, "todo": todo}


def candidate_action(session: Session, cand_id: uuid.UUID, action: str, opts: dict, user_id) -> dict:
    """approve（opts 可改 name/def/parent/type）、merge（opts.target＝節點 key）、skip"""
    c = session.get(KgCandidate, cand_id)
    if c is None:
        raise LookupError("找不到候選")
    if c.status != "pending":
        raise ValueError("這個候選已處理過")
    defs = load_defs(session, c.kb_id)
    nodes = {n.node_key: n for n in _graph_nodes(session, c.kb_id, include_off=True)}
    result = ""
    if action == "approve":
        name = js_str(opts.get("name") or c.name).strip()
        if not name:
            raise ValueError("請填名稱")
        t = js_str(opts.get("type") or c.ntype or "概念")
        if t not in new_types(defs):
            raise ValueError("節點類型不正確（主軸不接受新增）")
        pk = opts.get("parent") or c.parent_hint
        parent = nodes.get(pk)
        key = "n_" + uuid.uuid4().hex[:8]
        path = " > ".join(x for x in (parent.path, parent.name) if x) if parent else ""
        n = KgNode(kb_id=c.kb_id, node_key=key, name=name, ntype=t, parent_id=parent.id if parent else None, path=path,
                   definition=js_str(opts["def"] if opts.get("def") is not None else c.definition), status="active", origin="candidate")
        session.add(n); session.flush()
        if parent:
            session.add(KgEdge(kb_id=c.kb_id, subj_id=parent.id, rel="hasPart", obj_id=n.id, note=rel_label(defs, "hasPart"), origin="candidate"))
        rel = json.loads(c.rel_hint) if c.rel_hint else None
        if rel and rel.get("target") in nodes:
            session.add(KgEdge(kb_id=c.kb_id, subj_id=n.id, rel=rel["type"], obj_id=nodes[rel["target"]].id, note=rel_label(defs, rel["type"]), origin="candidate"))
        if c.chunk_id:
            session.add(KgLink(chunk_id=c.chunk_id, node_id=n.id, tag="primary", method="candidate"))
        result, c.status = key, "accepted"
    elif action == "merge":
        t = nodes.get(opts.get("target"))
        if t is None:
            raise ValueError("請選擇要合併到的節點")
        if c.name not in (t.aliases or []):
            t.aliases = list(t.aliases or []) + [c.name]
        if c.chunk_id and session.get(KgLink, (c.chunk_id, t.id)) is None:
            session.add(KgLink(chunk_id=c.chunk_id, node_id=t.id, tag="primary", method="candidate"))
        result, c.status = t.node_key, "merged"
    elif action == "skip":
        c.status = "rejected"
    else:
        raise ValueError("未知的操作")
    c.result, c.reviewed_by, c.reviewed_at = result, user_id, datetime.now(timezone.utc)
    session.flush()
    return {"ok": True, "result": result}


# ───────── 依文件產生圖譜（對應 adminKgGenBatch、adminKgGenFinish） ─────────

def gen_batch_prompt(defs, domain: str, role_user: str, group: list[dict]) -> str:
    types = [t for t in new_types(defs) if t != "子分類"]
    return (f"你在從{domain}的專業文件建立知識圖譜，只輸出 JSON。\n"
            f"從下面段落抽出重要的「概念」（下列類型）。只抽段落真正在談、對{role_user}有用的，不要抽一般詞彙。\n"
            "每個概念：name（簡短標準名稱）、type（從下列類型選）、definition（一句話，依段落內容）、aliases（段落裡出現的其他說法，可空）、"
            "tips（段落中可直接照做的實務要點，0–2 條，可空）、related（與同批其他概念的關係：{\"rel\":關係ID,\"target\":概念 name}，方向為「本概念 → target」，可空）、chunks（出現在哪幾段，填段落編號）。\n\n"
            "【類型】\n" + "\n".join(f"- {t['label']}：{t['def']}" for t in defs["types"] if t["label"] in types) + "\n\n"
            "【關係】\n" + "\n".join(f"- {x['id']}（{x['label']}）：{x['def']}" for x in defs["rels"] if x["id"] != "hasPart") + "\n\n"
            '格式：{"concepts":[{"name":"","type":"","definition":"","aliases":[],"tips":[],"related":[],"chunks":[1]}]}\n\n' +
            "\n\n".join(f"【段落{j + 1}｜{c['title']}】\n" + _u16cut(c["text"], 1200) for j, c in enumerate(group)))


def gen_batch_items(defs, r: Any, group: list[dict]) -> list[dict]:
    types = [t for t in new_types(defs) if t != "子分類"]
    rel_ids = [x["id"] for x in defs["rels"]]
    concepts = r.get("concepts") if isinstance(r, dict) else None
    out = []
    for c in _concat(concepts):
        if not isinstance(c, dict) or not c.get("name"):
            continue
        docs = []
        for n in _concat(c.get("chunks")):
            v = _num(n)
            i = int(v) - 1 if v is not None and float(v).is_integer() else -1
            d = group[i]["docId"] if 0 <= i < len(group) else None
            if d and d not in docs:
                docs.append(d)
        out.append({"name": _u16cut(js_str(c["name"]).strip(), 40), "type": c.get("type") if c.get("type") in types else "概念",
                    "def": _u16cut(js_str(c.get("definition") or ""), 200),
                    "aliases": [y for y in (js_str(x).strip() for x in _concat(c.get("aliases"))) if y][:6],
                    "tips": [js_str(x) for x in _concat(c.get("tips")) if js_str(x)][:2],
                    "related": [{"rel": x["rel"], "target": js_str(x["target"]).strip()} for x in _concat(c.get("related"))
                                if isinstance(x, dict) and x.get("rel") in rel_ids and x.get("rel") != "hasPart" and x.get("target")],
                    "docs": docs})
    return out


def _norm(s) -> str:
    return re.sub(r"[\s「」『』（）()]", "", js_str(s)).lower()


def gen_merge(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """依名稱與別名合併；回傳（保留的前 300 個, 全部）"""
    by_key: dict[str, dict] = {}
    lst: list[dict] = []
    for it in items:
        keys = [k for k in (_norm(x) for x in [it["name"], *it["aliases"]]) if k]
        c = next((by_key[k] for k in keys if k in by_key), None)
        if c is None:
            c = {"name": it["name"], "types": {}, "defs": [], "aliases": {}, "tips": [], "related": [], "docs": {}, "n": 0}
            lst.append(c)
        c["n"] += 1
        c["types"][it["type"]] = c["types"].get(it["type"], 0) + 1
        if it["def"]:
            c["defs"].append(it["def"])
        for a in it["aliases"] + [it["name"]]:
            if a != c["name"]:
                c["aliases"][a] = 1
        for t in it["tips"]:
            if t not in c["tips"]:
                c["tips"].append(t)
        c["related"] = c["related"] + it["related"]
        for d in it["docs"]:
            c["docs"][d] = 1
        for k in keys:
            by_key[k] = c
    lst.sort(key=lambda c: (-c["n"], -len(c["docs"])))
    keep = lst[:300]
    for i, c in enumerate(keep):
        c["id"] = f"c{i + 1}"
        c["type"] = sorted(c["types"], key=lambda t: -c["types"][t])[0]
    return keep, lst


def hierarchy_prompt(domain: str, keep: list[dict]) -> str:
    return (f"你在整理{domain}知識圖譜的階層，只輸出 JSON。\n"
            "下面是從文件抽出的概念（ID｜名稱｜類型｜出現次數）。請：\n"
            "1. 定出 4–6 個「主軸」（最上層大類），每個主軸下 2–8 個「子分類」。\n"
            "2. 把每個概念放進最適合的子分類（parent 填子分類 ID）；若某概念其實是另一個概念的下位項，parent 可填該概念 ID。\n"
            "3. 若兩個概念其實是同一件事，在 merge 列出 {\"from\":被合併ID,\"into\":保留ID}。\n"
            '格式：{"hubs":[{"id":"h1","label":""}],"subs":[{"id":"s1","label":"","hub":"h1","definition":""}],"place":[{"id":"c1","parent":"s1"}],"merge":[{"from":"c9","into":"c2"}]}\n\n' +
            "\n".join(f"{c['id']}｜{c['name']}｜{c['type']}｜{c['n']}" for c in keep))


def gen_build(defs, keep: list[dict], h: Any, docs: dict[str, dict], pre: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """依階層結果組 nodes、edges、sources、hubs（與匯入格式相同）"""
    h = h if isinstance(h, dict) else {}
    by_id = {c["id"]: c for c in keep}
    merge_to: dict[str, str] = {}
    for m in _concat(h.get("merge")):
        if isinstance(m, dict) and m.get("from") in by_id and m.get("into") in by_id and m["from"] != m["into"]:
            merge_to[m["from"]] = m["into"]

    def root(i):
        x, n = i, 0
        while x in merge_to and n < 10:
            x, n = merge_to[x], n + 1
        return x

    for f in merge_to:
        t, s = by_id[root(f)], by_id[f]
        t["aliases"][s["name"]] = 1
        for a in s["aliases"]:
            t["aliases"][a] = 1
        for x in s["tips"]:
            if x not in t["tips"]:
                t["tips"].append(x)
        t["related"] = t["related"] + s["related"]
        for d in s["docs"]:
            t["docs"][d] = 1
        t["n"] += s["n"]
    nodes_c = [c for c in keep if c["id"] not in merge_to]
    hubs = [x for x in _concat(h.get("hubs")) if isinstance(x, dict) and x.get("id") and x.get("label")][:6]
    hub_ids = {x["id"]: x for x in hubs}
    subs = [x for x in _concat(h.get("subs")) if isinstance(x, dict) and x.get("id") and x.get("label") and x.get("hub") in hub_ids]
    sub_ids = {x["id"]: x for x in subs}
    place: dict[str, Any] = {}
    for p in _concat(h.get("place")):
        if isinstance(p, dict) and p.get("id") and p["id"] in by_id and p["id"] not in merge_to and p["id"] not in place:
            place[p["id"]] = p.get("parent")
    type_id = {t["label"]: t["id"] for t in defs["types"]}
    nodes, edges = [], []
    for x in hubs:
        nodes.append({"id": x["id"], "label": x["label"], "group": x["id"], "groupLabel": x["label"], "type": type_id.get("主軸") or "hub", "typeLabel": "主軸",
                      "parent": "", "path": [], "definition": "", "practiceTips": [], "sources": []})
    for x in subs:
        nodes.append({"id": x["id"], "label": x["label"], "group": x["hub"], "groupLabel": hub_ids[x["hub"]]["label"], "type": type_id.get("子分類") or "sub",
                      "typeLabel": "子分類", "parent": x["hub"], "path": [hub_ids[x["hub"]]["label"]], "definition": js_str(x.get("definition") or ""),
                      "practiceTips": [], "sources": []})
        edges.append({"source": x["hub"], "target": x["id"], "label": rel_label(defs, "hasPart"), "relation": "hasPart"})
    valid = {c["id"]: c for c in nodes_c}

    def parent0(c):
        p = place.get(c["id"])
        if p and isinstance(p, str) and root(p) in valid and root(p) != c["id"]:
            return root(p)
        return p if isinstance(p, str) and p in sub_ids else ""

    def parent_of(c):
        p = parent0(c)
        x, n = p, 0
        while x and x in valid and n < 20:
            n += 1
            if x == c["id"]:
                return ""
            x = parent0(valid[x])
        return p

    def path_of(i, seen=None):
        if i in hub_ids:
            return [hub_ids[i]["label"]]
        if i in sub_ids:
            return [hub_ids[sub_ids[i]["hub"]]["label"], sub_ids[i]["label"]]
        seen = seen if seen is not None else set()
        c = valid.get(i)
        if not c or i in seen:
            return []
        seen.add(i)
        p = parent_of(c)
        return path_of(p, seen) + [c["name"]] if p else [c["name"]]

    for c in nodes_c:
        p = parent_of(c)
        path = path_of(p) if p else []
        sub = sub_ids.get(p)
        hub = hub_ids[sub["hub"]] if sub else None
        defs_sorted = sorted(c["defs"], key=lambda d: -len(d.encode("utf-16-le")))
        nodes.append({"id": c["id"], "label": c["name"], "group": hub["id"] if hub else "", "groupLabel": hub["label"] if hub else "",
                      "type": type_id.get(c["type"]) or "concept", "typeLabel": c["type"], "parent": p, "path": path,
                      "definition": defs_sorted[0] if defs_sorted else "", "practiceTips": c["tips"][:4], "sources": list(c["docs"]),
                      "aliases": list(c["aliases"])[:8], "mentions": c["n"]})
        if p:
            edges.append({"source": p, "target": c["id"], "label": rel_label(defs, "hasPart"), "relation": "hasPart"})
    name_to: dict[str, str] = {}
    for c in nodes_c:
        name_to[_norm(c["name"])] = c["id"]
        for a in c["aliases"]:
            name_to.setdefault(_norm(a), c["id"])
    seen_e = set()
    for c in nodes_c:
        for r in c["related"]:
            t = name_to.get(_norm(r["target"]))
            if not t or t == c["id"]:
                continue
            k = (c["id"], r["rel"], t)
            if k in seen_e:
                continue
            seen_e.add(k)
            edges.append({"source": c["id"], "target": t, "label": rel_label(defs, r["rel"]), "relation": r["rel"]})
    for n in nodes:
        n["id"] = pre + n["id"]
        if n["parent"]:
            n["parent"] = pre + n["parent"]
        if n["group"]:
            n["group"] = pre + n["group"]
    for e in edges:
        e["source"], e["target"] = pre + e["source"], pre + e["target"]
    used: dict[str, int] = {}
    for c in nodes_c:
        for d in c["docs"]:
            used[d] = 1
    sources = [{"id": d, "title": js_str((docs.get(d) or {}).get("title") or d), "publisher": js_str((docs.get(d) or {}).get("publisher") or ""), "url": "",
                "scope": js_str((docs.get(d) or {}).get("scope") or "")} for d in used]
    return nodes, edges, sources, hubs


def ontology_ttl(defs, version: str) -> str:
    def q(s):
        return '"' + js_str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"@zh-Hant'

    def cls(i):
        s = re.sub(r"(^|[_-])(\w)", lambda m: m.group(2).upper(), str(i), flags=re.ASCII)
        return "kg:" + re.sub(r"[^A-Za-z0-9]", "", s)

    L = ["@prefix kg: <https://example.org/ece-kg/> .", "@prefix owl: <http://www.w3.org/2002/07/owl#> .", "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
         "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .", "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .", "@prefix dct: <http://purl.org/dc/terms/> .",
         "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .", "",
         "<https://example.org/ece-kg/ontology> a owl:Ontology ;", "  dct:title " + q("知識圖譜 Ontology（依現有文件產生，待專家審核）") + " ;", '  owl:versionInfo "' + version + '" .', "",
         "#################### 類別 ####################",
         "kg:KnowledgeNode a owl:Class ; rdfs:label " + q("知識節點") + " ."]
    for t in defs["types"]:
        L += [cls(t["id"]) + " a owl:Class ; rdfs:subClassOf kg:KnowledgeNode ;", "  rdfs:label " + q(t["label"]) + " ;", "  skos:definition " + q(t["def"]) + " ."]
    L += ["kg:Source a owl:Class ; rdfs:label " + q("資料來源") + " .", "kg:Article a owl:Class ; rdfs:label " + q("文章") + " .", "kg:Audience a owl:Class ; rdfs:label " + q("讀者對象") + " ."]
    for i, a in enumerate(defs["audiences"]):
        L.append(f"kg:audience{i + 1} a kg:Audience ; rdfs:label " + q(a["label"]) + " .")
    L += ["", "#################### 關係 ####################",
          "kg:semanticRelation a owl:ObjectProperty ; rdfs:subPropertyOf skos:related ; rdfs:label " + q("語意關聯") + " ;", "  rdfs:domain kg:KnowledgeNode ; rdfs:range kg:KnowledgeNode ."]
    for r in defs["rels"]:
        if r["id"] == "hasPart":
            L += ["kg:hasPart a owl:ObjectProperty , owl:TransitiveProperty ; rdfs:subPropertyOf skos:narrowerTransitive ;", "  rdfs:label " + q(r["label"]) + " ; owl:inverseOf kg:partOf ;",
                  "  rdfs:domain kg:KnowledgeNode ; rdfs:range kg:KnowledgeNode ;", "  skos:definition " + q(r["def"]) + " .",
                  "kg:partOf a owl:ObjectProperty , owl:TransitiveProperty ; rdfs:label " + q("屬於") + " ."]
        else:
            L += ["kg:" + r["id"] + " a owl:ObjectProperty" + (" , owl:SymmetricProperty" if r["id"] == "relatedTo" else "") + " ; rdfs:subPropertyOf kg:semanticRelation ;",
                  "  rdfs:label " + q(r["label"]) + " ;", "  skos:definition " + q(r["def"]) + " ."]
    L += ["", "#################### 資料屬性 ####################",
          "kg:practiceTip a owl:DatatypeProperty ; rdfs:label " + q("實務要點") + " ; rdfs:domain kg:KnowledgeNode ; rdfs:range rdf:langString .",
          "kg:hasSource a owl:ObjectProperty ; rdfs:subPropertyOf dct:source ; rdfs:label " + q("資料來源") + " ; rdfs:domain kg:KnowledgeNode ; rdfs:range kg:Source .",
          "", "#################### 文章標註 ####################",
          "kg:primaryTag a owl:ObjectProperty ; rdfs:label " + q("主要標籤") + " ; rdfs:domain kg:Article ; rdfs:range kg:KnowledgeNode ; skos:definition " + q("文章直接討論的主題。") + " .",
          "kg:secondaryTag a owl:ObjectProperty ; rdfs:label " + q("次要標籤") + " ; rdfs:domain kg:Article ; rdfs:range kg:KnowledgeNode ; skos:definition " + q("文章隱含的解釋、理論或策略。") + " .",
          "kg:audience a owl:ObjectProperty ; rdfs:label " + q("讀者對象") + " ; rdfs:domain kg:Article ; rdfs:range kg:Audience .",
          "kg:targetAge a owl:DatatypeProperty ; rdfs:label " + q("適用年齡") + " ; rdfs:domain kg:Article ; rdfs:range xsd:string .",
          "kg:context a owl:DatatypeProperty ; rdfs:label " + q("情境") + " ; rdfs:domain kg:Article ; rdfs:range rdf:langString .",
          "kg:proposedNode a owl:DatatypeProperty ; rdfs:label " + q("建議新增節點") + " ; rdfs:domain kg:Article ; rdfs:range xsd:string .", ""]
    return "\n".join(L)


async def generate(session: Session, llm: LLMClient, model: str, domain: str, role_user: str, kb_id: uuid.UUID, by: str, progress=None) -> dict:
    """依已核可段落產生圖譜草稿（只產生檔案，不取代系統裡的圖譜）"""
    defs = load_defs(session, kb_id)
    rows = session.execute(select(Chunk, Document).join(Document, Document.id == Chunk.doc_id)
                           .where(Chunk.kb_id == kb_id, Document.status == "approved").order_by(Document.uploaded_at, Chunk.seq)).all()
    chunks = [{"id": str(c.id), "docId": str(d.id), "title": d.title, "text": c.text} for c, d in rows]
    docs = {str(d.id): {"title": d.title, "publisher": d.source_note, "scope": d.evidence} for _, d in rows}
    release(session)        # 之後幾十批都在等模型，只讀不寫：先把連線還回去
    items: list[dict] = []
    nb = (len(chunks) + GEN_BATCH - 1) // GEN_BATCH
    for b in range(nb):
        group = chunks[b * GEN_BATCH:(b + 1) * GEN_BATCH]
        try:
            r, _ = await llm.generate_json(model, gen_batch_prompt(defs, domain, role_user, group), temperature=0)
            items += gen_batch_items(defs, r, group)
        except Exception:
            pass
        if progress:
            progress(0.8 * (b + 1) / max(nb, 1), f"抽取概念 {b + 1}/{nb} 批")
    keep, lst = gen_merge(items)
    if not keep:
        raise ValueError("沒有抽出任何概念（已核可的文件太少或模型失敗）")
    release(session)
    h, _ = await llm.generate_json(model, hierarchy_prompt(domain, keep), temperature=0)
    now = datetime.now(timezone.utc)
    pre = "g" + now.strftime("%y%m%d%H%M") + "_"
    nodes, edges, sources, hubs = gen_build(defs, keep, h, docs, pre)
    version = now.strftime("%Y.%m.%d-%H%M")
    meta = {"title": "知識圖譜（依現有文件自動產生，待專家審核）", "version": version, "date": now.strftime("%Y-%m-%d"), "generatedBy": by,
            "note": "由 AI 從已核可文件抽取，節點與關係需專家逐一確認後再匯入。", "counts": {"nodes": len(nodes), "edges": len(edges), "sources": len(sources)},
            "extracted": len(items), "merged": len(lst)}
    out = {"meta": meta, "groups": [{"id": pre + x["id"], "label": x["label"]} for x in hubs],
           "nodeTypes": [{"id": t["id"], "label": t["label"], "definition": t["def"]} for t in defs["types"]],
           "relations": [{"id": r["id"], "label": r["label"], "definition": r["def"]} for r in defs["rels"]], "sources": sources, "nodes": nodes, "edges": edges}
    return {"json": out, "ttl": ontology_ttl(defs, version), "stats": meta["counts"], "extracted": len(items), "merged": len(lst), "kept": sum(1 for n in nodes if n["typeLabel"] not in ("主軸", "子分類"))}


def export_graph(session: Session, kb_id: uuid.UUID) -> dict:
    """目前的圖譜匯出成與匯入相同的格式"""
    defs = load_defs(session, kb_id)
    nodes = _graph_nodes(session, kb_id, include_off=True)
    key = {n.id: n.node_key for n in nodes}
    edges = session.scalars(select(KgEdge).where(KgEdge.kb_id == kb_id)).all()
    srcs = session.scalars(select(KgSource).where(KgSource.kb_id == kb_id)).all()
    return {"meta": {"title": "知識圖譜", "date": datetime.now(timezone.utc).strftime("%Y-%m-%d")},
            "nodeTypes": [{"id": t["id"], "label": t["label"], "definition": t["def"]} for t in defs["types"]],
            "relations": [{"id": r["id"], "label": r["label"], "definition": r["def"]} for r in defs["rels"]],
            "audiences": [{"id": a["id"], "label": a["label"], "definition": a["def"]} for a in defs["audiences"]],
            "sources": [{"id": s.source_key, "title": s.title, "publisher": s.publisher, "url": s.url, "scope": s.scope} for s in srcs],
            "nodes": [{"id": n.node_key, "label": n.name, "typeLabel": n.ntype, "parent": key.get(n.parent_id, ""), "path": [x for x in n.path.split(" > ") if x],
                       "definition": n.definition, "practiceTips": [x for x in n.tips.split(" | ") if x], "sources": [x for x in n.source_key.split(";") if x],
                       "aliases": list(n.aliases or []), "status": n.status} for n in nodes],
            "edges": [{"source": key[e.subj_id], "target": key[e.obj_id], "relation": e.rel, "label": e.note} for e in edges if e.subj_id in key and e.obj_id in key]}
