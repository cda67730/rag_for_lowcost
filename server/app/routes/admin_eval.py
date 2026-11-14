"""後台 API：評測（docs/admin-api.md §11）。出題與執行是背景工作；評測不寫對話紀錄。"""
from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ApiError, not_found
from app.routes.admin import DB, Me, audit, body, get_session_factory, uid
from app.routes.admin_bot import admin_bot
from app.routes.chat import get_llm
from db.models import Chunk, Conversation, Document, EvalQuestion, EvalResult, EvalRun, Flow, Job, User
from graph.chat_graph import Deps, run_chat
from graph.check import load_forbidden
from services import evaluation as E
from services.llm import LLMClient
from services.retrieval import mounts
from settings.loader import SETTING_DEFAULTS, TEXT_DEFAULTS, load_config

router = APIRouter(prefix="/api/admin")
SOURCES = ("ai", "real", "expert")
SAFE_SCORE = {"通過": 1.0, "部分通過": 0.5, "注意": 0.5, "未通過": 0.0}   # safe_note 存「判定｜說明」，safe 存分數方便排序篩選


def q_out(q: EvalQuestion) -> dict:
    return {"id": str(q.id), "kind": q.kind, "source": q.source, "type": q.qtype, "question": q.question, "expect_chunks": [str(x) for x in q.expect_chunks],
            "expect_docs": [str(x) for x in q.expect_docs], "key_points": q.key_points, "expect_referral": q.expect_referral, "expect_rtype": q.expect_rtype,
            "status": q.status, "expert_ok": q.expert_ok, "bank_version": q.bank_version}


def approved_chunks(s: Session, bot_id) -> list[dict]:
    kbs = [m.kb_id for m in mounts(s, bot_id)]
    if not kbs:
        return []
    rows = s.execute(select(Chunk, Document).join(Document, Document.id == Chunk.doc_id)
                     .where(Chunk.kb_id.in_(kbs), Document.status == "approved").order_by(Document.uploaded_at, Document.id, Chunk.seq)).all()
    return [{"id": str(c.id), "docId": str(d.id), "title": d.title, "text": c.text} for c, d in rows]


def add_questions(s: Session, bot_id, lst: list[dict], version: int) -> int:
    for x in lst:
        s.add(EvalQuestion(bot_id=bot_id, kind="answer", status="active", bank_version=version, **x))
    return len(lst)


def current_version(s: Session, bot_id) -> int:
    return s.scalar(select(func.max(EvalQuestion.bank_version)).where(EvalQuestion.bot_id == bot_id)) or 1


# ───────── 題庫 ─────────

@router.get("/bots/{bot}/eval/questions")
def list_questions(bot: str, kind: str | None = None, source: str | None = None, status: str | None = None, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot, {"reviewer", "analyst"})
    q = select(EvalQuestion).where(EvalQuestion.bot_id == b.id)
    for col, v in ((EvalQuestion.kind, kind), (EvalQuestion.source, source), (EvalQuestion.status, status)):
        if v:
            q = q.where(col == v)
    rows = s.scalars(q.order_by(EvalQuestion.created_at.desc())).all()
    active = [x for x in s.scalars(select(EvalQuestion).where(EvalQuestion.bot_id == b.id, EvalQuestion.status == "active"))]
    cnt = lambda k: {v: sum(1 for x in active if getattr(x, k) == v) for v in sorted({getattr(x, k) for x in active})}
    cfg = load_config(s, b.id)
    ch = approved_chunks(s, b.id)
    return {"items": [q_out(x) for x in rows[:500]], "total": len(rows), "active": len(active), "bySource": cnt("source"), "byType": cnt("qtype"),
            "docs": len({c["docId"] for c in ch}), "chunks": len(ch), "bank_version": current_version(s, b.id),
            "models": {"gen": E.model_of(cfg, "model_eval_gen"), "judge": E.model_of(cfg, "model_eval_judge"), "answer": cfg.str("model_answer")}}


@router.post("/bots/{bot}/eval/questions", status_code=201)
async def add_question(bot: str, request: Request, u: User = Me, s: Session = DB):
    """{text}：專家題，一行一題；或 {question, kind, expect_chunks, expect_docs, key_points, expect_referral, expect_rtype}"""
    b = admin_bot(s, u, bot, {"reviewer"})
    d = await body(request)
    v = current_version(s, b.id)
    if "text" in d:
        n = add_questions(s, b.id, E.expert_questions(str(d.get("text") or "")), v)
        if not n:
            raise ApiError(422, "invalid", "沒有讀到題目（一行一題，至少 4 個字）", {"text": "沒有題目"})
        s.commit()
        return {"added": n}
    q = str(d.get("question") or "").strip()
    kind = d.get("kind") or "answer"
    errs = {}
    if len(q) < 4:
        errs["question"] = "至少 4 個字"
    if kind not in ("answer", "retrieval"):
        errs["kind"] = "answer 或 retrieval"
    try:
        chunks = [uuid.UUID(str(x)) for x in d.get("expect_chunks") or []]
        docs = [uuid.UUID(str(x)) for x in d.get("expect_docs") or []]
    except ValueError:
        errs["expect_chunks"] = "要是段落／文件 ID"
        chunks = docs = []
    if kind == "retrieval" and not chunks and not docs:
        errs["expect_chunks"] = "檢索題要指定應命中的段落或文件"
    rt = d.get("expect_rtype") or "answer"
    if rt not in set(E.EVAL_EXPECT.values()):
        errs["expect_rtype"] = "、".join(sorted(set(E.EVAL_EXPECT.values())))
    if errs:
        raise ApiError(422, "invalid", "欄位有誤", errs)
    x = EvalQuestion(bot_id=b.id, kind=kind, source="expert", qtype=str(d.get("type") or "一般"), question=q[:500], expect_chunks=chunks, expect_docs=docs,
                     key_points=str(d.get("key_points") or ""), expect_referral=str(d.get("expect_referral") or ""), expect_rtype=rt, status="active",
                     expert_ok=True, bank_version=v)
    s.add(x)
    s.commit()
    return q_out(x)


def get_q(s: Session, u: User, qid: str, also=frozenset({"reviewer"})) -> EvalQuestion:
    q = s.get(EvalQuestion, uid(qid))
    if q is None:
        raise not_found()
    admin_bot(s, u, str(q.bot_id), also)
    return q


@router.patch("/eval/questions/{qid}")
async def patch_question(qid: str, request: Request, u: User = Me, s: Session = DB):
    q = get_q(s, u, qid)
    d = await body(request)
    if "status" in d:
        if d["status"] not in ("draft", "active", "retired"):
            raise ApiError(422, "invalid", "欄位有誤", {"status": "draft、active 或 retired"})
        q.status = d["status"]
    if "expert_ok" in d:
        q.expert_ok = bool(d["expert_ok"])
    for f in ("question", "key_points", "expect_referral"):
        if f in d:
            setattr(q, f, str(d[f] or "")[:500])
    s.commit()
    return q_out(q)


@router.delete("/eval/questions/{qid}")
def delete_question(qid: str, u: User = Me, s: Session = DB):
    q = get_q(s, u, qid)
    s.delete(q)
    s.commit()
    return {"ok": True}


@router.post("/bots/{bot}/eval/import-logs")
async def import_logs(bot: str, request: Request, u: User = Me, s: Session = DB):
    """真實提問：每段對話的第一則（不含後台測試），沒有標準段落，只評對題、依據、安全、穩定"""
    b = admin_bot(s, u, bot)
    d = await body(request)
    n = max(1, min(200, int(d.get("n") or 30)))
    have = {q.strip() for q in s.scalars(select(EvalQuestion.question).where(EvalQuestion.bot_id == b.id))}
    seen, lst = set(), []
    for c in s.scalars(select(Conversation).where(Conversation.bot_id == b.id, Conversation.turn <= 1, ~Conversation.session.like("admin-test%"))
                       .order_by(Conversation.created_at.desc())):
        if len(lst) >= n:
            break
        q = c.user_text.strip()
        if c.session in seen or len(q) < 8 or q in have:
            continue
        seen.add(c.session)
        have.add(q)
        lst.append({"source": "real", "qtype": "一般", "question": q[:500], "expect_rtype": "answer"})
    added = add_questions(s, b.id, lst, current_version(s, b.id))
    s.commit()
    return {"added": added}


@router.post("/bots/{bot}/eval/freeze")
def freeze(bot: str, u: User = Me, s: Session = DB):
    """把目前 active 的題目標成新版本；之後的評測可以指定版本，前後比較才公平"""
    b = admin_bot(s, u, bot)
    v = current_version(s, b.id) + 1
    n = s.query(EvalQuestion).filter(EvalQuestion.bot_id == b.id, EvalQuestion.status == "active").update({"bank_version": v})
    audit(s, b.workspace_id, u, "eval.freeze", b.slug, {"bank_version": v, "questions": n}, bot_id=b.id)
    s.commit()
    return {"bank_version": v, "questions": n}


async def _gen_job(factory, llm: LLMClient, job_id, max_chunks: int, special_n: int) -> None:
    with factory() as s:
        job = s.get(Job, job_id)
        if job.status != "queued":
            return
        job.status = "running"
        s.commit()
        try:
            cfg = load_config(s, job.bot_id)
            model = E.model_of(cfg, "model_eval_gen")
            pick = E.sample_even(approved_chunks(s, job.bot_id), max(6, max_chunks)) if max_chunks else []
            v = current_version(s, job.bot_id)
            added, failed = 0, 0
            nb = (len(pick) + E.GEN_BATCH - 1) // E.GEN_BATCH
            for i in range(nb):
                group = pick[i * E.GEN_BATCH:(i + 1) * E.GEN_BATCH]
                try:
                    r, _ = await llm.generate_json(model, E.gen_prompt(group))
                    added += add_questions(s, job.bot_id, [dict(x, expect_chunks=[uuid.UUID(c) for c in x["expect_chunks"]],
                                                                expect_docs=[uuid.UUID(c) for c in x["expect_docs"]]) for x in E.gen_parse(group, r)], v)
                except Exception:
                    failed += 1
                job.progress, job.message = 0.9 * (i + 1) / (nb + 1), f"出題 {i + 1}/{nb} 批"
                s.commit()
            if special_n:
                refs = [k for k in load_referral_names(s, job.bot_id) if k and k != "系統能力不足"]
                titles = list(dict.fromkeys(c["title"] for c in approved_chunks(s, job.bot_id)))[:40]
                n = max(2, min(20, special_n))
                try:
                    r, _ = await llm.generate_json(model, E.special_prompt(n, refs, titles, cfg.str("system_prompt")))
                    added += add_questions(s, job.bot_id, E.special_parse(refs, r), v)
                except Exception:
                    failed += 1
            job.status, job.result = "done", {"added": added, "failed_batches": failed}
            job.message = f"新增 {added} 題" + (f"，{failed} 批失敗" if failed else "")
        except Exception as e:
            s.rollback()
            job = s.get(Job, job_id)
            job.status, job.message = "failed", "出題失敗：" + type(e).__name__
        job.progress, job.finished_at = 1.0, datetime.now(timezone.utc)
        s.commit()


def load_referral_names(s: Session, bot_id) -> list[str]:
    from db.models import Referral
    return list(s.scalars(select(Referral.category).where(Referral.bot_id == bot_id).order_by(Referral.sort, Referral.category)))


@router.post("/bots/{bot}/eval/generate", status_code=202)
async def generate(bot: str, request: Request, tasks: BackgroundTasks, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm),
                   factory=Depends(get_session_factory)):
    b = admin_bot(s, u, bot)
    d = await body(request)
    mc, sn = int(d.get("max_chunks") if d.get("max_chunks") is not None else 60), int(d.get("special") if d.get("special") is not None else 6)
    job = Job(workspace_id=b.workspace_id, bot_id=b.id, kind="eval_generate", target=str(b.id), created_by=u.id)
    s.add(job)
    s.commit()
    tasks.add_task(_gen_job, factory, llm, job.id, mc, sn)
    return {"job_id": str(job.id)}


# ───────── 執行 ─────────

def _hit_label(r: EvalResult) -> str:
    return "" if r.found is None else "段落命中" if r.found else "文件命中" if r.rank else "沒找到"


async def _run_job(factory, llm: LLMClient, job_id, run_id, qids: list[uuid.UUID]) -> None:
    with factory() as s:
        job, run = s.get(Job, job_id), s.get(EvalRun, run_id)
        if job.status != "queued":
            return
        job.status = run.status = "running"
        s.commit()
        try:
            cfg = load_config(s, run.bot_id, run.flow_id, run.settings_override or None)
            forbidden = load_forbidden(s, run.bot_id)
            total = len(qids) * run.repeat
            done = 0
            for qid in qids:
                q = s.get(EvalQuestion, qid)
                for k in range(1, run.repeat + 1):
                    s.refresh(job, ["status"])
                    if job.status == "cancelled":
                        run.status = "cancelled"
                        s.commit()
                        return
                    t0 = time.monotonic()
                    err = ""
                    try:
                        res = await run_chat(Deps(s, llm, run.bot_id, run.flow_id, override=run.settings_override or None, log=False, with_trace=True, with_hits=True),
                                             {"message": q.question, "history": [], "nameMap": {}, "followups": 0, "answered": 0, "session": f"eval-{run.id}", "unit": "自動評測"})
                    except Exception as e:
                        err, res = str(e)[:200] or type(e).__name__, {"type": "error"}
                    o = await E.score(cfg, llm, {"question": q.question, "expect_chunks": q.expect_chunks, "expect_docs": q.expect_docs, "key_points": q.key_points,
                                                 "expect_referral": q.expect_referral, "expect_rtype": q.expect_rtype}, res, err, forbidden, rnd=random.random)
                    s.add(EvalResult(run_id=run.id, question_id=q.id, attempt=k, rtype=o["rtype"], found=o["found"], rank=o["rank"], on_topic=o["on_topic"],
                                     coverage=o["coverage"], grounded=o["grounded"], safe=SAFE_SCORE.get(o["safe"]), safe_note=f"{o['safe']}｜{o['safe_note']}" if o["safe"] else "",
                                     format_issues=str(o["format_issues"]), answer_model=cfg.str("model_answer"), judge_model=E.model_of(cfg, "model_eval_judge"),
                                     judge_note=o["judge_note"], answer_summary=o["answer_summary"], ms=E.elapsed_ms(t0), expert_check=o["expert_check"]))
                    done += 1
                    job.progress, job.message = done / max(total, 1), f"{done}/{total}"
                    s.commit()
            run.summary = E.summarize(result_rows(s, run.id))
            run.status = job.status = "done"
        except Exception as e:
            s.rollback()
            job, run = s.get(Job, job_id), s.get(EvalRun, run_id)
            run.status = job.status = "failed"
            job.message = "評測失敗：" + type(e).__name__
        run.finished_at = job.finished_at = datetime.now(timezone.utc)
        job.progress = 1.0
        s.commit()


def result_rows(s: Session, run_id) -> list[dict]:
    rows = s.execute(select(EvalResult, EvalQuestion).join(EvalQuestion, EvalQuestion.id == EvalResult.question_id).where(EvalResult.run_id == run_id)
                     .order_by(EvalQuestion.created_at, EvalQuestion.question, EvalResult.attempt)).all()
    out = []
    for r, q in rows:
        safe, _, note = (r.safe_note or "").partition("｜")
        out.append({"question_id": str(q.id), "question": q.question, "qtype": q.qtype, "source": q.source, "attempt": r.attempt, "rtype": r.rtype,
                    "hit": _hit_label(r), "rank": r.rank, "on_topic": r.on_topic, "coverage": r.coverage, "grounded": r.grounded, "safe": safe, "safe_note": note,
                    "format_issues": int(r.format_issues or 0), "judge_note": r.judge_note, "answer_summary": r.answer_summary, "ms": r.ms,
                    "expert_check": r.expert_check, "expert_note": r.expert_note})
    return out


@router.post("/bots/{bot}/eval/runs", status_code=202)
async def start_run(bot: str, request: Request, tasks: BackgroundTasks, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm),
                    factory=Depends(get_session_factory)):
    b = admin_bot(s, u, bot)
    d = await body(request)
    flow_id = None
    if d.get("flow_id"):
        f = s.get(Flow, uid(d["flow_id"]))
        if f is None or f.bot_id != b.id:
            raise not_found()
        flow_id = f.id
    ov = d.get("settings_override") or {}
    if not isinstance(ov, dict) or any(k not in SETTING_DEFAULTS and k not in TEXT_DEFAULTS for k in ov):
        raise ApiError(422, "unknown_key", "settings_override 有不認得的設定")
    q = select(EvalQuestion).where(EvalQuestion.bot_id == b.id, EvalQuestion.status == "active", EvalQuestion.kind == (d.get("kind") or "answer"))
    if d.get("source"):
        q = q.where(EvalQuestion.source == d["source"])
    if d.get("bank_version"):
        q = q.where(EvalQuestion.bank_version == int(d["bank_version"]))
    qs = s.scalars(q.order_by(EvalQuestion.created_at)).all()
    if d.get("max"):
        qs = E.sample_even(qs, int(d["max"]))
    if not qs:
        raise ApiError(422, "no_questions", "沒有符合條件的題目")
    repeat = max(1, min(3, int(d.get("repeat") or 1)))
    run = EvalRun(bot_id=b.id, flow_id=flow_id, kind=d.get("kind") or "answer", bank_version=int(d.get("bank_version") or current_version(s, b.id)),
                  settings_override=ov, repeat=repeat, started_by=u.id)
    s.add(run); s.flush()
    job = Job(workspace_id=b.workspace_id, bot_id=b.id, kind="eval_run", target=str(run.id), created_by=u.id)
    s.add(job)
    audit(s, b.workspace_id, u, "eval.run", b.slug, {"run": str(run.id), "questions": len(qs), "repeat": repeat}, bot_id=b.id)
    s.commit()
    tasks.add_task(_run_job, factory, llm, job.id, run.id, [x.id for x in qs])
    return {"run_id": str(run.id), "job_id": str(job.id), "questions": len(qs), "repeat": repeat}


def run_out(r: EvalRun, n: int) -> dict:
    return {"id": str(r.id), "kind": r.kind, "status": r.status, "flow_id": str(r.flow_id) if r.flow_id else None, "bank_version": r.bank_version,
            "repeat": r.repeat, "settings_override": r.settings_override, "results": n, "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None, "summary": r.summary.get("all") if r.summary else None}


@router.get("/bots/{bot}/eval/runs")
def list_runs(bot: str, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot, {"reviewer", "analyst"})
    runs = s.scalars(select(EvalRun).where(EvalRun.bot_id == b.id).order_by(EvalRun.started_at.desc())).all()
    n = dict(s.execute(select(EvalResult.run_id, func.count()).where(EvalResult.run_id.in_([r.id for r in runs])).group_by(EvalResult.run_id)).all()) if runs else {}
    return {"items": [run_out(r, n.get(r.id, 0)) for r in runs]}


def get_run(s: Session, u: User, run: str) -> EvalRun:
    r = s.get(EvalRun, uid(run))
    if r is None:
        raise not_found()
    admin_bot(s, u, str(r.bot_id), {"reviewer", "analyst"})
    return r


@router.get("/eval/runs/{run}")
def run_detail(run: str, u: User = Me, s: Session = DB):
    r = get_run(s, u, run)
    rows = result_rows(s, r.id)
    summary = r.summary or (E.summarize(rows) if rows else {})
    return {**run_out(r, len(rows)), "summary": summary, "results": rows}


@router.get("/eval/compare")
def compare(a: str, b: str, u: User = Me, s: Session = DB):
    ra, rb = get_run(s, u, a), get_run(s, u, b)
    if ra.bot_id != rb.bot_id:
        raise ApiError(422, "invalid", "只能比較同一個機器人的評測")
    sa = ra.summary or E.summarize(result_rows(s, ra.id))
    sb = rb.summary or E.summarize(result_rows(s, rb.id))
    out = E.compare(sa.get("all") or {}, sb.get("all") or {})
    out["same_bank"] = ra.bank_version == rb.bank_version
    return {"a": run_out(ra, 0), "b": run_out(rb, 0), **out}


@router.post("/eval/results/{run}/{qid}/{attempt}/review")
async def review(run: str, qid: str, attempt: int, request: Request, u: User = Me, s: Session = DB):
    r = s.get(EvalRun, uid(run))
    if r is None:
        raise not_found()
    admin_bot(s, u, str(r.bot_id), {"reviewer"})
    res = s.get(EvalResult, (r.id, uid(qid), attempt))
    if res is None:
        raise not_found()
    d = await body(request)
    if d.get("status") not in ("同意", "不同意", "待抽查"):
        raise ApiError(422, "invalid", "狀態不正確", {"status": "同意、不同意或待抽查"})
    res.expert_check, res.expert_note = d["status"], str(d.get("comment") or "")[:1000]
    r.summary = E.summarize(result_rows(s, r.id))
    s.commit()
    return {"ok": True, "agreement": r.summary.get("agreement")}
