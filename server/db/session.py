"""資料庫連線。DATABASE_URL 從環境變數讀（見 server/.env.example）。"""
import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL 未設定（見 server/.env.example）")
    return url


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(database_url(), pool_pre_ping=True)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def session() -> Session:
    engine()
    assert _Session is not None
    return _Session()


def get_session() -> Iterator[Session]:
    """FastAPI 相依注入用：每個請求一個 session，結束自動關閉"""
    with session() as s:
        yield s
