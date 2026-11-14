"""後台 API 第三批：Workspace 與成員、稽核（§2）、刪 Bot（§3）、模型（§4）、知識庫管理（§7）、文件與段落管理（§8）、測試對話與紀錄統計（§10）。"""
from __future__ import annotations

import secrets
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import auth
from app.errors import ApiError, not_found
from app.perms import ROLES, require, roles_of, ws_roles
from app.routes.admin import DB, Me, audit, body, cursor_of, get_bot, get_doc, get_kb, kb_out, page, uid
from app.routes.admin_bot import admin_bot
from app.routes.chat import get_llm
from db.models import AuditLog, Bot, BotKnowledge, Chunk, Conversation, Feedback, Flow, Job, KbAllowedBot, KnowledgeBase, Membership, User, Workspace
from graph.chat_graph import Deps, run_chat
from services import describe as describe_svc
from services import ingest
from services.llm import EMBED_MODELS, LLMClient, provider_of
from services.retrieval import CHUNK_TYPES
from settings.loader import SETTING_DEFAULTS, TEXT_DEFAULTS, load_config

router = APIRouter(prefix="/api/admin")
TEST_SESSION = "admin-test-"     # 測試對話的 session 前綴：紀錄與統計預設排除


def get_ws(s: Session, ws: str) -> Workspace:
    w = s.get(Workspace, uid(ws))
    if w is None:
        raise not_found()
    return w


# ───────── §2 Workspace 與成員 ─────────

@router.get("/workspaces/{ws}")
def get_workspace(ws: str, u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    _any_member(s, u, w.id)
    bots = s.scalar(select(func.count()).select_from(Bot).where(Bot.workspace_id == w.id))
    kbs = s.scalar(select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.workspace_id == w.id))
    return {"id": str(w.id), "slug": w.slug, "name": w.name, "bots": bots, "kbs": kbs}


def _any_member(s: Session, u: User, ws_id) -> None:
    if not s.scalar(select(Membership.id).where(Membership.user_id == u.id, Membership.workspace_id == ws_id).limit(1)):
        raise not_found()


@router.patch("/workspaces/{ws}")
async def patch_workspace(ws: str, request: Request, u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    require(s, u, w.id, set(), ws_level=True)
    d = await body(request)
    name = str(d.get("name") or "").strip()
    if not name:
        raise ApiError(422, "invalid", "欄位有誤", {"name": "必填"})
    audit(s, w.id, u, "workspace.rename", name, {"before": w.name})
    w.name = name
    s.commit()
    return {"id": str(w.id), "slug": w.slug, "name": w.name}


def member_out(m: Membership, user: User) -> dict:
    return {"id": str(m.id), "user_id": str(user.id), "login": user.login, "display_name": user.display_name, "role": m.role,
            "bot_id": str(m.bot_id) if m.bot_id else None, "disabled": user.disabled}


@router.get("/workspaces/{ws}/members")
def list_members(ws: str, u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    require(s, u, w.id, set(), ws_level=True)
    rows = s.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == w.id).order_by(User.login, Membership.role)).all()
    return {"items": [member_out(m, x) for m, x in rows]}


def _check_member_fields(s: Session, w: Workspace, role, bot_id) -> uuid.UUID | None:
    errs = {}
    if role not in ROLES:
        errs["role"] = "只能是 " + "、".join(ROLES)
    bid = None
    if bot_id:
        b = s.get(Bot, uid(bot_id))
        if b is None or b.workspace_id != w.id:
            errs["bot_id"] = "找不到這個機器人"
        else:
            bid = b.id
    if role == "owner" and bot_id:
        errs["role"] = "owner 只能是整個工作區"
    if errs:
        raise ApiError(422, "invalid", "欄位有誤", errs)
    return bid


@router.post("/workspaces/{ws}/members", status_code=201)
async def add_member(ws: str, request: Request, u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    require(s, u, w.id, set(), ws_level=True)
    d = await body(request)
    login = str(d.get("login") or "").strip()
    if not login:
        raise ApiError(422, "invalid", "欄位有誤", {"login": "必填"})
    bid = _check_member_fields(s, w, d.get("role"), d.get("bot_id"))
    user = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
    temp = None
    if user is None:
        pw = str(d.get("password") or "")
        if pw:
            auth.check_password_rules(pw)
        else:
            pw = temp = secrets.token_urlsafe(12)
        user = User(login=login, display_name=str(d.get("display_name") or login), pw_hash=auth.hash_password(pw))
        s.add(user); s.flush()
    if s.scalar(select(Membership.id).where(Membership.user_id == user.id, Membership.workspace_id == w.id,
                                            Membership.bot_id == bid if bid else Membership.bot_id.is_(None))):
        raise ApiError(409, "exists", "這個人在這個範圍已經有角色了")
    m = Membership(user_id=user.id, workspace_id=w.id, bot_id=bid, role=d["role"])
    s.add(m)
    audit(s, w.id, u, "member.add", login, {"role": m.role, "bot_id": str(bid) if bid else None}, bot_id=bid)
    s.commit()
    out = member_out(m, user)
    if temp:
        out["temp_password"] = temp      # 只顯示這一次
    return out


def _owners_left(s: Session, ws_id, excluding) -> int:
    return s.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == ws_id, Membership.role == "owner", Membership.id != excluding)) or 0


@router.patch("/workspaces/{ws}/members/{mid}")
async def patch_member(ws: str, mid: str, request: Request, u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    require(s, u, w.id, set(), ws_level=True)
    m = s.get(Membership, uid(mid))
    if m is None or m.workspace_id != w.id:
        raise not_found()
    d = await body(request)
    role = d.get("role", m.role)
    bid = _check_member_fields(s, w, role, d["bot_id"] if "bot_id" in d else (str(m.bot_id) if m.bot_id else None))
    if m.role == "owner" and role != "owner" and not _owners_left(s, w.id, m.id):
        raise ApiError(409, "last_owner", "工作區至少要有一位 owner")
    before = {"role": m.role, "bot_id": str(m.bot_id) if m.bot_id else None}
    m.role, m.bot_id = role, bid
    audit(s, w.id, u, "member.update", str(m.user_id), {"before": before, "after": {"role": role, "bot_id": str(bid) if bid else None}})
    s.commit()
    return member_out(m, s.get(User, m.user_id))


@router.delete("/workspaces/{ws}/members/{mid}")
def delete_member(ws: str, mid: str, u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    require(s, u, w.id, set(), ws_level=True)
    m = s.get(Membership, uid(mid))
    if m is None or m.workspace_id != w.id:
        raise not_found()
    if m.role == "owner" and not _owners_left(s, w.id, m.id):
        raise ApiError(409, "last_owner", "工作區至少要有一位 owner")
    audit(s, w.id, u, "member.remove", str(m.user_id), {"role": m.role})
    s.delete(m)
    s.commit()
    return {"ok": True}


@router.post("/workspaces/{ws}/users/{user_id}/reset-password")
def reset_password(ws: str, user_id: str, u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    require(s, u, w.id, set(), ws_level=True)
    target = s.get(User, uid(user_id))
    if target is None or not s.scalar(select(Membership.id).where(Membership.user_id == target.id, Membership.workspace_id == w.id).limit(1)):
        raise not_found()
    temp = secrets.token_urlsafe(12)
    target.pw_hash, target.token_version, target.failed_count, target.locked_until = auth.hash_password(temp), target.token_version + 1, 0, None
    audit(s, w.id, u, "user.reset_password", target.login)
    s.commit()
    return {"temp_password": temp}


@router.get("/workspaces/{ws}/audit")
def list_audit(ws: str, bot: str | None = None, kb: str | None = None, action: str | None = None, limit: int = 50, cursor: str | None = None,
               u: User = Me, s: Session = DB):
    w = get_ws(s, ws)
    require(s, u, w.id, set(), ws_level=True)
    _, off, lim = page([], limit, cursor)
    q = select(AuditLog, User.login).outerjoin(User, User.id == AuditLog.user_id).where(AuditLog.workspace_id == w.id)
    if bot:
        q = q.where(AuditLog.bot_id == uid(bot))
    if kb:
        q = q.where(AuditLog.kb_id == uid(kb))
    if action:
        q = q.where(AuditLog.action.like(action.replace("%", "") + "%"))
    rows = s.execute(q.order_by(AuditLog.at.desc(), AuditLog.id.desc()).offset(off).limit(lim + 1)).all()
    return {"items": [{"id": a.id, "at": a.at.isoformat(), "user": login, "action": a.action, "target": a.target, "detail": a.detail,
                       "bot_id": str(a.bot_id) if a.bot_id else None, "kb_id": str(a.kb_id) if a.kb_id else None} for a, login in rows[:lim]],
            "next": cursor_of(off, lim, len(rows))}


# ───────── §3 刪 Bot ─────────

@router.delete("/bots/{bot}")
def delete_bot(bot: str, confirm: str = "", u: User = Me, s: Session = DB):
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, set(), ws_level=True)
    if confirm != b.slug:
        raise ApiError(422, "confirm", "請在 confirm 參數填入機器人的 slug 確認刪除（對話紀錄會一併刪除）", {"confirm": "要等於 slug"})
    audit(s, b.workspace_id, u, "bot.delete", b.slug, bot_id=b.id)
    s.delete(b)
    s.commit()
    return {"ok": True}


# ───────── §4 模型 ─────────

@router.get("/models")
def models(u: User = Me, llm: LLMClient = Depends(get_llm)):
    gen = sorted({SETTING_DEFAULTS.get(k) for k in ("model_triage", "model_answer", "model_check") if SETTING_DEFAULTS.get(k)})
    return {"providers": {p: bool(llm.keys.get(p)) for p in ("gemini", "openai")},
            "suggested": [m for m in gen if llm.keys.get(provider_of(m))],
            "embed": [{"id": k, "dims": list(v["dims"]), "provider": provider_of(k), "available": bool(llm.keys.get(provider_of(k)))} for k, v in EMBED_MODELS.items()]}


@router.post("/models/ping")
async def ping_model(request: Request, u: User = Me, llm: LLMClient = Depends(get_llm)):
    d = await body(request)
    model, kind = str(d.get("model") or "").strip(), d.get("kind") or "gen"
    if not model:
        raise ApiError(422, "invalid", "欄位有誤", {"model": "必填"})
    t = time.monotonic()
    try:
        if kind == "embed":
            v = await llm.embed(model, ["測試"], dim=int(d.get("dim") or 768), task="query")
            reply = f"向量維度 {len(v[0])}"
        else:
            r, _ = await llm.generate_json(model, '只輸出 JSON：{"ok":true,"say":"用一句繁體中文打招呼"}', temperature=0, no_think=True)
            reply = str((r or {}).get("say") if isinstance(r, dict) else r)[:60]
    except Exception as e:     # LLMClient 的錯誤訊息已去掉金鑰
        return {"ok": False, "ms": int((time.monotonic() - t) * 1000), "error": str(e)[:200]}
    return {"ok": True, "ms": int((time.monotonic() - t) * 1000), "reply": reply}


# ───────── §7 知識庫管理 ─────────

@router.patch("/kbs/{kb}")
async def patch_kb(kb: str, request: Request, u: User = Me, s: Session = DB):
    k = get_kb(s, kb)
    require(s, u, k.workspace_id, {"admin"}, ws_level=True)
    d = await body(request)
    errs = {}
    if "embed_model" in d or "embed_dim" in d:
        errs["embed_model"] = "向量模型與維度建立後不能改；要換請另建知識庫"
    if "name" in d and not str(d["name"]).strip():
        errs["name"] = "必填"
    if "visibility" in d and d["visibility"] not in ("workspace", "restricted"):
        errs["visibility"] = "workspace 或 restricted"
    size = int(d.get("chunk_size", k.chunk_size))
    overlap = int(d.get("chunk_overlap", k.chunk_overlap))
    if not 100 <= size <= 4000:
        errs["chunk_size"] = "100–4000"
    if not 0 <= overlap < size:
        errs["chunk_overlap"] = "0 以上、小於段落長度"
    if errs:
        raise ApiError(422, "invalid", "欄位有誤", errs)
    before = kb_out(s, k)
    for f in ("name", "description", "visibility"):
        if f in d:
            setattr(k, f, str(d[f]).strip())
    if "auto_kg_tag" in d:
        k.auto_kg_tag = bool(d["auto_kg_tag"])
    k.chunk_size, k.chunk_overlap = size, overlap
    audit(s, k.workspace_id, u, "kb.update", k.slug, {"before": {x: before[x] for x in ("name", "visibility", "chunk_size", "chunk_overlap")}}, kb_id=k.id)
    s.commit()
    return kb_out(s, k)


@router.put("/kbs/{kb}/allowed-bots")
async def put_allowed(kb: str, request: Request, u: User = Me, s: Session = DB):
    k = get_kb(s, kb)
    require(s, u, k.workspace_id, {"admin"}, ws_level=True)
    d = await body(request)
    ids = d.get("bot_ids")
    if not isinstance(ids, list):
        raise ApiError(422, "invalid", "bot_ids 要是陣列")
    bots = []
    for i in ids:
        b = s.get(Bot, uid(i))
        if b is None or b.workspace_id != k.workspace_id:
            raise ApiError(422, "invalid", "有找不到的機器人", {"bot_ids": str(i)})
        bots.append(b.id)
    s.query(KbAllowedBot).filter(KbAllowedBot.kb_id == k.id).delete()
    s.add_all([KbAllowedBot(kb_id=k.id, bot_id=b) for b in dict.fromkeys(bots)])
    audit(s, k.workspace_id, u, "kb.allowed_bots", k.slug, {"bot_ids": [str(b) for b in bots]}, kb_id=k.id)
    s.commit()
    return {"bot_ids": [str(b) for b in dict.fromkeys(bots)]}


@router.delete("/kbs/{kb}")
def delete_kb(kb: str, u: User = Me, s: Session = DB):
    k = get_kb(s, kb)
    require(s, u, k.workspace_id, set(), ws_level=True)
    mounted = s.execute(select(Bot.id, Bot.name).join(BotKnowledge, BotKnowledge.bot_id == Bot.id).where(BotKnowledge.kb_id == k.id)).all()
    if mounted:
        raise ApiError(409, "mounted", "這個知識庫還被機器人掛著，請先卸下", {"bots": ", ".join(n for _, n in mounted)})
    audit(s, k.workspace_id, u, "kb.delete", k.slug, kb_id=k.id)
    s.delete(k)
    s.commit()
    return {"ok": True}


@router.get("/bots/{bot}/knowledge/available")
def available_kbs(bot: str, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    mounted = s.execute(select(KnowledgeBase.embed_model, KnowledgeBase.embed_dim).join(BotKnowledge, BotKnowledge.kb_id == KnowledgeBase.id)
                        .where(BotKnowledge.bot_id == b.id)).first()
    allowed = select(KbAllowedBot.kb_id).where(KbAllowedBot.bot_id == b.id)
    q = select(KnowledgeBase).where(KnowledgeBase.workspace_id == b.workspace_id,
                                    or_(KnowledgeBase.visibility == "workspace", KnowledgeBase.id.in_(allowed)))
    out = []
    for k in s.scalars(q.order_by(KnowledgeBase.slug)):
        ok = mounted is None or (k.embed_model, k.embed_dim) == tuple(mounted)
        out.append({"id": str(k.id), "slug": k.slug, "name": k.name, "embed_model": k.embed_model, "embed_dim": k.embed_dim, "compatible": ok})
    return {"items": out}


# ───────── §8 文件與段落管理 ─────────

@router.delete("/documents/{doc}")
def delete_document(doc: str, u: User = Me, s: Session = DB):
    d, k = get_doc(s, u, doc)
    require(s, u, k.workspace_id, {"admin"}, ws_level=True)
    audit(s, k.workspace_id, u, "document.delete", d.title, {"doc_id": str(d.id)}, kb_id=k.id)
    s.delete(d)
    s.commit()
    return {"ok": True}


@router.patch("/chunks/{chunk}")
async def patch_chunk(chunk: str, request: Request, u: User = Me, s: Session = DB):
    c = s.get(Chunk, uid(chunk))
    if c is None:
        raise not_found()
    d0, k = get_doc(s, u, str(c.doc_id))
    d = await body(request)
    t = d.get("type")
    if t not in ("", *CHUNK_TYPES):
        raise ApiError(422, "invalid", "欄位有誤", {"type": "只能是 " + "、".join(CHUNK_TYPES) + " 或空白"})
    before, c.ctype = c.ctype, t
    audit(s, k.workspace_id, u, "chunk.type", d0.title, {"seq": c.seq, "before": before, "after": t}, kb_id=k.id)
    s.commit()
    return {"id": str(c.id), "type": c.ctype}


@router.post("/documents/{doc}/classify")
async def classify_document(doc: str, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm)):
    d, k = get_doc(s, u, doc)
    require(s, u, k.workspace_id, {"admin"}, ws_level=True)
    chunks = s.scalars(select(Chunk).where(Chunk.doc_id == d.id).order_by(Chunk.seq)).all()
    model, role_user = ingest._bot_context(s, k.id)
    types, bad = await ingest.classify(llm, model, role_user, [c.text for c in chunks])
    for c, t in zip(chunks, types):
        if t:                        # 這次分類失敗的段落保留原本的類型
            c.ctype = t
    audit(s, k.workspace_id, u, "document.classify", d.title, {"chunks": len(chunks), "failed_batches": bad}, kb_id=k.id)
    s.commit()
    return {"chunks": len(chunks), "classified": sum(1 for t in types if t), "failed_batches": bad}


# ───────── §10 測試對話、紀錄、統計 ─────────

@router.post("/bots/{bot}/test-chat")
async def test_chat(bot: str, request: Request, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm)):
    b = admin_bot(s, u, bot)
    d = await body(request)
    if not str(d.get("message") or "").strip():
        raise ApiError(422, "invalid", "請輸入內容", {"message": "必填"})
    flow_id = None
    if d.get("flow_id"):
        f = s.get(Flow, uid(d["flow_id"]))
        if f is None or f.bot_id != b.id:
            raise not_found()
        flow_id = f.id
    override = d.get("settings_override") or None
    if override is not None:
        bad = [k for k in override if k not in SETTING_DEFAULTS and k not in TEXT_DEFAULTS] if isinstance(override, dict) else ["settings_override"]
        if bad:
            raise ApiError(422, "unknown_key", "有不認得的設定", {k: "不認得的設定" for k in bad})
    sess = str(d.get("session") or "")
    req = {k: d.get(k) for k in ("message", "history", "nameMap", "followups", "answered", "turn")}
    req.update(session=sess if sess.startswith(TEST_SESSION) else TEST_SESSION + (sess or uuid.uuid4().hex[:12]), unit="（後台測試）")
    res = await run_chat(Deps(s, llm, b.id, flow_id, override=override, with_trace=True), req)
    s.commit()
    return {"ok": True, **res, "session": req["session"]}


def _log_query(b: Bot, days: int | None, unit: str | None, rtype: str | None, q: str | None, rewritten: bool | None, include_test: bool):
    x = select(Conversation).where(Conversation.bot_id == b.id)
    if days:
        x = x.where(Conversation.created_at >= datetime.now(timezone.utc) - timedelta(days=days))
    if not include_test:
        x = x.where(~Conversation.session.like(TEST_SESSION + "%"))
    if unit:
        x = x.where(Conversation.unit == unit)
    if rtype:
        x = x.where(Conversation.rtype == rtype)
    if q:
        x = x.where(Conversation.user_text.contains(q))
    if rewritten is not None:
        x = x.where(Conversation.rewritten.is_(rewritten))
    return x


def log_row(c: Conversation, fb: list[str] | None = None) -> dict:
    fb = fb or []
    return {"id": c.id, "at": c.created_at.isoformat(), "session": c.session, "turn": c.turn, "unit": c.unit, "message": c.user_text[:120], "type": c.rtype,
            "intent": c.intent, "referral": (c.referral or {}).get("category", "") if isinstance(c.referral, dict) else "", "retrieved": c.hits, "top": c.top_score,
            "cited": c.cited, "skills": c.skills, "rewritten": c.rewritten, "ms": c.ms,
            "fb": "up" if "有幫助" in fb else "down" if "沒幫助" in fb else "", "copied": any(k.startswith("複製") for k in fb)}


def _feedback(s: Session, bot_id) -> dict[tuple[str, int], list[str]]:
    out: dict = {}
    for f in s.scalars(select(Feedback).where(Feedback.bot_id == bot_id)):
        out.setdefault((f.session, f.turn), []).append(f.kind)
    return out


@router.get("/bots/{bot}/logs")
def list_logs(bot: str, days: int | None = None, unit: str | None = None, type: str | None = None, q: str | None = None, rewritten: bool | None = None,
              include_test: bool = False, limit: int = 50, cursor: str | None = None, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot, {"analyst"})
    _, off, lim = page([], limit, cursor)
    rows = s.scalars(_log_query(b, days, unit, type, q, rewritten, include_test).order_by(Conversation.created_at.desc(), Conversation.id.desc()).offset(off).limit(lim + 1)).all()
    fb = _feedback(s, b.id)
    return {"items": [log_row(c, fb.get((c.session, c.turn))) for c in rows[:lim]], "next": cursor_of(off, lim, len(rows))}


@router.get("/logs/{log_id}")
def log_detail(log_id: int, u: User = Me, s: Session = DB):
    c = s.get(Conversation, log_id)
    if c is None:
        raise not_found()
    admin_bot(s, u, str(c.bot_id), {"analyst"})
    fb = _feedback(s, c.bot_id).get((c.session, c.turn))
    return {**log_row(c, fb), "message": c.user_text, "triage": c.triage, "answer": c.answer, "sources": c.sources, "checks": c.checks, "trace": c.trace,
            "stability": c.stability, "quality": c.quality, "referral": c.referral}


def _pct(a, b):
    return round(a / b * 1000) / 10 if b else None


@router.get("/bots/{bot}/stats")
def stats(bot: str, days: int | None = 30, include_test: bool = False, u: User = Me, s: Session = DB):
    """對應 Apps Script adminLogs 的統計部分"""
    b = admin_bot(s, u, bot, {"analyst"})
    all_ = s.scalars(_log_query(b, days, None, None, None, None, include_test).order_by(Conversation.created_at)).all()
    fb = _feedback(s, b.id)
    rows = [log_row(c, fb.get((c.session, c.turn))) for c in all_]
    answered = [r for r in rows if r["type"] in ("answer", "continue", "ai_fallback")]
    ret = [r for r in rows if r["type"] in ("answer", "continue", "ai_fallback", "conservative")]
    ups, downs = sum(1 for r in rows if r["fb"] == "up"), sum(1 for r in rows if r["fb"] == "down")
    st = {
        "turns": len(rows), "sessions": len({r["session"] for r in rows}), "byType": dict(Counter(r["type"] for r in rows)),
        "retrievalTurns": len(ret), "hitRate": _pct(sum(1 for r in ret if r["retrieved"] > 0), len(ret)),
        "citeRate": _pct(sum(r["cited"] for r in answered), sum(r["retrieved"] for r in answered)),
        "avgTop": round(sum(r["top"] for r in ret) / len(ret), 3) if ret else None,
        "rewriteRate": _pct(sum(1 for r in answered if r["rewritten"]), len(answered)),
        "upRate": _pct(ups, ups + downs), "ups": ups, "downs": downs, "copied": sum(1 for r in rows if r["copied"]),
        "avgMs": round(sum(r["ms"] for r in rows) / len(rows)) if rows else None,
    }
    gaps = [{"id": r["id"], "at": r["at"], "message": r["message"], "top": r["top"]} for r in rows
            if (r["type"] in ("conservative", "ai_fallback") or (r["type"] in ("answer", "continue") and r["retrieved"] == 0))][-30:][::-1]
    docs = Counter(src.get("title", "") for c in all_ for src in (c.sources or []) if isinstance(src, dict))
    gap_kinds = Counter((c.quality or {}).get("gap", {}).get("kind") for c in all_ if isinstance(c.quality, dict) and (c.quality.get("gap") or {}).get("kind"))
    l5 = Counter(i.get("type", "其他") for c in all_ if isinstance(c.quality, dict) for a in c.quality.get("l5") or [] for i in a.get("issues") or [])
    ev = Counter(e.get("kind") for c in all_ if isinstance(c.stability, dict) for e in c.stability.get("ev") or [])
    units = sorted({c.unit for c in all_ if c.unit})
    return {"stats": st, "gaps": gaps, "topDocs": [{"title": t, "n": n} for t, n in docs.most_common(10)],
            "quality": {"gapKinds": dict(gap_kinds), "l5": dict(l5)}, "stability": dict(ev), "units": units}


# ───────── §4 用說的轉設定 ─────────

@router.post("/bots/{bot}/describe")
async def describe(bot: str, request: Request, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm)):
    b = admin_bot(s, u, bot)
    d = await body(request)
    try:
        return await describe_svc.describe(llm, load_config(s, b.id), str(d.get("kind") or ""), str(d.get("text") or ""), d.get("current"))
    except describe_svc.DescribeError as e:
        raise ApiError(422, "describe", str(e))


@router.post("/bots/{bot}/describe-back")
async def describe_back(bot: str, request: Request, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm)):
    b = admin_bot(s, u, bot)
    d = await body(request)
    try:
        return {"text": await describe_svc.describe_back(llm, load_config(s, b.id), str(d.get("kind") or ""))}
    except describe_svc.DescribeError as e:
        raise ApiError(422, "describe", str(e))


# ───────── §10 紀錄匯出 ─────────

CSV_COLS = [("id", "編號"), ("at", "時間"), ("session", "Session"), ("turn", "輪次"), ("unit", "單位"), ("message", "訊息（去識別化）"), ("type", "回應類型"),
            ("intent", "意圖"), ("referral", "轉介"), ("retrieved", "檢索段數"), ("top", "最高相似度"), ("cited", "被引用段數"), ("skills", "Skill"),
            ("rewritten", "重寫"), ("ms", "耗時ms"), ("fb", "回饋")]


@router.get("/bots/{bot}/logs/export.csv")
def export_logs(bot: str, days: int | None = None, unit: str | None = None, type: str | None = None, q: str | None = None, rewritten: bool | None = None,
                include_test: bool = False, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot, {"analyst"})
    rows = s.scalars(_log_query(b, days, unit, type, q, rewritten, include_test).order_by(Conversation.created_at).limit(50000)).all()
    fb = _feedback(s, b.id)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([h for _, h in CSV_COLS])
    for c in rows:
        r = log_row(c, fb.get((c.session, c.turn)))
        r["message"], r["skills"], r["rewritten"] = c.user_text, "、".join(r["skills"]), "是" if r["rewritten"] else ""
        w.writerow([_csv_safe(r[k]) for k, _ in CSV_COLS])
    audit(s, b.workspace_id, u, "logs.export", b.slug, {"rows": len(rows)}, bot_id=b.id)
    s.commit()
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",      # BOM：Excel 才認得 UTF-8
                    headers={"Content-Disposition": f'attachment; filename="{b.slug}-logs.csv"'})


def _csv_safe(v):
    """避免試算表把 = + - @ 開頭的內容當公式執行"""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@") else v


# ───────── §12 取消工作 ─────────

@router.post("/jobs/{job}/cancel")
def cancel_job(job: str, u: User = Me, s: Session = DB):
    j = s.get(Job, uid(job))
    if j is None or not (ws_roles(s, u, j.workspace_id) or (j.bot_id and roles_of(s, u, j.workspace_id, j.bot_id))):
        raise not_found()
    require(s, u, j.workspace_id, {"admin"}, bot_id=j.bot_id, ws_level=j.bot_id is None)
    if j.status in ("done", "failed", "cancelled"):
        raise ApiError(409, "finished", "工作已經結束")
    j.status, j.message, j.finished_at = "cancelled", "已取消（已完成的部分保留）", datetime.now(timezone.utc)
    audit(s, j.workspace_id, u, "job.cancel", j.kind, {"job_id": str(j.id)}, bot_id=j.bot_id, kb_id=j.kb_id)
    s.commit()
    return {"status": "cancelled"}
