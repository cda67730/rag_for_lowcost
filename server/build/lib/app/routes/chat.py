"""聊天頁用的 API（docs/api.md）。行為對應 apps-script/Code.gs 的 doPost。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import limits
from db.models import Bot, Feedback, Flow, InviteCode
from db.session import get_session
from graph.chat_graph import Deps, run_chat
from services.llm import LLMClient
from settings.loader import BotConfig, js_str, load_config

log = logging.getLogger("chat")
router = APIRouter(prefix="/api")
FAIL_DELAY = 0.8   # 邀請碼錯誤時拖慢猜測速度（秒）

_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    """金鑰只從環境變數讀；整個程序共用一個連線池"""
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm


async def body(request: Request) -> dict:
    """接受 application/json 與 text/plain（Apps Script 版前端為了避開 CORS 預檢用 text/plain）"""
    raw = await request.body()
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def fail(msg: str) -> dict:
    return {"ok": False, "error": msg}


def resolve(session: Session, req: dict) -> tuple[Bot, Flow | None] | None:
    slug = js_str(req.get("bot")).strip()
    bot = session.scalar(select(Bot).where(Bot.slug == slug, Bot.disabled.is_(False))) if slug else None
    if bot is None:
        return None
    q = select(Flow).where(Flow.bot_id == bot.id, Flow.kind == "chat")
    fid = js_str(req.get("flow")).strip()
    flow = None
    if fid:
        try:
            flow = session.scalar(q.where(Flow.id == fid))
        except Exception:   # 不是合法 uuid
            session.rollback()
            flow = None
        if flow is None:
            return None
    else:
        flow = session.scalar(q.where(Flow.is_default))
    return bot, flow


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def verify(session: Session, cfg: BotConfig, bot: Bot, code, ip: str) -> dict:
    """回傳 {info} 或 {error}；info：unit、trace、open、invite_id。錯誤時 slow=True，由呼叫端拖慢回應"""
    if not cfg.flag("enable_invite"):
        return {"info": {"unit": "公開", "trace": False, "open": True, "invite_id": None}}
    c = js_str(code).strip().upper()
    if limits.code_locked(session, str(bot.id), ip, c):
        return {"error": "嘗試次數過多，請 15 分鐘後再試"}
    hit = None
    if c:
        hit = session.scalar(select(InviteCode).where(InviteCode.bot_id == bot.id, func.upper(func.btrim(InviteCode.code)) == c, InviteCode.enabled,
                                                      (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at > datetime.now(timezone.utc))))
    if hit is None:
        limits.code_fail(session, str(bot.id), ip)
        session.commit()
        return {"error": "邀請碼不正確或已停用", "slow": True}
    limits.code_ok(session, str(bot.id), ip, c)
    return {"info": {"unit": hit.unit or hit.code, "trace": hit.trace, "open": False, "invite_id": hit.id}}


def gate(action: str, req: dict, ip: str, session: Session) -> dict:
    """聊天前的資料庫工作（找 bot、讀設定、驗邀請碼、算次數）。同步執行、在執行緒裡跑，不卡事件迴圈；
    結束時交易已提交，連線還給連線池"""
    r = resolve(session, req)
    if r is None:
        return {"error": "服務暫停"}
    bot, flow = r
    cfg = load_config(session, bot.id, flow.id if flow else None)
    if action == "ui":
        session.commit()
        return {"done": {"ok": True, "ui": cfg.ui_config(), "invite": cfg.flag("enable_invite")}}
    vc = verify(session, cfg, bot, req.get("code"), ip)
    if "error" in vc:
        session.commit()
        return vc
    info = vc["info"]
    who = "ip_" + js_str(req.get("session"))[:40] if info["open"] else js_str(req.get("code")).strip().upper()
    if not limits.hour_ok(session, str(bot.id), who, cfg.int("hourly_limit") or 300):
        session.commit()
        return {"error": "使用次數過多，請稍後再試"}
    if action == "chat" and not limits.day_ok(session, str(bot.id), cfg.int("daily_limit") or 500):
        session.commit()
        return {"error": "今日額度已用完，請明天再試"}
    if action == "check":
        session.commit()
        return {"done": {"ok": True, "botName": cfg.str("bot_name") or "AI 助手", "welcome": cfg.str("welcome"), "trace": info["trace"], "ui": cfg.ui_config()}}
    if action == "feedback":
        try:
            turn = int(float(req.get("turn") or 0))
        except (TypeError, ValueError):
            turn = 0
        session.add(Feedback(bot_id=bot.id, session=js_str(req.get("session"))[:100], turn=turn, kind=js_str(req.get("kind"))[:100], note=js_str(req.get("note"))[:500]))
        session.commit()
        return {"done": {"ok": True}}
    session.commit()
    return {"bot": bot, "flow": flow, "info": info}


async def handle(action: str, request: Request, session: Session, llm: LLMClient) -> dict:
    req = await body(request)
    if action not in ("ui", "check", "chat", "feedback"):
        return fail("未知的操作")
    if action == "chat" and not js_str(req.get("message")).strip():
        return fail("請輸入內容")
    g = await asyncio.to_thread(gate, action, req, client_ip(request), session)
    if "error" in g:
        if g.get("slow"):
            await asyncio.sleep(FAIL_DELAY)     # 邀請碼錯誤：拖慢猜測速度，等待時不佔連線
        return fail(g["error"])
    if "done" in g:
        return g["done"]
    bot, flow, info = g["bot"], g["flow"], g["info"]
    turn_req = {k: req.get(k) for k in ("message", "session", "history", "nameMap", "followups", "answered", "turn")}
    turn_req.update(unit=info["unit"], invite_code_id=info["invite_id"])
    res = await run_chat(Deps(session, llm, bot.id, flow.id if flow else None, with_trace=info["trace"]), turn_req)
    await asyncio.to_thread(session.commit)
    return {"ok": True, **res}


def _route(action: str):
    async def endpoint(request: Request, session: Session = Depends(get_session), llm: LLMClient = Depends(get_llm)):
        try:
            return await handle(action, request, session, llm)
        except Exception:   # 不把內部錯誤細節回給使用者
            log.exception("api %s 失敗", action)
            await asyncio.to_thread(session.rollback)
            return fail("伺服器發生錯誤，請稍後再試")
    endpoint.__name__ = f"api_{action}"
    return endpoint


for _a in ("ui", "check", "chat", "feedback"):
    router.add_api_route(f"/{_a}", _route(_a), methods=["POST"])
