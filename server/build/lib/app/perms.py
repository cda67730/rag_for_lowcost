"""後台權限：依 memberships 算出某人在某個 Workspace／Bot 的角色。

owner 只在 Workspace 層；admin／reviewer／analyst 可以在 Workspace 層（整個工作區）或指定 Bot。
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import forbidden, not_found
from db.models import Membership, User

ROLES = ("owner", "admin", "reviewer", "analyst")


def roles_of(session: Session, user: User, ws_id: uuid.UUID, bot_id: uuid.UUID | None = None) -> set[str]:
    """Workspace 層的角色一律算；有 bot_id 時再加上這個 Bot 的角色"""
    rows = session.scalars(select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws_id))
    return {m.role for m in rows if m.bot_id is None or (bot_id is not None and m.bot_id == bot_id)}


def ws_roles(session: Session, user: User, ws_id: uuid.UUID) -> set[str]:
    rows = session.scalars(select(Membership).where(Membership.user_id == user.id, Membership.workspace_id == ws_id, Membership.bot_id.is_(None)))
    return {m.role for m in rows}


def member_anywhere(session: Session, user: User, ws_id: uuid.UUID) -> bool:
    return session.scalar(select(Membership.id).where(Membership.user_id == user.id, Membership.workspace_id == ws_id).limit(1)) is not None


def require(session: Session, user: User, ws_id: uuid.UUID, allowed: set[str], bot_id: uuid.UUID | None = None, ws_level: bool = False) -> str:
    """allowed 不含 owner 時也放行 owner。完全不是成員→404（不洩漏存在與否）；是成員但角色不夠→403"""
    have = ws_roles(session, user, ws_id) if ws_level else roles_of(session, user, ws_id, bot_id)
    ok = have & (set(allowed) | {"owner"})
    if ok:
        return next(r for r in ROLES if r in ok)
    if not member_anywhere(session, user, ws_id):
        raise not_found()
    if not ws_level and bot_id is not None and not roles_of(session, user, ws_id, bot_id):
        raise not_found()   # 看不到這個 Bot
    raise forbidden()
