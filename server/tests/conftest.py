"""測試用資料庫：每次測試階段建一個全新的資料庫、跑完遷移，結束後刪掉。

需要環境變數 TEST_DATABASE_URL 指向一台 PostgreSQL（含 pgvector），例如
  postgresql+psycopg://postgres@/postgres?host=/tmp&port=55432
沒設就略過需要資料庫的測試。
"""
import os
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

HERE = os.path.dirname(__file__)


@pytest.fixture(scope="session")
def db_url():
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("未設定 TEST_DATABASE_URL，略過資料庫測試")
    name = "rag_test_" + uuid.uuid4().hex[:8]
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    cfg = Config(os.path.join(HERE, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "..", "db", "migrations"))
    os.environ["DATABASE_URL"] = url
    command.upgrade(cfg, "head")
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine(db_url):
    e = create_engine(db_url)
    yield e
    e.dispose()


@pytest.fixture
def db(engine):
    """每個測試一個 session；測試結束整個交易回滾，測試之間互不影響"""
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield s
    s.close()
    trans.rollback()
    conn.close()
