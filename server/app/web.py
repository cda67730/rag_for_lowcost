"""由伺服器直接提供聊天頁（/chat/）與後台（/admin/）：整個系統一個網址，不用另外架 GitHub Pages、不用設 CORS。

- 前端檔案的位置：WEB_DIR；沒設時找 server/web（Docker 映像檔裡）或 repo 的 web/（本機）。找不到就不提供，只有 API。
- SERVE_WEB=false 可以關掉（例如前端放在 GitHub Pages）。
- 兩個 config.js 由伺服器產生：在原檔內容後面把後端改成「這個伺服器、同一個網址」，其他設定（例如預設機器人 BOT）照原檔。
"""
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

SERVER_DIR = Path(__file__).resolve().parent.parent

# 原檔後面加的覆寫：後端＝同一個網址的這個伺服器
OVERRIDE = {
    "chat": '\n// 由伺服器提供時：後端固定是同一個網址的 Python 版\nwindow.CHAT_CONFIG.BACKEND = "server";\nwindow.CHAT_CONFIG.API_URL = location.origin;\n',
    "admin": "\n// 由伺服器提供時：API 在同一個網址\nwindow.ADMIN_CONFIG.API_URL = location.origin;\n",
}
SECURITY_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "strict-origin-when-cross-origin"}
# 內容安全政策：只執行自己網址的程式（不允許行內 script、eval、外部 script），連線只到自己
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
CSP = {"admin": _CSP + "; frame-ancestors 'none'", "chat": _CSP}      # 聊天頁可以嵌進其他網站


def web_dir() -> Path | None:
    raw = os.environ.get("WEB_DIR", "").strip()
    for d in ([Path(raw)] if raw else [SERVER_DIR / "web", SERVER_DIR.parent / "web"]):
        if (d / "chat" / "index.html").is_file() and (d / "admin" / "index.html").is_file():
            return d.resolve()
    return None


def enabled() -> bool:
    return os.environ.get("SERVE_WEB", "true").strip().lower() not in ("0", "false", "no", "off")


def mount_web(app: FastAPI) -> Path | None:
    """掛上 /chat/ 與 /admin/；回傳前端資料夾（沒掛回 None）。要在其他路由都加好之後才呼叫"""
    d = web_dir()
    if not enabled() or d is None:
        return None

    def config_js(part: str):
        def handler():
            src = (d / part / "config.js").read_text(encoding="utf-8")
            return Response(src + OVERRIDE[part], media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-cache"})
        return handler

    for part in ("chat", "admin"):
        app.add_api_route(f"/{part}/config.js", config_js(part), methods=["GET"], include_in_schema=False)
        app.add_api_route(f"/{part}", lambda part=part: RedirectResponse(f"/{part}/"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/", lambda: RedirectResponse("/chat/"), methods=["GET"], include_in_schema=False)
    app.mount("/chat", StaticFiles(directory=d / "chat", html=True), name="web-chat")
    app.mount("/admin", StaticFiles(directory=d / "admin", html=True), name="web-admin")

    @app.middleware("http")
    async def web_headers(request: Request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p.startswith(("/chat/", "/admin/")):
            for k, v in SECURITY_HEADERS.items():
                resp.headers.setdefault(k, v)
            resp.headers.setdefault("Cache-Control", "no-cache")      # 更新後馬上看到新版（仍會用 ETag 省流量）
            part = "admin" if p.startswith("/admin/") else "chat"
            resp.headers.setdefault("Content-Security-Policy", CSP[part])
            if request.url.scheme == "https":
                resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
            if part == "admin":
                resp.headers.setdefault("X-Frame-Options", "DENY")   # 後台不讓別的網站嵌入；聊天頁可以嵌進其他網站
        return resp
    return d
