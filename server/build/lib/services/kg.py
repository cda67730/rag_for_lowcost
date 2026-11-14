"""知識圖譜比對（問答時用，不呼叫模型）。對應 apps-script/Graph.gs 的 kgMatch_。

多知識庫：Bot 掛的每個知識庫各有自己的圖譜，問答時把它們合在一起比對；段落 ID 全域唯一，不會撞。
"""
from __future__ import annotations

import uuid

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from db.models import KgEdge, KgLink, KgNode
from services.deid import u16len
from settings.loader import BotConfig

KG_TOP_TYPE = "主軸"          # 圖譜最上層的分類，不當成命中節點
KG_REL_ORDER = ("hasStrategy", "appliedIn", "theoreticalBasisFor", "supports", "informs", "influences", "correspondsTo", "regulates", "precedes", "relatedTo")
_RANK = ("direct", "sub", "hop")


def _u16cut(s: str, n: int) -> str:
    return s.encode("utf-16-le")[: n * 2].decode("utf-16-le", errors="ignore")


def _rel_rank(rel: str) -> int:
    return KG_REL_ORDER.index(rel) if rel in KG_REL_ORDER else -1   # 與 JS indexOf 相同：不認得的關係排最前


def match(nodes: list[dict], edges: list[dict], links: list[dict], text: str, concepts: list[str], max_nodes: int) -> dict:
    """nodes：{id,name,type,aliases,def,tips}（已排除停用）；edges：{subj,rel,obj}；links：{chunk,node,tag: primary|secondary}"""
    hay = str(text or "") + "\n" + "\n".join(concepts or [])
    by_id = {n["id"]: n for n in nodes}
    # 1. 直接命中：名稱或同義說法出現在問題裡（長的優先，避免「情緒」蓋過「情緒調節」）
    direct = []
    for n in nodes:
        if n["type"] == KG_TOP_TYPE:
            continue
        names = [x.strip() for x in [n["name"], *(n.get("aliases") or [])]]
        hit = next((x for x in names if u16len(x) >= 2 and x in hay), None)
        if hit:
            direct.append((n["id"], hit))
    direct.sort(key=lambda x: -u16len(x[1]))
    picked: dict[str, int] = {i: 0 for i, _ in direct[: max(1, max_nodes)]}
    # 2. 沿關係擴一層（不走 hasPart 的階層），每個直接命中的節點最多帶 3 個鄰居
    for nid in list(picked):
        nb = sorted((e for e in edges if (e["subj"] == nid or e["obj"] == nid) and e["rel"] != "hasPart"), key=lambda e: _rel_rank(e["rel"]))
        seen: list[str] = []
        for e in nb:
            o = e["obj"] if e["subj"] == nid else e["subj"]
            if o in by_id and o not in picked and o not in seen:
                seen.append(o)
        for o in seen[:3]:
            picked[o] = 1
    out = [{"id": i, "name": by_id[i]["name"], "type": by_id[i]["type"], "def": _u16cut(by_id[i].get("def") or "", 200),
            "tips": _u16cut(by_id[i].get("tips") or "", 300), "hop": h} for i, h in picked.items()]
    # 3. 標到這些節點的段落：直接命中的主要標籤 > 次要標籤 > 鄰居
    chunks: dict[str, str] = {}
    if out:
        for l in links:
            h = picked.get(l["node"])
            if h is None:
                continue
            w = ("sub" if l["tag"] == "secondary" else "direct") if h == 0 else "hop"
            cur = chunks.get(l["chunk"])
            if not cur or _RANK.index(w) < _RANK.index(cur):
                chunks[l["chunk"]] = w
    return {"nodes": out, "chunks": chunks, "note": "" if out else "問題裡沒有對到任何節點"}


def kg_match(session: Session, cfg: BotConfig, kb_ids: list[uuid.UUID], text: str, concepts: list[str]) -> dict:
    """從資料庫讀 Bot 掛的知識庫的圖譜後比對"""
    if not kb_ids or not session.scalar(select(exists().where(KgNode.kb_id.in_(kb_ids)))):
        return {"nodes": [], "chunks": {}, "note": "尚未匯入圖譜"}
    rows = session.scalars(select(KgNode).where(KgNode.kb_id.in_(kb_ids), KgNode.status == "active").order_by(KgNode.kb_id, KgNode.created_at, KgNode.node_key)).all()
    nodes = [{"id": str(n.id), "name": n.name, "type": n.ntype, "aliases": list(n.aliases or []), "def": n.definition, "tips": n.tips} for n in rows]
    edges = [{"subj": str(e.subj_id), "rel": e.rel, "obj": str(e.obj_id)} for e in session.scalars(select(KgEdge).where(KgEdge.kb_id.in_(kb_ids)))]
    ids = [n.id for n in rows]
    links = [{"chunk": str(l.chunk_id), "node": str(l.node_id), "tag": l.tag} for l in session.scalars(select(KgLink).where(KgLink.node_id.in_(ids)))] if ids else []
    return match(nodes, edges, links, text, concepts, cfg.int("kg_max_nodes") or 3)
