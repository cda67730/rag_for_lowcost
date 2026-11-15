"""建立／刪除工作區、總管理員重設密碼：後台（總管理員）與管理指令共用。"""
import re
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auth
from app.errors import ApiError
from db.models import Bot, Conversation, Document, Job, KnowledgeBase, Membership, SystemAudit, User, Workspace

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")


def find_user(s: Session, login: str) -> User | None:
    return s.scalar(select(User).where(func.lower(User.login) == login.strip().lower()))


def create_workspace(s: Session, slug: str, name: str, owner_login: str, password: str | None = None) -> dict:
    """回傳 {workspace, owner, temp_password}。owner 帳號已存在就直接加為 owner、不動他的密碼；不存在就建立（沒給密碼時產生一次性密碼）"""
    slug, name, owner_login = slug.strip().lower(), name.strip(), owner_login.strip()
    errs = {}
    if not SLUG.fullmatch(slug):
        errs["slug"] = "2–41 個小寫英數字或連字號"
    if not name:
        errs["name"] = "必填"
    if not owner_login:
        errs["owner"] = "必填"
    if errs:
        raise ApiError(422, "invalid", "欄位有誤", errs)
    if s.scalar(select(Workspace.id).where(Workspace.slug == slug)):
        raise ApiError(409, "slug_taken", "這個 slug 已被使用", {"slug": "已被使用"})
    user, temp = find_user(s, owner_login), None
    if user is None:
        if password:
            auth.check_password_rules(password)
        else:
            password = temp = secrets.token_urlsafe(12)
        user = User(login=owner_login, display_name=owner_login, pw_hash=auth.hash_password(password))
        s.add(user)
    w = Workspace(slug=slug, name=name)
    s.add(w)
    s.flush()
    s.add(Membership(user_id=user.id, workspace_id=w.id, role="owner"))
    s.flush()
    return {"workspace": w, "owner": user, "temp_password": temp}


def workspace_stats(s: Session, w: Workspace) -> dict:
    n = lambda q: s.scalar(q) or 0
    return {"bots": n(select(func.count()).select_from(Bot).where(Bot.workspace_id == w.id)),
            "kbs": n(select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.workspace_id == w.id)),
            "documents": n(select(func.count()).select_from(Document).join(KnowledgeBase, KnowledgeBase.id == Document.kb_id).where(KnowledgeBase.workspace_id == w.id)),
            "conversations": n(select(func.count()).select_from(Conversation).join(Bot, Bot.id == Conversation.bot_id).where(Bot.workspace_id == w.id)),
            "members": n(select(func.count(func.distinct(Membership.user_id))).where(Membership.workspace_id == w.id))}


def delete_workspace(s: Session, w: Workspace) -> dict:
    """連同機器人、知識庫、紀錄、成員角色一起刪（資料庫設了 CASCADE）。帳號本身保留（可能還屬於別的工作區）。還有背景工作在跑就不刪"""
    if s.scalar(select(Job.id).where(Job.workspace_id == w.id, Job.status.in_(("queued", "running"))).limit(1)):
        raise ApiError(409, "jobs_running", "這個工作區還有背景工作在進行，等做完或取消後再刪")
    stats = workspace_stats(s, w)
    s.delete(w)
    s.flush()
    return stats


def reset_password(s: Session, user: User) -> str:
    temp = secrets.token_urlsafe(12)
    user.pw_hash, user.token_version, user.failed_count, user.locked_until = auth.hash_password(temp), user.token_version + 1, 0, None
    return temp


def other_workspaces(s: Session, user: User, ws_id) -> bool:
    """這個帳號是否也屬於別的工作區"""
    return bool(s.scalar(select(Membership.id).where(Membership.user_id == user.id, Membership.workspace_id != ws_id).limit(1)))


def system_audit(s: Session, user: User | None, action: str, target: str = "", detail: dict | None = None) -> None:
    s.add(SystemAudit(user_id=user.id if user else None, login=user.login if user else "", action=action, target=target, detail=detail or {}))
