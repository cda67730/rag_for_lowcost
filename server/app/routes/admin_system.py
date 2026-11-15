"""總管理員：列出、建立、刪除工作區，重設任何帳號的密碼，看跨工作區的操作紀錄。
總管理員不會因此看到各工作區的內容；要管某個工作區的機器人與資料，還是要是那裡的成員。"""
from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError, forbidden, not_found
from app.routes.admin import DB, Me, body, uid
from db.models import Membership, SystemAudit, User, Workspace
from services import workspaces as W

router = APIRouter(prefix="/api/admin/system")


def superadmin(u: User) -> User:
    if not u.is_superadmin:
        raise forbidden()
    return u


def ws_out(s: Session, w: Workspace) -> dict:
    owners = s.scalars(select(User.login).join(Membership, Membership.user_id == User.id)
                       .where(Membership.workspace_id == w.id, Membership.role == "owner").order_by(User.login)).all()
    return {"id": str(w.id), "slug": w.slug, "name": w.name, "created_at": w.created_at.isoformat(), "owners": list(owners), **W.workspace_stats(s, w)}


@router.get("/workspaces")
def list_workspaces(u: User = Me, s: Session = DB):
    superadmin(u)
    return {"items": [ws_out(s, w) for w in s.scalars(select(Workspace).order_by(Workspace.created_at, Workspace.slug))]}


@router.post("/workspaces", status_code=201)
async def create_workspace(request: Request, u: User = Me, s: Session = DB):
    """owner 預設是自己；填別人的帳號時，帳號存在就加為 owner（不動密碼），不存在就建立並回傳一次性密碼"""
    superadmin(u)
    d = await body(request)
    r = W.create_workspace(s, str(d.get("slug") or ""), str(d.get("name") or ""), str(d.get("owner") or u.login), str(d.get("password") or "") or None)
    w = r["workspace"]
    W.system_audit(s, u, "workspace.create", w.slug, {"name": w.name, "owner": r["owner"].login, "new_owner_account": r["temp_password"] is not None})
    s.commit()
    out = ws_out(s, w)
    if r["temp_password"]:
        out["temp_password"] = r["temp_password"]       # 只顯示這一次
    return out


@router.delete("/workspaces/{ws}")
async def delete_workspace(ws: str, request: Request, u: User = Me, s: Session = DB):
    """要在 confirm 填工作區的 slug 才會刪：連同機器人、知識庫、文件、紀錄一起刪，不能復原"""
    superadmin(u)
    w = s.get(Workspace, uid(ws))
    if w is None:
        raise not_found()
    d = await body(request)
    if str(d.get("confirm") or "").strip() != w.slug:
        raise ApiError(422, "confirm", "請輸入工作區的 slug 確認刪除", {"confirm": f"請輸入 {w.slug}"})
    slug, name = w.slug, w.name
    stats = W.delete_workspace(s, w)
    W.system_audit(s, u, "workspace.delete", slug, {"name": name, **stats})
    s.commit()
    return {"ok": True, "deleted": stats}


@router.post("/users/reset-password")
async def reset_password(request: Request, u: User = Me, s: Session = DB):
    """任何帳號（包括屬於多個工作區的）都能重設；工作區 owner 只能重設只屬於自己工作區的帳號"""
    superadmin(u)
    d = await body(request)
    target = W.find_user(s, str(d.get("login") or ""))
    if target is None:
        raise ApiError(404, "not_found", "找不到這個帳號", {"login": "找不到"})
    temp = W.reset_password(s, target)
    W.system_audit(s, u, "user.reset_password", target.login)
    s.commit()
    return {"login": target.login, "temp_password": temp}


@router.get("/audit")
def audit(limit: int = 100, u: User = Me, s: Session = DB):
    superadmin(u)
    rows = s.scalars(select(SystemAudit).order_by(SystemAudit.at.desc(), SystemAudit.id.desc()).limit(max(1, min(limit, 500)))).all()
    return {"items": [{"at": r.at.isoformat(), "login": r.login, "action": r.action, "target": r.target, "detail": r.detail} for r in rows]}
