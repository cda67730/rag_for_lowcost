"""後台 API 第二批（docs/admin-api.md §3 流程、§4 流程設定、§5 範本、§6 Skill 與對照表、邀請碼）。權限都是該 Bot 的 admin（owner 一律可以）。"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError, not_found
from app.perms import require
from app.routes.admin import DB, Me, audit, body, get_bot, uid
from db.models import Bot, FlowSetting, ForbiddenRule, Flow, InviteCode, Referral, Skill, SkillVersion, Synonym, User
from services.templates import apply_template, export_template
from settings.loader import FLOW_OVERRIDABLE
from settings.store import SettingsError, save_flow_settings

router = APIRouter(prefix="/api/admin")


def admin_bot(s: Session, u: User, bot: str, also: set[str] = frozenset()) -> Bot:
    b = get_bot(s, bot)
    require(s, u, b.workspace_id, {"admin", *also}, bot_id=b.id)
    return b


def now():
    return datetime.now(timezone.utc)


# ───────── 流程 ─────────

def flow_out(f: Flow) -> dict:
    return {"id": str(f.id), "bot_id": str(f.bot_id), "kind": f.kind, "name": f.name, "is_default": f.is_default}


def get_flow(s: Session, u: User, flow: str) -> tuple[Flow, Bot]:
    f = s.get(Flow, uid(flow))
    if f is None:
        raise not_found()
    return f, admin_bot(s, u, str(f.bot_id))


@router.get("/bots/{bot}/flows")
def list_flows(bot: str, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot, {"analyst"})
    return {"items": [flow_out(f) for f in s.scalars(select(Flow).where(Flow.bot_id == b.id).order_by(Flow.created_at))]}


@router.post("/bots/{bot}/flows", status_code=201)
async def create_flow(bot: str, request: Request, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    d = await body(request)
    if (d.get("kind") or "chat") != "chat":
        raise ApiError(422, "invalid", "第一版只支援 chat 流程", {"kind": "只能是 chat"})
    name = str(d.get("name") or "").strip()
    if not name:
        raise ApiError(422, "invalid", "欄位有誤", {"name": "必填"})
    f = Flow(bot_id=b.id, kind="chat", name=name)
    s.add(f); s.flush()
    if d.get("copy_from"):
        src, _ = get_flow(s, u, d["copy_from"])
        if src.bot_id != b.id:
            raise not_found()
        for r in s.scalars(select(FlowSetting).where(FlowSetting.flow_id == src.id)):
            s.add(FlowSetting(flow_id=f.id, key=r.key, value=r.value, updated_by=u.id))
    audit(s, b.workspace_id, u, "flow.create", name, bot_id=b.id)
    s.commit()
    return flow_out(f)


@router.patch("/flows/{flow}")
async def patch_flow(flow: str, request: Request, u: User = Me, s: Session = DB):
    f, b = get_flow(s, u, flow)
    d = await body(request)
    if "name" in d:
        if not str(d["name"]).strip():
            raise ApiError(422, "invalid", "欄位有誤", {"name": "必填"})
        f.name = str(d["name"]).strip()
    if d.get("is_default") is True and not f.is_default:
        s.query(Flow).filter(Flow.bot_id == b.id, Flow.is_default).update({"is_default": False})
        s.flush()
        f.is_default = True
    audit(s, b.workspace_id, u, "flow.update", f.name, flow_out(f), bot_id=b.id)
    s.commit()
    return flow_out(f)


@router.delete("/flows/{flow}")
def delete_flow(flow: str, u: User = Me, s: Session = DB):
    f, b = get_flow(s, u, flow)
    if f.is_default:
        raise ApiError(409, "default_flow", "不能刪除預設流程，請先把別的流程設為預設")
    audit(s, b.workspace_id, u, "flow.delete", f.name, bot_id=b.id)
    s.delete(f)
    s.commit()
    return {"ok": True}


@router.get("/flows/{flow}/settings")
def get_flow_settings(flow: str, u: User = Me, s: Session = DB):
    f, _ = get_flow(s, u, flow)
    rows = s.scalars(select(FlowSetting).where(FlowSetting.flow_id == f.id).order_by(FlowSetting.key))
    return {"items": [{"key": r.key, "value": r.value} for r in rows], "overridable": sorted(FLOW_OVERRIDABLE)}


@router.patch("/flows/{flow}/settings")
async def patch_flow_settings(flow: str, request: Request, u: User = Me, s: Session = DB):
    f, _ = get_flow(s, u, flow)
    d = await body(request)
    items = d.get("items")
    if not isinstance(items, list) or not all(isinstance(x, dict) and "key" in x for x in items):
        raise ApiError(422, "invalid", "items 要是 [{key, value}] 陣列")
    try:
        changed = save_flow_settings(s, f.id, {x["key"]: x.get("value") for x in items}, user_id=u.id)
    except SettingsError as e:
        raise ApiError(422, "invalid", "設定值有誤", e.fields)
    s.commit()
    return {"changed": changed}


# ───────── 範本 ─────────

@router.post("/bots/{bot}/template/apply")
async def template_apply(bot: str, request: Request, replace: bool = False, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        out = apply_template(s, b.id, raw, replace=replace, user_id=u.id)
    except ValueError as e:
        raise ApiError(422, "bad_template", str(e))
    s.commit()
    return out


@router.get("/bots/{bot}/template/export")
def template_export(bot: str, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    return JSONResponse(export_template(s, b.id), headers={"Content-Disposition": f'attachment; filename="{b.slug}-template.json"'})


# ───────── Skill ─────────

def skill_out(k: Skill) -> dict:
    return {"id": str(k.id), "key": k.skill_key, "name": k.name, "topics": k.topics, "always": k.always_on, "content": k.content, "enabled": k.enabled,
            "version": k.version, "updated_at": k.updated_at.isoformat() if k.updated_at else None}


def _skill_fields(d: dict) -> dict:
    name = str(d.get("name") or "").strip()
    topics = d.get("topics") if d.get("topics") is not None else []
    if isinstance(topics, str):
        topics = [t.strip() for t in re.split(r"[,，、]", topics) if t.strip()]
    errs = {}
    if not name:
        errs["name"] = "必填"
    if not isinstance(topics, list) or not all(isinstance(t, str) for t in topics):
        errs["topics"] = "要是文字清單"
    if errs:
        raise ApiError(422, "invalid", "欄位有誤", errs)
    return {"name": name, "topics": [t.strip() for t in topics if t.strip()], "always_on": bool(d.get("always")), "content": str(d.get("content") or ""),
            "enabled": bool(d.get("enabled", True))}


def _snapshot(s: Session, k: Skill, u: User) -> None:
    s.add(SkillVersion(skill_id=k.id, version=k.version, name=k.name, topics=k.topics, always_on=k.always_on, content=k.content, updated_by=u.id, updated_at=now()))


def get_skill(s: Session, u: User, skill: str) -> tuple[Skill, Bot]:
    k = s.get(Skill, uid(skill))
    if k is None:
        raise not_found()
    return k, admin_bot(s, u, str(k.bot_id))


def _dup_name(s: Session, bot_id, name: str, skip=None) -> None:
    q = select(Skill.id).where(Skill.bot_id == bot_id, Skill.name == name)
    if skip is not None:
        q = q.where(Skill.id != skip)
    if s.scalar(q):
        raise ApiError(409, "name_taken", "已有同名的 Skill", {"name": "重複"})


@router.get("/bots/{bot}/skills")
def list_skills(bot: str, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    return {"items": [skill_out(k) for k in s.scalars(select(Skill).where(Skill.bot_id == b.id).order_by(Skill.skill_key))]}


@router.post("/bots/{bot}/skills", status_code=201)
async def create_skill(bot: str, request: Request, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    f = _skill_fields(await body(request))
    _dup_name(s, b.id, f["name"])
    n = len(s.scalars(select(Skill.id).where(Skill.bot_id == b.id)).all()) + 1
    key = f"S{n:02d}"
    while s.scalar(select(Skill.id).where(Skill.bot_id == b.id, Skill.skill_key == key)):
        n += 1
        key = f"S{n:02d}"
    k = Skill(bot_id=b.id, skill_key=key, version=1, updated_by=u.id, updated_at=now(), **f)
    s.add(k); s.flush()
    _snapshot(s, k, u)
    audit(s, b.workspace_id, u, "skill.create", k.name, bot_id=b.id)
    s.commit()
    return skill_out(k)


@router.put("/skills/{skill}")
async def update_skill(skill: str, request: Request, u: User = Me, s: Session = DB):
    k, b = get_skill(s, u, skill)
    f = _skill_fields(await body(request))
    _dup_name(s, b.id, f["name"], skip=k.id)
    before = skill_out(k)
    for a, v in f.items():
        setattr(k, a, v)
    k.version, k.updated_by, k.updated_at = k.version + 1, u.id, now()
    s.flush()
    _snapshot(s, k, u)
    audit(s, b.workspace_id, u, "skill.update", k.name, {"before_version": before["version"], "after_version": k.version}, bot_id=b.id)
    s.commit()
    return skill_out(k)


@router.get("/skills/{skill}/versions")
def skill_versions(skill: str, u: User = Me, s: Session = DB):
    k, _ = get_skill(s, u, skill)
    rows = s.scalars(select(SkillVersion).where(SkillVersion.skill_id == k.id).order_by(SkillVersion.version.desc()))
    return {"items": [{"version": v.version, "name": v.name, "topics": v.topics, "always": v.always_on, "content": v.content,
                       "updated_at": v.updated_at.isoformat()} for v in rows]}


@router.post("/skills/{skill}/restore")
async def restore_skill(skill: str, request: Request, u: User = Me, s: Session = DB):
    k, b = get_skill(s, u, skill)
    d = await body(request)
    v = s.get(SkillVersion, (k.id, int(d.get("version") or 0)))
    if v is None:
        raise not_found()
    _dup_name(s, b.id, v.name, skip=k.id)
    k.name, k.topics, k.always_on, k.content = v.name, list(v.topics), v.always_on, v.content
    k.version, k.updated_by, k.updated_at = k.version + 1, u.id, now()
    s.flush()
    _snapshot(s, k, u)
    audit(s, b.workspace_id, u, "skill.restore", k.name, {"from_version": v.version, "new_version": k.version}, bot_id=b.id)
    s.commit()
    return skill_out(k)


@router.delete("/skills/{skill}")
def delete_skill(skill: str, u: User = Me, s: Session = DB):
    k, b = get_skill(s, u, skill)
    audit(s, b.workspace_id, u, "skill.delete", k.name, bot_id=b.id)
    s.delete(k)
    s.commit()
    return {"ok": True}


# ───────── 對照表（整份讀寫） ─────────

LISTS = {
    "synonyms": (Synonym, ("word", "concept"), lambda r: {"word": r.word, "concept": r.concept}, (Synonym.word, Synonym.concept)),
    "forbidden": (ForbiddenRule, ("rule",), lambda r: {"rule": r.rule, "kind": r.kind, "note": r.note}, (ForbiddenRule.rule,)),
    "referrals": (Referral, ("category",), lambda r: {"category": r.category, "who": r.who, "contact": r.contact, "now": r.now_action}, (Referral.sort, Referral.category)),
}


def _list_row(kind: str, bot_id, i: int, x) -> object:
    if not isinstance(x, dict):
        raise ValueError("要是物件")
    g = lambda k: str(x.get(k) or "").strip()
    if kind == "synonyms":
        if not g("word") or not g("concept"):
            raise ValueError("word、concept 必填")
        return Synonym(bot_id=bot_id, word=g("word"), concept=g("concept"))
    if kind == "forbidden":
        if not g("rule"):
            raise ValueError("rule 必填")
        kd = g("kind") or "word"
        if kd not in ("word", "regex"):
            raise ValueError("kind 只能是 word 或 regex")
        if kd == "regex":
            try:
                re.compile(g("rule"))
            except re.error as e:
                raise ValueError(f"正規式寫法有誤：{e}")
        return ForbiddenRule(bot_id=bot_id, rule=g("rule"), kind=kd, note=g("note"))
    if not g("category"):
        raise ValueError("category 必填")
    return Referral(bot_id=bot_id, category=g("category"), who=g("who"), contact=g("contact"), now_action=g("now"), sort=i)


def _make_list_routes(kind: str):
    model, uniq, out, order = LISTS[kind]

    def read(bot: str, u: User = Me, s: Session = DB):
        b = admin_bot(s, u, bot)
        return {"items": [out(r) for r in s.scalars(select(model).where(model.bot_id == b.id).order_by(*order))]}

    async def write(bot: str, request: Request, u: User = Me, s: Session = DB):
        b = admin_bot(s, u, bot)
        d = await body(request)
        items = d.get("items")
        if not isinstance(items, list):
            raise ApiError(422, "invalid", "items 要是陣列")
        rows, errs, seen = [], {}, set()
        for i, x in enumerate(items):
            try:
                r = _list_row(kind, b.id, i, x)
            except ValueError as e:
                errs[f"items.{i}"] = str(e)
                continue
            key = tuple(getattr(r, c) for c in uniq)
            if key in seen:
                errs[f"items.{i}"] = "重複"
                continue
            seen.add(key)
            rows.append(r)
        if errs:
            raise ApiError(422, "invalid", "清單有誤", errs)
        before = len(s.scalars(select(model.id).where(model.bot_id == b.id)).all())
        s.query(model).filter(model.bot_id == b.id).delete()
        s.add_all(rows)
        audit(s, b.workspace_id, u, f"{kind}.replace", b.slug, {"before": before, "after": len(rows)}, bot_id=b.id)
        s.commit()
        return read(bot, u, s)

    router.add_api_route(f"/bots/{{bot}}/{kind}", read, methods=["GET"], name=f"get_{kind}")
    router.add_api_route(f"/bots/{{bot}}/{kind}", write, methods=["PUT"], name=f"put_{kind}")


for _k in LISTS:
    _make_list_routes(_k)


# ───────── 邀請碼 ─────────

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去掉容易看錯的 0O1I


def code_out(c: InviteCode) -> dict:
    return {"id": str(c.id), "code": c.code, "unit": c.unit, "enabled": c.enabled, "trace": c.trace,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None}


def _expires(v):
    if v in (None, ""):
        return None
    try:
        t = datetime.fromisoformat(str(v))
    except ValueError:
        raise ApiError(422, "invalid", "欄位有誤", {"expires_at": "日期格式要是 ISO 8601"})
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@router.get("/bots/{bot}/invite-codes")
def list_codes(bot: str, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    return {"items": [code_out(c) for c in s.scalars(select(InviteCode).where(InviteCode.bot_id == b.id).order_by(InviteCode.created_at))]}


@router.post("/bots/{bot}/invite-codes", status_code=201)
async def create_code(bot: str, request: Request, u: User = Me, s: Session = DB):
    b = admin_bot(s, u, bot)
    d = await body(request)
    code = str(d.get("code") or "").strip().upper()
    if code and not re.fullmatch(r"[A-Z0-9_-]{4,32}", code):
        raise ApiError(422, "invalid", "欄位有誤", {"code": "4–32 個英數字、底線或連字號"})
    if not code:
        while True:
            code = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
            if not s.scalar(select(InviteCode.id).where(InviteCode.bot_id == b.id, InviteCode.code == code)):
                break
    elif s.scalar(select(InviteCode.id).where(InviteCode.bot_id == b.id, InviteCode.code == code)):
        raise ApiError(409, "code_taken", "這組邀請碼已存在", {"code": "已存在"})
    c = InviteCode(bot_id=b.id, code=code, unit=str(d.get("unit") or "").strip(), trace=bool(d.get("trace")), expires_at=_expires(d.get("expires_at")))
    s.add(c)
    audit(s, b.workspace_id, u, "invite.create", c.unit or "", bot_id=b.id)      # 邀請碼本身不寫進稽核
    s.commit()
    return code_out(c)


def get_code(s: Session, u: User, cid: str) -> tuple[InviteCode, Bot]:
    c = s.get(InviteCode, uid(cid))
    if c is None:
        raise not_found()
    return c, admin_bot(s, u, str(c.bot_id))


@router.patch("/invite-codes/{cid}")
async def patch_code(cid: str, request: Request, u: User = Me, s: Session = DB):
    c, b = get_code(s, u, cid)
    d = await body(request)
    for f in ("enabled", "trace"):
        if f in d:
            setattr(c, f, bool(d[f]))
    if "unit" in d:
        c.unit = str(d["unit"] or "").strip()
    if "expires_at" in d:
        c.expires_at = _expires(d["expires_at"])
    audit(s, b.workspace_id, u, "invite.update", c.unit, {"enabled": c.enabled, "trace": c.trace}, bot_id=b.id)
    s.commit()
    return code_out(c)


@router.delete("/invite-codes/{cid}")
def delete_code(cid: str, u: User = Me, s: Session = DB):
    c, b = get_code(s, u, cid)
    audit(s, b.workspace_id, u, "invite.delete", c.unit, bot_id=b.id)
    s.delete(c)
    s.commit()
    return {"ok": True}
