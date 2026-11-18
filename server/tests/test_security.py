"""安全：真正的使用者 IP（不信使用者自己填的部分）、請求大小上限、登入的 IP 限制、聊天訊息長度、解壓炸彈、CSP"""
import io
import zipfile

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.netguard import BodyLimit, ProxyHeaders, Trusted, body_limit
from services import parsers
from test_api_admin import PW, env  # noqa: F401


def test_trusted_any_takes_rightmost_public():
    t = Trusted("*")
    assert t.client("6.6.6.6, 61.216.10.9") == "61.216.10.9"                 # 使用者自己填的 6.6.6.6 不採用
    assert t.client("6.6.6.6, 61.216.10.9, 10.0.0.5, 100.64.0.2") == "61.216.10.9"   # 跳過平台內部的代理
    assert t.client("61.216.10.9") == "61.216.10.9"
    assert t.client("10.0.0.1") == "10.0.0.1" and t.client("junk, 1.2.3.4") == "1.2.3.4" and t.client("1.2.3.4, junk") is None
    assert t.client("[2001:db8::1]:443, 61.216.10.7:5555") == "61.216.10.7"


def test_trusted_list_skips_known_proxies():
    t = Trusted("127.0.0.1, 10.1.0.0/16")
    assert "10.1.2.3" in t and "127.0.0.1" in t and "8.8.8.8" not in t and None not in t
    assert t.client("6.6.6.6, 198.51.100.4, 10.1.0.9") == "198.51.100.4"
    assert t.client("10.1.0.2, 127.0.0.1") == "10.1.0.2"


def _probe(trusted):
    a = FastAPI()

    @a.get("/ip")
    def ip(request: Request):
        return {"ip": request.client.host, "scheme": request.url.scheme}
    return TestClient(ProxyHeaders(a, trusted), client=("10.0.0.2", 1234))


def test_proxy_middleware():
    c = _probe("*")
    assert c.get("/ip", headers={"X-Forwarded-For": "6.6.6.6, 61.216.10.9", "X-Forwarded-Proto": "https"}).json() == {"ip": "61.216.10.9", "scheme": "https"}
    assert c.get("/ip").json()["ip"] == "10.0.0.2"
    c = _probe("127.0.0.1")       # 直接連進來的不是信任的代理：不看標頭
    assert c.get("/ip", headers={"X-Forwarded-For": "6.6.6.6"}).json()["ip"] == "10.0.0.2"


def test_spoofed_ip_cannot_dodge_invite_lockout(monkeypatch):
    """換 X-Forwarded-For 不能繞過按 IP 的限制：中介層取到的都是同一個真實 IP"""
    c = _probe("*")
    seen = {c.get("/ip", headers={"X-Forwarded-For": f"9.9.9.{i}, 61.216.10.50"}).json()["ip"] for i in range(5)}
    assert seen == {"61.216.10.50"}


def _echo():
    a = FastAPI()

    @a.post("/{p:path}")
    async def echo(request: Request):
        return {"n": len(await request.body())}
    return TestClient(BodyLimit(a))


def test_body_limit(monkeypatch):
    monkeypatch.setenv("MAX_CHAT_BODY_MB", "0.01")
    monkeypatch.setenv("MAX_BODY_MB", "0.02")
    c = _echo()
    assert body_limit("/api/chat") == 10485 and body_limit("/api/admin/kbs/x/documents") == 55 * 1024 * 1024
    assert c.post("/api/chat", content=b"x" * 5000).json() == {"n": 5000}
    r = c.post("/api/chat", content=b"x" * 20000)
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    assert c.post("/api/admin/bots", content=b"x" * 15000).status_code == 200
    assert c.post("/api/admin/bots", content=b"x" * 30000).status_code == 413

    def gen():                     # 分段傳送、沒有 Content-Length
        for _ in range(10):
            yield b"x" * 3000
    assert c.post("/api/chat", content=gen()).status_code == 413

    def small():
        for _ in range(3):
            yield b"x" * 3000
    assert c.post("/api/admin/x", content=small()).json() == {"n": 9000}


def test_main_app_rejects_big_chat_body():
    c = TestClient(app)
    r = c.post("/api/chat", content=b"{" + b" " * (600 * 1024) + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_chat_message_too_long(env):  # noqa: F811
    c = env[0]
    r = c.post("/api/chat", json={"action": "chat", "bot": "nope", "message": "字" * 4001, "session": "s"})
    assert "太長" in r.json()["error"]


def test_login_ip_limit_and_offloaded(env):  # noqa: F811
    c = env[0]
    for i in range(auth.IP_MAX_FAILS):
        assert c.post("/api/admin/auth/login", json={"login": f"nobody{i}", "password": "x"}).status_code == 401
    r = c.post("/api/admin/auth/login", json={"login": "owner", "password": PW})
    assert r.status_code == 429 and r.json()["code"] == "ip_locked"                  # 換帳號也一樣擋（擋常見密碼輪流試）
    auth._ip_fails.clear()
    assert c.post("/api/admin/auth/login", json={"login": "owner", "password": PW}).status_code == 200
    assert c.post("/api/admin/auth/login", json={"login": "owner", "password": "x" * 5000}).status_code == 401   # 超長密碼不跑 argon2


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in entries:
            z.writestr(n, d)
    return buf.getvalue()


def test_zip_bomb_rejected(monkeypatch):
    bomb = _zip([("[Content_Types].xml", "<x/>"), ("word/document.xml", b"\0" * (20 * 1024 * 1024))])
    with pytest.raises(parsers.ParseError, match="解壓後太大"):
        parsers.detect("a.docx", bomb)
    monkeypatch.setattr(parsers, "MAX_UNZIPPED", 1024)
    with pytest.raises(parsers.ParseError, match="解壓後太大"):
        parsers.detect("a.docx", _zip([("word/document.xml", "a" * 2000)]))
    monkeypatch.setattr(parsers, "MAX_UNZIPPED", 300 * 1024 * 1024)
    assert parsers.detect("a.docx", _zip([("word/document.xml", "<w:document>你好</w:document>")])) == "docx"


def test_csp_headers():
    c = TestClient(app)
    a = c.get("/admin/").headers["content-security-policy"]
    ch = c.get("/chat/").headers["content-security-policy"]
    assert "script-src 'self'" in a and "frame-ancestors 'none'" in a and "unsafe-eval" not in a
    assert "frame-ancestors" not in ch and "connect-src 'self'" in ch
