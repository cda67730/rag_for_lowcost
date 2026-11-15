"""總管理員（建立／刪除工作區、重設任何帳號密碼）與跨工作區帳號的密碼重設限制。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli
from db import models as m
from test_api_admin import PW, env  # noqa: F401


def other_ws(db):
    return db.scalar(select(m.Workspace).where(m.Workspace.slug == "o"))


def test_cross_workspace_takeover_blocked(env):  # noqa: F811
    """別的工作區的 owner 把你加進他的工作區，再重設你的密碼：不行"""
    c, db, ws, bot, bot2, users, as_ = env
    o, h = other_ws(db), as_("outsider")
    assert c.post(f"/api/admin/workspaces/{o.id}/members", headers=h, json={"login": "owner", "role": "analyst"}).status_code == 201
    r = c.post(f"/api/admin/workspaces/{o.id}/users/{users['owner'].id}/reset-password", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "shared_account" and "temp_password" not in r.json()
    assert c.post("/api/admin/auth/login", json={"login": "owner", "password": PW}).status_code == 200      # 密碼沒被改
    # 只屬於本工作區的帳號照常可以重設
    r = c.post(f"/api/admin/workspaces/{ws.id}/users/{users['rev'].id}/reset-password", headers=as_("owner")).json()
    assert c.post("/api/admin/auth/login", json={"login": "rev", "password": r["temp_password"]}).status_code == 200
    # 總管理員的帳號也不行（就算只屬於這個工作區）
    users["wsadmin"].is_superadmin = True; db.flush()
    assert c.post(f"/api/admin/workspaces/{ws.id}/users/{users['wsadmin'].id}/reset-password", headers=as_("owner")).json()["code"] == "shared_account"


def test_system_requires_superadmin(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    h = as_("owner")
    assert c.get("/api/admin/me", headers=h).json()["is_superadmin"] is False
    for method, path in [("GET", "/workspaces"), ("POST", "/workspaces"), ("DELETE", f"/workspaces/{ws.id}"), ("POST", "/users/reset-password"), ("GET", "/audit")]:
        assert c.request(method, "/api/admin/system" + path, headers=h, json={}).status_code == 403, path
    assert c.get("/api/admin/system/workspaces").status_code == 401


def test_superadmin_create_list_delete(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    users["owner"].is_superadmin = True; db.flush()
    h = as_("owner")
    S = "/api/admin/system"
    items = c.get(S + "/workspaces", headers=h).json()["items"]
    assert {x["slug"] for x in items} == {"w", "o"} and next(x for x in items if x["slug"] == "w")["bots"] == 2
    # 建立：預設自己是 owner
    r = c.post(S + "/workspaces", headers=h, json={"slug": "New-Team", "name": "新團隊"})
    assert r.status_code == 201 and r.json()["slug"] == "new-team" and r.json()["owners"] == ["owner"] and "temp_password" not in r.json()
    me = c.get("/api/admin/me", headers=h).json()
    assert any(w["slug"] == "new-team" and w["roles"] == ["owner"] for w in me["workspaces"])
    # 指定既有帳號：不動他的密碼
    r = c.post(S + "/workspaces", headers=h, json={"slug": "t2", "name": "二", "owner": "REV", "password": "會被忽略的密碼"}).json()
    assert r["owners"] == ["rev"] and "temp_password" not in r
    assert c.post("/api/admin/auth/login", json={"login": "rev", "password": PW}).status_code == 200
    # 指定新帳號：建立並給一次性密碼
    r = c.post(S + "/workspaces", headers=h, json={"slug": "t3", "name": "三", "owner": "carol"}).json()
    assert r["owners"] == ["carol"] and c.post("/api/admin/auth/login", json={"login": "carol", "password": r["temp_password"]}).status_code == 200
    assert c.post(S + "/workspaces", headers=h, json={"slug": "x", "name": ""}).json()["fields"].keys() == {"slug", "name"}
    assert c.post(S + "/workspaces", headers=h, json={"slug": "new-team", "name": "重複"}).json()["code"] == "slug_taken"
    # 刪除：要輸入 slug；有背景工作在跑不刪
    db.add(m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768))
    db.add(m.Conversation(bot_id=bot.id, session="s", turn=1, user_text="q", rtype="answer"))
    job = m.Job(workspace_id=ws.id, kind="eval_run", status="running"); db.add(job); db.flush()
    assert c.request("DELETE", f"{S}/workspaces/{ws.id}", headers=h, json={"confirm": "錯的"}).json()["code"] == "confirm"
    assert c.request("DELETE", f"{S}/workspaces/{ws.id}", headers=h, json={"confirm": "w"}).json()["code"] == "jobs_running"
    job.status = "done"; db.flush()
    ws_id, bot_id, ana_id = ws.id, bot.id, users["ana"].id
    r = c.request("DELETE", f"{S}/workspaces/{ws_id}", headers=h, json={"confirm": "w"}).json()
    assert r["ok"] and r["deleted"] == {"bots": 2, "kbs": 1, "documents": 0, "conversations": 1, "members": 5}
    db.expire_all()
    assert db.get(m.Workspace, ws_id) is None and db.get(m.Bot, bot_id) is None and not db.query(m.Conversation).count()
    assert not db.query(m.Membership).filter_by(workspace_id=ws_id).count() and db.get(m.User, ana_id) is not None   # 帳號保留
    assert c.get(f"/api/admin/workspaces/{ws_id}/bots", headers=h).status_code == 404
    log = c.get(S + "/audit", headers=h).json()["items"]
    assert [x["action"] for x in log][:2] == ["workspace.delete", "workspace.create"] and log[0]["detail"]["bots"] == 2 and log[0]["login"] == "owner"


def test_superadmin_reset_any_password(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    users["outsider"].is_superadmin = True
    db.add(m.Membership(user_id=users["ana"].id, workspace_id=other_ws(db).id, role="analyst")); db.flush()   # ana 屬於兩個工作區
    h = as_("outsider")
    old = as_("ana")
    r = c.post("/api/admin/system/users/reset-password", headers=h, json={"login": "Ana"}).json()
    assert r["login"] == "ana" and c.get("/api/admin/me", headers=old).status_code == 401               # 舊登入失效
    assert c.post("/api/admin/auth/login", json={"login": "ana", "password": r["temp_password"]}).status_code == 200
    assert c.post("/api/admin/system/users/reset-password", headers=h, json={"login": "nobody"}).status_code == 404


def test_cli_workspace_reset_superadmin(env, monkeypatch, capsys):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    monkeypatch.setattr(cli, "session", lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False))
    monkeypatch.delenv("RAG_OWNER_PASSWORD", raising=False)

    def no_prompt(*a):
        raise AssertionError("帳號已存在就不該問密碼")
    monkeypatch.setattr(cli.getpass, "getpass", no_prompt)
    cli.main(["create-workspace", "--slug", "c1", "--name", "一", "--owner", "owner"])         # 第一個總管理員
    out = capsys.readouterr().out
    assert "使用原有帳號" in out and "總管理員" in out
    db.expire_all()
    assert db.get(m.User, users["owner"].id).is_superadmin
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a: "new-user-password")
    cli.main(["create-workspace", "--slug", "c2", "--name", "二", "--owner", "dave"])         # 已經有總管理員：dave 不會變成總管理員
    assert "總管理員" not in capsys.readouterr().out
    assert not db.scalar(select(m.User).where(m.User.login == "dave")).is_superadmin
    assert c.post("/api/admin/auth/login", json={"login": "dave", "password": "new-user-password"}).status_code == 200
    monkeypatch.setattr(cli.getpass, "getpass", lambda *a: "short")
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["create-workspace", "--slug", "c3", "--name", "三", "--owner", "erin"])
    with pytest.raises(SystemExit):
        cli.main(["create-workspace", "--slug", "c1", "--name", "重複", "--owner", "owner"])
    cli.main(["reset-password", "--login", "rev"])
    temp = capsys.readouterr().out.split("一次性密碼：")[1].split("（")[0]
    assert c.post("/api/admin/auth/login", json={"login": "rev", "password": temp}).status_code == 200
    cli.main(["superadmin", "--login", "rev"])
    cli.main(["superadmin", "--login", "owner", "--revoke"])
    db.expire_all()
    assert db.get(m.User, users["rev"].id).is_superadmin and not db.get(m.User, users["owner"].id).is_superadmin
    acts = [a for (a,) in db.execute(select(m.SystemAudit.action).order_by(m.SystemAudit.id))]
    assert acts == ["workspace.create", "workspace.create", "user.reset_password", "user.superadmin", "user.superadmin"]


def test_grant_and_revoke_superadmin(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    users["owner"].is_superadmin = True; db.flush()
    h, S = as_("owner"), "/api/admin/system/superadmins"
    assert [x["login"] for x in c.get(S, headers=h).json()["items"]] == ["owner"]
    assert c.post(S, headers=as_("rev"), json={"login": "rev"}).status_code == 403             # 不是總管理員不能自己升級
    assert c.post(S, headers=h, json={"login": "nobody"}).status_code == 404
    users["ana"].disabled = True; db.flush()
    assert c.post(S, headers=h, json={"login": "ana"}).json()["code"] == "disabled"
    r = c.post(S, headers=h, json={"login": "REV"}).json()
    assert [x["login"] for x in r["items"]] == ["owner", "rev"]
    hr = as_("rev")
    assert c.get("/api/admin/system/workspaces", headers=hr).status_code == 200 and c.get("/api/admin/me", headers=hr).json()["is_superadmin"]
    assert c.post(S, headers=h, json={"login": "rev"}).status_code == 200                       # 重複設定不出錯、不重複記
    # 取消別人：立即生效
    assert [x["login"] for x in c.post(S + "/revoke", headers=h, json={"login": "rev"}).json()["items"]] == ["owner"]
    assert c.get("/api/admin/system/workspaces", headers=hr).status_code == 403
    # 最後一位不能取消（停用的帳號不算數）
    users["ana"].is_superadmin = True; db.flush()
    assert c.post(S + "/revoke", headers=h, json={"login": "owner"}).json()["code"] == "last_superadmin"
    assert c.post(S + "/revoke", headers=h, json={"login": "wsadmin"}).status_code == 404
    # 有另一位時可以取消自己
    c.post(S, headers=h, json={"login": "rev"})
    r = c.post(S + "/revoke", headers=h, json={"login": "owner"}).json()
    assert r == {"items": [], "self_revoked": True} and c.get("/api/admin/system/workspaces", headers=h).status_code == 403
    acts = [(x["action"], x["target"], x["detail"].get("on")) for x in c.get("/api/admin/system/audit", headers=hr).json()["items"]]
    assert acts == [("user.superadmin", "owner", False), ("user.superadmin", "rev", True), ("user.superadmin", "rev", False), ("user.superadmin", "rev", True)]
