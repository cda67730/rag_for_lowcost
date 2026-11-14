"""FastAPI 入口：健康檢查、聊天 API（app/routes/chat.py）、後台 API（app/routes/admin.py）。"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.errors import ApiError, handler
from app.routes.admin import fail_stale_jobs
from app.routes.admin import router as admin_router
from app.routes.admin_bot import router as admin_bot_router
from app.routes.admin_ops import router as admin_ops_router
from app.routes.admin_kg import router as admin_kg_router
from app.routes.chat import router as chat_router
from db.session import engine, session



@asynccontextmanager
async def lifespan(app):
    try:
        with session() as s:
            n = fail_stale_jobs(s)
            if n:
                logging.getLogger("app").warning("重新啟動：%d 個中斷的背景工作標為失敗", n)
    except Exception:   # 資料庫還沒好（例如遷移前）不擋啟動，/healthz 會回 503
        logging.getLogger("app").exception("啟動時無法整理背景工作")
    yield


app = FastAPI(title="RAG 知識問答", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_exception_handler(ApiError, handler)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(admin_bot_router)
app.include_router(admin_ops_router)
app.include_router(admin_kg_router)

origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])


@app.get("/healthz")
def healthz():
    """資料庫連得上、pgvector 已安裝、遷移已跑過，才算健康"""
    try:
        with engine().connect() as c:
            vector = c.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
            migrated = c.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
            rev = c.scalar(text("SELECT version_num FROM alembic_version")) if migrated else None
    except Exception as e:   # 不回傳連線字串等細節
        return JSONResponse({"ok": False, "error": "資料庫無法連線", "detail": type(e).__name__}, status_code=503)
    ok = bool(vector and rev)
    return JSONResponse({"ok": ok, "pgvector": vector, "migration": rev}, status_code=200 if ok else 503)
