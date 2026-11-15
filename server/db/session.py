"""資料庫連線。DATABASE_URL 從環境變數讀（見 server/.env.example）。"""
import os
from collections.abc import Iterator
from pathlib import Path

from dotenv import load_dotenv

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

def _load_env_file(p: Path) -> None:
    """server/.env：伺服器、後台指令、遷移都會讀到（這個模組是共同的入口）。已經設定的環境變數優先，Docker／systemd 傳進來的值不會被蓋掉。
    用 UTF-8 讀（記事本存成「UTF-8 含 BOM」也可以）；Windows 上被存成 ANSI（Big5）時改用系統編碼讀"""
    if not p.is_file():
        return
    try:
        load_dotenv(p, override=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        import locale
        load_dotenv(p, override=False, encoding=locale.getpreferredencoding(False))


_load_env_file(Path(__file__).resolve().parent.parent / ".env")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL 未設定（見 server/.env.example）")
    # 雲端資料庫（Supabase、Neon…）給的連線字串多半是 postgres:// 或 postgresql://：改用這個專案的驅動（psycopg 3）
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
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


def release(s: Session) -> None:
    """結束目前的交易、把連線還給連線池。在等模型的那段時間之前呼叫：等幾秒到幾分鐘都不佔資料庫連線"""
    if s.in_transaction():
        s.commit()
