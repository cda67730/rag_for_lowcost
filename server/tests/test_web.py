"""伺服器直接提供聊天頁（/chat/）與後台（/admin/）。前端原檔不改：config.js 由伺服器在後面加上「後端＝同一個網址」。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import web
from app.main import app

WEB = Path(__file__).resolve().parent.parent.parent / "web"


def test_serves_chat_and_admin():
    c = TestClient(app)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/chat/"
    assert c.get("/chat", follow_redirects=False).headers["location"] == "/chat/"
    r = c.get("/chat/")
    assert r.status_code == 200 and "chat.js" in r.text and r.headers["x-content-type-options"] == "nosniff" and "x-frame-options" not in r.headers
    r = c.get("/admin/")
    assert r.status_code == 200 and "lib.js" in r.text and r.headers["x-frame-options"] == "DENY"
    assert c.get("/chat/trace.js").status_code == 200 and c.get("/admin/views/system.js").status_code == 200   # 後台引用 ../chat/trace.js
    assert c.get("/chat/../server/.env").status_code == 404 and c.get("/admin/nope.js").status_code == 404
    paths = [getattr(r, "path", "") for r in app.routes]
    assert paths[-2:] == ["/chat", "/admin"] and paths.index("/healthz") < paths.index("/chat/config.js")   # API 路由在前，靜態檔掛在最後
    assert "/api/admin/auth/login" in c.get("/openapi.json").json()["paths"]


def test_config_js_points_to_same_origin():
    c = TestClient(app)
    chat = c.get("/chat/config.js")
    orig = (WEB / "chat" / "config.js").read_text(encoding="utf-8")
    assert chat.text.startswith(orig) and 'BACKEND = "server"' in chat.text and "API_URL = location.origin" in chat.text
    assert chat.headers["content-type"].startswith("text/javascript") and chat.headers["cache-control"] == "no-cache"
    adm = c.get("/admin/config.js").text
    assert adm.startswith((WEB / "admin" / "config.js").read_text(encoding="utf-8")) and adm.rstrip().endswith("window.ADMIN_CONFIG.API_URL = location.origin;")
    assert 'BACKEND: "apps-script"' in orig            # 原檔（GitHub Pages 用）沒被改


def test_disabled_or_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SERVE_WEB", "false")
    a = FastAPI()
    assert web.mount_web(a) is None and TestClient(a).get("/chat/").status_code == 404
    monkeypatch.setenv("SERVE_WEB", "true")
    monkeypatch.setenv("WEB_DIR", str(tmp_path))       # 找不到前端檔案：只有 API
    assert web.mount_web(FastAPI()) is None
    monkeypatch.setenv("WEB_DIR", str(WEB))
    assert web.mount_web(FastAPI()) == WEB.resolve()
