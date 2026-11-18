"""兩個 ASGI 中介層：

1. ProxyHeaders：從 X-Forwarded-For 取「真正的」使用者 IP。
   取代 uvicorn 內建的 --proxy-headers：uvicorn 在 FORWARDED_ALLOW_IPS=* 時取最左邊那個，
   而最左邊是使用者自己可以填的，換個假 IP 就能繞過按 IP 的限制。
   這裡從右邊往左找：反向代理是把位址「接在後面」，所以右邊才是可信的。
   - 直接連進來的對象要在 FORWARDED_ALLOW_IPS 裡（預設只有 127.0.0.1），否則不看這個標頭
   - FORWARDED_ALLOW_IPS=*（雲端平台）：跳過右邊的內網位址，取第一個公網位址
   - 列出代理位址（可用 CIDR）：跳過右邊這些代理，取第一個不在名單裡的
2. BodyLimit：請求內容的大小上限，超過回 413，不把整包讀進記憶體。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re

UPLOAD_PATH = re.compile(r"^/api/admin/kbs/[^/]+/documents$")
CHAT_PATHS = ("/api/chat", "/api/v1/")


def _mb(name: str, default: float) -> int:
    try:
        return int(float(os.environ.get(name, default)) * 1024 * 1024)
    except ValueError:
        return int(default * 1024 * 1024)


def body_limit(path: str) -> int:
    if UPLOAD_PATH.match(path):
        return _mb("MAX_UPLOAD_MB", 55)          # 解析器本身上限 50MB，留一點給 multipart 外框
    if path.startswith(CHAT_PATHS):
        return _mb("MAX_CHAT_BODY_MB", 0.5)
    return _mb("MAX_BODY_MB", 10)


def _ip(s: str):
    s = s.strip().strip('"')
    if s.startswith("["):                        # [IPv6]:port
        s = s[1:].split("]", 1)[0]
    elif s.count(":") == 1:                      # IPv4:port
        s = s.split(":", 1)[0]
    try:
        return ipaddress.ip_address(s)
    except ValueError:
        return None


def _internal(ip) -> bool:
    """不是公網位址：內網、本機、link-local、電信共用位址（100.64/10，雲端平台內部常用）、保留位址"""
    return not ip.is_global


class Trusted:
    def __init__(self, raw: str):
        items = [x.strip() for x in raw.split(",") if x.strip()]
        self.any = "*" in items
        self.nets = []
        for x in items:
            if x == "*":
                continue
            try:
                self.nets.append(ipaddress.ip_network(x, strict=False))
            except ValueError:
                pass

    def __contains__(self, host: str | None) -> bool:
        if self.any:
            return True
        ip = _ip(host or "")
        return ip is not None and any(ip in n for n in self.nets)

    def client(self, xff: str) -> str | None:
        hops = [h for h in (x.strip() for x in xff.split(",")) if h]
        for h in reversed(hops):
            ip = _ip(h)
            if ip is None:
                return None                      # 格式不對就不採用，保留直接連線的位址
            if self.any:
                if not _internal(ip):
                    return str(ip)
            elif not any(ip in n for n in self.nets):
                return str(ip)
        ip = _ip(hops[0]) if hops else None
        return str(ip) if ip else None


class ProxyHeaders:
    def __init__(self, app, trusted: str | None = None):
        self.app = app
        self.trusted = Trusted(trusted if trusted is not None else os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            peer = (scope.get("client") or (None, 0))[0]
            if peer in self.trusted:
                xff, proto = [], None
                for k, v in scope["headers"]:
                    if k == b"x-forwarded-for":
                        xff.append(v.decode("latin1"))
                    elif k == b"x-forwarded-proto":
                        proto = v.decode("latin1").split(",")[-1].strip()
                if xff:
                    host = self.trusted.client(",".join(xff))
                    if host:
                        scope = dict(scope, client=(host, 0))
                if proto in ("http", "https"):
                    scope = dict(scope, scheme=proto if scope["type"] == "http" else proto.replace("http", "ws"))
        await self.app(scope, receive, send)


async def _too_big(send, limit: int):
    body = json.dumps({"ok": False, "error": f"內容太大（上限 {limit // 1024 // 1024 or 1} MB）", "code": "too_large"}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await self.app(scope, receive, send)
        limit = body_limit(scope["path"])
        cl = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if cl is not None:
            try:
                if int(cl) > limit:
                    return await _too_big(send, limit)
            except ValueError:
                return await _too_big(send, limit)
            return await self.app(scope, self._counting(receive, limit), send)
        # 沒有 Content-Length（分段傳送）：先收齊（最多 limit），超過直接 413
        chunks, size = [], 0
        while True:
            msg = await receive()
            if msg["type"] != "http.request":
                return
            size += len(msg.get("body", b""))
            if size > limit:
                return await _too_big(send, limit)
            chunks.append(msg)
            if not msg.get("more_body"):
                break
        it = iter(chunks)

        async def replay():
            try:
                return next(it)
            except StopIteration:
                return await receive()
        await self.app(scope, replay, send)

    @staticmethod
    def _counting(receive, limit: int):
        """有 Content-Length 也再數一次：謊報長度的請求讀到超過上限就當成中斷"""
        seen = 0

        async def wrapped():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > limit:
                    return {"type": "http.disconnect"}
            return msg
        return wrapped
