"""外部資料：設定檢查、欄位過濾、外部連線的安全限制、API 金鑰與 /api/v1/ask；確認外部資料不進資料庫"""
import asyncio
import functools
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.routes.chat as C
from app.main import app
from db import models as m
from db.session import get_session
from services import apikeys
from services import external as EXT
from test_api_admin import env  # noqa: F401
from test_chat_graph import FakeLLM, bot  # noqa: F401

SRC = {"key": "crm", "name": "會員系統", "url": "https://api.example.com/members/{member_id}", "secret_env": "EXT_SECRET_CRM",
       "pick": [{"path": "data.level", "label": "會員等級"}, {"path": "data.tags.0", "label": "標籤"}]}
FIELDS = [{"key": "member_id", "label": "會員編號"}, {"key": "child_age", "label": "孩子年齡", "hint": "歲"}]


# ───────── 設定檢查 ─────────

def test_clean_config_rejects_bad_values():
    assert EXT.clean_fields(FIELDS)[1] == {"key": "child_age", "label": "孩子年齡", "hint": "歲"}
    for bad in [[{"key": "Bad"}], [{"key": "a"}, {"key": "a"}], "x", [1]]:
        with pytest.raises(EXT.ConfigError):
            EXT.clean_fields(bad)
    s = EXT.clean_sources([SRC])[0]
    assert s["method"] == "GET" and s["secret_header"] == "Authorization" and s["secret_prefix"] == "Bearer " and s["timeout"] == 5 and s["enabled"]
    for patch in [{"secret_env": "JWT_SECRET"}, {"secret_env": "GEMINI_API_KEY"}, {"url": "ftp://x/y"}, {"url": "https://{member_id}.evil.com/"},
                  {"method": "DELETE"}, {"query": {"a": {"b": 1}}}, {"pick": [{"label": "沒有 path"}]}, {"key": "Crm"}]:
        with pytest.raises(EXT.ConfigError):
            EXT.clean_sources([{**SRC, **patch}])
    with pytest.raises(EXT.ConfigError):
        EXT.clean_sources([dict(SRC, key=f"s{i}") for i in range(6)])


def test_accept_context_only_listed_fields():
    ctx = EXT.accept_context(FIELDS, {"member_id": "M1", "child_age": 4, "password": "x", "child_name": "小宇", "member_id2": "y", "nested": {"a": 1}})
    assert ctx == {"member_id": "M1", "child_age": "4"}
    assert EXT.accept_context(FIELDS, {"member_id": "x" * 900})["member_id"] == "x" * 500
    assert EXT.accept_context(FIELDS, "not a dict") == {} and EXT.accept_context(FIELDS, {"member_id": " "}) == {}


def test_host_allowlist(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALLOWED_HOSTS", "api.example.com, *.corp.example")
    assert EXT.host_allowed("api.example.com") and EXT.host_allowed("API.example.com.")
    assert EXT.host_allowed("a.corp.example") and not EXT.host_allowed("corp.example")
    assert not EXT.host_allowed("evil.com") and not EXT.host_allowed("api.example.com.evil.com")
    monkeypatch.setenv("EXTERNAL_ALLOWED_HOSTS", "")
    assert not EXT.host_allowed("api.example.com")


# ───────── 外部連線 ─────────

def aio(fn):
    @functools.wraps(fn)
    def run(*a, **kw):
        return asyncio.run(fn(*a, **kw))
    return run


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.fixture
def ext_env(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALLOWED_HOSTS", "api.example.com")
    monkeypatch.setenv("EXT_SECRET_CRM", "s3cret")

    async def public(host, port):
        return True
    monkeypatch.setattr(EXT, "_public_ip_only", public)


@aio
async def test_fetch_success_picks_fields_and_sends_secret(ext_env):
    seen = {}

    def h(req):
        seen.update(url=str(req.url), auth=req.headers.get("authorization"))
        return httpx.Response(200, json={"data": {"level": "金卡", "tags": ["新會員"], "phone": "0900"}})
    async with mock_client(h) as c:
        r = await EXT.fetch(EXT.clean_sources([SRC])[0], {"member_id": "A/1 2"}, c)
    assert r["ok"] and r["items"] == [("會員等級", "金卡"), ("標籤", "新會員")]
    assert seen == {"url": "https://api.example.com/members/A%2F1%202", "auth": "Bearer s3cret"}


@aio
async def test_fetch_refusals(ext_env, monkeypatch):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    src = EXT.clean_sources([SRC])[0]
    async with mock_client(h) as c:
        assert (await EXT.fetch(src, {}, c))["status"] == "缺少欄位：member_id"
        assert (await EXT.fetch(src, {"member_id": "1"}, c))["status"] == "HTTP 302"          # 不跟隨轉址
        assert (await EXT.fetch(dict(src, url="https://evil.com/{member_id}"), {"member_id": "1"}, c))["status"].startswith("主機不在")
        assert (await EXT.fetch(dict(src, url="http://api.example.com/{member_id}"), {"member_id": "1"}, c))["status"] == "只允許 https"
        monkeypatch.delenv("EXT_SECRET_CRM")
        assert "沒有設定 EXT_SECRET_CRM" in (await EXT.fetch(src, {"member_id": "1"}, c))["status"]
    assert len(calls) == 1


@aio
async def test_private_address_rejected(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALLOWED_HOSTS", "localhost")
    r = await EXT.fetch(EXT.clean_sources([dict(SRC, url="https://localhost/{member_id}", secret_env="")])[0], {"member_id": "1"})
    assert r["status"] == "主機解析到內網或本機位址，已拒絕"


@aio
async def test_fetch_big_or_bad_responses(ext_env):
    src = EXT.clean_sources([SRC])[0]
    for resp, status in [(httpx.Response(200, content=b"x" * (EXT.MAX_BYTES + 10)), "回應超過 256KB"), (httpx.Response(200, content=b"<html>"), "回應不是 JSON"),
                         (httpx.Response(500), "HTTP 500")]:
        async with mock_client(lambda req, r=resp: r) as c:
            assert (await EXT.fetch(src, {"member_id": "1"}, c))["status"] == status

    def boom(req):
        raise httpx.ConnectTimeout("t")
    async with mock_client(boom) as c:
        assert (await EXT.fetch(src, {"member_id": "1"}, c))["status"] == "逾時"


@aio
async def test_gather_summary_has_no_values(ext_env):
    async with mock_client(lambda req: httpx.Response(200, json={"data": {"level": "金卡"}})) as c:
        txt, summ = await EXT.gather(EXT.clean_fields(FIELDS), EXT.clean_sources([SRC]), {"member_id": "M77", "child_age": "4"}, c)
    assert "孩子年齡（歲）：4" in txt and "會員等級（會員系統）：金卡" in txt and txt.startswith("【背景資料】")
    dumped = json.dumps(summ, ensure_ascii=False)
    assert "M77" not in dumped and "金卡" not in dumped and "會員等級" in dumped and "孩子年齡" in dumped


# ───────── API 金鑰與 /api/v1/ask ─────────

@pytest.fixture
def v1(bot, monkeypatch, ext_env):  # noqa: F811
    db, b = bot
    llm = FakeLLM()
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[C.get_llm] = lambda: llm
    k, token = apikeys.create(db, b.id, "會員系統")
    db.add(m.BotIntegration(bot_id=b.id, context_fields=EXT.clean_fields(FIELDS), sources=EXT.clean_sources([SRC])))
    db.flush()
    real = httpx.AsyncClient

    def fake_client(*a, **kw):
        return real(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": {"level": "金卡會員ZZ"}})), follow_redirects=False)
    monkeypatch.setattr(EXT.httpx, "AsyncClient", fake_client)
    yield TestClient(app), db, b, llm, k, token
    app.dependency_overrides.clear()


def test_key_stored_as_hash_only(v1):
    c, db, b, llm, k, token = v1
    assert token.startswith("rk_") and k.key_hash == apikeys.digest(token) and token not in json.dumps(db.execute(text("select row_to_json(a) from api_keys a")).scalars().all())
    assert apikeys.verify(db, token).id == k.id and apikeys.verify(db, token + "x") is None and apikeys.verify(db, "") is None
    k.enabled = False; db.flush()
    assert apikeys.verify(db, token) is None


def test_ask_uses_context_but_stores_none_of_it(v1):
    c, db, b, llm, k, token = v1
    r = c.post("/api/v1/ask", headers={"Authorization": "Bearer " + token},
               json={"message": "收玩具時丟積木", "session": "ext1", "context": {"member_id": "MEMBER-SECRET-9", "child_age": "4", "phone": "0912345678"}})
    d = r.json()
    assert r.status_code == 200 and d["ok"] and d["type"] == "answer" and "trace" not in d
    answer_prompt = [p for k_, p in llm.calls if k_ == "answer"][0]
    assert "MEMBER-SECRET-9" in answer_prompt and "金卡會員ZZ" in answer_prompt and "孩子年齡（歲）：4" in answer_prompt
    assert "0912345678" not in answer_prompt                                    # 沒列在設定裡的欄位不送給模型
    row = db.query(m.Conversation).filter_by(bot_id=b.id).one()
    assert row.unit == "API：會員系統" and row.session == "ext1"
    step = [t for t in row.trace if t["step"] == "外部資料"][0]
    assert step["detail"]["fields"] == ["會員編號", "孩子年齡"] and step["detail"]["sources"][0]["fields"] == ["會員等級"]
    # 整個資料庫都找不到原始外部資料
    db.flush()
    for needle in ["MEMBER-SECRET-9", "金卡會員ZZ", "0912345678"]:
        hit = db.execute(text("select count(*) from conversations c where c::text like :n"), {"n": f"%{needle}%"}).scalar()
        assert hit == 0, needle
        assert db.execute(text("select count(*) from audit_log a where a::text like :n"), {"n": f"%{needle}%"}).scalar() == 0
    assert k.last_used_at is not None


def test_ask_errors(v1):
    c, db, b, llm, k, token = v1
    H = {"Authorization": "Bearer " + token}
    assert c.post("/api/v1/ask", json={"message": "x", "session": "s"}).status_code == 401
    assert c.post("/api/v1/ask", headers={"Authorization": "Bearer rk_nope"}, json={"message": "x", "session": "s"}).status_code == 401
    assert c.post("/api/v1/ask", headers=H, json={"message": "", "session": "s"}).status_code == 422
    assert c.post("/api/v1/ask", headers=H, json={"message": "x"}).status_code == 422
    assert c.post("/api/v1/ask", headers=H, json={"message": "x" * 4001, "session": "s"}).status_code == 422
    b.disabled = True; db.flush()
    assert c.post("/api/v1/ask", headers=H, json={"message": "x", "session": "s"}).status_code == 403


def test_admin_keys_and_integration(env, monkeypatch):  # noqa: F811
    c, db, ws, b, bot2, users, as_ = env
    h = as_("owner")
    K = f"/api/admin/bots/{b.id}/api-keys"
    assert c.post(K, headers=as_("ana"), json={"name": "x"}).status_code == 403
    assert c.post(K, headers=h, json={"name": ""}).status_code == 422
    r = c.post(K, headers=h, json={"name": "會員系統"}).json()
    tok = r["token"]
    assert tok.startswith("rk_") and r["prefix"] == tok[:10]
    lst = c.get(K, headers=h).json()["items"]
    assert len(lst) == 1 and "token" not in lst[0] and "key_hash" not in lst[0]
    assert tok not in json.dumps([dict(a.detail) for a in db.query(m.AuditLog).all()], ensure_ascii=False)
    assert c.patch(f"/api/admin/api-keys/{r['id']}", headers=h, json={"enabled": False}).json()["enabled"] is False
    assert c.patch(f"/api/admin/api-keys/{r['id']}", headers=as_("outsider"), json={"enabled": True}).status_code == 404
    assert c.delete(f"/api/admin/api-keys/{r['id']}", headers=h).json() == {"ok": True}
    I = f"/api/admin/bots/{b.id}/integration"
    monkeypatch.setenv("EXTERNAL_ALLOWED_HOSTS", "api.example.com")
    g = c.get(I, headers=h).json()
    assert g["context_fields"] == [] and g["server"]["allowed_hosts"] == ["api.example.com"]
    bad = c.put(I, headers=h, json={"context_fields": FIELDS, "sources": [dict(SRC, secret_env="JWT_SECRET")]})
    assert bad.status_code == 422 and "sources[0].secret_env" in bad.json()["fields"]
    ok = c.put(I, headers=h, json={"context_fields": FIELDS, "sources": [SRC]}).json()
    assert [f["key"] for f in ok["context_fields"]] == ["member_id", "child_age"] and ok["server"]["secret_env_set"] == {"EXT_SECRET_CRM": False}
    t = c.post(I + "/test", headers=h, json={"context_fields": FIELDS, "sources": [SRC], "context": {}}).json()
    assert t["results"][0]["status"] == "缺少欄位：member_id"


def test_ask_model_down_returns_503(v1):
    c, db, b, llm, k, token = v1
    from services.llm import LLMError
    llm.resp["answer"] = LLMError("down")
    llm.resp["triage"] = LLMError("down")

    async def bad_embed(*a, **kw):
        raise LLMError("down")
    llm.embed = bad_embed
    r = c.post("/api/v1/ask", headers={"Authorization": "Bearer " + token}, json={"message": "x", "session": "s", "context": {"member_id": "LEAK-1"}})
    assert r.status_code in (200, 503)
    assert "LEAK-1" not in r.text
