"""問答流程（LangGraph）。對應 apps-script/Chat.gs 的 runChat_()：直線程序的每一段是一個節點，提早結束是指向 finish 的條件邊。

節點與 Apps Script 的對照見 docs/langgraph-mapping.md。資料庫連線、模型客戶端等不放進 state，從 config["configurable"]["deps"] 取。
注意：LangGraph 依節點第一個參數的型別提示過濾輸入，所以每個節點都標 ChatState，新欄位一定要加進 ChatState。
"""
from __future__ import annotations

import asyncio
import operator
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Conversation, Referral, Skill
from graph import prompts as P
from graph.check import clean_answer, load_forbidden, run_checks, validate_answer
from services import deid as D
from services.kg import kg_match
from services.llm import LLMClient
from services.retrieval import SearchResult, expand_query, load_synonyms, mounts, rerank_select, search
from settings.loader import BotConfig, js_str, js_truthy, load_config


# ───────── 依賴與 state ─────────

@dataclass
class Deps:
    session: Session
    llm: LLMClient
    bot_id: uuid.UUID
    flow_id: uuid.UUID | None = None
    override: dict | None = None      # 評測時的設定覆蓋
    log: bool = True                  # 寫對話紀錄（評測不寫）
    with_trace: bool = False
    with_hits: bool = False


@dataclass
class Turn:
    """一個回合裡跨節點共用、會被就地更新的東西（計時、穩定度事件、品質紀錄）"""
    t0: float
    stage: str = ""
    ev: list[dict] = field(default_factory=list)
    rewrite_why: list[str] = field(default_factory=list)

    def ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def note(self, kind: str, msg: str) -> None:
        if len(self.ev) < 30:
            self.ev.append({"at": self.stage, "kind": kind, "note": D_cut(js_str(msg), 120)})


def D_cut(s: str, n: int) -> str:
    return s.encode("utf-16-le")[: n * 2].decode("utf-16-le", errors="ignore")


class ChatState(TypedDict, total=False):
    req: dict                          # API 請求：message、history、nameMap、followups、answered、session、turn、unit
    turn: Turn
    cfg: BotConfig
    data: dict                         # 設定以外、從資料庫讀一次的東西：mounts、skills、refs、synonyms、forbidden
    trace: Annotated[list, operator.add]
    deid: dict                         # {text, map}
    merge_deid: bool
    p03: dict
    prev: list[str]                    # 之前的提問者訊息
    history: list[dict]
    exp_all: dict
    exp_new: dict
    tri: dict
    vecs: list[list[float]]
    ref: dict | None
    intent_key: str
    is_new: bool
    case_text: str
    answered: int
    fu_done: int
    metrics: dict
    quality: dict
    pick: list[dict]
    skill_text: str
    mode: str
    kg: dict | None
    hits: list[dict]
    focus: str
    fallback: bool
    attempt: int
    max_attempts: int
    fb_note: list[str] | None
    ans: dict
    chk: dict
    rewritten: bool
    prompt_text: str
    _next: str                         # 生成迴圈裡下一步：check／next／pass
    out: dict                          # finish 用的 type 與額外欄位
    result: dict


def T(st: ChatState, step: str, detail: Any, kind: str | None = None) -> dict:
    return {"step": step, "kind": kind or ("text" if isinstance(detail, str) else "json"), "detail": detail, "ms": st["turn"].ms()}


def deps_of(config) -> Deps:
    return config["configurable"]["deps"]


# ───────── 從資料庫讀的資料 ─────────

def load_skills(session: Session, bot_id: uuid.UUID) -> list[dict]:
    rows = session.scalars(select(Skill).where(Skill.bot_id == bot_id, Skill.enabled).order_by(Skill.skill_key))
    return [{"id": s.skill_key, "name": s.name, "tags": [t.strip() for t in s.topics if t.strip()], "always": s.always_on, "content": s.content, "version": s.version}
            for s in rows]


def load_referrals(session: Session, bot_id: uuid.UUID) -> dict[str, dict]:
    rows = session.scalars(select(Referral).where(Referral.bot_id == bot_id).order_by(Referral.sort, Referral.category))
    return {r.category: {"who": r.who, "contact": r.contact, "now": r.now_action} for r in rows}


# ───────── 節點 ─────────

def n_load_config(st: ChatState, config) -> dict:
    d = deps_of(config)
    cfg = load_config(d.session, d.bot_id, d.flow_id, d.override)
    data = {"mounts": mounts(d.session, d.bot_id), "skills": load_skills(d.session, d.bot_id), "refs": load_referrals(d.session, d.bot_id),
            "synonyms": load_synonyms(d.session, d.bot_id), "forbidden": load_forbidden(d.session, d.bot_id)}
    for k, m in cfg.notes:      # 設定讀取時發現的問題也記進穩定度
        st["turn"].note(k, m)
    return {"cfg": cfg, "data": data, "metrics": {"retrieved": None, "top": None, "cited": None, "skills": []}, "quality": {"gap": None, "l5": []}}


async def n_deid(st: ChatState, config) -> dict:
    """P03：關閉→原文；ai 且 L4 開→先套已知代稱，新名字等 L4 回報；其他→單獨處理"""
    cfg, req, tn = st["cfg"], st["req"], st["turn"]
    use_deid, use_tri = cfg.flag("enable_deid"), cfg.flag("enable_triage")
    mode = js_str(cfg.raw("deid_mode") or "ai")
    merge = use_deid and use_tri and mode != "rule"
    raw = js_str(req.get("message"))
    nm = dict(req.get("nameMap") or {})
    if not use_deid:
        d = {"text": raw, "map": {}}
        p03 = {"step": "P03 去識別化（已關閉）", "kind": "text", "detail": "原文直接使用", "ms": tn.ms()}
    elif merge:
        d = {"text": D.apply(D.scrub(raw), nm), "map": nm}
        p03 = {"step": "P03 去識別化", "kind": "text", "detail": "併入 L4 處理中", "ms": tn.ms()}
    else:
        r = await D.deidentify(deps_of(config).llm, cfg, raw, nm, tn.note)
        d = {"text": r.text, "map": r.map}
        p03 = {"step": "P03 去識別化", "kind": "text",
               "detail": ("規則" if mode == "rule" else "AI") + (f"（AI 失敗改用規則：{r.fallback}）" if r.fallback else "") + "｜" + _deid_note(d["map"]), "ms": tn.ms()}
    history = list(req.get("history") or [])[-8:]
    prev = [js_str(h.get("text")) for h in history if isinstance(h, dict) and h.get("role") == "teacher"]
    return {"deid": d, "merge_deid": merge, "p03": p03, "history": history, "prev": prev}


def _deid_note(m: dict) -> str:
    return "代稱：" + ("、".join(m.values()) if m else "無")


def n_expand_query(st: ChatState, config) -> dict:
    syn = st["data"]["synonyms"]
    full = "\n".join(st["prev"] + [st["deid"]["text"]])
    return {"exp_all": expand_query(full, syn), "exp_new": expand_query(st["deid"]["text"], syn)}


def _embed_spec(st: ChatState) -> tuple[str, int] | None:
    ms = st["data"]["mounts"]
    return (ms[0].embed_model, ms[0].embed_dim) if ms else None


async def n_triage_and_embed(st: ChatState, config) -> dict:
    """L4 分流 ‖ 查詢向量（並行）；向量同時算整段個案與只有最新訊息，分流後再選用"""
    d, cfg, tn = deps_of(config), st["cfg"], st["turn"]
    topics = list(dict.fromkeys(t for s in st["data"]["skills"] if not s["always"] for t in s["tags"]))
    spec = _embed_spec(st)

    async def embed():
        if not spec:
            return [[], []]
        return await d.llm.embed(spec[0], [st["exp_all"]["text"], st["exp_new"]["text"]], dim=spec[1], task="query")

    if cfg.flag("enable_triage"):
        prompt = P.triage_prompt(cfg, st["prev"], st["deid"]["text"], topics, list(st["data"]["refs"]), list(st["deid"]["map"]) if st["merge_deid"] else None)
        tn.stage = "L4"
        (tri, _), vecs = await asyncio.gather(d.llm.generate_json(cfg.str("model_triage"), prompt, temperature=0, no_think=True, notes=tn.note), embed())
        _validate_triage(cfg, tri, tn.note)
        tri = tri if isinstance(tri, dict) else {}
        return {"tri": tri, "vecs": vecs}
    vecs = await embed()
    full = "\n".join(st["prev"] + [st["deid"]["text"]])
    words = full + "\n" + "\n".join(st["exp_all"]["concepts"])
    tri = {"intent": cfg.intent_name("more" if st["prev"] else "new"), "focus": "", "referral": {"category": "none", "level": "none"},
           "topics": [t for t in topics if t and t in words], "in_scope": True, "info_enough": True, "missing": [], "skipped": True}
    return {"tri": tri, "vecs": vecs}


def _validate_triage(cfg: BotConfig, t: Any, note) -> None:
    if not isinstance(t, dict):
        return note("型別錯誤", "分流結果不是物件")
    for k in ("intent", "focus", "referral", "topics", "in_scope", "info_enough"):
        if k not in t:
            note("缺欄位", k)
    if "intent" in t and not cfg.intent_key(t["intent"]):
        note("選項不符", "intent＝" + js_str(t["intent"]))
    if "topics" in t and not isinstance(t["topics"], list):
        note("型別錯誤", "topics 不是陣列")


def n_apply_deid_result(st: ChatState, config) -> dict:
    """L4 回報的名字套成代稱；focus／missing／轉介原因一併換掉；沒回清單就退回規則"""
    cfg, tri, d, p03, tn = st["cfg"], dict(st["tri"]), st["deid"], dict(st["p03"]), st["turn"]
    upd: dict = {}
    if st["merge_deid"]:
        labels = cfg.deid_labels()
        if isinstance(tri.get("deid"), list):
            r = D.from_items(d["text"], d["map"], tri["deid"], labels)
            p03["detail"] = "AI（併入 L4）｜" + _deid_note(r.map)
        else:
            tn.note("缺欄位", "deid")
            r = D.rule(d["text"], d["map"], labels)
            p03["detail"] = "AI（L4 沒回報名字清單，改用規則）｜" + _deid_note(r.map)
        tri.pop("deid", None)
        d = {"text": r.text, "map": r.map}
        if js_truthy(tri.get("focus")):
            tri["focus"] = D.apply(js_str(tri["focus"]), d["map"])
        if isinstance(tri.get("missing"), list):
            tri["missing"] = [D.apply(js_str(x), d["map"]) for x in tri["missing"]]
        rf = tri.get("referral")
        if isinstance(rf, dict) and js_truthy(rf.get("reason")):
            tri["referral"] = {**rf, "reason": D.apply(js_str(rf["reason"]), d["map"])}
        p03["ms"] = tn.ms()
        upd = {"deid": d}
    rf = tri.get("referral") if isinstance(tri.get("referral"), dict) else {}
    ref = rf if (js_truthy(rf.get("category")) and rf.get("category") != "none" and js_truthy(rf.get("level")) and rf.get("level") != "none") else None
    # 意圖：new 只看這一則；enable_case_flow 關閉時整段對話當同一個案、每次都完整回答
    prev = st["prev"]
    key = cfg.intent_key(tri.get("intent")) or ("more" if prev else "new")
    if not cfg.flag("enable_case_flow") and key != "off":
        key = "more" if prev else "new"
    tri["intent_key"] = key
    is_new = key == "new" or not prev
    case_text = d["text"] if is_new else "\n".join(prev + [d["text"]])
    req = st["req"]
    exp = st["exp_new"] if is_new else st["exp_all"]
    trace = [p03]
    trace.append(T(st, "L4 分流", tri, "triage") if cfg.flag("enable_triage") else
                 T(st, "L4 分流（已關閉）", "未呼叫分流 AI；主題依關鍵字比對：" + ("、".join(tri["topics"]) or "無") + "｜不偵測轉介、不追問、同一段對話視為同一個案"))
    trace.append(T(st, "P06 同義詞擴充", "、".join(exp["words"]) + " → " + "、".join(exp["concepts"]) if exp["concepts"] else "無"))
    return {**upd, "tri": tri, "ref": ref, "intent_key": key, "is_new": is_new, "case_text": case_text,
            "answered": 0 if is_new else _js_number(req.get("answered")), "fu_done": 0 if is_new else _js_number(req.get("followups")), "trace": trace}


def _js_number(v: Any) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def route_state(st: ChatState) -> str:
    ref, key, tri = st["ref"], st["intent_key"], st["tri"]
    if ref and ref.get("level") == "緊急":
        return "finish_referral"
    if key == "off" or tri.get("in_scope") is False:
        return "finish_out_of_scope"
    if key != "specific" and not js_truthy(tri.get("info_enough")) and st["fu_done"] < _max_fu(st["cfg"]):
        return "followup"
    return "load_skills"


def _max_fu(cfg: BotConfig) -> int:
    v = cfg.raw("max_followups")
    return 1 if v is None else _js_number(v)


def n_finish_referral(st: ChatState, config) -> dict:
    ref = st["ref"]
    r = st["data"]["refs"].get(ref.get("category")) or {}
    return {"trace": [T(st, "P04 狀態機", f"緊急轉介：{ref.get('category')} → 不走一般回答")],
            "out": {"type": "referral", "referral": {"category": ref.get("category"), "level": ref.get("level"), "reason": ref.get("reason"),
                                                      "who": r.get("who"), "contact": r.get("contact"), "now": r.get("now")}}}


def n_finish_out_of_scope(st: ChatState, config) -> dict:
    return {"trace": [T(st, "P04 狀態機", f"超出服務範圍（{st['cfg'].intent_name(st['intent_key'])}）")],
            "out": {"type": "out_of_scope", "message": st["cfg"].text("msg_out_of_scope")}}


def _pick_skills(st: ChatState) -> list[dict]:
    cfg, skills, topics = st["cfg"], st["data"]["skills"], st["tri"].get("topics") or []
    topics = topics if isinstance(topics, list) else []
    pick = [s for s in skills if s["always"] or any(t in topics for t in s["tags"])]
    if not any(not s["always"] for s in pick):
        dflt = next((s for s in skills if s["name"] == cfg.str("default_skill")), None)
        if dflt:
            pick.append(dflt)
    return pick


def _skill_trace(st, pick):
    return T(st, "P05 Skill 載入", [{"name": s["name"], "version": s["version"], "always": s["always"]} for s in pick], "skills")


async def n_followup(st: ChatState, config) -> dict:
    cfg = st["cfg"]
    pick = _pick_skills(st)
    st["metrics"]["skills"] = [s["name"] for s in pick]
    missing = st["tri"].get("missing")
    prompt = P.followup_prompt(cfg, cfg.str("system_prompt"), P.skill_text(cfg, pick), [js_str(m) for m in missing] if isinstance(missing, list) else [], st["case_text"])
    fu, _ = await deps_of(config).llm.generate_json(cfg.str("model_answer"), prompt, temperature=0.4, no_think=True, notes=st["turn"].note)
    fu = fu if isinstance(fu, dict) else {}
    qs = fu.get("questions") if isinstance(fu.get("questions"), list) else []
    return {"trace": [T(st, "P04 狀態機", "資訊不足 → 追問"), _skill_trace(st, pick), T(st, "A1 追問", fu)],
            "out": {"type": "followup", "empathy": js_str(fu.get("empathy")), "questions": [js_str(q) for q in qs][:2], "followups": st["fu_done"] + 1}}


def n_load_skills(st: ChatState, config) -> dict:
    cfg = st["cfg"]
    pick = _pick_skills(st)
    st["metrics"]["skills"] = [s["name"] for s in pick]
    case_flow = cfg.flag("enable_case_flow")
    v = cfg.raw("full_answer_rounds")
    full_rounds = 2 if v is None or v == "" else _js_number(v)
    key, answered = st["intent_key"], st["answered"]
    mode = "full" if not case_flow else "continue" if key == "specific" and not st["is_new"] else ("continue" if answered >= full_rounds else "full")
    ref = st["ref"]
    msg = (f"{cfg.intent_name(key)}｜{'資訊足夠' if js_truthy(st['tri'].get('info_enough')) else '已追問過'}｜已完整回答 {answered} 次 → "
           + ("完整回答" if mode == "full" else "延續回覆") + ("" if case_flow else "（個案機制已關閉）") + (f"（附轉介：{ref.get('category')}・{ref.get('level')}）" if ref else ""))
    return {"pick": pick, "skill_text": P.skill_text(cfg, pick), "mode": mode, "trace": [T(st, "P04 狀態機", msg), _skill_trace(st, pick)]}


def n_kg_match(st: ChatState, config) -> dict:
    cfg = st["cfg"]
    focus = js_str(st["tri"].get("focus")) or st["deid"]["text"]
    if not cfg.flag("enable_kg"):
        return {"kg": None, "focus": focus}
    exp = st["exp_new"] if st["is_new"] else st["exp_all"]
    kg = kg_match(deps_of(config).session, cfg, [m.kb_id for m in st["data"]["mounts"]], st["case_text"] + "\n" + focus, exp["concepts"])
    mode = cfg.str("kg_mode") or "hybrid"
    tr = [T(st, "知識圖譜", {"mode": mode, "nodes": [("↳ " if n["hop"] else "") + n["name"] + "（" + n["type"] + "）" for n in kg["nodes"]], "chunks": len(kg["chunks"]), "note": kg["note"]}, "json")]
    if mode == "only" and not kg["chunks"]:
        tr.append(T(st, "P06 檢索", "圖譜 only 模式但沒有對到任何段落 → 視為沒有可用知識"))
    return {"kg": kg, "focus": focus, "trace": tr}


async def n_retrieve(st: ChatState, config) -> dict:
    d, cfg = deps_of(config), st["cfg"]
    exp = st["exp_new"] if st["is_new"] else st["exp_all"]
    vecs = st["vecs"] or [[], []]
    qvec = vecs[1] if st["is_new"] else vecs[0]
    pool = max(cfg.int("rerank_pool") or 12, cfg.int("top_k") or 6)
    if qvec:
        sd = search(d.session, cfg, st["data"]["mounts"], qvec, st["case_text"], exp["concepts"], pool=pool, kg=st["kg"])
    else:   # 沒有掛知識庫
        sd = SearchResult(cfg.num("min_score") or 0.45, pool, 0, [], [], None, ["沒有掛載知識庫"])
    for n in sd.notes:
        st["turn"].note("設定錯誤", n)
    m = st["metrics"]
    m["top"] = sd.cands[0]["score"] if sd.cands else 0
    tr = [T(st, "P06 檢索", {"minScore": sd.min_score, "topK": sd.top_k, "total": sd.total, "cands": sd.cands, "kgMode": sd.kg_mode}, "retrieval")]
    st["turn"].stage = "重排"
    rr = await rerank_select(cfg, d.llm, st["focus"], st["case_text"], sd.hits, st["turn"].note)
    hits = rr["hits"]
    refs = {h["id"]: h["ref"] for h in hits}
    for c in sd.cands:
        c["ref"] = refs.get(c["id"], "")
    m.update({"retrieved": len(hits), "actionable": rr["actionable"], "direct": rr["direct"]})
    if sd.hits:
        tr.append(T(st, "重新排序與配額", rr["trace"], "rerank"))
    # 生成迴圈的初始值：最多兩次（第二次是重寫），知識不足改由 AI 補足時會再加一次
    return {"hits": hits, "trace": tr, "metrics": m, "fallback": False, "attempt": 0, "max_attempts": 2, "fb_note": None, "rewritten": False,
            "chk": {"pass": False, "items": [], "reasons": []}}


def _kb_only(cfg: BotConfig) -> bool:
    return cfg.flag("knowledge_only")


def n_judge_knowledge(st: ChatState, config) -> dict:
    """完整回答時沒有段落、或沒有能直接回答焦點的段落：knowledge_only 開→保守回答；關→由 AI 補足（照樣過檢查並標示）"""
    hits, m = st["hits"], st["metrics"]
    if st["mode"] == "full" and (not hits or not m.get("direct")):
        k_only = _kb_only(st["cfg"])
        why = "沒有能直接回答焦點的段落（只有背景或原則）" if hits else "沒有可用知識"
        q = dict(st["quality"], gap={"kind": "只有背景或原則" if hits else "沒有可用知識", "outcome": "保守回答" if k_only else "AI 補足",
                                       "focus": st["focus"], "topics": st["tri"].get("topics") or [], "top": m["top"]})
        if k_only:
            return {"quality": q, "trace": [T(st, "P10 降級", why + " → 保守回答，記為知識缺口")],
                    "out": {"type": "conservative", "message": P.conservative_msg(st["cfg"], st["data"]["refs"]), "sources": []}}
        return {"quality": q, "fallback": True, "trace": [T(st, "P04 狀態機", why + " → 由 AI 協助補足（knowledge_only 已關閉），記為知識缺口")]}
    return {}


def route_judge(st: ChatState) -> str:
    return "finish" if st.get("out") else "assemble"


def n_assemble(st: ChatState, config) -> dict:
    cfg, ref, data = st["cfg"], st["ref"], st["data"]
    sys_p = cfg.str("system_prompt")
    ref_who = (data["refs"].get(ref.get("category")) or {}).get("who", "") if ref else ""
    words = [f["rule"] for f in data["forbidden"] if not f["regex"]]
    ctx_hist = [] if st["is_new"] else st["history"]
    common = dict(sys=sys_p, skills=st["skill_text"], hits=st["hits"], history=ctx_hist, ref=ref, ref_who=ref_who, feedback=st["fb_note"],
                  focus=st["focus"], fallback=st["fallback"], kg=st["kg"], forbidden_words=words)
    if st["mode"] == "full":
        prompt = P.assemble_full(cfg, tri=st["tri"], case_text=st["case_text"], **common)
    else:
        prompt = P.assemble_continue(cfg, prev_user=st["prev"], latest=st["deid"]["text"], intent=cfg.intent_name(st["intent_key"]), **common)
    tr = []
    if st["attempt"] == 0:
        tr.append(T(st, "P07 Prompt 組裝", {"summary": ("完整回答" if st["mode"] == "full" else "延續回覆") + f"｜固定 system prompt {D.u16len(sys_p)} 字｜Skill {D.u16len(st['skill_text'])} 字｜"
                                                    f"知識 {len(st['hits'])} 段｜歷史 {len(ctx_hist)} 則｜共 {D.u16len(prompt)} 字", "prompt": prompt}, "prompt"))
    return {"prompt_text": prompt, "fb_note": None, "trace": tr}


def _gap_found(st: ChatState, kind: str, detail: str) -> dict:
    """生成或檢查時才發現知識不足：knowledge_only 開→保守回答；關→改由 AI 補足再寫一次"""
    k_only = _kb_only(st["cfg"])
    q = dict(st["quality"], gap={"kind": kind, "outcome": "保守回答" if k_only else "AI 補足", "focus": st["focus"], "topics": st["tri"].get("topics") or [],
                                   "top": st["metrics"]["top"], "detail": D_cut(js_str(detail), 160)})
    if k_only:
        return {"quality": q, "trace": [T(st, "P10 降級", f"{kind}（{detail}）→ 保守回答，記為知識缺口")],
                "out": {"type": "conservative", "message": P.conservative_msg(st["cfg"], st["data"]["refs"]), "sources": []}}
    return {"quality": q, "trace": [T(st, "P04 狀態機", f"{kind}（{detail}）→ 改由 AI 協助補足並標示，記為知識缺口")],
            "fallback": True, "rewritten": True, "max_attempts": max(st["max_attempts"], st["attempt"] + 2), "fb_note": None, "_next": "next"}


async def n_generate(st: ChatState, config) -> dict:
    cfg, tn, mode = st["cfg"], st["turn"], st["mode"]
    label = "A1 重寫" if st["attempt"] else "A1 生成"
    tn.stage = "A1"
    try:
        raw, _ = await deps_of(config).llm.generate_json(cfg.str("model_answer"), st["prompt_text"], temperature=cfg.num("temperature") or 0.3, notes=tn.note)
    except Exception as e:
        if "JSON" not in str(e):
            raise
        tn.rewrite_why.append("格式")
        chk = {"pass": False, "items": [{"name": "格式", "pass": False, "note": "回覆不是有效的 JSON"}], "reasons": ["上一版回覆不是有效的 JSON，請只輸出 JSON"]}
        return {"chk": chk, "fb_note": chk["reasons"], "rewritten": True, "trace": [T(st, label, "回覆不是有效的 JSON")], "_next": "next"}
    validate_answer(cfg, raw, mode, tn.note)
    gap = js_str(raw.get("knowledge_gap") if isinstance(raw, dict) else None).strip()
    ans = clean_answer(cfg, raw, mode)
    has_gap = bool(gap) and gap != "null"
    tr = [T(st, label, f"建議 {len(cfg.answer_items(ans))} 個" + (f"｜模型回報知識不足：{gap}" if has_gap else ""))]
    if has_gap and not st["fallback"]:
        g = _gap_found({**st, "trace": []}, "回答時發現知識不足", gap)
        return {**g, "ans": ans, "chk": {"pass": False, "items": [], "reasons": []}, "trace": tr + g["trace"]}
    return {"ans": ans, "trace": tr, "_next": "check"}


async def n_check(st: ChatState, config) -> dict:
    cfg, tn = st["cfg"], st["turn"]
    tn.stage = "L5"
    chk = await run_checks(cfg, deps_of(config).llm, case_text=st["case_text"], ans=st["ans"], hits=st["hits"], forbidden=st["data"]["forbidden"],
                           rng=cfg.items_range(st["mode"]), focus=st["focus"], fallback=st["fallback"], notes=tn.note)
    tr = [T(st, "檢查", chk["items"], "checks")]
    q = st["quality"]
    if chk["l5"] or chk["relFail"]:
        q = dict(q, l5=q["l5"] + [{"attempt": st["attempt"], "issues": chk["l5"], "relevance": chk["relFail"]}])
    if not chk["pass"] and chk["relFail"] and chk["relFail"]["cause"] == "知識不足" and not st["fallback"]:
        g = _gap_found({**st, "quality": q}, "檢查發現知識不足", chk["relFail"]["note"] or "知識沒有針對這個問題")
        return {**g, "chk": chk, "trace": tr + g["trace"]}
    if chk["pass"]:
        return {"chk": chk, "quality": q, "trace": tr, "_next": "pass"}
    tn.rewrite_why.extend(i["name"] for i in chk["items"] if not i["pass"])
    return {"chk": chk, "quality": q, "fb_note": chk["reasons"], "rewritten": True, "trace": tr, "_next": "next"}


def route_after(st: ChatState) -> str:
    if st.get("out"):
        return "finish"
    nxt = st.get("_next")
    if nxt == "check":
        return "check"
    if nxt == "pass":
        return "finish_answer"
    return "next_attempt"


def n_next_attempt(st: ChatState, config) -> dict:
    a = st["attempt"] + 1
    if a < st["max_attempts"]:
        return {"attempt": a, "_next": "assemble"}
    chk = st["chk"]
    q = dict(st["quality"], l5Final="保守回答")
    return {"attempt": a, "quality": q, "trace": [T(st, "P10 降級", "重寫後仍未通過 → 保守回答：" + "；".join(chk["reasons"]))],
            "out": {"type": "conservative", "message": P.conservative_msg(st["cfg"], st["data"]["refs"], "check"), "sources": [], "checks": chk["items"]}}


def route_next(st: ChatState) -> str:
    return "finish" if st.get("out") else "assemble"


def n_finish_answer(st: ChatState, config) -> dict:
    cfg, ans, hits, ref, chk = st["cfg"], dict(st["ans"]), st["hits"], st["ref"], st["chk"]
    used: list[str] = []
    for s in cfg.answer_items(ans):
        for r in (s.get("refs") or []) if isinstance(s, dict) else []:
            if r not in used:
                used.append(r)
    m = st["metrics"]
    refs_in = {h["ref"] for h in hits}
    m["cited"] = sum(1 for r in used if r in refs_in)
    m["usedRefs"] = used
    sources = [{"ref": h["ref"], "title": h["title"], "source": h["source"], "page": h["page"], "evidence": h["evidence"], "docId": h["doc_id"],
                "snippet": D_cut(h["text"], 280)} for h in hits if h["ref"] in used]
    refs = st["data"]["refs"]
    who = (refs.get(ref.get("category")) or {}).get("who", "") if ref else ""
    referral = {"category": ref.get("category"), "level": ref.get("level"), "who": who, "contact": (refs.get(ref.get("category")) or {}).get("contact", "")} if ref else None
    tr = [T(st, "P10 組合", "檢查全部通過" + ("（重寫一次後）" if st["rewritten"] else ""))]
    if st["fallback"]:
        ans["notice"] = cfg.text("msg_ai_fallback")
    common = {"answer": ans, "sources": sources, "referral": referral, "checks": chk["items"], "rewritten": st["rewritten"], "answered": st["answered"] + 1}
    if st["mode"] == "continue":
        opts = ans.get("options") or P.continue_options(cfg)
        ans["options"] = [x for x in (js_str(o) for o in opts) if x][:4]
        return {"metrics": m, "trace": tr, "out": {"type": "continue", **common}}
    return {"metrics": m, "trace": tr, "out": {"type": "ai_fallback" if st["fallback"] else "answer", **common}}


def n_finish(st: ChatState, config) -> dict:
    d, cfg, tn = deps_of(config), st["cfg"], st["turn"]
    out = st["out"]
    res = {"nameMap": st["deid"]["map"], "deidText": st["deid"]["text"], "triage": st["tri"], "metrics": st["metrics"],
           "intent": cfg.intent_name(st["intent_key"]), "newCase": st["is_new"], "answered": st["answered"], **out}
    res["ms"] = tn.ms()
    res["quality"] = st["quality"]
    res["schema"] = [f.to_public() for f in cfg.schema()]
    res["stability"] = {"models": {"triage": cfg.str("model_triage") if cfg.flag("enable_triage") else "", "answer": cfg.str("model_answer"),
                                   "check": cfg.str("model_check"), "rerank": cfg.str("model_rerank")}, "ev": tn.ev, "rewriteWhy": tn.rewrite_why}
    trace = st.get("trace") or []
    if d.log:
        log_turn(d, st["req"], res, trace)
    if d.with_trace:
        res["trace"] = trace
    if d.with_hits:
        res["hits"] = [{"id": h["id"], "docId": h["doc_id"], "ref": h["ref"], "title": h["title"], "text": h["text"]} for h in st.get("hits") or []]
    return {"result": res}


def log_turn(d: Deps, req: dict, res: dict, trace: list[dict]) -> None:
    """對話紀錄：只存去識別化後的文字；trace 不存完整 prompt"""
    m = res.get("metrics") or {}
    tr = [{k: ({kk: vv for kk, vv in v.items() if kk != "prompt"} if k == "detail" and isinstance(v, dict) else v) for k, v in t.items()} for t in trace]
    history = req.get("history") or []
    turn = _js_number(req.get("turn")) or len(history) // 2 + 1
    ans = res.get("answer") or ({"questions": res["questions"], "empathy": res.get("empathy")} if "questions" in res else {"message": res["message"]} if "message" in res else None)
    d.session.add(Conversation(
        bot_id=d.bot_id, flow_id=d.flow_id, session=js_str(req.get("session")), turn=turn, invite_code_id=req.get("invite_code_id"),
        unit=js_str(req.get("unit")), user_text=res.get("deidText") or "",
        rtype=res["type"], intent=res.get("intent") or "", triage=res.get("triage"), referral=res.get("referral"), answer=ans,
        sources=res.get("sources") or [], checks=res.get("checks") or [], rewritten=bool(res.get("rewritten")), skills=m.get("skills") or [],
        hits=m.get("retrieved") or 0, top_score=m.get("top") or 0, cited=m.get("cited") or 0, ms=res.get("ms") or 0,
        trace=tr, stability=res.get("stability"), quality=res.get("quality")))
    d.session.flush()


# ───────── 組圖 ─────────

def build_graph():
    g = StateGraph(ChatState)
    for name, fn in [("load_config", n_load_config), ("deid", n_deid), ("expand_query", n_expand_query), ("triage_and_embed", n_triage_and_embed),
                     ("apply_deid_result", n_apply_deid_result), ("finish_referral", n_finish_referral), ("finish_out_of_scope", n_finish_out_of_scope),
                     ("followup", n_followup), ("load_skills", n_load_skills), ("kg_match", n_kg_match), ("retrieve", n_retrieve),
                     ("judge_knowledge", n_judge_knowledge), ("assemble", n_assemble), ("generate", n_generate), ("check", n_check),
                     ("next_attempt", n_next_attempt), ("finish_answer", n_finish_answer), ("finish", n_finish)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_config")
    for a, b in [("load_config", "deid"), ("deid", "expand_query"), ("expand_query", "triage_and_embed"), ("triage_and_embed", "apply_deid_result"),
                 ("finish_referral", "finish"), ("finish_out_of_scope", "finish"), ("followup", "finish"), ("load_skills", "kg_match"),
                 ("kg_match", "retrieve"), ("retrieve", "judge_knowledge"), ("assemble", "generate"), ("finish_answer", "finish"), ("finish", END)]:
        g.add_edge(a, b)
    g.add_conditional_edges("apply_deid_result", route_state, ["finish_referral", "finish_out_of_scope", "followup", "load_skills"])
    g.add_conditional_edges("judge_knowledge", route_judge, ["finish", "assemble"])
    g.add_conditional_edges("generate", route_after, ["check", "next_attempt", "finish"])
    g.add_conditional_edges("check", route_after, ["finish_answer", "next_attempt", "finish"])
    g.add_conditional_edges("next_attempt", route_next, ["assemble", "finish"])
    return g.compile()


GRAPH = build_graph()


async def run_chat(deps: Deps, req: dict) -> dict:
    """一個回合；回傳與 Apps Script runChat_ 相同結構的 payload"""
    st = await GRAPH.ainvoke({"req": req, "turn": Turn(time.monotonic()), "trace": []}, config={"configurable": {"deps": deps}, "recursion_limit": 60})
    return st["result"]
