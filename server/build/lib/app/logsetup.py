"""LOG_LEVEL：debug／info／warning／error，預設 info。設錯用 info 並記警告。"""
import logging
import os

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "warn": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}


def setup_logging() -> int:
    raw = os.environ.get("LOG_LEVEL", "").strip().lower()
    level = LEVELS.get(raw or "info")
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    root.setLevel(level or logging.INFO)
    for name in ("httpx", "httpcore"):       # 第三方套件的每個請求都記會太吵：除非開 debug
        logging.getLogger(name).setLevel(logging.DEBUG if level == logging.DEBUG else logging.WARNING)
    if level is None:
        logging.getLogger("app").warning("LOG_LEVEL=%r 不認得，改用 info（可用 debug／info／warning／error）", raw)
    return root.level
