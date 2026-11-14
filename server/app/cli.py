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
    i = sub.add_parser("import-apps-script", help="從 Apps Script 版的試算表（下載成 .xlsx）建一個機器人與同名知識庫")
    i.add_argument("--workspace", required=True, help="工作區 slug")
    i.add_argument("--slug", required=True, help="新機器人的 slug")
    i.add_argument("--kb-slug", help="新知識庫的 slug，預設同機器人")
    i.add_argument("--xlsx", required=True, help="Google 試算表「檔案 › 下載 › Microsoft Excel」存下的檔案")
    i.add_argument("--with-logs", action="store_true", help="一併搬對話紀錄與回饋（預設不搬）")
    i.add_argument("--no-embed", action="store_true", help="先不算向量，之後在後台按「補算缺少的向量」")
    r = sub.add_parser("purge-traces", help="清掉超過保留天數的流程紀錄（伺服器每天也會自動清一次）")
    r.add_argument("--days", type=int, help="保留天數，預設讀 TRACE_RETENTION_DAYS（沒設是 90）")
    a = p.parse_args(argv)
    if a.cmd == "purge-traces":
        from services import retention
        days = a.days if a.days is not None else retention.retention_days()
        if not days or days < 1:
            raise SystemExit("保留天數是 0：不清除")
        with session() as s:
            print(f"已清除 {retention.purge_traces(s, days)} 筆超過 {days} 天的流程紀錄")
        return
    if a.cmd == "import-apps-script":
        import asyncio
        from pathlib import Path
        from app.routes.chat import get_llm
        from services.apps_script_import import ImportError_, import_workbook
        with session() as s:
            try:
                rep = asyncio.run(import_workbook(s, get_llm(), Path(a.xlsx).read_bytes(), workspace_slug=a.workspace, bot_slug=a.slug, kb_slug=a.kb_slug,
                                                  with_logs=a.with_logs, embed=not a.no_embed))
            except ImportError_ as e:
                raise SystemExit("無法匯入：" + str(e))
            s.commit()
        L = {"settings": "設定", "skills": "Skill", "synonyms": "同義詞", "forbidden": "禁止用語", "referral": "轉介資源", "invite_codes": "邀請碼", "documents": "文件",
             "chunks": "段落", "eval_questions": "評測題", "logs": "對話紀錄", "feedback": "回饋", "missing_vectors": "缺向量的段落",
             "defs": "定義", "nodes": "節點", "edges": "關係", "sources": "來源", "links": "段落標註", "candidates": "候選"}
        print("完成：" + "、".join(f"{L.get(k, k)} {v}" for k, v in rep.items() if k not in ("skipped", "kg", "bot_id", "kb_id")))
        print("知識圖譜：" + "、".join(f"{L.get(k, k)} {v}" for k, v in rep["kg"].items()))
        for x in rep["skipped"]:
            print("略過：" + x)
        print("下一步：到後台確認設定、審核文件；試算表檔案含對話與邀請碼，用完請刪除。")
        return
    if a.cmd == "create-workspace":
        pw = os.environ.get("RAG_OWNER_PASSWORD") or getpass.getpass("owner 密碼（至少 10 個字元）：")
        w, u = create_workspace(a.slug, a.name, a.owner, pw)
        print(f"已建立 Workspace {w.slug}（{w.id}），owner：{u.login}")


if __name__ == "__main__":
    main(sys.argv[1:])
