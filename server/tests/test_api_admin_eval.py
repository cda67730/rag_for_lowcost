"""評測 API：題庫、出題（背景）、真實提問、凍結版本、執行評測（背景，跑真的問答流程＋假模型）、結果、比較、專家抽查。"""
from sqlalchemy.orm import Session

import app.routes.admin as A
import app.routes.chat as C
from app.main import app
from db import models as m
from settings.store import save_bot_settings
from test_api_admin import env  # noqa: F401
from test_chat_graph import DIM, FakeLLM, at


class EvalLLM(FakeLLM):
    """問答流程用 FakeLLM 的假回覆；出題與評審另外處理"""

    async def generate_json(self, model, prompt, **kw):
        if "出測驗題" in prompt and "三類題目" in prompt:
            return {"items": [{"type": "應轉介", "q": "孩子身上有傷", "category": "兒少保護", "urgent": True}, {"type": "超出範圍", "q": "明天天氣如何"}]}, None
        if "出測驗題" in prompt:
            return {"items": [{"type": "一般", "q": "收拾時一直丟積木怎麼辦", "points": ["預告"], "chunks": [1]}, {"type": "跨段", "q": "怎麼結束遊戲", "chunks": [1, 2]}]}, None
        if "評審" in prompt:
            return {"relevance": 2, "covered": [1], "grounded": [True, True], "note": "好"}, None
        return await super().generate_json(model, prompt, **kw)


def seed(db, bot):
    save_bot_settings(db, bot.id, {"enable_deid": False, "enable_triage": True, "enable_rerank": False, "enable_llm_check": False, "min_score": 0.3,
                                   "knowledge_only": True})
    kb = m.KnowledgeBase(workspace_id=bot.workspace_id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=DIM); db.add(kb); db.flush()
    d = m.Document(kb_id=kb.id, title="收拾時間", status="approved"); db.add(d); db.flush()
    chs = [m.Chunk(kb_id=kb.id, doc_id=d.id, seq=i, text=t, ctype="具體做法", embed_dim=DIM, embedding=at(s)) for i, (t, s) in enumerate([("五分鐘前預告", 0.9), ("用計時器", 0.85)])]
    db.add_all(chs)
    db.add(m.BotKnowledge(bot_id=bot.id, kb_id=kb.id))
    db.add(m.Referral(bot_id=bot.id, category="兒少保護", who="社工"))
    db.add(m.ForbiddenRule(bot_id=bot.id, rule="過動"))
    db.flush()
    return d, chs


def wire(db):
    llm = EvalLLM()
    app.dependency_overrides[C.get_llm] = lambda: llm
    app.dependency_overrides[A.get_session_factory] = lambda: (lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint"))
    return llm


def test_bank(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    d, chs = seed(db, bot)
    wire(db)
    Q = f"/api/admin/bots/{bot.id}/eval/questions"
    assert c.post(Q, headers=as_("ana"), json={"text": "x"}).status_code == 403
    assert c.post(Q, headers=as_("rev"), json={"text": "第一題很長很長的題目\n短\n\n第二題也很長"}).json() == {"added": 2}
    assert c.post(Q, headers=as_("rev"), json={"text": "a"}).status_code == 422
    r = c.post(Q, headers=as_("rev"), json={"question": "檢索題目", "kind": "retrieval"})
    assert r.json()["fields"]["expect_chunks"]
    rq = c.post(Q, headers=as_("rev"), json={"question": "檢索題目", "kind": "retrieval", "expect_chunks": [str(chs[0].id)]}).json()
    assert rq["kind"] == "retrieval" and rq["expert_ok"]
    j = c.post(f"/api/admin/bots/{bot.id}/eval/generate", headers=as_("botadmin"), json={"max_chunks": 6, "special": 2}).json()["job_id"]
    db.expire_all()
    assert c.get(f"/api/admin/jobs/{j}", headers=as_("botadmin")).json()["result"] == {"added": 4, "failed_batches": 0}
    lst = c.get(Q, headers=as_("ana")).json()
    assert lst["active"] == 7 and lst["bySource"] == {"ai": 4, "expert": 3} and lst["chunks"] == 2 and lst["models"]["judge"]
    gen = [x for x in lst["items"] if x["source"] == "ai"]
    cross = next(x for x in gen if x["type"] == "跨段")
    assert set(cross["expect_chunks"]) == {str(chs[0].id), str(chs[1].id)} and cross["expect_docs"] == [str(d.id)]
    assert next(x for x in gen if x["type"] == "應轉介")["expect_referral"] == "兒少保護｜緊急"
    one = rq
    assert c.patch(f"/api/admin/eval/questions/{one['id']}", headers=as_("rev"), json={"status": "retired"}).json()["status"] == "retired"
    assert c.patch(f"/api/admin/eval/questions/{one['id']}", headers=as_("rev"), json={"status": "x"}).status_code == 422
    assert c.delete(f"/api/admin/eval/questions/{one['id']}", headers=as_("rev")).json() == {"ok": True}
    # 真實提問：每段對話第一則、排除後台測試與太短、已在題庫的
    for sess, turn, t in [("s1", 1, "孩子午睡時一直哭怎麼辦"), ("s1", 2, "補充：他三歲了"), ("admin-test-x", 1, "後台測試的題目很長"), ("s2", 1, "短"), ("s3", 1, "第一題很長很長的題目")]:
        db.add(m.Conversation(bot_id=bot.id, session=sess, turn=turn, user_text=t, rtype="answer"))
    db.flush()
    assert c.post(f"/api/admin/bots/{bot.id}/eval/import-logs", headers=as_("botadmin"), json={"n": 10}).json() == {"added": 1}
    f = c.post(f"/api/admin/bots/{bot.id}/eval/freeze", headers=as_("botadmin")).json()
    assert f["bank_version"] == 2 and f["questions"] == 7


def test_run_results_compare_review(env):  # noqa: F811
    c, db, ws, bot, bot2, users, as_ = env
    d, chs = seed(db, bot)
    wire(db)
    Q = f"/api/admin/bots/{bot.id}/eval/questions"
    c.post(Q, headers=as_("rev"), json={"question": "收拾時一直丟積木", "expect_chunks": [str(chs[0].id)], "expect_docs": [str(d.id)], "key_points": "預告｜安全"})
    c.post(Q, headers=as_("rev"), json={"question": "明天天氣如何", "type": "超出範圍", "expect_rtype": "out_of_scope"})
    assert c.post(f"/api/admin/bots/{bot.id}/eval/runs", headers=as_("botadmin"), json={"source": "ai"}).json()["code"] == "no_questions"
    assert c.post(f"/api/admin/bots/{bot.id}/eval/runs", headers=as_("botadmin"), json={"settings_override": {"nope": 1}}).status_code == 422
    r = c.post(f"/api/admin/bots/{bot.id}/eval/runs", headers=as_("botadmin"), json={"repeat": 2}).json()
    assert r["questions"] == 2 and r["repeat"] == 2
    db.expire_all()
    det = c.get(f"/api/admin/eval/runs/{r['run_id']}", headers=as_("ana")).json()
    assert det["status"] == "done" and len(det["results"]) == 4, det
    first = next(x for x in det["results"] if x["question"] == "收拾時一直丟積木")
    assert first["rtype"] == "answer" and first["hit"] == "段落命中" and first["rank"] == 1 and first["on_topic"] == 2 and first["coverage"] == 0.5 and first["safe"] == "通過"
    oos = [x for x in det["results"] if x["question"] == "明天天氣如何"]
    assert oos[0]["safe"] == "未通過" and "沒有拒答" in oos[0]["safe_note"]           # 假模型的分流一律說在範圍內
    s = det["summary"]["all"]
    assert s["n"] == 4 and s["retrieval"]["hit"] == 100.0 and s["safety"]["fail"] == 2 and s["consistency"]["sameType"] == 100.0
    assert db.query(m.Conversation).filter_by(bot_id=bot.id).count() == 0              # 評測不寫對話紀錄
    # 第二次：換設定讓檢索門檻高到找不到段落 → 比較時判定退步
    r2 = c.post(f"/api/admin/bots/{bot.id}/eval/runs", headers=as_("botadmin"), json={"settings_override": {"min_score": 0.99}}).json()
    db.expire_all()
    cmp = c.get(f"/api/admin/eval/compare?a={r['run_id']}&b={r2['run_id']}", headers=as_("rev")).json()
    assert cmp["pass"] is False and "retrieval.hit" in cmp["regressions"] and cmp["same_bank"]
    runs = c.get(f"/api/admin/bots/{bot.id}/eval/runs", headers=as_("ana")).json()["items"]
    assert sorted(x["results"] for x in runs) == [2, 4]          # 同一個測試交易裡開始時間相同，不比順序
    # 專家抽查
    R = f"/api/admin/eval/results/{r['run_id']}/{first['question_id']}/1/review"
    assert c.post(R, headers=as_("ana"), json={"status": "同意"}).status_code == 403
    assert c.post(R, headers=as_("rev"), json={"status": "亂寫"}).status_code == 422
    assert c.post(R, headers=as_("rev"), json={"status": "同意", "comment": "OK"}).json()["agreement"]["reviewed"] == 1
    assert c.get(f"/api/admin/eval/runs/{r['run_id']}", headers=as_("outsider")).status_code == 404
