"""回答驗證、清理與檢查：與 Apps Script 原版逐項比對（需要 node）。"""
import asyncio
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from graph.check import clean_answer, run_checks, strip_refs, validate_answer
from settings.loader import build_config

HERE = Path(__file__).parent
ROLES = {"role_user": "老師", "role_subject": "孩子", "domain_desc": "學前融合", "role_escalate": "園長"}
NO_CITE = dict(ROLES, enable_citation="FALSE")
FREE = dict(ROLES, enable_answer_schema="FALSE")
CUSTOM = dict(ROLES, answer_schema=json.dumps([{"key": "reply", "type": "text", "modes": "both", "required": True},
                                                {"key": "tips", "type": "list", "modes": "full"},
                                                {"key": "steps", "type": "items", "modes": "both", "count": "1-2", "count_continue": "0-1"}]))
CFGS = [{}, ROLES, NO_CITE, FREE, CUSTOM]

TEXTS = ["先預告(K1)", "做法（K1、K2）。", "句尾 K3", "句尾 K3, K4  ", "【K12】開頭", "[K1,K2] 中間 (K9)", "沒有出處", "K1 不是結尾的 K1 呢", "　全形空白（K2）　", None, 12, ["a(K1)", "b"], {"x": "c（K1）"}]
ANSWERS = [
    None, [], "文字", {},
    {"reply": "辛苦了(K1)", "understanding": "他在等", "steps": [{"title": "預告（K1）", "how": "五分鐘前 K2", "refs": ["K1", "K2"]}], "referral": None},
    {"reply": 1, "steps": "不是陣列", "referral": "null", "options": ["繼續", 2]},
    {"reply": "x", "steps": [None, "s", [], {"title": "t"}, {"title": "u", "how": "h", "refs": "K1"}, {"title": "v", "how": "w", "refs": [None, 1, "", "K2"]}], "referral": "園長（K1）"},
    {"reply": "回答 (K1)", "refs_items": [{"title": "a", "how": "b", "refs": ["K1"]}], "tips": "一條 K2", "steps": {"title": "單一", "how": "物件", "refs": []}, "referral": ""},
    {"reply": None, "tips": ["a", None, "", "b (K1)"], "steps": [], "options": [], "referral": "轉介"},
    {"reply": "null", "tips": "null", "steps": [{"title": "(K1)", "how": "", "refs": [0, False]}], "referral": "（K2）"},
]
CASES = [{"fn": "strip", "args": {"texts": TEXTS}}]
for cfg in CFGS:
    for mode in ["full", "continue"]:
        for a in ANSWERS:
            CASES.append({"fn": "validate", "cfg": cfg, "args": {"ans": a, "mode": mode}})
            CASES.append({"fn": "clean", "cfg": cfg, "args": {"ans": a, "mode": mode}})

HITS = [{"ref": "K1", "text": "先預告。"}, {"ref": "K2", "text": "用計時器。"}]
FORB = [{"rule": "過動", "regex": False, "note": "不診斷"}, {"rule": "\\d{4}", "regex": True, "note": ""}]
GOOD = {"reply": "辛苦了", "steps": [{"title": "預告", "how": "先說", "refs": ["K1"]}, {"title": "計時", "how": "用計時器", "refs": ["K2"]}], "referral": None}
SPARSE = {"reply": "他過動嗎", "steps": [{"title": "預告", "how": "1234", "refs": []}, {"title": "x", "how": "y", "refs": ["K9", 1]}, {"how": "沒標題", "refs": []}], "referral": None}
RESPS = [
    {"issues": [], "relevance": {"addressed": True, "cause": None, "note": ""}},
    {"issues": [{"type": "依據", "severe": True, "note": "沒根據"}, {"type": "語氣", "severe": False, "note": "太硬"}], "relevance": {"addressed": False, "cause": "知識不足", "note": "沒談到"}},
    {"issues": ["一句字串", {"issue": "用 issue 欄"}, {"severe": 0, "x": 1}, 5, [1]], "relevance": {"addressed": "false", "note": 3}},
    {"issues": [{"type": "安全", "note": "很長" * 120}], "relevance": {"addressed": False, "cause": "別的", "note": ""}},
    {"issues": "不是陣列"},
    {"relevance": None},
    [],
    {"issues": [None]},
    {"issues": [{"type": "語氣", "severe": False, "note": "稍硬"}], "relevance": {"addressed": True}},
    {"__throw": "模型逾時"},
]
for cfg in [dict(ROLES, enable_llm_check="TRUE"), dict(ROLES, enable_llm_check="TRUE", enable_relevance_check="FALSE"),
            dict(ROLES, enable_llm_check="FALSE"), dict(NO_CITE, enable_llm_check="TRUE")]:
    for ans, fb, focus in [(GOOD, False, "怎麼收玩具"), (SPARSE, False, ""), (SPARSE, True, "預告")]:
        for resp in RESPS:
            CASES.append({"fn": "checks", "cfg": cfg, "forbidden": FORB, "resp": resp,
                          "args": {"caseText": "他收玩具時丟積木", "ans": ans, "hits": HITS, "range": [2, 3], "focus": focus, "fallback": fb}})
ONE = {"reply": "電話 0912", "steps": [{"title": "預告", "how": "先說", "refs": ["K1"]}], "referral": None}
CASES.append({"fn": "checks", "cfg": ROLES, "forbidden": FORB, "resp": RESPS[0], "args": {"caseText": "c", "ans": ONE, "hits": HITS, "range": None, "focus": "", "fallback": False}})


class FakeLLM:
    def __init__(self, resp):
        self.resp, self.sent = resp, None

    async def generate_json(self, model, prompt, **kw):
        self.sent = {"model": model, "o": {"temperature": kw.get("temperature"), "noThink": kw.get("no_think")}}
        if isinstance(self.resp, dict) and "__throw" in self.resp:
            raise RuntimeError(self.resp["__throw"])
        return copy.deepcopy(self.resp), None


def norm(x):
    """JS 例外訊息與 Python 不同：只比對「檢查未完成（略過）」這個事實"""
    s = json.dumps(x, ensure_ascii=False)
    return json.loads(re.sub(r"檢查未完成（略過）：[^\"]*", "檢查未完成（略過）", s))


def run_py(c):
    cfg = build_config(c.get("cfg") or {})
    notes = []
    add = lambda k, m: notes.append([k, m])
    a = c["args"]
    sent = None
    if c["fn"] == "strip":
        out = [strip_refs(t) for t in a["texts"]]
    elif c["fn"] == "validate":
        validate_answer(cfg, a["ans"], a["mode"], add); out = None
    elif c["fn"] == "clean":
        out = clean_answer(cfg, a["ans"], a["mode"])
    else:
        llm = FakeLLM(c["resp"])
        out = asyncio.run(run_checks(cfg, llm, case_text=a["caseText"], ans=a["ans"], hits=a["hits"], forbidden=c["forbidden"],
                                     rng=a["range"], focus=a["focus"], fallback=a["fallback"], notes=add))
        sent = llm.sent
    return {"out": out, "notes": [n for n in notes if n[0] != "設定錯誤"], "sent": sent}


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(HERE / "parity" / "run_checks_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


@pytest.mark.parametrize("i", range(len(CASES)))
def test_same_as_apps_script(gas, i):
    assert norm(run_py(CASES[i])) == norm(gas[i]), json.dumps(CASES[i], ensure_ascii=False)[:300]


def test_invalid_regex_skipped_with_note():
    """Apps Script 遇到寫錯的正規式整個回答會失敗；這裡略過該條並記設定錯誤"""
    notes = []
    r = asyncio.run(run_checks(build_config(ROLES), None, case_text="c", ans={"reply": "過動"}, hits=[], forbidden=[{"rule": "(", "regex": True, "note": ""}, {"rule": "過動", "regex": False, "note": ""}],
                               notes=lambda k, m: notes.append((k, m))))
    assert r["items"][0]["pass"] is False and "過動" in r["items"][0]["note"] and notes[0][0] == "設定錯誤"
