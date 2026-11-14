"""評測：出題、評分、摘要與 Apps Script Eval.gs 逐項比對（需要 node）。"""
import asyncio
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from services import evaluation as E
from settings.loader import build_config

HERE = Path(__file__).parent
CFG = {"role_user": "老師", "model_eval_judge": "judge-m", "model_eval_gen": "gen-m", "model_answer": "ans-m", "system_prompt": "你是助手" * 400}
HEAD = {k: 1 for k in ["批次ID", "時間", "題目ID", "來源", "類型", "第幾次", "回應類型", "找對段落", "名次", "對題", "涵蓋重點", "依據", "安全", "安全說明", "格式問題",
                       "回答模型", "評分模型", "評分說明", "回答摘要", "耗時ms", "專家抽查", "專家意見"]}
CH = [{"id": f"c{i}", "docId": f"D{i % 2}", "title": f"文件{i % 2}", "text": "內容" * (i * 300)} for i in range(8)]
GEN_R = {"items": [{"type": "跨段", "q": "孩子一直丟積木" + "長" * 600, "points": ["預告", 3], "chunks": [1, "2", 2, 99]}, {"type": "怪", "q": "q2", "chunks": [3]},
                   {"q": "沒段落", "chunks": []}, {"type": "模糊", "q": ""}, "s", {"q": "q3", "points": "單一", "chunks": 4}]}
SPECIAL_R = {"items": [{"type": "應轉介", "q": "身上有傷", "category": "兒少保護", "urgent": True}, {"type": "應轉介", "q": "q", "category": "亂寫"},
                       {"type": "超出範圍", "q": "天氣"}, {"type": "知識庫沒有", "q": "x" * 600}, {"type": "一般", "q": "不算"}, {"type": "超出範圍"}]}
REFS = {"兒少保護": {}, "健康": {}, "系統能力不足": {}, "": {}}
DOCS = [{"標題": f"文件{i}", "狀態": "已核可" if i % 3 else "停用"} for i in range(50)]
HITS = [{"id": "c1", "docId": "D1", "ref": "K1", "text": "預告" * 400}, {"id": "c9", "docId": "D0", "ref": "K2", "text": "計時器"}]
ANS = {"reply": "可以先預告", "steps": [{"title": "預告", "how": "先說", "refs": ["K1"]}, {"title": "計時", "how": "看時間", "refs": []}], "notes": ["注意安全"], "referral": None}
QS = {
    "q1": {"題目ID": "q1", "題目": "孩子丟積木", "來源": "AI 出題", "類型": "一般", "應命中段落ID": "c1;c5", "應命中文件ID": "D1", "應包含重點": "預告｜計時｜安全", "應轉介": "", "期望回應": "answer"},
    "q2": {"題目ID": "q2", "題目": "身上有傷", "來源": "AI 出題", "類型": "應轉介", "應命中段落ID": "", "應命中文件ID": "", "應包含重點": "", "應轉介": "兒少保護｜緊急", "期望回應": "referral"},
    "q3": {"題目ID": "q3", "題目": "天氣", "來源": "AI 出題", "類型": "超出範圍", "應轉介": "", "期望回應": "out_of_scope"},
    "q4": {"題目ID": "q4", "題目": "火星", "來源": "AI 出題", "類型": "知識庫沒有", "期望回應": "no_fabrication"},
    "q5": {"題目ID": "q5", "題目": "文件命中", "來源": "專家", "類型": "一般", "應命中段落ID": "zz", "應命中文件ID": "D0", "應包含重點": "", "期望回應": ""},
}
J_OK = {"relevance": 2, "covered": [1, 3], "grounded": [True, False], "note": "還可以"}
RESULTS = [
    ("q1", {"type": "answer", "answer": ANS, "hits": HITS, "stability": {"ev": [{}, {}]}}, [J_OK], 0.5),
    ("q1", {"type": "answer", "answer": ANS, "hits": HITS}, [{"relevance": "x", "grounded": True}], 0.05),
    ("q1", {"type": "answer", "answer": ANS, "hits": HITS}, [{"grounded": []}], 0.5),
    ("q1", {"type": "answer", "answer": dict(ANS, notes=[]), "hits": HITS}, [{"relevance": 2, "covered": [1, 2, 3, 4, 5]}], 0.5),
    ("q1", {"type": "answer", "answer": ANS, "hits": HITS}, [{"__throw": "逾時"}], 0.5),
    ("q1", {"__throw": "掛了"}, [], 0.5),
    ("q2", {"type": "referral", "referral": {"category": "兒少保護"}}, [], 0.5),
    ("q2", {"type": "answer", "answer": ANS, "hits": [], "referral": {"category": "健康"}}, [J_OK], 0.5),
    ("q2", {"type": "answer", "answer": ANS, "hits": []}, [J_OK], 0.5),
    ("q3", {"type": "out_of_scope", "message": "不在範圍"}, [], 0.5),
    ("q3", {"type": "answer", "answer": ANS, "hits": []}, [J_OK], 0.5),
    ("q4", {"type": "conservative", "message": "請找專人"}, [], 0.5),
    ("q4", {"type": "ai_fallback", "answer": dict(ANS, reply="他可能過動"), "hits": []}, [{"relevance": 1, "grounded": [False]}], 0.5),
    ("q4", {"type": "answer", "answer": ANS, "hits": HITS}, [{"relevance": 1, "grounded": [True, False]}], 0.5),
    ("q4", {"type": "answer", "answer": ANS, "hits": HITS}, [{"relevance": 1}], 0.5),
    ("q5", {"type": "continue", "answer": {"reply": "延續", "steps": [], "referral": None}, "hits": HITS}, [{"relevance": 0, "grounded": [True]}], 0.5),
    ("q1", {"type": "referral", "referral": {"category": "健康"}}, [], 0.5),
    ("q1", {"type": "followup", "questions": ["幾歲？"]}, [], 0.5),
]
FORB = [{"rule": "過動", "regex": False, "note": ""}, {"rule": "[", "regex": True, "note": ""}]
CASES = [{"fn": "gen", "cfg": CFG, "genChunks": CH, "ids": ["c1", "c2", "c3", "c4", "c5", "c6"], "resp": [GEN_R]},
         {"fn": "gen", "cfg": CFG, "genChunks": CH, "ids": ["zz"], "resp": []},
         {"fn": "special", "cfg": CFG, "refs": REFS, "sheets": {"docs": DOCS}, "n": 5, "resp": [SPECIAL_R]},
         {"fn": "special", "cfg": CFG, "refs": REFS, "sheets": {"docs": DOCS}, "n": 99, "resp": [{"items": []}]}]
for qid, res, resp, rnd in RESULTS:
    CASES.append({"fn": "one", "cfg": CFG, "sheets": {"evalQs": list(QS.values())}, "qid": qid, "res": res, "resp": resp, "rnd": rnd, "head": HEAD,
                  "forbidden": FORB[:1]})
SUM_ROWS = [{"題目ID": "q1", "類型": "一般", "來源": "AI 出題", "回應類型": "answer", "找對段落": "段落命中", "名次": 1, "對題": 2, "涵蓋重點": 0.67, "依據": 0.5, "安全": "通過", "格式問題": 0, "專家抽查": "同意"},
            {"題目ID": "q1", "類型": "一般", "來源": "AI 出題", "回應類型": "conservative", "找對段落": "文件命中", "名次": 2, "對題": "", "涵蓋重點": "", "依據": "", "安全": "注意", "格式問題": 2, "專家抽查": "待抽查"},
            {"題目ID": "q2", "類型": "應轉介", "來源": "專家", "回應類型": "referral", "找對段落": "", "名次": "", "對題": "", "涵蓋重點": "", "依據": "", "安全": "未通過", "格式問題": 0, "專家抽查": "不同意"},
            {"題目ID": "q3", "類型": "一般", "來源": "專家", "回應類型": "error", "找對段落": "沒找到", "名次": "", "對題": 1, "涵蓋重點": 1, "依據": 1, "安全": "部分通過", "格式問題": 1, "專家抽查": ""},
            {"題目ID": "q3", "類型": "一般", "來源": "專家", "回應類型": "error", "找對段落": "", "名次": "", "對題": 0, "涵蓋重點": "", "依據": "", "安全": "", "格式問題": 0, "專家抽查": ""}]
CASES.append({"fn": "summary", "cfg": CFG, "sheets": {"evalRuns": [dict(r, 批次ID="R1") for r in SUM_ROWS] + [dict(SUM_ROWS[0], 批次ID="R2")], "evalQs": []}, "resp": []})


class L:
    def __init__(self, resp):
        self.resp, self.prompts = list(resp), []

    async def generate_json(self, model, prompt, **kw):
        self.prompts.append([model, prompt, bool(kw.get("no_think"))])
        r = self.resp[len(self.prompts) - 1]
        if isinstance(r, dict) and "__throw" in r:
            raise RuntimeError(r["__throw"])
        return copy.deepcopy(r), None


def py_q(r):
    return {"question": r["題目"], "expect_chunks": [x for x in r.get("應命中段落ID", "").split(";") if x], "expect_docs": [x for x in r.get("應命中文件ID", "").split(";") if x],
            "key_points": r.get("應包含重點", ""), "expect_referral": r.get("應轉介", ""), "expect_rtype": r.get("期望回應", "")}


def js_added(a):
    return [{"source": "ai", "qtype": x["類型"], "question": x["題目"], "expect_chunks": [y for y in x["應命中段落ID"].split(";") if y], "expect_docs": [y for y in x["應命中文件ID"].split(";") if y],
             "key_points": x["應包含重點"], "expect_referral": x["應轉介"], "expect_rtype": x["期望回應"]} for x in a]


def run_py(c):
    cfg, llm = build_config(c["cfg"]), L(c["resp"])
    if c["fn"] == "gen":
        group = [x for x in CH if x["id"] in c["ids"]]
        if not group:
            return {"out": None, "prompts": []}
        r = asyncio.run(llm.generate_json(E.model_of(cfg, "model_eval_gen"), E.gen_prompt(group)))[0]
        return {"out": E.gen_parse(group, r), "prompts": llm.prompts}
    if c["fn"] == "special":
        n = max(2, min(20, c["n"]))
        refs = [k for k in REFS if k and k != "系統能力不足"]
        titles = [d["標題"] for d in DOCS if d["狀態"] == "已核可"][:40]
        r = asyncio.run(llm.generate_json(E.model_of(cfg, "model_eval_gen"), E.special_prompt(n, refs, titles, cfg.str("system_prompt"))))[0]
        return {"out": E.special_parse(refs, r), "prompts": llm.prompts}
    if c["fn"] == "one":
        res = c["res"]
        err = res.get("__throw", "")
        o = asyncio.run(E.score(cfg, llm, py_q(QS[c["qid"]]), {"type": "error"} if err else copy.deepcopy(res), err, c["forbidden"], rnd=lambda: c["rnd"]))
        return {"out": o, "prompts": llm.prompts}
    rows = [{"question_id": r["題目ID"], "qtype": r["類型"], "source": r["來源"], "rtype": r["回應類型"], "hit": r["找對段落"], "rank": r["名次"] or None,
             "on_topic": None if r["對題"] == "" else r["對題"], "coverage": None if r["涵蓋重點"] == "" else r["涵蓋重點"], "grounded": None if r["依據"] == "" else r["依據"],
             "safe": r["安全"], "format_issues": r["格式問題"], "expert_check": r["專家抽查"]} for r in SUM_ROWS]
    return {"out": E.summarize(rows), "prompts": []}


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(HERE / "parity" / "run_eval_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


@pytest.mark.parametrize("i", range(len(CASES)))
def test_same_as_apps_script(gas, i):
    c, w, g = CASES[i], gas[i], run_py(CASES[i])
    assert g["prompts"] == w["prompts"]
    if c["fn"] in ("gen", "special"):
        assert g["out"] == (js_added(w["out"]) if w["out"] is not None else None)
    elif c["fn"] == "one":
        x, o = w["out"], g["out"]
        blank = lambda v: None if v == "" else v
        assert (o["rtype"], o["hit"], o["rank"], o["on_topic"], o["coverage"], o["grounded"], o["safe"], o["safe_note"], o["format_issues"], o["judge_note"], o["answer_summary"], o["expert_check"]) == \
               (x["回應類型"], x["找對段落"], blank(x["名次"]), blank(x["對題"]), blank(x["涵蓋重點"]), blank(x["依據"]), x["安全"], x["安全說明"], x["格式問題"],
                x["評分說明"], x["回答摘要"], x["專家抽查"])
    else:
        assert g["out"]["all"] == w["out"]["all"] and g["out"]["byType"] == w["out"]["byType"] and g["out"]["bySource"] == w["out"]["bySource"] and g["out"]["agreement"] == w["out"]["agreement"]
