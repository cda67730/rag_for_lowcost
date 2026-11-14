"""命令列工具。建立 Workspace 與第一個 owner 不開 API，用這裡：

    python -m app.cli create-workspace --slug main --name 主要工作區 --owner admin

密碼會提示輸入（也可用環境變數 RAG_OWNER_PASSWORD 給，方便自動化）。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.auth import hash_password
from db.models import Membership, User, Workspace
from db.session import session


def create_workspace(slug: str, name: str, owner: str, password: str) -> tuple[Workspace, User]:
    if len(password) < 10:
        raise SystemExit("密碼至少 10 個字元")
    with session() as s:
        if s.scalar(select(Workspace).where(Workspace.slug == slug)):
            raise SystemExit(f"Workspace「{slug}」已存在")
        u = s.scalar(select(User).where(User.login == owner))
        if u is None:
            u = User(login=owner, display_name=owner, pw_hash=hash_password(password))
            s.add(u)
        w = Workspace(slug=slug, name=name)
        s.add(w); s.flush()
        s.add(Membership(user_id=u.id, workspace_id=w.id, role="owner"))
        s.commit()
        return w, u


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-workspace", help="建立 Workspace 與 owner 帳號")
    c.add_argument("--slug", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--owner", required=True, help="owner 的登入帳號；不存在就建立")
    a = p.parse_args(argv)
    if a.cmd == "create-workspace":
        pw = os.environ.get("RAG_OWNER_PASSWORD") or getpass.getpass("owner 密碼（至少 10 個字元）：")
        w, u = create_workspace(a.slug, a.name, a.owner, pw)
        print(f"已建立 Workspace {w.slug}（{w.id}），owner：{u.login}")


if __name__ == "__main__":
    main(sys.argv[1:])
