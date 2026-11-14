"""後台 API 第一批（docs/admin-api.md §1、3、4、7、8、12）：登入、機器人、設定、知識庫與掛載、文件、背景任務。"""
from __future__ import annotations

import base64
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.errors import ApiError, not_found
from app.perms import require, roles_of, ws_roles
from app.routes.chat import get_llm
from db import session as dbs
from db.models import (SUPPORTED_EMBED_DIMS, AuditLog, Bot, BotKnowledge, BotSetting, Chunk, Document, DocumentFile, Flow, Job, KbAllowedBot, KnowledgeBase,
                       Membership, User, Workspace)
from db.session import get_session
from services import ingest
from services.llm import LLMClient, validate_embed_config
from services.parsers import ParseError
from settings.loader import FLOW_OVERRIDABLE, GROUPS, SETTING_DEFAULTS, SETTING_NOTES, TEXT_DEFAULTS, TIERS, load_config
from settings.store import SettingsError, apply_tier, save_bot_settings

router = APIRouter(prefix="/api/admin")
Me = Depends(auth.current_user)
DB = Depends(get_session)


def get_session_factory():
    """背景任務用：每個任務自己開 session（測試時換掉）"""
    return dbs.session


# ───────── 共用 ─────────

def uid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError:
        raise not_found()


def audit(s: Session, ws_id, user: User, action: str, target: str = "", detail: dict | None = None, bot_id=None, kb_id=None) -> None:
    s.add(AuditLog(workspace_id=ws_id, user_id=user.id, action=action, target=target, detail=detail or {}, bot_id=bot_id, kb_id=kb_id))


def page(items: list, limit: int, cursor: str | None) -> tuple[list, int, int]:
    off = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
    limit = max(1, min(int(limit or 50), 200))
    return items, off, limit


def cursor_of(off: int, limit: int, got: int) -> str | None:
    return base64.urlsafe_b64encode(str(off + limit).encode()).decode() if got > limit else None


async def body(request: Request) -> dict:
    try:
        d = await request.json()
    except ValueError:
        raise ApiError(422, "bad_json", "請求內容不是有效的 JSON")
    if not isinstance(d, dict):
        raise ApiError(422, "bad_json", "請求內容要是物件")
    return d


def get_bot(s: Session, bot: str) -> Bot:
    b = s.get(Bot, uid(bot))
    if b is None:
        raise not_found()
    return b


def get_kb(s: Session, kb: str) -> KnowledgeBase:
    k = s.get(KnowledgeBase, uid(kb))
    if k is None:
        raise not_found()
    return k


def kb_reader(s: Session, user: User, k: KnowledgeBase) -> None:
    """知識庫：Workspace 層的任一角色看得到；Bot 層的人只看得到自己 Bot 掛的庫"""
    if ws_roles(s, user, k.workspace_id):
        return
    bots = s.scalars(select(BotKnowledge.bot_id).where(BotKnowledge.kb_id == k.id)).all()
    if not any(roles_of(s, user, k.workspace_id, b) for b in bots):
        raise not_found()


def bot_out(b: Bot) -> dict:
    return {"id": str(b.id), "workspace_id": str(b.workspace_id), "slug": b.slug, "name": b.name, "disabled": b.disabled}


def kb_out(s: Session, k: KnowledgeBase) -> dict:
    docs = s.scalar(select(func.count()).select_from(Document).where(Document.kb_id == k.id)) or 0
    chunks = s.scalar(select(func.count()).select_from(Chunk).where(Chunk.kb_id == k.id)) or 0
    bots = s.execute(select(Bot.id, Bot.name).join(BotKnowledge, BotKnowledge.bot_id == Bot.id).where(BotKnowledge.kb_id == k.id)).all()
    return {"id": str(k.id), "workspace_id": str(k.workspace_id), "slug": k.slug, "name": k.name, "description": k.description, "visibility": k.visibility,
            "embed_model": k.embed_model, "embed_dim": k.embed_dim, "chunk_size": k.chunk_size, "chunk_overlap": k.chunk_overlap, "auto_kg_tag": k.auto_kg_tag,
            "documents": docs, "chunks": chunks, "bots": [{"id": str(i), "name": n} for i, n in bots]}


def doc_out(d: Document) -> dict:
    return {"id": str(d.id), "kb_id": str(d.kb_id), "title": d.title, "source_note": d.source_note, "evidence": d.evidence, "filename": d.filename,
            "mime": d.mime, "size_bytes": d.size_bytes, "status": d.status, "error": d.error, "chunk_count": d.chunk_count,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None, "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None}


# ───────── 1. 認證與自己 ─────────

@router.post("/auth/login")
async def login(request: Request, s: Session = DB):
    d = await body(request)
    u = auth.login(s, str(d.get("login") or ""), str(d.get("password") or ""))
    return {"token": auth.make_token(u), "user": {"id": str(u.id), "login": u.login, "display_name": u.display_name}}


@router.post("/auth/refresh")
def refresh(u: User = Me):
    return {"token": auth.make_token(u)}


@router.post("/auth/logout")
def logout(u: User = Me, s: Session = DB):
    u.token_version += 1
    s.commit()
    return {"ok": True}


@router.get("/me")
def me(u: User = Me, s: Session = DB):
    rows = s.execute(select(Membership, Workspace).join(Workspace, Workspace.id == Membership.workspace_id).where(Membership.user_id == u.id)).all()
    wss: dict[str, dict] = {}
    for m, w in rows:
        e = wss.setdefault(str(w.id), {"id": str(w.id), "slug": w.slug, "name": w.name, "roles": [], "bots": []})
        if m.bot_id is None:
            e["roles"].append(m.role)
        else:
            e["bots"].append({"bot_id": str(m.bot_id), "role": m.role})
    return {"id": str(u.id), "login": u.login, "display_name": u.display_name, "workspaces": list(wss.values())}


@router.post("/me/password")
async def change_password(request: Request, u: User = Me, s: Session = DB):
    d = await body(request)
    try:
        auth._ph.verify(u.pw_hash, str(d.get("old") or ""))
    except Exception:
        raise ApiError(422, "bad_password", "目前的密碼不正確", {"old": "不正確"})
    new = str(d.get("new") or "")
    auth.check_password_rules(new)
    u.pw_hash, u.token_version = auth.hash_password(new), u.token_version + 1
    s.commit()
    return {"token": auth.make_token(u)}


# ───────── 3. 機器人 ─────────

@router.get("/workspaces/{ws}/bots")
def list_bots(ws: str, u: User = Me, s: Session = DB):
    w = uid(ws)
    if ws_roles(s, u, w):
        bots = s.scalars(select(Bot).where(Bot.workspace_id == w).order_by(Bot.slug)).all()
    else:
        ids = s.scalars(select(Membership.bot_id).where(Membership.user_id == u.id, Membership.workspace_id == w, Membership.bot_id.is_not(None))).all()
        if not ids:
            raise not_found()
        bots = s.scalars(select(Bot).where(Bot.id.in_(ids)).order_by(Bot.slug)).all()
    return {"items": [bot_out(b) for b in bots], "next": None}


@router.post("/workspaces/{ws}/bots", status_code=201)
async def create_bot(ws: str, request: Request, u: User = Me, s: Session = DB):
    w = uid(ws)
    require(s, u, w, {"admin"}, ws_level=True)
    d = await body(request)
    slug, name = str(d.get("slug") or "").strip().lower(), str(d.get("name") or "").strip()
    errs = {}
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", slug):
        errs["slug"] = "2–41 個小寫英數字或連字號"
    if not name:
        errs["name"] = "必填"
    if errs:
        raise ApiError(422, "invalid", "欄位有誤", errs)
    if s.scalar(select(Bot.id).where(Bot.slug == slug)):
        raise ApiError(409, "slug_taken", "這個 slug 已被使用", {"slug": "已被使用"})
    b = Bot(workspace_id=w, slug=slug, name=name)
    s.add(b); s.flush()
    s.add(Flow(bot_id=b.id, kind="chat", name="預設", is_default=True))
    audit(s, w, u, "bot.create", slug, bot_id=b.id)
    s.commit()
    return bot_out(b)


@router.get("/bots/{bot}")
def get_bot_detail(bot: str, u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin", "reviewer", "analyst"}, bot_id=b.id)
    flow = s.scalar(select(Flow).where(Flow.bot_id == b.id, Flow.is_default))
    kbs = s.execute(select(BotKnowledge, KnowledgeBase.name).join(KnowledgeBase, KnowledgeBase.id == BotKnowledge.kb_id).where(BotKnowledge.bot_id == b.id).order_by(BotKnowledge.sort)).all()
    return {**bot_out(b), "default_flow": str(flow.id) if flow else None,
            "knowledge": [{"kb_id": str(k.kb_id), "name": n, "label": k.label, "enabled": k.enabled} for k, n in kbs]}


@router.patch("/bots/{bot}")
async def patch_bot(bot: str, request: Request, u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin"}, bot_id=b.id)
    d = await body(request)
    before = bot_out(b)
    if "name" in d:
        if not str(d["name"]).strip():
            raise ApiError(422, "invalid", "欄位有誤", {"name": "必填"})
        b.name = str(d["name"]).strip()
    if "disabled" in d:
        b.disabled = bool(d["disabled"])
    audit(s, b.workspace_id, u, "bot.update", b.slug, {"before": before, "after": bot_out(b)}, bot_id=b.id)
    s.commit()
    return bot_out(b)


# ───────── 4. 設定 ─────────

@router.get("/bots/{bot}/settings")
def get_settings(bot: str, u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin"}, bot_id=b.id)
    cfg = load_config(s, b.id)
    stored = cfg.values     # 預設＋Bot 設定合併後的值
    own = {r.key for r in s.scalars(select(BotSetting).where(BotSetting.bot_id == b.id))}
    items = []
    for k, dflt in list(SETTING_DEFAULTS.items()) + [(k, v) for k, v in TEXT_DEFAULTS.items() if k not in SETTING_DEFAULTS]:
        items.append({"key": k, "value": stored.get(k, dflt), "default": dflt, "is_default": k not in own, "note": SETTING_NOTES.get(k, ""),
                      "flow_overridable": k in FLOW_OVERRIDABLE, "text": k in TEXT_DEFAULTS})
    return {"items": items, "groups": GROUPS, "tiers": TIERS, "schema": [f.to_public() for f in cfg.schema()], "notes": cfg.notes}


@router.patch("/bots/{bot}/settings")
async def patch_settings(bot: str, request: Request, u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin"}, bot_id=b.id)
    d = await body(request)
    items = d.get("items")
    if not isinstance(items, list) or not all(isinstance(x, dict) and "key" in x for x in items):
        raise ApiError(422, "invalid", "items 要是 [{key, value}] 陣列")
    unknown = [x["key"] for x in items if x["key"] not in SETTING_DEFAULTS and x["key"] not in TEXT_DEFAULTS]
    if unknown:
        raise ApiError(422, "unknown_key", "有不認得的設定", {k: "不認得的設定" for k in unknown})
    try:
        changed = save_bot_settings(s, b.id, {x["key"]: x.get("value") for x in items}, user_id=u.id)
    except SettingsError as e:
        raise ApiError(422, "invalid", "設定值有誤", e.fields)
    s.commit()
    return {"changed": changed}


@router.post("/bots/{bot}/tier")
async def post_tier(bot: str, request: Request, u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin"}, bot_id=b.id)
    d = await body(request)
    flow_id = None
    if d.get("flow_id"):
        f = s.get(Flow, uid(d["flow_id"]))
        if f is None or f.bot_id != b.id:
            raise not_found()
        flow_id = f.id
    try:
        changed = apply_tier(s, b.id, str(d.get("tier") or ""), user_id=u.id, flow_id=flow_id)
    except SettingsError as e:
        raise ApiError(422, "invalid", "版本有誤", e.fields)
    s.commit()
    return {"changed": changed}


# ───────── 7. 知識庫與掛載 ─────────

@router.get("/workspaces/{ws}/kbs")
def list_kbs(ws: str, u: User = Me, s: Session = DB):
    w = uid(ws)
    if ws_roles(s, u, w):
        kbs = s.scalars(select(KnowledgeBase).where(KnowledgeBase.workspace_id == w).order_by(KnowledgeBase.slug)).all()
    else:
        bots = s.scalars(select(Membership.bot_id).where(Membership.user_id == u.id, Membership.workspace_id == w, Membership.bot_id.is_not(None))).all()
        if not bots:
            raise not_found()
        kbs = s.scalars(select(KnowledgeBase).join(BotKnowledge, BotKnowledge.kb_id == KnowledgeBase.id).where(BotKnowledge.bot_id.in_(bots)).distinct()).all()
    return {"items": [kb_out(s, k) for k in kbs], "next": None}


@router.post("/workspaces/{ws}/kbs", status_code=201)
async def create_kb(ws: str, request: Request, u: User = Me, s: Session = DB):
    w = uid(ws)
    require(s, u, w, {"admin"}, ws_level=True)
    d = await body(request)
    errs = {}
    slug, name = str(d.get("slug") or "").strip().lower(), str(d.get("name") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", slug):
        errs["slug"] = "2–41 個小寫英數字或連字號"
    if not name:
        errs["name"] = "必填"
    model = str(d.get("embed_model") or "gemini-embedding-001")
    try:
        dim = int(d.get("embed_dim") or 768)
        validate_embed_config(model, dim, SUPPORTED_EMBED_DIMS)
    except (ValueError, TypeError) as e:
        errs["embed_dim"] = str(e)
        dim = 0
    size, overlap = int(d.get("chunk_size") or 500), int(d.get("chunk_overlap") if d.get("chunk_overlap") is not None else 80)
    if not 100 <= size <= 4000:
        errs["chunk_size"] = "100–4000"
    if not 0 <= overlap < size:
        errs["chunk_overlap"] = "0 以上、小於段落長度"
    vis = str(d.get("visibility") or "workspace")
    if vis not in ("workspace", "restricted"):
        errs["visibility"] = "workspace 或 restricted"
    if errs:
        raise ApiError(422, "invalid", "欄位有誤", errs)
    if s.scalar(select(KnowledgeBase.id).where(KnowledgeBase.workspace_id == w, KnowledgeBase.slug == slug)):
        raise ApiError(409, "slug_taken", "這個 slug 已被使用", {"slug": "已被使用"})
    k = KnowledgeBase(workspace_id=w, slug=slug, name=name, description=str(d.get("description") or ""), visibility=vis, embed_model=model, embed_dim=dim,
                      chunk_size=size, chunk_overlap=overlap)
    s.add(k); s.flush()
    audit(s, w, u, "kb.create", slug, {"embed_model": model, "embed_dim": dim}, kb_id=k.id)
    s.commit()
    return kb_out(s, k)


@router.get("/kbs/{kb}")
def get_kb_detail(kb: str, u: User = Me, s: Session = DB):
    k = get_kb(s, kb)
    kb_reader(s, u, k)
    return kb_out(s, k)


@router.get("/bots/{bot}/knowledge")
def get_knowledge(bot: str, u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin"}, bot_id=b.id)
    rows = s.execute(select(BotKnowledge, KnowledgeBase).join(KnowledgeBase, KnowledgeBase.id == BotKnowledge.kb_id).where(BotKnowledge.bot_id == b.id).order_by(BotKnowledge.sort)).all()
    return {"items": [{"kb_id": str(k.id), "name": k.name, "label": bk.label, "min_hits": bk.min_hits, "weight": bk.weight, "enabled": bk.enabled,
                       "embed_model": k.embed_model, "embed_dim": k.embed_dim} for bk, k in rows]}


@router.put("/bots/{bot}/knowledge")
async def put_knowledge(bot: str, request: Request, u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin"}, bot_id=b.id)
    d = await body(request)
    items = d.get("items")
    if not isinstance(items, list):
        raise ApiError(422, "invalid", "items 要是陣列")
    errs: dict[str, str] = {}
    rows, specs = [], set()
    for i, x in enumerate(items):
        k = s.get(KnowledgeBase, uid(x.get("kb_id"))) if isinstance(x, dict) else None
        if k is None or k.workspace_id != b.workspace_id:
            errs[f"items.{i}.kb_id"] = "找不到這個知識庫"
            continue
        if k.visibility == "restricted" and not s.get(KbAllowedBot, (k.id, b.id)):
            errs[f"items.{i}.kb_id"] = "這個知識庫沒有開放給此機器人"
            continue
        try:
            mh, wt = int(x.get("min_hits") or 0), float(x.get("weight") if x.get("weight") is not None else 1.0)
        except (TypeError, ValueError):
            errs[f"items.{i}"] = "min_hits、weight 要是數字"
            continue
        if not (0 <= mh <= 10 and 0 < wt <= 3):
            errs[f"items.{i}"] = "min_hits 0–10、weight 大於 0 且不超過 3"
            continue
        specs.add((k.embed_model, k.embed_dim))
        rows.append(BotKnowledge(bot_id=b.id, kb_id=k.id, label=str(x.get("label") or k.name), min_hits=mh, weight=wt, enabled=bool(x.get("enabled", True)), sort=i))
    if len(specs) > 1:
        errs["items"] = "同一個機器人掛的知識庫要用同一個向量模型與維度"
    if len({r.kb_id for r in rows}) != len(rows):
        errs["items"] = "同一個知識庫重複了"
    if errs:
        raise ApiError(422, "invalid", "掛載清單有誤", errs)
    before = [str(x) for x in s.scalars(select(BotKnowledge.kb_id).where(BotKnowledge.bot_id == b.id))]
    s.query(BotKnowledge).filter(BotKnowledge.bot_id == b.id).delete()
    s.add_all(rows)
    audit(s, b.workspace_id, u, "bot.knowledge", b.slug, {"before": before, "after": [str(r.kb_id) for r in rows]}, bot_id=b.id)
    s.commit()
    return get_knowledge(bot, u, s)


# ───────── 8. 文件與段落 ─────────

def doc_writer(s: Session, u: User, k: KnowledgeBase) -> None:
    require(s, u, k.workspace_id, {"admin", "reviewer"}, ws_level=True)


@router.get("/kbs/{kb}/documents")
def list_documents(kb: str, status: str | None = None, limit: int = 50, cursor: str | None = None, u: User = Me, s: Session = DB):
    k = get_kb(s, kb)
    doc_writer(s, u, k)
    _, off, lim = page([], limit, cursor)
    q = select(Document).where(Document.kb_id == k.id)
    if status:
        q = q.where(Document.status == status)
    rows = s.scalars(q.order_by(Document.uploaded_at.desc(), Document.id).offset(off).limit(lim + 1)).all()
    return {"items": [doc_out(d) for d in rows[:lim]], "next": cursor_of(off, lim, len(rows))}


@router.post("/kbs/{kb}/documents", status_code=202)
async def upload_document(kb: str, request: Request, tasks: BackgroundTasks, u: User = Me, s: Session = DB,
                          llm: LLMClient = Depends(get_llm), factory=Depends(get_session_factory)):
    k = get_kb(s, kb)
    doc_writer(s, u, k)
    ctype = request.headers.get("content-type", "")
    try:
        if ctype.startswith("multipart/form-data"):
            form = await request.form()
            f = form.get("file")
            if f is None or not hasattr(f, "read"):
                raise ApiError(422, "invalid", "缺少檔案", {"file": "必填"})
            data = await f.read()
            meta: dict[str, Any] = {k2: str(form.get(k2) or "") for k2 in ("title", "source_note", "evidence")}
            doc = ingest.create_document(s, k, filename=f.filename or "upload", data=data, user_id=u.id, force=str(form.get("force") or "") in ("1", "true"), **meta)
        else:
            d = await body(request)
            doc = ingest.create_document(s, k, text=str(d.get("text") or ""), title=str(d.get("title") or "貼上的文字"), source_note=str(d.get("source_note") or ""),
                                         evidence=str(d.get("evidence") or ""), user_id=u.id, force=bool(d.get("force")))
    except ParseError as e:
        raise ApiError(422, "bad_file", str(e))
    except ingest.Duplicate as e:
        raise ApiError(409, "duplicate", "這個知識庫已經有相同的檔案", {"existing": doc_out(e.doc)})
    job = Job(workspace_id=k.workspace_id, kb_id=k.id, kind="ingest", target=str(doc.id), created_by=u.id)
    s.add(job)
    audit(s, k.workspace_id, u, "document.upload", doc.title, {"doc_id": str(doc.id), "filename": doc.filename}, kb_id=k.id)
    s.commit()
    tasks.add_task(ingest.run_ingest_job, factory, llm, job.id)
    return {"doc_id": str(doc.id), "job_id": str(job.id), "status": "processing"}


def get_doc(s: Session, u: User, doc: str) -> tuple[Document, KnowledgeBase]:
    d = s.get(Document, uid(doc))
    if d is None:
        raise not_found()
    k = s.get(KnowledgeBase, d.kb_id)
    doc_writer(s, u, k)
    return d, k


@router.get("/documents/{doc}")
def get_document(doc: str, u: User = Me, s: Session = DB):
    d, _ = get_doc(s, u, doc)
    job = s.scalar(select(Job).where(Job.target == str(d.id), Job.kind == "ingest").order_by(Job.created_at.desc()).limit(1))
    return {**doc_out(d), "job": {"id": str(job.id), "status": job.status, "progress": job.progress, "message": job.message} if job else None}


@router.patch("/documents/{doc}")
async def patch_document(doc: str, request: Request, u: User = Me, s: Session = DB):
    d, k = get_doc(s, u, doc)
    body_ = await body(request)
    before = doc_out(d)
    for f in ("title", "source_note", "evidence"):
        if f in body_:
            setattr(d, f, str(body_[f] or "").strip()[:300 if f == "title" else 2000])
    if "status" in body_:
        st = body_["status"]
        if st not in ("approved", "disabled"):
            raise ApiError(422, "invalid", "狀態只能改成 approved 或 disabled", {"status": "approved 或 disabled"})
        if d.status in ("processing", "failed"):
            raise ApiError(409, "not_ready", "文件還沒處理完成，不能審核")
        d.status, d.reviewed_by, d.reviewed_at = st, u.id, datetime.now(timezone.utc)
    audit(s, k.workspace_id, u, "document.update", d.title, {"before": before, "after": doc_out(d)}, kb_id=k.id)
    s.commit()
    return doc_out(d)


@router.get("/documents/{doc}/chunks")
def document_chunks(doc: str, u: User = Me, s: Session = DB):
    d, _ = get_doc(s, u, doc)
    rows = s.scalars(select(Chunk).where(Chunk.doc_id == d.id).order_by(Chunk.seq)).all()
    return {"items": [{"id": str(c.id), "seq": c.seq, "text": c.text, "page": c.page, "type": c.ctype, "has_vector": c.embedding is not None} for c in rows]}


@router.get("/documents/{doc}/file")
def document_file(doc: str, u: User = Me, s: Session = DB):
    d, _ = get_doc(s, u, doc)
    f = s.get(DocumentFile, d.id)
    from urllib.parse import quote
    return Response(f.content, media_type=d.mime or "application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(d.filename or 'file')}"})


@router.post("/kbs/{kb}/repair")
async def repair(kb: str, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm)):
    k = get_kb(s, kb)
    require(s, u, k.workspace_id, {"admin"}, ws_level=True)
    n = await ingest.repair_vectors(s, llm, k.id)
    audit(s, k.workspace_id, u, "kb.repair", k.slug, {"fixed": n}, kb_id=k.id)
    s.commit()
    return {"fixed": n}


# ───────── 12. 背景任務 ─────────

@router.get("/jobs/{job}")
def get_job(job: str, u: User = Me, s: Session = DB):
    j = s.get(Job, uid(job))
    if j is None or not (ws_roles(s, u, j.workspace_id) or (j.bot_id and roles_of(s, u, j.workspace_id, j.bot_id))):
        raise not_found()
    return {"id": str(j.id), "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.message, "result": j.result}


def fail_stale_jobs(s: Session) -> int:
    """伺服器重啟時，還在 queued／running 的工作已經沒人做了：標成 failed，前端可重試"""
    jobs = s.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all()
    for j in jobs:
        j.status, j.message, j.finished_at = "failed", "伺服器重新啟動，工作中斷，請重試", datetime.now(timezone.utc)
        if j.kind == "ingest":
            d = s.get(Document, uid(j.target)) if j.target else None
            if d is not None and d.status == "processing":
                d.status, d.error = "failed", "伺服器重新啟動，處理中斷，請重新處理"
    s.commit()
    return len(jobs)
