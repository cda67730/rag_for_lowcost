"""外部呼叫用的 API 金鑰：明碼只在建立時回傳一次，資料庫只存 SHA-256"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ApiKey

PREFIX = "rk_"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(session: Session, bot_id, name: str) -> tuple[ApiKey, str]:
    token = PREFIX + secrets.token_urlsafe(32)
    k = ApiKey(bot_id=bot_id, name=name.strip()[:60], prefix=token[:10], key_hash=digest(token))
    session.add(k)
    session.flush()
    return k, token


def verify(session: Session, token: str) -> ApiKey | None:
    """找到而且啟用中才回傳；順便更新最後使用時間"""
    if not token or not token.startswith(PREFIX) or len(token) > 200:
        return None
    k = session.scalar(select(ApiKey).where(ApiKey.key_hash == digest(token)))
    if k is None or not k.enabled:
        return None
    k.last_used_at = datetime.now(timezone.utc)
    return k
