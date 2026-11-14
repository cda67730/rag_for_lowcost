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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def engine() -> Engine:
    global _engine, _Session
    if _engine is None:
        # 連線只在讀寫的短時間持有（等模型時會先還回去），池子不用很大；等不到連線時 10 秒內就失敗，不要讓請求一直排隊
        _engine = create_engine(database_url(), pool_pre_ping=True, pool_size=_env_int("DB_POOL_SIZE", 10),
                                max_overflow=_env_int("DB_MAX_OVERFLOW", 20), pool_timeout=_env_int("DB_POOL_TIMEOUT", 10))
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
