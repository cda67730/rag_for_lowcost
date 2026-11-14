"""後台登入：argon2id 密碼、JWT（含 token_version，加一即讓舊 token 失效）、連續錯 5 次鎖 15 分鐘。"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, Header
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ApiError
from db.models import User
from db.session import get_session

TOKEN_HOURS = 8
MAX_FAILS, LOCK_MINUTES = 5, 15
_ph = PasswordHasher()   # argon2id，參數用套件預設


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def check_password_rules(pw: str) -> None:
    if len(pw) < 10:
        raise ApiError(422, "weak_password", "密碼至少 10 個字元", {"password": "至少 10 個字元"})


def _secret() -> str:
    s = os.environ.get("JWT_SECRET", "")
    if len(s) < 32:
        raise RuntimeError("JWT_SECRET 未設定或少於 32 個字元（見 server/.env.example）")
    return s


def make_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "tv": user.token_version, "iat": now, "exp": now + timedelta(hours=TOKEN_HOURS)}, _secret(), algorithm="HS256")


def login(session: Session, login_name: str, password: str) -> User:
    unauthorized = ApiError(401, "bad_login", "帳號或密碼錯誤")
    u = session.scalar(select(User).where(func.lower(User.login) == (login_name or "").strip().lower()))
    now = datetime.now(timezone.utc)
    if u is None:
        _ph.hash("timing")          # 帳號不存在也花一樣的時間
        raise unauthorized
    if u.locked_until and u.locked_until > now:
        raise ApiError(429, "locked", f"錯誤次數過多，請 {LOCK_MINUTES} 分鐘後再試")
    try:
        _ph.verify(u.pw_hash, password or "")
    except (VerifyMismatchError, InvalidHashError):
        u.failed_count += 1
        if u.failed_count >= MAX_FAILS:
            u.locked_until, u.failed_count = now + timedelta(minutes=LOCK_MINUTES), 0
        session.commit()
        raise unauthorized
    if u.disabled:
        raise unauthorized
    if _ph.check_needs_rehash(u.pw_hash):
        u.pw_hash = _ph.hash(password)
    u.failed_count, u.locked_until, u.last_login_at = 0, None, now
    session.commit()
    return u


def current_user(authorization: str = Header(default=""), session: Session = Depends(get_session)) -> User:
    if not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthenticated", "請先登入")
    try:
        p = jwt.decode(authorization[7:], _secret(), algorithms=["HS256"])
        u = session.get(User, uuid.UUID(p["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise ApiError(401, "unauthenticated", "登入已過期，請重新登入")
    if u is None or u.disabled or u.token_version != p.get("tv"):
        raise ApiError(401, "unauthenticated", "登入已過期，請重新登入")
    return u
