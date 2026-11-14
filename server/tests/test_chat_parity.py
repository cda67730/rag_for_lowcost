"""整個回合與 Apps Script runChat_ 比對（需要 node）：模型回覆與檢索結果兩邊給一樣的，比對分支、回傳內容與流程紀錄。"""
import asyncio
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import graph.chat_graph as G
from services.retrieval import SearchResult
from settings.store import save_bot_settings
from test_chat_graph import GOOD, OK_CHECK, TRI, FakeLLM, bot  # noqa: F401  （bot 是 fixture）

HERE = Path(__file__).parent
POOL = [{"ref": f"K{i + 1}", "id": f"c{i}", "doc_id": "d1", "kb_id": "k", "label": "手冊", "title": "收拾時間", "source": "手冊", "evidence": "", "page": str(i + 1),
         "text": t, "score": s, "type": ty} for i, (t, s, ty) in enumerate([("五分鐘前預告", 0.9, "具體做法"), ("用計時器", 0.85, "具體做法"), ("背景原則", 0.8, "原則／背景")])]
CANDS = [{"rank": i + 1, "ref": h["ref"], "id": h["id"], "score": h["score"], "text": h["text"]} for i, h in enumerate(POOL)]
BASE = {"enable_deid": "TRUE", "deid_mode": "ai", "enable_triage": "TRUE", "enable_rerank": "FALSE", "enable_llm_check": "TRUE", "enable_relevance_check": "TRUE",
        "knowledge_only": "TRUE", "enable_case_flow": "TRUE", "enable_kg": "FALSE", "min_score": "0.3", "role_user": "老師", "role_subject": "孩子",
        "model_triage": "gemini-tri", "model_answer": "gemini-ans", "model_check": "gemini-chk", "model_rerank": "", "temperature": "0.7"}
SKILLS = [{"id": "s1", "name": "理解行為", "tags": ["情緒行為"], "always": False, "content": "先理解", "version": 1},
          {"id": "s2", "name": "轉介", "tags": [], "always": True, "content": "一律", "version": 1},
          {"id": "s3", "name": "通用", "tags": ["別的"], "always": False, "content": "通用框架", "version": 1}]
REFS = {"兒少保護": {"who": "社工", "contact": "113", "now": "先通報"}, "系統能力不足": {"who": "輔導老師", "contact": "", "now": ""}}
FORB = [{"rule": "過動", "regex": False, "note": "不診斷"}]
HIST = [{"role": "teacher", "text": "小宇第一則"}, {"role": "bot", "text": "回答"}]
DEID = [{"text": "小宇", "type": "subject"}]
GAP = {"issues": [], "relevance": {"addressed": False, "cause": "知識不足", "note": "沒談到"}}
SEV = {"issues": [{"type": "安全", "severe": True, "note": "有風險"}], "relevance": {"addressed": True}}

SCEN = {
    "answer": ({}, {"triage": dict(TRI, deid=DEID, focus="小宇收玩具")}, {}),
    "no_deid_list": ({}, {"triage": {k: v for k, v in TRI.items() if k != "deid"}}, {}),
    "urgent": ({}, {"triage": dict(TRI, referral={"category": "兒少保護", "level": "緊急", "reason": "小宇有傷"}, deid=DEID)}, {}),
    "refer_attached": ({}, {"triage": dict(TRI, referral={"category": "兒少保護", "level": "建議諮詢", "reason": "觀察"})}, {}),
    "off": ({}, {"triage": dict(TRI, intent="與主題無關")}, {}),
    "followup": ({}, {"triage": dict(TRI, info_enough=False, missing=["時段", "小宇幾歲"], deid=DEID)}, {}),
    "followup_done": ({}, {"triage": dict(TRI, intent="補充同一問題", info_enough=False)}, {"history": HIST, "followups": 1}),
    "rewrite": ({}, {"answer": [dict(GOOD, reply="他可能過動"), GOOD]}, {}),
    "fail_twice": ({}, {"check": SEV}, {}),
    "bad_json": ({}, {"answer": [{"__throw": "模型回覆不是有效的 JSON"}, GOOD]}, {}),
    "gap_cons": ({}, {"answer": dict(GOOD, knowledge_gap="段落在談別的年齡")}, {}),
    "gap_fallback": ({"knowledge_only": "FALSE"}, {"answer": [dict(GOOD, knowledge_gap="別的年齡"), GOOD]}, {}),
    "rel_gap": ({"knowledge_only": "FALSE"}, {"check": [SEV, GAP, OK_CHECK]}, {}),
    "continue": ({}, {"triage": dict(TRI, intent="補充同一問題"), "answer": {"reply": "延續（K1）", "steps": [{"title": "t", "how": "h", "refs": ["K1"]}], "referral": None}}, {"history": HIST, "answered": 2}),
    "continue_opts": ({}, {"triage": dict(TRI, intent="要求特定協助"), "answer": {"reply": "x", "steps": [], "options": ["a", "", "b", "c", "d", "e"], "referral": None}}, {"history": HIST}),
    "case_flow_off": ({"enable_case_flow": "FALSE"}, {}, {"history": HIST, "answered": 5}),
    "triage_off": ({"enable_triage": "FALSE", "enable_deid": "FALSE"}, {}, {}),
    "rule_deid_default_skill": ({"deid_mode": "rule", "default_skill": "通用"}, {"triage": dict(TRI, topics=[])}, {}),
    "rerank_principle_only": ({"enable_rerank": "TRUE"}, {"rerank": {"scores": [1, 1, 1]}}, {}),
    "rerank_ok": ({"enable_rerank": "TRUE"}, {"rerank": {"scores": [3, 0, 2]}, "answer": dict(GOOD, steps=[GOOD["steps"][0], dict(GOOD["steps"][1], refs=["K2"])])}, {}),
    "fallback_ai": ({"knowledge_only": "FALSE", "enable_rerank": "TRUE"}, {"rerank": {"scores": [1, 1, 1]}}, {}),
    "l5_off": ({"enable_llm_check": "FALSE", "enable_relevance_check": "FALSE"}, {}, {}),
}


def resp_for(extra):
    r = {"triage": TRI, "answer": GOOD, "check": OK_CHECK, "rerank": {"scores": [3, 3, 1]}, "followup": {"empathy": "辛苦了", "questions": ["幾歲？", "什麼時候？", "多久？"]}}
    r.update(extra)
    return r


def js_pool():
    return [dict({k: v for k, v in h.items() if k != "doc_id"}, docId=h["doc_id"]) for h in POOL]


def pick(r):
    """兩邊都有、應該一致的欄位；ms 與實作細節（docId、穩定度事件）不比"""
    keep = ["type", "nameMap", "deidText", "triage", "intent", "newCase", "answered", "answer", "referral", "checks", "rewritten", "followups", "empathy", "questions", "message", "quality"]
    out = {k: r.get(k) for k in keep}
    out["sources"] = [{k: s[k] for k in ("ref", "title", "source", "page", "snippet")} for s in r.get("sources") or []]
    out["metrics"] = {k: (r.get("metrics") or {}).get(k) for k in ("retrieved", "top", "cited", "skills", "actionable", "direct", "usedRefs")}
    out["rewriteWhy"] = r["stability"]["rewriteWhy"]
    out["schema"] = r["schema"]
    out["trace"] = [(t["step"], t["kind"], t["detail"] if t["kind"] in ("text", "triage", "checks", "skills") else None) for t in r["trace"]]
    return json.loads(re.sub(r"檢查未完成（略過）：[^\"]*", "檢查未完成（略過）", json.dumps(out, ensure_ascii=False)))


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    cases = []
    for name, (cfg, extra, req) in SCEN.items():
        cases.append({"cfg": dict(BASE, **cfg), "skills": SKILLS, "refs": REFS, "forbidden": FORB, "resp": resp_for(extra),
                      "search": {"minScore": 0.3, "topK": 12, "total": 3, "cands": CANDS, "hits": js_pool(), "kgMode": None},
                      "req": {"message": "小宇收玩具時一直丟積木", "session": "s", **req}})
    r = subprocess.run(["node", str(HERE / "parity" / "run_chat_gas.js")], input=json.dumps(cases, ensure_ascii=False), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return dict(zip(SCEN, json.loads(r.stdout)))


@pytest.mark.parametrize("name", list(SCEN))
def test_same_as_apps_script(gas, bot, monkeypatch, name):  # noqa: F811
    db, b = bot
    cfg, extra, req = SCEN[name]
    save_bot_settings(db, b.id, dict(BASE, **cfg))
    monkeypatch.setattr(G, "search", lambda *a, **k: SearchResult(0.3, 12, 3, copy.deepcopy(CANDS), copy.deepcopy(POOL), None, []))
    llm = FakeLLM(**copy.deepcopy(resp_for(extra)))
    r = asyncio.run(G.run_chat(G.Deps(db, llm, b.id, log=False, with_trace=True), {"message": "小宇收玩具時一直丟積木", "session": "s", **req}))
    want = gas[name]
    assert llm.sent == want["calls"]      # 呼叫順序、模型、temperature、no_think
    py, js = pick(r), pick(want["r"])
    for k in js:
        assert py[k] == js[k], (name, k)
