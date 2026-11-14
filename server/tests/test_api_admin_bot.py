"""後台 API 第二批：流程與流程設定、範本、Skill 版本、對照表、邀請碼。"""
import json
from pathlib import Path

from db import models as m
from test_api_admin import env  # noqa: F401

TEMPLATE = Path(__file__).parents[2] / "templates" / "internal-helpdesk.json"


def test_flows(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    h = as_("botadmin")
    F = f"/api/admin/bots/{bot.id}/flows"
    assert c.get(F, headers=as_("ana")).status_code == 200 and c.post(F, headers=as_("ana"), json={"name": "x"}).status_code == 403
    assert c.post(F, headers=h, json={"name": "x", "kind": "collect"}).status_code == 422
    dflt = c.get(F, headers=h).json()["items"][0]
    c.patch(f"/api/admin/flows/{dflt['id']}/settings", headers=h, json={"items": [{"key": "top_k", "value": 4}]})
    new = c.post(F, headers=h, json={"name": "精簡", "copy_from": dflt["id"]}).json()
    assert c.get(f"/api/admin/flows/{new['id']}/settings", headers=h).json()["items"] == [{"key": "top_k", "value": "4"}]
    r = c.patch(f"/api/admin/flows/{new['id']}/settings", headers=h, json={"items": [{"key": "role_user", "value": "x"}]})
    assert r.status_code == 422                                                    # 不能在流程層覆蓋的 key
    c.patch(f"/api/admin/flows/{new['id']}/settings", headers=h, json={"items": [{"key": "top_k", "value": None}]})
    assert c.get(f"/api/admin/flows/{new['id']}/settings", headers=h).json()["items"] == []
    assert c.delete(f"/api/admin/flows/{dflt['id']}", headers=h).status_code == 409
    assert c.patch(f"/api/admin/flows/{new['id']}", headers=h, json={"is_default": True}).json()["is_default"]
    assert [f["is_default"] for f in c.get(F, headers=h).json()["items"]] == [False, True]
    assert c.delete(f"/api/admin/flows/{dflt['id']}", headers=h).json() == {"ok": True}
    assert c.get(f"/api/admin/flows/{new['id']}/settings", headers=as_("outsider")).status_code == 404
    f2 = m.Flow(bot_id=bot2.id, name="B 的流程"); db.add(f2); db.flush()
    assert c.post(F, headers=as_("owner"), json={"name": "跨 Bot", "copy_from": str(f2.id)}).status_code == 404   # 不能從別的 Bot 複製


def test_template_apply_and_export(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    h = as_("botadmin")
    r = c.post(f"/api/admin/bots/{bot.id}/template/apply", headers=h, content=TEMPLATE.read_bytes())
    assert r.status_code == 200 and r.json()["skills"] > 0
    assert c.post(f"/api/admin/bots/{bot.id}/template/apply", headers=h, content=b"{oops").json()["code"] == "bad_template"
    ex = c.get(f"/api/admin/bots/{bot.id}/template/export", headers=h)
    assert "attachment" in ex.headers["content-disposition"] and len(ex.json()["skills"]) == r.json()["skills"]
    assert c.get(f"/api/admin/bots/{bot2.id}/template/export", headers=h).status_code == 404


def test_skills_versions_restore(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    h = as_("botadmin")
    S = f"/api/admin/bots/{bot.id}/skills"
    k = c.post(S, headers=h, json={"name": "理解行為", "topics": "情緒、行為", "content": "v1"}).json()
    assert k["key"] == "S01" and k["topics"] == ["情緒", "行為"] and k["version"] == 1
    assert c.post(S, headers=h, json={"name": "理解行為"}).status_code == 409
    assert c.post(S, headers=h, json={"name": "", "topics": 3}).json()["fields"].keys() == {"name", "topics"}
    k2 = c.put(f"/api/admin/skills/{k['id']}", headers=h, json={"name": "理解行為", "topics": ["情緒"], "content": "v2", "always": True}).json()
    assert k2["version"] == 2 and k2["always"] is True
    vs = c.get(f"/api/admin/skills/{k['id']}/versions", headers=h).json()["items"]
    assert [(v["version"], v["content"]) for v in vs] == [(2, "v2"), (1, "v1")]
    k3 = c.post(f"/api/admin/skills/{k['id']}/restore", headers=h, json={"version": 1}).json()
    assert (k3["version"], k3["content"], k3["topics"]) == (3, "v1", ["情緒", "行為"])
    assert c.post(f"/api/admin/skills/{k['id']}/restore", headers=h, json={"version": 9}).status_code == 404
    other = c.post(S, headers=h, json={"name": "第二個"}).json()
    assert other["key"] == "S02"
    assert c.put(f"/api/admin/skills/{other['id']}", headers=h, json={"name": "理解行為"}).status_code == 409
    assert c.delete(f"/api/admin/skills/{other['id']}", headers=as_("ana")).status_code == 403
    assert c.delete(f"/api/admin/skills/{other['id']}", headers=h).json() == {"ok": True}
    assert [x["name"] for x in c.get(S, headers=h).json()["items"]] == ["理解行為"]


def test_lists(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    h = as_("botadmin")
    B = f"/api/admin/bots/{bot.id}"
    assert c.put(B + "/synonyms", headers=h, json={"items": [{"word": "丟", "concept": "攻擊"}, {"word": "推", "concept": "攻擊"}]}).json()["items"][0] == {"word": "丟", "concept": "攻擊"}
    r = c.put(B + "/synonyms", headers=h, json={"items": [{"word": "丟", "concept": "攻擊"}, {"word": "丟", "concept": "攻擊"}, {"word": ""}]})
    assert r.status_code == 422 and r.json()["fields"].keys() == {"items.1", "items.2"}
    assert len(c.get(B + "/synonyms", headers=h).json()["items"]) == 2                      # 失敗時不動原本的
    bad = c.put(B + "/forbidden", headers=h, json={"items": [{"rule": "(", "kind": "regex"}, {"rule": "x", "kind": "zz"}]})
    assert "正規式" in bad.json()["fields"]["items.0"] and "kind" in bad.json()["fields"]["items.1"]
    assert c.put(B + "/forbidden", headers=h, json={"items": [{"rule": "過動", "note": "不診斷"}, {"rule": "\\d{4}", "kind": "regex"}]}).status_code == 200
    refs = c.put(B + "/referrals", headers=h, json={"items": [{"category": "兒少保護", "who": "社工", "now": "通報"}, {"category": "健康", "who": "醫師"}]}).json()["items"]
    assert [r["category"] for r in refs] == ["兒少保護", "健康"] and refs[0]["now"] == "通報"
    assert c.get(B + "/referrals", headers=as_("ana")).status_code == 403
    assert db.query(m.AuditLog).filter_by(action="synonyms.replace").count() == 1


def test_invite_codes(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    h = as_("botadmin")
    I = f"/api/admin/bots/{bot.id}/invite-codes"
    a = c.post(I, headers=h, json={"unit": "甲園", "trace": True}).json()
    assert len(a["code"]) == 8 and not set(a["code"]) & set("0O1I") and a["trace"]
    b = c.post(I, headers=h, json={"code": "my-code", "expires_at": "2030-01-01"}).json()
    assert b["code"] == "MY-CODE" and b["expires_at"].startswith("2030-01-01")
    assert c.post(I, headers=h, json={"code": "my-code"}).status_code == 409
    assert c.post(I, headers=h, json={"code": "x"}).status_code == 422
    assert c.post(I, headers=h, json={"expires_at": "明天"}).status_code == 422
    assert c.patch(f"/api/admin/invite-codes/{a['id']}", headers=h, json={"enabled": False}).json()["enabled"] is False
    assert [x["code"] for x in c.get(I, headers=h).json()["items"]] == [a["code"], "MY-CODE"]
    assert c.delete(f"/api/admin/invite-codes/{b['id']}", headers=h).json() == {"ok": True}
    assert c.patch(f"/api/admin/invite-codes/{a['id']}", headers=as_("outsider"), json={}).status_code == 404
    assert all(a["code"] not in json.dumps(x.detail, ensure_ascii=False) + x.target for x in db.query(m.AuditLog))   # 邀請碼不進稽核
