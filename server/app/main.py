"""FastAPI 入口：健康檢查、聊天 API（app/routes/chat.py）；後台路由依 docs/architecture.md 的順序陸續加入。"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.routes.chat import router as chat_router
from db.session import engine

app = FastAPI(title="RAG 知識問答", docs_url=None, redoc_url=None)
app.include_router(chat_router)

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
