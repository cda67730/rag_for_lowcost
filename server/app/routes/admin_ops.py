"""後台 API 第三批：Workspace 與成員、稽核（§2）、刪 Bot（§3）、模型（§4）、知識庫管理（§7）、文件與段落管理（§8）、測試對話與紀錄統計（§10）。"""
from __future__ import annotations

import base64
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, literal, or_, select, tuple_
from sqlalchemy.orm import Session, defer

from app import auth
from app.errors import ApiError, not_found
from app.perms import ROLES, require, roles_of, ws_roles
from app.routes.admin import DB, Me, audit, body, cursor_of, get_bot, get_session_factory, get_doc, get_kb, kb_out, page, uid
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


def _feedback(s: Session, bot_id, keys: list[tuple[str, int]] | None = None) -> dict[tuple[str, int], list[str]]:
    """{(session, turn): [回饋種類]}；給 keys 時只讀那幾輪的回饋"""
    out: dict = {}
    q = select(Feedback.session, Feedback.turn, Feedback.kind).where(Feedback.bot_id == bot_id)
    if keys is not None:
        if not keys:
            return out
        q = q.where(tuple_(Feedback.session, Feedback.turn).in_(list(set(keys))))
    for f in s.execute(q):
        out.setdefault((f.session, f.turn), []).append(f.kind)
    return out


@router.get("/bots/{bot}/logs")
def list_logs(bot: str, days: int | None = None, unit: str | None = None, type: str | None = None, q: str | None = None, rewritten: bool | None = None,
              include_test: bool = False, limit: int = 50, cursor: str | None = None, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot, {"analyst"})
    lim = max(1, min(int(limit or 50), 200))
    x = _log_query(b, days, unit, type, q, rewritten, include_test).options(*(defer(getattr(Conversation, k)) for k in _HEAVY))
    if cursor:      # 從上一頁最後一筆接著往下讀（走索引），不用「跳過前 N 筆」，翻到多後面都一樣快
        at, last = _log_cursor_decode(cursor)
        x = x.where(tuple_(Conversation.created_at, Conversation.id) < tuple_(literal(at), literal(last)))
    rows = s.scalars(x.order_by(Conversation.created_at.desc(), Conversation.id.desc()).limit(lim + 1)).all()
    page_rows = rows[:lim]
    fb = _feedback(s, b.id, [(c.session, c.turn) for c in page_rows])      # 只讀這一頁用得到的回饋
    return {"items": [log_row(c, fb.get((c.session, c.turn))) for c in page_rows],
            "next": _log_cursor(page_rows[-1]) if len(rows) > lim else None}


# 列表用不到、又可能很大的欄位（流程紀錄、回答全文等），列表查詢時不讀
_HEAVY = ("trace", "answer", "triage", "checks", "sources", "quality", "stability")


def _log_cursor(c: Conversation) -> str:
    return base64.urlsafe_b64encode(f"{c.created_at.isoformat()}|{c.id}".encode()).decode()


def _log_cursor_decode(cursor: str) -> tuple[datetime, int]:
    try:
        at, last = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        t = datetime.fromisoformat(at)
        if t.tzinfo is None:
            raise ValueError
        return t, int(last)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(422, "bad_cursor", "翻頁位置不正確，請重新查詢")


@router.get("/logs/{log_id}")
def log_detail(log_id: int, u: User = Me, s: Session = DB):
    c = s.get(Conversation, log_id)
    if c is None:
        raise not_found()
    admin_bot(s, u, str(c.bot_id), {"analyst"})
    fb = _feedback(s, c.bot_id, [(c.session, c.turn)]).get((c.session, c.turn))
    return {**log_row(c, fb), "message": c.user_text, "triage": c.triage, "answer": c.answer, "sources": c.sources, "checks": c.checks, "trace": c.trace,
            "stability": c.stability, "quality": c.quality, "referral": c.referral}


def _pct(a, b):
    return round(a / b * 1000) / 10 if b else None


@router.get("/bots/{bot}/stats")
def stats(bot: str, days: int | None = 30, include_test: bool = False, u: User = Me, s: Session = DB):
    """對應 Apps Script adminLogs 的統計部分"""
    b = admin_bot(s, u, bot, {"analyst"})
    return stats_of(s, b, days, include_test)


# 統計全部在資料庫裡算：不把每一筆紀錄（含整包 trace）載進 Python
_STATS_SQL = """
WITH c AS (
  SELECT c.id, c.created_at, c.session, c.unit, c.user_text, c.rtype, c.hits, c.top_score::text::float8 AS top, c.top_score, c.cited, c.rewritten, c.ms,
         c.sources, c.quality, c.stability,
         EXISTS (SELECT 1 FROM feedback f WHERE f.bot_id = c.bot_id AND f.session = c.session AND f.turn = c.turn AND f.kind = '有幫助') AS up,
         EXISTS (SELECT 1 FROM feedback f WHERE f.bot_id = c.bot_id AND f.session = c.session AND f.turn = c.turn AND f.kind = '沒幫助') AS down,
         EXISTS (SELECT 1 FROM feedback f WHERE f.bot_id = c.bot_id AND f.session = c.session AND f.turn = c.turn AND f.kind LIKE '複製%%') AS copied
  FROM conversations c
  WHERE c.bot_id = :bot AND (CAST(:since AS timestamptz) IS NULL OR c.created_at >= :since) AND (CAST(:test AS text) IS NULL OR c.session NOT LIKE CAST(:test AS text))
)
"""
_ARR = "CASE WHEN jsonb_typeof({0}) = 'array' THEN {0} ELSE '[]'::jsonb END"
_OBJ = "CASE WHEN jsonb_typeof({0}) = 'object' THEN {0} ELSE '{{}}'::jsonb END"


def stats_of(s: Session, b: Bot, days: int | None, include_test: bool) -> dict:
    from sqlalchemy import text
    p = {"bot": b.id, "since": datetime.now(timezone.utc) - timedelta(days=days) if days else None, "test": None if include_test else TEST_SESSION + "%"}
    q = lambda body: s.execute(text(_STATS_SQL + body), p).all()
    ANS, RET = "('answer','continue','ai_fallback')", "('answer','continue','ai_fallback','conservative')"
    (n, sessions, n_ret, ret_hit, top_sum, n_ans, cited, retrieved, rewr, ups, downs, copied, ms_sum), = q(f"""
      SELECT count(*), count(DISTINCT session),
             count(*) FILTER (WHERE rtype IN {RET}), count(*) FILTER (WHERE rtype IN {RET} AND hits > 0), coalesce(sum(top) FILTER (WHERE rtype IN {RET}), 0),
             count(*) FILTER (WHERE rtype IN {ANS}), coalesce(sum(cited) FILTER (WHERE rtype IN {ANS}), 0), coalesce(sum(hits) FILTER (WHERE rtype IN {ANS}), 0),
             count(*) FILTER (WHERE rtype IN {ANS} AND rewritten),
             count(*) FILTER (WHERE up), count(*) FILTER (WHERE down AND NOT up), count(*) FILTER (WHERE copied), coalesce(sum(ms), 0)
      FROM c""")
    st = {
        "turns": n, "sessions": sessions, "byType": {t: k for t, k in q("SELECT rtype, count(*) FROM c GROUP BY rtype ORDER BY min(created_at), min(id)")},
        "retrievalTurns": n_ret, "hitRate": _pct(ret_hit, n_ret), "citeRate": _pct(cited, retrieved),
        "avgTop": round(top_sum / n_ret, 3) if n_ret else None, "rewriteRate": _pct(rewr, n_ans),
        "upRate": _pct(ups, ups + downs), "ups": ups, "downs": downs, "copied": copied, "avgMs": round(ms_sum / n) if n else None,
    }
    gaps = [{"id": i, "at": at.isoformat(), "message": m[:120], "top": t} for i, at, m, t in q("""
      SELECT id, created_at, user_text, top_score FROM c
      WHERE rtype IN ('conservative','ai_fallback') OR (rtype IN ('answer','continue') AND hits = 0)
      ORDER BY created_at DESC, id DESC LIMIT 30""")]
    docs = q(f"""
      SELECT coalesce(e.v->>'title', ''), count(*) FROM c, jsonb_array_elements({_ARR.format('c.sources')}) WITH ORDINALITY AS e(v, i)
      WHERE jsonb_typeof(e.v) = 'object' GROUP BY 1 ORDER BY 2 DESC, min(c.created_at), min(c.id), min(e.i) LIMIT 10""")
    gap_kinds = q(f"""
      SELECT {_OBJ.format("c.quality->'gap'")}->>'kind' AS k, count(*) FROM c WHERE jsonb_typeof(c.quality) = 'object'
      GROUP BY 1 HAVING coalesce({_OBJ.format("c.quality->'gap'")}->>'kind', '') <> '' ORDER BY min(c.created_at), min(c.id)""")
    l5 = q(f"""
      SELECT CASE WHEN i.v ? 'type' THEN i.v->>'type' ELSE '其他' END, count(*)
      FROM c, jsonb_array_elements({_ARR.format("c.quality->'l5'")}) WITH ORDINALITY a(v, n),
              jsonb_array_elements({_ARR.format("a.v->'issues'")}) WITH ORDINALITY i(v, m)
      WHERE jsonb_typeof(c.quality) = 'object' AND jsonb_typeof(a.v) = 'object' AND jsonb_typeof(i.v) = 'object'
      GROUP BY 1 ORDER BY min(c.created_at), min(c.id), min(a.n), min(i.m)""")
    ev = q(f"""
      SELECT e.v->>'kind', count(*) FROM c, jsonb_array_elements({_ARR.format("c.stability->'ev'")}) WITH ORDINALITY e(v, i)
      WHERE jsonb_typeof(c.stability) = 'object' AND jsonb_typeof(e.v) = 'object' GROUP BY 1 ORDER BY min(c.created_at), min(c.id), min(e.i)""")
    units = sorted(u for (u,) in q("SELECT DISTINCT unit FROM c WHERE coalesce(unit, '') <> ''"))
    return {"stats": st, "gaps": gaps, "topDocs": [{"title": t, "n": k} for t, k in docs],
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
                include_test: bool = False, u: User = Me, s: Session = DB, factory=Depends(get_session_factory)):
    b = admin_bot(s, u, bot, {"analyst"})
    filt = (days, unit, type, q, rewritten, include_test)
    n = s.scalar(select(func.count()).select_from(_log_query(b, *filt).with_only_columns(Conversation.id).limit(CSV_MAX).subquery()))
    audit(s, b.workspace_id, u, "logs.export", b.slug, {"rows": n}, bot_id=b.id)
    s.commit()
    return StreamingResponse(_csv_stream(factory, b, filt, n), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{b.slug}-logs.csv"'})


CSV_MAX, CSV_BATCH = 50000, 1000


def _csv_stream(factory, b: Bot, filt: tuple, n: int):
    """邊查邊送：每次讀 1000 筆、寫成 CSV 就送出去，記憶體裡不會同時放上萬筆紀錄。
    用自己的 session（請求的 session 在回應開始送出後就可能被關掉）"""
    buf = io.StringIO()
    w = csv.writer(buf)

    def take() -> str:
        out = buf.getvalue()
        buf.seek(0); buf.truncate()
        return out
    w.writerow([h for _, h in CSV_COLS])
    yield "\ufeff" + take()          # BOM：Excel 才認得 UTF-8
    s = factory()
    try:
        last, sent = None, 0
        while sent < n:
            x = _log_query(b, *filt).options(*(defer(getattr(Conversation, k)) for k in _HEAVY))
            if last:
                x = x.where(tuple_(Conversation.created_at, Conversation.id) > tuple_(literal(last[0]), literal(last[1])))
            rows = s.scalars(x.order_by(Conversation.created_at, Conversation.id).limit(min(CSV_BATCH, n - sent))).all()
            if not rows:
                break
            fb = _feedback(s, b.id, [(c.session, c.turn) for c in rows])
            for c in rows:
                r = log_row(c, fb.get((c.session, c.turn)))
                r["message"], r["skills"], r["rewritten"] = c.user_text, "、".join(r["skills"] or []), "是" if r["rewritten"] else ""
                w.writerow([_csv_safe(r[k]) for k, _ in CSV_COLS])
            sent += len(rows)
            last = (rows[-1].created_at, rows[-1].id)
            s.expunge_all()
            yield take()
    finally:
        s.close()


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


# ───────── §10 流程控制台 ─────────

FLOW_STEP_OF = (("P03", "deid"), ("L4", "triage"), ("P04", "state"), ("P05", "skill"), ("P06", "retrieve"), ("重新排序", "rerank"), ("P07", "assemble"),
                ("A1", "answer"), ("檢查", "check"), ("P10", "final"), ("知識圖譜", "kg"))


def step_key(step) -> str:
    s = str(step or "")
    return next((k for p, k in FLOW_STEP_OF if s.startswith(p)), "")


def flow_stats_of(rows: list[Conversation]) -> dict:
    """每一步的執行次數與平均耗時，加上檢索、重排、知識缺口、生成、檢查的運作數據（對應 Apps Script adminFlowStats）"""
    st: dict[str, dict] = {}
    out = {"turns": len(rows), "ms": 0, "types": {}, "retrieval": {"n": 0, "hit": 0, "top": 0.0, "topN": 0}, "rerank": {"n": 0, "used": 0, "failed": 0, "off": 0},
           "gap": {"n": 0, "byKind": {}, "byOutcome": {}}, "answer": {"n": 0, "rewrite": 0, "format": 0}, "check": {"n": 0, "l5": 0, "severe": 0, "conservative": 0}, "steps": st}
    for r in rows:
        out["types"][r.rtype] = out["types"].get(r.rtype, 0) + 1
        out["ms"] += r.ms or 0
        prev, seen = 0, set()
        for t in r.trace if isinstance(r.trace, list) else []:
            k = step_key(t.get("step"))
            ms = int(t.get("ms") or 0)
            d, prev = max(0, ms - prev), ms
            if not k:
                continue
            o = st.setdefault(k, {"runs": 0, "ms": 0})
            o["ms"] += d
            if k not in seen:
                o["runs"] += 1
                seen.add(k)
            det = t.get("detail")
            if k == "rerank" and isinstance(det, dict):
                out["rerank"]["n"] += 1
                why = str(det.get("why") or "")
                if det.get("used"):
                    out["rerank"]["used"] += 1
                elif "失敗" in why:
                    out["rerank"]["failed"] += 1
                elif "關閉" in why:
                    out["rerank"]["off"] += 1
        if "retrieve" in seen:
            out["retrieval"]["n"] += 1
            out["retrieval"]["hit"] += 1 if (r.hits or 0) > 0 else 0
            out["retrieval"]["top"] += r.top_score or 0
            out["retrieval"]["topN"] += 1
        q = r.quality if isinstance(r.quality, dict) else {}
        g = q.get("gap")
        if isinstance(g, dict):
            out["gap"]["n"] += 1
            out["gap"]["byKind"][g.get("kind")] = out["gap"]["byKind"].get(g.get("kind"), 0) + 1
            out["gap"]["byOutcome"][g.get("outcome")] = out["gap"]["byOutcome"].get(g.get("outcome"), 0) + 1
        if "answer" in seen:
            out["answer"]["n"] += 1
            out["answer"]["rewrite"] += 1 if r.rewritten else 0
            ev = (r.stability or {}).get("ev") or [] if isinstance(r.stability, dict) else []
            out["answer"]["format"] += 1 if any(e.get("at") == "A1" for e in ev if isinstance(e, dict)) else 0
        if "check" in seen:
            out["check"]["n"] += 1
            l5 = q.get("l5") or []
            out["check"]["l5"] += 1 if l5 else 0
            out["check"]["severe"] += 1 if any(any(i.get("severe") for i in x.get("issues") or []) or x.get("relevance") for x in l5) else 0
            out["check"]["conservative"] += 1 if q.get("l5Final") == "保守回答" else 0
    for o in st.values():
        o["avg"] = round(o["ms"] / o["runs"]) if o["runs"] else None
    out["avgMs"] = round(out["ms"] / len(rows)) if rows else None
    out["retrieval"]["top"] = round(out["retrieval"]["top"], 4)
    return out


@router.get("/bots/{bot}/flow-stats")
def flow_stats(bot: str, days: int | None = 30, include_test: bool = False, u: User = Me, s: Session = DB):
    from db.models import ForbiddenRule, Referral, Skill, Synonym
    from services.retrieval import mounts
    b = admin_bot(s, u, bot, {"analyst"})
    rows = s.scalars(_log_query(b, days, None, None, None, None, include_test).order_by(Conversation.created_at.desc()).limit(500)).all()
    out = flow_stats_of(list(reversed(rows)))
    count = lambda model: s.scalar(select(func.count()).select_from(model).where(model.bot_id == b.id)) or 0
    kbs = [m.kb_id for m in mounts(s, b.id)]
    out.update(skills=s.scalar(select(func.count()).select_from(Skill).where(Skill.bot_id == b.id, Skill.enabled)) or 0,
               skillsAlways=s.scalar(select(func.count()).select_from(Skill).where(Skill.bot_id == b.id, Skill.enabled, Skill.always_on)) or 0,
               chunks=s.scalar(select(func.count()).select_from(Chunk).where(Chunk.kb_id.in_(kbs), Chunk.active)) or 0 if kbs else 0,
               synonyms=count(Synonym), forbidden=count(ForbiddenRule), referral=count(Referral))
    return out


@router.post("/bots/{bot}/flow-trial")
async def flow_trial(bot: str, request: Request, u: User = Me, s: Session = DB, llm: LLMClient = Depends(get_llm)):
    """流程控制台「試跑」：跑一次測試對話，只回走過的節點與每一步耗時"""
    r = await test_chat(bot, request, u, s, llm)
    prev, steps = 0, []
    for t in r.get("trace") or []:
        ms = int(t.get("ms") or 0)
        steps.append({"step": t["step"], "key": step_key(t["step"]), "ms": max(0, ms - prev)})
        prev = ms
    return {"type": r["type"], "ms": r["ms"], "steps": steps}
