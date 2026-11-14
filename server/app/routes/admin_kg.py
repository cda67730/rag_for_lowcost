"""後台 API：知識圖譜（docs/admin-api.md §9）與文件圖譜標註（§8 kg-tag）。管理者：Workspace 層 admin；審核候選另開放 reviewer。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError, not_found
from app.perms import require
from app.routes.admin import DB, Me, audit, body, get_doc, get_kb, get_session_factory, uid
from app.routes.chat import get_llm
from db.models import BotKnowledge, Job, KgCandidate, User
from services import kg_admin
from services.llm import LLMClient
from settings.loader import build_config, load_config

router = APIRouter(prefix="/api/admin")


def kb_admin(s: Session, u: User, kb: str, also=frozenset()):
    k = get_kb(s, kb)
    require(s, u, k.workspace_id, {"admin", *also}, ws_level=True)
    return k


def _ctx(s: Session, kb_id) -> tuple[str, str, str, str]:
    """模型與用詞：取掛載這個知識庫的第一個 Bot（與段落分類相同）"""
    bot_id = s.scalar(select(BotKnowledge.bot_id).where(BotKnowledge.kb_id == kb_id).order_by(BotKnowledge.sort).limit(1))
    cfg = load_config(s, bot_id) if bot_id else build_config({})
    r = cfg.roles()
    return cfg.str("model_triage"), cfg.str("model_answer"), r["domain"], r["user"]


@router.get("/kbs/{kb}/kg")
def kg_overview(kb: str, u: User = Me, s: Session = DB):
    k = kb_admin(s, u, kb, {"reviewer"})
    return kg_admin.overview(s, k.id)


@router.post("/kbs/{kb}/kg/import")
async def kg_import(kb: str, request: Request, u: User = Me, s: Session = DB):
    k = kb_admin(s, u, kb)
    try:
        out = kg_admin.import_graph(s, k.id, (await request.body()).decode("utf-8", errors="replace"))
    except ValueError as e:
        raise ApiError(422, "bad_graph", str(e))
    audit(s, k.workspace_id, u, "kg.import", k.slug, out, kb_id=k.id)
    s.commit()
    return out


@router.get("/kbs/{kb}/kg/export")
def kg_export(kb: str, u: User = Me, s: Session = DB):
    k = kb_admin(s, u, kb, {"reviewer"})
    return JSONResponse(kg_admin.export_graph(s, k.id), headers={"Content-Disposition": f'attachment; filename="{k.slug}-knowledge-graph.json"'})


@router.get("/kbs/{kb}/kg/ontology.ttl")
def kg_ttl(kb: str, u: User = Me, s: Session = DB):
    k = kb_admin(s, u, kb, {"reviewer"})
    ttl = kg_admin.ontology_ttl(kg_admin.load_defs(s, k.id), datetime.now(timezone.utc).strftime("%Y.%m.%d-%H%M"))
    return Response(ttl, media_type="text/turtle; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{k.slug}-ontology.ttl"'})


@router.post("/kg/candidates/{cid}")
async def kg_candidate(cid: str, request: Request, u: User = Me, s: Session = DB):
    c = s.get(KgCandidate, uid(cid))
    if c is None:
        raise not_found()
    k = kb_admin(s, u, str(c.kb_id), {"reviewer"})
    d = await body(request)
    try:
        out = kg_admin.candidate_action(s, c.id, str(d.get("action") or ""), d.get("opts") or {}, u.id)
    except ValueError as e:
        raise ApiError(422, "invalid", str(e))
    audit(s, k.workspace_id, u, "kg.candidate." + str(d.get("action")), c.name, out, kb_id=k.id)
    s.commit()
    return out


@router.post("/documents/{doc}/kg-tag")
async def kg_tag(doc: str, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm)):
    d, k = get_doc(s, u, doc)
    require(s, u, k.workspace_id, {"admin"}, ws_level=True)
    model, _, domain, _ = _ctx(s, k.id)
    out = await kg_admin.map_document(s, llm, model, domain, d.id)
    audit(s, k.workspace_id, u, "kg.tag", d.title, out, kb_id=k.id)
    s.commit()
    return out


async def _gen_job(factory, llm: LLMClient, job_id: uuid.UUID, by: str) -> None:
    with factory() as s:
        job = s.get(Job, job_id)
        if job.status != "queued":
            return
        job.status = "running"
        s.commit()
        _, model, domain, role_user = _ctx(s, job.kb_id)

        def progress(p, m):
            job.progress, job.message = p, m
            s.commit()

        try:
            r = await kg_admin.generate(s, llm, model, domain, role_user, job.kb_id, by, progress)
            job.status, job.result, job.message = "done", r, f"完成：{r['stats']['nodes']} 個節點、{r['stats']['edges']} 條關係"
        except Exception as e:
            s.rollback()
            job = s.get(Job, job_id)
            job.status, job.message = "failed", str(e)[:300] if isinstance(e, ValueError) else "產生失敗：" + type(e).__name__
            job.progress, job.finished_at = 1.0, datetime.now(timezone.utc)
        s.commit()


@router.post("/kbs/{kb}/kg/generate", status_code=202)
def kg_generate(kb: str, tasks: BackgroundTasks, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm), factory=Depends(get_session_factory)):
    """依已核可文件產生圖譜草稿（背景工作）；完成後在工作結果裡拿 json 與 ttl，確認後再用匯入取代"""
    k = kb_admin(s, u, kb)
    job = Job(workspace_id=k.workspace_id, kb_id=k.id, kind="kg_generate", target=str(k.id), created_by=u.id)
    s.add(job)
    audit(s, k.workspace_id, u, "kg.generate", k.slug, kb_id=k.id)
    s.commit()
    tasks.add_task(_gen_job, factory, llm, job.id, u.login)
    return {"job_id": str(job.id)}
