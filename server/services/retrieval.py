"""檢索：同義詞擴充 → 對 Bot 掛的每個知識庫做向量搜尋 → 關鍵字／圖譜加分、權重、門檻 → 每庫最少段數保底 → AI 重排與配額。

與 apps-script/Knowledge.gs 的差異（刻意的）：
- Apps Script 在記憶體裡算全部段落（int8 壓縮向量）；這裡由 pgvector 對每個知識庫取最近的候選，再加分排序，所以分數到小數第三位不會完全相同。
- 多知識庫：每個掛載有標籤（出處顯示用）、權重（分數乘數）、最少段數（保底）。
rerank 與配額（rerank_select）與 Apps Script 的 rerankSelect_ 逐一對應，有比對測試。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from db.models import BotKnowledge, Document, KnowledgeBase, Synonym
from services.llm import LLMClient, Notes
from settings.loader import BotConfig

CHUNK_TYPES = ("具體做法", "案例", "溝通範例", "原則／背景", "轉介資訊")
ACTIONABLE = ("具體做法", "案例", "溝通範例")
KG_WEIGHT = {"direct": 1.0, "sub": 0.5, "hop": 0.5}
_CJK_RUN = re.compile("[一-鿿]{2,4}")


def r3(x: float) -> float:
    """JavaScript 的 Math.round(x * 1000) / 1000（.5 往正無限大）"""
    import math
    return math.floor(x * 1000 + 0.5) / 1000


# ───────── 同義詞與關鍵字 ─────────

def expand_query(text_: str, synonyms: list[tuple[str, str]]) -> dict:
    hits = [(w, c) for w, c in synonyms if w in text_]
    concepts = list(dict.fromkeys(c for _, c in hits))
    return {"text": text_ + "\n相關概念：" + "、".join(concepts) if concepts else text_, "concepts": concepts, "words": [w for w, _ in hits]}


def keywords(raw_text: str, concepts: list[str]) -> list[str]:
    return list(dict.fromkeys(list(concepts) + _CJK_RUN.findall(str(raw_text))))[:30]


def load_synonyms(session: Session, bot_id: uuid.UUID) -> list[tuple[str, str]]:
    return [(w, c) for w, c in session.execute(select(Synonym.word, Synonym.concept).where(Synonym.bot_id == bot_id).order_by(Synonym.word))]


# ───────── 掛載的知識庫 ─────────

@dataclass
class Mount:
    kb_id: uuid.UUID
    label: str
    min_hits: int
    weight: float
    embed_model: str
    embed_dim: int


def mounts(session: Session, bot_id: uuid.UUID) -> list[Mount]:
    rows = session.execute(select(BotKnowledge, KnowledgeBase).join(KnowledgeBase, KnowledgeBase.id == BotKnowledge.kb_id)
                           .where(BotKnowledge.bot_id == bot_id, BotKnowledge.enabled).order_by(BotKnowledge.sort, KnowledgeBase.slug))
    return [Mount(k.id, bk.label, bk.min_hits, bk.weight, k.embed_model, k.embed_dim) for bk, k in rows]


# ───────── 向量檢索 ─────────

@dataclass
class SearchResult:
    min_score: float
    top_k: int
    total: int
    cands: list[dict]
    hits: list[dict]
    kg_mode: str | None = None
    notes: list[str] = field(default_factory=list)


def _pgvector_version(session: Session) -> tuple[int, ...]:
    """每個連線查一次，存在 session.info"""
    v = session.info.get("pgvector_version")
    if v is None:
        raw = session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar() or "0"
        v = session.info["pgvector_version"] = tuple(int(x) for x in raw.split(".") if x.isdigit())
    return v


def _vector_candidates(session: Session, m: Mount, qvec: list[float], limit: int, only_ids: list[str] | None) -> list[dict]:
    """HNSW 先取近鄰再套 kb_id 等條件，別的知識庫或已刪除的段落會佔掉名額。
    對策：調高 ef_search；（relaxed_order 的回傳可能略為亂序，search() 會再排序）pgvector 0.8 以上開 iterative_scan；仍不足 limit 筆時改用精確搜尋重查一次。"""
    dim = int(m.embed_dim)
    sql = (f"SELECT id::text AS id, doc_id::text AS doc_id, text, page, ctype, 1 - (embedding::vector({dim}) <=> CAST(:q AS vector({dim}))) AS vec "
           f"FROM chunks WHERE embed_dim = {dim} AND active AND kb_id = :kb" + (" AND id = ANY(CAST(:ids AS uuid[]))" if only_ids is not None else "") +
           f" ORDER BY embedding::vector({dim}) <=> CAST(:q AS vector({dim})) LIMIT :n")
    params: dict[str, Any] = {"q": str(list(qvec)), "kb": m.kb_id, "n": limit}
    if only_ids is not None:
        params["ids"] = only_ids
    session.execute(text("SELECT set_config('hnsw.ef_search', :v, true)"), {"v": str(min(1000, max(100, limit * 2)))})
    if _pgvector_version(session) >= (0, 8):
        session.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    rows = [dict(r._mapping) for r in session.execute(text(sql), params)]
    if len(rows) < limit:
        # 排序式加上「+ 0」讓 HNSW 索引用不上，變成精確搜尋（只掃這個知識庫的段落）
        exact = sql.replace(f"ORDER BY embedding::vector({dim}) <=> CAST(:q AS vector({dim}))", f"ORDER BY (embedding::vector({dim}) <=> CAST(:q AS vector({dim}))) + 0")
        rows = [dict(r._mapping) for r in session.execute(text(exact), params)]
    return rows


def search(session: Session, cfg: BotConfig, ms: list[Mount], qvec: list[float], raw_text: str, concepts: list[str],
           pool: int | None = None, kg: dict | None = None) -> SearchResult:
    """對每個掛載的知識庫取候選，加分排序後取前 pool 段（給重排）；每庫的 min_hits 在門檻內保底"""
    kg_mode = (cfg.str("kg_mode") or "hybrid") if kg else ""
    kg_boost = cfg.num("kg_boost") or 0.08
    keys = keywords(raw_text, concepts)
    min_score = cfg.num("min_score") or 0.45
    top_k = pool or cfg.int("top_k") or 6
    notes: list[str] = []
    dim = len(qvec)
    only = list(kg["chunks"]) if kg_mode == "only" else None
    scored: list[dict] = []
    for m in ms:
        if int(m.embed_dim) != dim:
            notes.append(f"知識庫維度 {m.embed_dim} 與查詢向量 {dim} 不同，已略過")
            continue
        for c in _vector_candidates(session, m, qvec, max(top_k * 4, 40), only):
            kw = min(sum(1 for w in keys if w in c["text"]), 5)
            kgv = (kg["chunks"].get(c["id"], "") if kg else None)
            g = kg_boost * KG_WEIGHT[kgv] if kgv else 0.0
            base = c["vec"] + kw * 0.01 + g
            scored.append({**c, "kb_id": m.kb_id, "label": m.label, "kw": kw, "kg": kgv, "score": base * m.weight, "_mount": m})
    scored.sort(key=lambda x: -x["score"])
    passed = [x for x in scored if x["score"] >= min_score]
    chosen = passed[:top_k]
    # 每庫最少段數：不足時用該庫門檻內的下一段，換掉「超出自己保底名額的庫」裡分數最低的
    for m in ms:
        need = m.min_hits - sum(1 for x in chosen if x["_mount"] is m)
        extra = [x for x in passed if x["_mount"] is m and x not in chosen]
        while need > 0 and extra:
            victims = [x for x in chosen if sum(1 for y in chosen if y["_mount"] is x["_mount"]) > x["_mount"].min_hits]
            add = extra.pop(0)
            if len(chosen) >= top_k:
                if not victims:
                    break
                chosen.remove(min(victims, key=lambda x: x["score"]))
            chosen.append(add); need -= 1
    chosen.sort(key=lambda x: -x["score"])
    docs = {}
    ids = list({uuid.UUID(x["doc_id"]) for x in chosen} | {uuid.UUID(x["doc_id"]) for x in scored[:10]})
    if ids:
        docs = {str(d.id): d for d in session.scalars(select(Document).where(Document.id.in_(ids)))}
    hits = []
    for i, x in enumerate(chosen):
        d = docs.get(x["doc_id"])
        hits.append({"ref": f"K{i + 1}", "id": x["id"], "doc_id": x["doc_id"], "kb_id": str(x["kb_id"]), "label": x["label"],
                     "title": d.title if d else "", "source": d.source_note if d else "", "evidence": d.evidence if d else "",
                     "page": x["page"], "text": x["text"], "score": r3(x["score"]), "type": x["ctype"]})
    ref_of = {h["id"]: h["ref"] for h in hits}
    cands = [{"rank": i + 1, "ref": ref_of.get(x["id"], ""), "id": x["id"], "doc_id": x["doc_id"], "label": x["label"],
              "title": docs[x["doc_id"]].title if x["doc_id"] in docs else "", "page": x["page"], "score": r3(x["score"]), "vec": r3(x["vec"]),
              "kw": x["kw"], "kg": x["kg"], "pass": x["id"] in ref_of, "text": x["text"][:300], "type": x["ctype"]} for i, x in enumerate(scored[:10])]
    return SearchResult(min_score, top_k, len(scored), cands, hits, kg_mode or None, notes)


# ───────── 重新排序與配額（對應 rerankSelect_） ─────────

def rerank_prompt(cfg: BotConfig, focus: str, case_text: str, pool: list[dict]) -> str:
    u = cfg.roles()["user"]
    return (f"你在替{u}的提問挑選參考資料，只輸出 JSON。\n"
            f"{u}這次想要的（提問焦點）：{focus}\n{u}的描述：" + str(case_text)[:1500] + "\n\n"
            "為每段資料打分：3＝能直接回答焦點（有具體可用的做法）、2＝部分回答、1＝只是相關背景或一般原則、0＝無關。\n"
            "注意：焦點若指名特定對象或情況（例如某種障礙類別、年齡、場合），段落必須是在談這個對象或情況才能給 2 或 3；談的是別的對象，即使做法看起來可以套用，也只能給 1。\n"
            '格式：{"scores":[3,1,…]}，順序與段落相同。\n\n' +
            "\n\n".join(f"【{i + 1}】" + (f"（{h['type']}）" if h.get("type") else "") + h["text"][:600] for i, h in enumerate(pool)))


def _num_or_none(v):
    """JavaScript 的 arr[i] == null ? null : Number(arr[i])（NaN→null），再夾在 0–3：true→1、''→0、'2'→2"""
    if v is None:
        return None
    if isinstance(v, bool):
        f = 1.0 if v else 0.0
    elif isinstance(v, str) and v.strip() == "":
        f = 0.0
    else:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
    return None if f != f else max(0.0, min(3.0, f))


async def rerank_select(cfg: BotConfig, llm: LLMClient | None, focus: str, case_text: str, pool: list[dict], notes: Notes | None = None) -> dict:
    top_k = cfg.int("top_k") or 6
    min_act, max_pri = cfg.int("quota_min_actionable"), cfg.int("quota_max_principle")
    model = cfg.str("model_rerank") or cfg.str("model_check") or cfg.str("model_triage")
    scores: list[float | None] = [None] * len(pool)
    used, why = False, ""
    if not cfg.flag("enable_rerank"):
        why = "重排已關閉"
    if pool and cfg.flag("enable_rerank"):
        try:
            r, _ = await llm.generate_json(model, rerank_prompt(cfg, focus, case_text, pool), temperature=0, no_think=True, notes=notes)
            arr = r if isinstance(r, list) else (r.get("scores") if isinstance(r, dict) else None)
            if not isinstance(arr, list):
                notes and notes("缺欄位", "scores")
                arr = []
            elif len(arr) != len(pool):
                notes and notes("數量不符", f"分數 {len(arr)} 個／段落 {len(pool)} 段")
            scores = [_num_or_none(arr[i]) if i < len(arr) else None for i in range(len(pool))]
            used = any(v is not None for v in scores)
            if not used:
                scores, why = [None] * len(pool), "讀不出分數"
        except Exception as e:
            why = "重排失敗：" + str(e)[:120]
            notes and notes("重排失敗", str(e))
    items = [{"h": h, "s": scores[i], "i": i} for i, h in enumerate(pool)]
    key = lambda x: 1.5 if x["s"] is None else x["s"]
    order = sorted([x for x in items if x["s"] is None or x["s"] > 0], key=lambda x: (-key(x), -x["h"]["score"]))
    is_act = lambda x: x["h"].get("type") in ACTIONABLE
    is_pri = lambda x: x["h"].get("type") == "原則／背景"
    pick = [x for x in order if is_act(x)][:min(min_act, top_k)]
    for x in order:
        if len(pick) >= top_k or x in pick:
            continue
        if is_pri(x) and sum(1 for y in pick if is_pri(y)) >= max_pri:
            continue
        pick.append(x)
    pick.sort(key=lambda x: order.index(x))
    hits = [{**x["h"], "ref": f"K{n + 1}", "rerank": x["s"]} for n, x in enumerate(pick)]
    direct = sum(1 for x in pick if x["s"] is not None and x["s"] >= 2) if used else len(pick)
    ref_of = {h["id"]: h["ref"] for h in hits}
    return {"hits": hits, "direct": direct, "actionable": sum(1 for x in pick if is_act(x)),
            "trace": {"used": used, "why": why, "model": model, "focus": focus, "topK": top_k, "minAct": min_act, "maxPri": max_pri,
                      "items": [{"id": x["h"]["id"], "title": x["h"].get("title", ""), "page": x["h"].get("page", ""), "type": x["h"].get("type") or "",
                                 "vec": x["h"]["score"], "rerank": x["s"], "ref": ref_of.get(x["h"]["id"], ""), "text": x["h"]["text"][:200]} for x in items]}}
