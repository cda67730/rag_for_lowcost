"""問答流程端到端：真資料庫＋假模型，逐條走過每個分支（緊急轉介、超出範圍、追問、回答、保守、AI 補足、重寫、延續）。"""
import asyncio
import copy
import json
import random

import pytest

from db import models as m
from graph.chat_graph import Deps, run_chat
from services.llm import LLMError
from settings.store import save_bot_settings

DIM = 768


def unit(v):
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


RND = random.Random(3)
Q = unit([RND.gauss(0, 1) for _ in range(DIM)])


def at(sim):
    r = [RND.gauss(0, 1) for _ in Q]
    d = sum(a * b for a, b in zip(r, Q))
    r = unit([a - d * b for a, b in zip(r, Q)])
    return [sim * a + (1 - sim * sim) ** 0.5 * b for a, b in zip(Q, r)]


TRI = {"intent": "新問題", "focus": "收玩具時丟積木", "referral": {"category": "none", "level": "none", "reason": ""}, "topics": ["情緒行為"],
       "in_scope": True, "info_enough": True, "missing": [], "deid": []}
GOOD = {"reply": "可以先預告", "steps": [{"title": "預告", "how": "五分鐘前提醒（K1）", "refs": ["K1"]}, {"title": "計時", "how": "用計時器", "refs": ["K2"]}],
        "notes": [], "referral": None, "knowledge_gap": None}
OK_CHECK = {"issues": [], "relevance": {"addressed": True, "cause": None, "note": ""}}


class FakeLLM:
    """依 prompt 內容判斷是哪一種呼叫；回應可以是值、清單（依序取）或例外"""

    def __init__(self, **resp):
        self.resp = {"triage": TRI, "answer": GOOD, "check": OK_CHECK, "rerank": {"scores": [3, 3, 1]}, "followup": {"empathy": "辛苦了", "questions": ["幾歲？", "什麼時候？", "多久？"]}}
        self.resp.update(resp)
        self.calls: list[tuple[str, str]] = []
        self.sent: list[list] = []          # [種類, 模型, temperature, no_think]
        self.embeds = 0

    @staticmethod
    def kind(p):
        for k, mark in [("triage", "的分流器"), ("rerank", "挑選參考資料"), ("check", "回答的審查器"), ("followup", '{"empathy":"","questions"'), ("deid", "去除個人資訊")]:
            if mark in p:
                return k
        return "answer"

    async def generate_json(self, model, prompt, **kw):
        k = self.kind(prompt)
        self.calls.append((k, prompt))
        self.sent.append([k, model, kw.get("temperature"), bool(kw.get("no_think"))])
        r = self.resp[k]
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, dict) and "__throw" in r:      # 與 Apps Script 比對用的寫法
            raise LLMError(r["__throw"])
        return copy.deepcopy(r), None

    async def embed(self, model, texts, *, dim, task="document"):
        self.embeds += 1
        assert task == "query" and dim == DIM and len(texts) == 2
        return [Q, Q]

    def kinds(self):
        return [k for k, _ in self.calls]


@pytest.fixture
def bot(db):
    ws = m.Workspace(slug="w", name="w"); db.add(ws); db.flush()
    b = m.Bot(workspace_id=ws.id, slug="b", name="b"); db.add(b); db.flush()
    save_bot_settings(db, b.id, {"enable_deid": True, "deid_mode": "ai", "enable_triage": True, "enable_rerank": False, "enable_llm_check": True,
                                 "enable_relevance_check": True, "knowledge_only": True, "enable_case_flow": True, "enable_kg": False, "min_score": 0.3,
                                 "role_user": "老師", "role_subject": "孩子"})
    kb = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=DIM); db.add(kb); db.flush()
    doc = m.Document(kb_id=kb.id, title="收拾時間", status="approved", source_note="手冊"); db.add(doc); db.flush()
    for i, (sim, t) in enumerate([(0.9, "五分鐘前預告"), (0.85, "用計時器"), (0.8, "背景原則")]):
        db.add(m.Chunk(kb_id=kb.id, doc_id=doc.id, seq=i, text=t, page=str(i + 1), ctype="具體做法" if i < 2 else "原則／背景", embed_dim=DIM, embedding=at(sim)))
    db.add(m.BotKnowledge(bot_id=b.id, kb_id=kb.id, label="手冊"))
    db.add(m.Skill(bot_id=b.id, skill_key="s1", name="理解行為", topics=["情緒行為"], content="先理解"))
    db.add(m.Skill(bot_id=b.id, skill_key="s2", name="轉介", topics=[], always_on=True, content="一律"))
    db.add(m.Skill(bot_id=b.id, skill_key="s3", name="通用", topics=["別的"], content="通用框架"))
    db.add(m.Referral(bot_id=b.id, category="兒少保護", who="社工", contact="113", now_action="先通報"))
    db.add(m.Referral(bot_id=b.id, category="系統能力不足", who="輔導老師"))
    db.add(m.ForbiddenRule(bot_id=b.id, rule="過動", kind="word", note="不診斷"))
    db.flush()
    return db, b


def chat(bot, llm, msg="小宇收玩具時一直丟積木", log=True, **req):
    db, b = bot
    return asyncio.run(run_chat(Deps(db, llm, b.id, log=log, with_trace=True), {"message": msg, "session": "s1", **req}))


def logs(db, b):
    return db.query(m.Conversation).filter_by(bot_id=b.id).order_by(m.Conversation.id).all()


def steps(r):
    return [t["step"] for t in r["trace"]]


def test_answer_path_with_merged_deid_and_log(bot):
    llm = FakeLLM(triage=dict(TRI, deid=[{"text": "小宇", "type": "subject"}], focus="小宇收玩具"))
    r = chat(bot, llm)
    assert r["type"] == "answer" and llm.kinds() == ["triage", "answer", "check"] and llm.embeds == 1
    assert r["nameMap"] == {"小宇": "孩子A"} and r["deidText"].startswith("孩子A") and r["triage"]["focus"] == "孩子A收玩具"
    assert "deid" not in r["triage"] and r["triage"]["intent_key"] == "new"
    assert r["answer"]["steps"][0]["how"] == "五分鐘前提醒" and [s["ref"] for s in r["sources"]] == ["K1", "K2"]
    assert r["sources"][0]["title"] == "收拾時間" and r["metrics"]["cited"] == 2 and r["metrics"]["skills"] == ["理解行為", "轉介"]
    assert all("小宇" not in p for k, p in llm.calls if k != "triage")      # 分流之後的 prompt 都是代稱
    assert steps(r)[:4] == ["P03 去識別化", "L4 分流", "P06 同義詞擴充", "P04 狀態機"] and "AI（併入 L4）" in r["trace"][0]["detail"]
    db, b = bot
    (row,) = logs(db, b)
    assert row.rtype == "answer" and "小宇" not in row.user_text and row.cited == 2 and row.hits == 3
    assert all("prompt" not in (t["detail"] if isinstance(t["detail"], dict) else {}) for t in row.trace)
    assert {f["key"] for f in r["schema"]} == {"reply", "steps", "notes"}


def test_missing_deid_list_falls_back_to_rule(bot):
    t = dict(TRI); t.pop("deid")
    r = chat(bot, FakeLLM(triage=t))
    assert r["nameMap"] == {"小宇": "孩子A"} and {"at": "L4", "kind": "缺欄位", "note": "deid"} in r["stability"]["ev"]


def test_urgent_referral_stops_before_answer(bot):
    llm = FakeLLM(triage=dict(TRI, referral={"category": "兒少保護", "level": "緊急", "reason": "小宇身上有傷"}, deid=[{"text": "小宇", "type": "subject"}]))
    r = chat(bot, llm)
    assert r["type"] == "referral" and llm.kinds() == ["triage"]
    assert r["referral"] == {"category": "兒少保護", "level": "緊急", "reason": "孩子A身上有傷", "who": "社工", "contact": "113", "now": "先通報"}


def test_out_of_scope(bot):
    for tri in [dict(TRI, intent="與主題無關"), dict(TRI, in_scope=False)]:
        llm = FakeLLM(triage=tri)
        r = chat(bot, llm, log=False)
        assert r["type"] == "out_of_scope" and r["message"] and llm.kinds() == ["triage"]


def test_followup_once_then_answer(bot):
    llm = FakeLLM(triage=dict(TRI, info_enough=False, missing=["時段"]))
    r = chat(bot, llm)
    assert r["type"] == "followup" and r["questions"] == ["幾歲？", "什麼時候？"] and r["followups"] == 1
    assert "目前缺少：時段" in llm.calls[-1][1]
    hist = [{"role": "teacher", "text": "小宇收玩具時一直丟積木"}, {"role": "bot", "text": "辛苦了"}]
    llm2 = FakeLLM(triage=dict(TRI, intent="補充同一問題", info_enough=False))
    r2 = chat(bot, llm2, msg="大概在下午", history=hist, followups=1, nameMap=r["nameMap"])
    assert r2["type"] == "answer" and r2["newCase"] is False      # 追問上限 1 次，第二次直接回答


def test_no_knowledge_conservative_and_ai_fallback(bot):
    db, b = bot
    llm = FakeLLM()
    db.query(m.Document).update({"status": "disabled"}); db.flush()
    r = chat(bot, llm, log=False)
    assert r["type"] == "conservative" and "輔導老師" in r["message"] and r["quality"]["gap"]["kind"] == "沒有可用知識" and llm.kinds() == ["triage"]
    save_bot_settings(db, b.id, {"knowledge_only": False})
    llm = FakeLLM(answer=dict(GOOD, steps=[{"title": "一般做法", "how": "先觀察", "refs": []}, {"title": "再試", "how": "x", "refs": []}]))
    r = chat(bot, llm, log=False)
    assert r["type"] == "ai_fallback" and r["answer"]["notice"] and r["quality"]["gap"]["outcome"] == "AI 補足"
    assert "知識不足" in llm.calls[1][1] or "補足" in llm.calls[1][1]


def test_rewrite_once_then_pass(bot):
    bad = dict(GOOD, reply="他可能過動")
    llm = FakeLLM(answer=[bad, GOOD])
    r = chat(bot, llm)
    assert r["type"] == "answer" and r["rewritten"] is True and llm.kinds() == ["triage", "answer", "check", "answer", "check"]
    assert "不可使用「過動」" in llm.calls[3][1] and r["stability"]["rewriteWhy"] == ["P08 規則檢查"]
    assert steps(r).count("P07 Prompt 組裝") == 1 and "A1 重寫" in steps(r)


def test_still_failing_after_rewrite_is_conservative(bot):
    llm = FakeLLM(check={"issues": [{"type": "安全", "severe": True, "note": "有風險"}], "relevance": {"addressed": True}})
    r = chat(bot, llm)
    assert r["type"] == "conservative" and r["checks"][2]["pass"] is False and llm.kinds().count("answer") == 2
    assert r["quality"]["l5Final"] == "保守回答" and len(r["quality"]["l5"]) == 2


def test_invalid_json_counts_as_failed_attempt(bot):
    llm = FakeLLM(answer=[LLMError("模型回覆不是有效的 JSON"), GOOD])
    r = chat(bot, llm)
    assert r["type"] == "answer" and r["rewritten"] and r["stability"]["rewriteWhy"] == ["格式"]
    assert "請只輸出 JSON" in llm.calls[2][1]
    with pytest.raises(LLMError):
        chat(bot, FakeLLM(answer=LLMError("連線逾時")), log=False)


def test_model_reported_gap(bot):
    db, b = bot
    llm = FakeLLM(answer=dict(GOOD, knowledge_gap="段落在談別的年齡"))
    r = chat(bot, llm, log=False)
    assert r["type"] == "conservative" and r["quality"]["gap"]["kind"] == "回答時發現知識不足" and "check" not in llm.kinds()
    save_bot_settings(db, b.id, {"knowledge_only": False})
    llm = FakeLLM(answer=[dict(GOOD, knowledge_gap="段落在談別的年齡"), GOOD])
    r = chat(bot, llm, log=False)
    assert r["type"] == "ai_fallback" and llm.kinds() == ["triage", "answer", "answer", "check"]


def test_relevance_gap_extends_attempts(bot):
    db, b = bot
    save_bot_settings(db, b.id, {"knowledge_only": False})
    gap = {"issues": [], "relevance": {"addressed": False, "cause": "知識不足", "note": "沒談到"}}
    llm = FakeLLM(check=[{"issues": [{"type": "語氣", "severe": True, "note": "硬"}], "relevance": {"addressed": True}}, gap, OK_CHECK])
    r = chat(bot, llm, log=False)
    # 第 1 次 L5 不過→重寫；第 2 次發現知識不足→改 AI 補足並多給一次；第 3 次通過
    assert r["type"] == "ai_fallback" and llm.kinds().count("answer") == 3 and r["quality"]["gap"]["kind"] == "檢查發現知識不足"


def test_continue_mode_after_full_rounds(bot):
    hist = [{"role": "teacher", "text": "第一則"}, {"role": "bot", "text": "回答"}]
    llm = FakeLLM(triage=dict(TRI, intent="補充同一問題"), answer={"reply": "延續", "steps": [], "referral": None})
    r = chat(bot, llm, msg="又發生了", history=hist, answered=2, log=False)
    assert r["type"] == "continue" and r["answered"] == 3 and 1 <= len(r["answer"]["options"]) <= 4
    assert "第一則" in llm.calls[1][1]


def test_triage_and_deid_off(bot):
    db, b = bot
    save_bot_settings(db, b.id, {"enable_triage": False, "enable_deid": False})
    llm = FakeLLM()
    r = chat(bot, llm, msg="情緒行為：小宇丟積木", log=False)
    assert r["type"] == "answer" and "triage" not in llm.kinds() and r["triage"]["topics"] == ["情緒行為"] and r["triage"]["skipped"]
    assert r["nameMap"] == {} and r["deidText"] == "情緒行為：小宇丟積木" and r["stability"]["models"]["triage"] == ""


def test_rule_deid_and_default_skill(bot):
    db, b = bot
    save_bot_settings(db, b.id, {"deid_mode": "rule", "default_skill": "通用"})
    llm = FakeLLM(triage=dict(TRI, topics=[]))
    r = chat(bot, llm, log=False)
    assert r["nameMap"] == {"小宇": "孩子A"} and "deid" not in llm.kinds() and r["metrics"]["skills"] == ["轉介", "通用"]
    assert "【通用】" in llm.calls[1][1]


def test_rerank_and_kg_only(bot):
    db, b = bot
    save_bot_settings(db, b.id, {"enable_rerank": True, "enable_kg": True, "kg_mode": "only"})
    llm = FakeLLM()
    r = chat(bot, llm, log=False)
    assert r["type"] == "conservative" and "知識圖譜" in steps(r)      # 沒有圖譜→only 模式沒有段落
    save_bot_settings(db, b.id, {"enable_kg": False})
    llm = FakeLLM(rerank={"scores": [3, 0, 1]})
    r = chat(bot, llm, log=False)
    assert llm.kinds()[:2] == ["triage", "rerank"] and r["metrics"]["direct"] == 1 and r["metrics"]["retrieved"] == 2


def test_turn_number_and_unique_log(bot):
    db, b = bot
    chat(bot, FakeLLM(), turn=5)
    hist = [{"role": "teacher", "text": "a"}, {"role": "bot", "text": "b"}]
    chat(bot, FakeLLM(), history=hist)
    assert [x.turn for x in logs(db, b)] == [5, 2]
    json.dumps([x.trace for x in logs(db, b)], ensure_ascii=False)



def test_duplicate_turn_logged_as_next_turn(bot):
    """使用者重送或重新整理後再問：同一輪已經有紀錄，就排到下一輪，回答照常回傳"""
    db, b = bot
    assert chat(bot, FakeLLM(), turn=5)["type"] == "answer"
    assert chat(bot, FakeLLM(), turn=5)["type"] == "answer"
    assert chat(bot, FakeLLM(), turn=5)["type"] == "answer"
    assert [x.turn for x in logs(db, b)] == [5, 6, 7]


def test_log_failure_does_not_fail_answer(bot, monkeypatch):
    import graph.chat_graph as G
    def boom(**kw):
        raise ValueError("寫不進去")
    monkeypatch.setattr(G, "Conversation", boom)
    assert chat(bot, FakeLLM())["type"] == "answer"
    db, b = bot
    assert logs(db, b) == [] and db.scalar(__import__("sqlalchemy").text("select 1")) == 1     # session 還能用


class SlowLLM(FakeLLM):
    """指定的呼叫種類要等很久；順便記下每次呼叫模型時是不是還握著資料庫交易"""

    def __init__(self, db, slow=(), **resp):
        super().__init__(**resp)
        self.db, self.slow, self.in_tx = db, slow, []

    async def generate_json(self, model, prompt, **kw):
        self.in_tx.append(self.db.in_transaction())
        if self.kind(prompt) in self.slow:
            await asyncio.sleep(5)
        return await super().generate_json(model, prompt, **kw)

    async def embed(self, model, texts, *, dim, task="document"):
        self.in_tx.append(self.db.in_transaction())
        return await super().embed(model, texts, dim=dim, task=task)


def test_no_db_transaction_held_while_waiting_for_model(bot):
    db, b = bot
    llm = SlowLLM(db)
    db.commit()
    r = chat(bot, llm)
    assert r["type"] == "answer" and len(llm.in_tx) >= 4 and not any(llm.in_tx)


def test_turn_timeout_gives_conservative_and_logs(bot):
    db, b = bot
    llm = SlowLLM(db, slow=("answer",), triage=dict(TRI, deid=[{"text": "小宇", "type": "subject"}]))
    r = asyncio.run(run_chat(Deps(db, llm, b.id, with_trace=True), {"message": "小宇收玩具時一直丟積木", "session": "s1", "turn": 3}, timeout=0.5))
    assert r["type"] == "conservative" and r["quality"]["timeout"] is True and r["sources"] == []
    assert "輔導老師" in r["message"]                         # 保守回答帶「系統能力不足」的轉介對象
    assert r["trace"][-1]["step"] == "P10 降級" and r["nameMap"] == {"小宇": "孩子A"}
    assert any(e["kind"] == "逾時" for e in r["stability"]["ev"])
    (row,) = logs(db, b)
    assert row.rtype == "conservative" and row.turn == 3 and "小宇" not in row.user_text and row.quality["timeout"] is True


def test_turn_timeout_keeps_names_found_by_model(bot):
    """L4 已經回報了規則抓不到的名字，之後才逾時：紀錄要用 L4 的去識別化結果"""
    db, b = bot
    llm = SlowLLM(db, slow=("answer",), triage=dict(TRI, deid=[{"text": "王大同", "type": "subject"}]))
    r = asyncio.run(run_chat(Deps(db, llm, b.id), {"message": "今天王大同收玩具時一直丟積木", "session": "s1"}, timeout=0.5))
    assert r["type"] == "conservative" and r["nameMap"] == {"王大同": "孩子A"}
    (row,) = logs(db, b)
    assert "王大同" not in row.user_text


def test_turn_timeout_after_separate_ai_deid(bot):
    """不併入 L4、單獨呼叫去識別化的情況，一樣保留模型找出的名字"""
    db, b = bot
    save_bot_settings(db, b.id, {"enable_triage": False})
    llm = SlowLLM(db, slow=("answer",), deid={"items": [{"text": "王大同", "type": "subject"}]})
    r = asyncio.run(run_chat(Deps(db, llm, b.id), {"message": "今天王大同收玩具時一直丟積木", "session": "s1"}, timeout=0.5))
    assert r["type"] == "conservative" and "deid" in llm.kinds()
    (row,) = logs(db, b)
    assert "王大同" not in row.user_text


def test_turn_timeout_before_deid_finished_uses_rule(bot):
    """分流（含去識別化）就逾時：紀錄改用規則去識別化，已知代稱照樣套用"""
    db, b = bot
    llm = SlowLLM(db, slow=("triage",))
    r = asyncio.run(run_chat(Deps(db, llm, b.id), {"message": "小宇跟小明搶玩具", "session": "s1", "nameMap": {"小宇": "孩子A"}}, timeout=0.5))
    assert r["type"] == "conservative" and "trace" not in r and r["deidText"].startswith("孩子A")
    (row,) = logs(db, b)
    assert "小宇" not in row.user_text and "小明" not in row.user_text      # 新出現的名字也用規則處理掉


def test_case_flow_off_always_full_with_whole_conversation(bot):
    db, b = bot
    save_bot_settings(db, b.id, {"enable_case_flow": False})
    hist = [{"role": "teacher", "text": "第一則"}, {"role": "bot", "text": "回答"}]
    llm = FakeLLM()      # 分流說是新問題，但個案機制關閉→當成同一段對話
    r = chat(bot, llm, msg="又一題", history=hist, answered=5, log=False)
    assert r["type"] == "answer" and r["newCase"] is False and r["triage"]["intent_key"] == "more" and "第一則" in llm.calls[1][1]


def test_more_in_full_mode_uses_whole_case(bot):
    hist = [{"role": "teacher", "text": "第一則"}, {"role": "bot", "text": "回答"}]
    llm = FakeLLM(triage=dict(TRI, intent="補充同一問題"))
    r = chat(bot, llm, msg="補充細節", history=hist, log=False)
    assert r["type"] == "answer" and "第一則\n補充細節" in llm.calls[1][1]


def test_knowledge_judgement_only_in_full_mode(bot):
    db, b = bot
    save_bot_settings(db, b.id, {"enable_rerank": True})
    llm = FakeLLM(rerank={"scores": [1, 1, 1]})
    r = chat(bot, llm, log=False)
    assert r["type"] == "conservative" and r["quality"]["gap"]["kind"] == "只有背景或原則"
    hist = [{"role": "teacher", "text": "第一則"}, {"role": "bot", "text": "回答"}]
    db.query(m.Document).update({"status": "disabled"}); db.flush()
    llm = FakeLLM(triage=dict(TRI, intent="補充同一問題"), answer={"reply": "延續", "steps": [], "referral": None})
    r = chat(bot, llm, msg="又發生了", history=hist, answered=2, log=False)
    assert r["type"] == "continue" and r["quality"]["gap"] is None
