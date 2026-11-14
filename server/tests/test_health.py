from fastapi.testclient import TestClient


def test_healthz_ok(db_url, monkeypatch):
    import db.session as dbs
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(dbs, "_engine", None)
    from app.main import app
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["pgvector"] and body["migration"]


def test_healthz_db_down(monkeypatch):
    import db.session as dbs
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none?connect_timeout=1")
    monkeypatch.setattr(dbs, "_engine", None)
    from app.main import app
    r = TestClient(app).get("/healthz")
    assert r.status_code == 503 and r.json()["ok"] is False
    assert "127.0.0.1" not in r.text   # 不洩漏連線資訊
