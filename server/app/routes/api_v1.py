"""給其他系統呼叫的 API（用 API 金鑰）。

POST /api/v1/ask
  Header：Authorization: Bearer <金鑰>
  Body：{"message": "...", "session": "對話代號", "history": [...], "context": {"欄位": "值"}}
  context 只收後台「API」分頁列出的欄位，只用在這一回合、不存進資料庫；回答照常寫進對話紀錄
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import limits
from app.routes.chat import body, get_llm
from db.models import Bot, Flow
from db.session import get_session
from graph.chat_graph import Deps, run_chat
from services import apikeys
from services.llm import LLMClient, LLMError
from settings.loader import js_str, load_config
from sqlalchemy import select

log = logging.getLogger("api_v1")
router = APIRouter(prefix="/api/v1")
MAX_MESSAGE = 4000
MAX_HISTORY = 40


def err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=status)


def gate(session: Session, token: str) -> dict:
    k = apikeys.verify(session, token)
    if k is None:
        session.commit()
        return {"status": 401, "error": "金鑰無效或已停用"}
    bot = session.get(Bot, k.bot_id)
    if bot is None or bot.disabled:
        session.commit()
        return {"status": 403, "error": "機器人已停用"}
    flow = session.scalar(select(Flow).where(Flow.bot_id == bot.id, Flow.kind == "chat", Flow.is_default))
    cfg = load_config(session, bot.id, flow.id if flow else None)
    if not limits.hour_ok(session, str(bot.id), "key_" + str(k.id), cfg.int("hourly_limit") or 300):
        session.commit()
        return {"status": 429, "error": "使用次數過多，請稍後再試"}
    if not limits.day_ok(session, str(bot.id), cfg.int("daily_limit") or 500):
        session.commit()
        return {"status": 429, "error": "今日額度已用完"}
    session.commit()
    return {"bot": bot, "flow": flow, "unit": "API：" + (k.name or k.prefix)}


@router.post("/ask")
async def ask(request: Request, session: Session = Depends(get_session), llm: LLMClient = Depends(get_llm)):
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    try:
        req = await body(request)
        msg = js_str(req.get("message")).strip()
        if not msg:
            return err(422, "message 不能空白")
        if len(msg) > MAX_MESSAGE:
            return err(422, f"message 最多 {MAX_MESSAGE} 字")
        sess = js_str(req.get("session")).strip()[:100]
        if not sess:
            return err(422, "session 不能空白（同一段對話用同一個代號）")
        hist = req.get("history") if isinstance(req.get("history"), list) else []
        g = await asyncio.to_thread(gate, session, token)
        if "error" in g:
            return err(g["status"], g["error"])
        bot, flow = g["bot"], g["flow"]
        turn_req = {k: req.get(k) for k in ("nameMap", "followups", "answered", "turn")}
        turn_req.update(message=msg, session=sess, history=hist[-MAX_HISTORY:], context=req.get("context"), unit=g["unit"], invite_code_id=None)
        res = await run_chat(Deps(session, llm, bot.id, flow.id if flow else None, with_trace=False), turn_req)
        await asyncio.to_thread(session.commit)
        res.pop("trace", None)
        return {"ok": True, **res}
    except LLMError as e:
        log.error("api v1 ask：模型呼叫失敗 %s", type(e).__name__)
        await asyncio.to_thread(session.rollback)
        return err(503, "模型暫時無法使用，請稍後再試")
    except Exception as e:   # 不把內部錯誤細節（也不把呼叫端資料）寫進日誌或回應
        log.error("api v1 ask 失敗：%s", type(e).__name__)
        await asyncio.to_thread(session.rollback)
        return err(500, "伺服器發生錯誤，請稍後再試")
