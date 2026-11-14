"""提示詞逐字與 Apps Script 原版相同（需要 node）。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from graph import prompts as P
from settings.loader import build_config

HERE = Path(__file__).parent
ROLES = {"role_user": "老師", "role_subject": "孩子", "domain_desc": "學前融合", "role_escalate": "園長"}
TRI = {"intent": "新問題", "focus": "收玩具時不丟積木", "referral": {"category": "none", "level": "none", "reason": ""}, "topics": ["情緒行為"],
       "in_scope": True, "info_enough": True, "missing": [], "score": 1.0, "x": 0.5, "q": '引號"與\\反斜線\n換行'}
HITS = [{"ref": "K1", "title": "收拾時間", "page": "3", "text": "先預告五分鐘。"}, {"ref": "K2", "title": "轉銜", "page": "", "text": "用計時器。"}]
HIST = [{"role": "teacher", "text": "他一直丟"}, {"role": "bot", "text": "了解"}]
KG = {"nodes": [{"name": "情緒調節", "type": "概念", "def": "調整情緒", "tips": "降低刺激"}, {"name": "轉銜預告", "type": "", "def": "", "tips": ""}]}
FORB = [{"rule": "過動", "regex": False, "note": "不診斷"}, {"rule": "\\d{4}", "regex": True, "note": ""}, {"rule": "懲罰", "regex": False, "note": ""}]
REF = {"category": "親師衝突", "level": "需要專人", "reason": "升高"}
SCHEMA_NO_ITEMS = json.dumps([{"key": "reply", "type": "text", "modes": "both"}])

CASES = []
for cfg in [{}, ROLES, dict(ROLES, intents="換人＝換了一個人\n補充＝補充", enable_citation="FALSE", answer_schema=SCHEMA_NO_ITEMS)]:
    for prev in [[], ["之前說過一次", "第二次"]]:
        for dk in [None, [], ["小宇", "王小明"]]:
            CASES.append({"fn": "triage", "cfg": cfg, "args": {"prev": prev, "latest": "這次 \"引號\"", "topics": ["情緒行為", "親師溝通"], "cats": ["兒少保護", "健康"], "deidKnown": dk}})
for cfg in [{}, ROLES]:
    for pick in [[], [{"name": "轉介", "content": "一律", "always": True}], [{"name": "理解", "content": "框架", "always": False}, {"name": "轉介", "content": "一律", "always": True}]]:
        CASES.append({"fn": "skill", "cfg": cfg, "args": {"pick": pick}})
    for missing in [[], ["時段", "經過"]]:
        CASES.append({"fn": "followup", "cfg": cfg, "args": {"sys": "SYS", "skills": "SK", "missing": missing, "caseText": "描述"}})
for cfg in [{}, ROLES, dict(ROLES, enable_citation="FALSE", answer_schema=SCHEMA_NO_ITEMS), dict(ROLES, enable_answer_schema="FALSE")]:
    for fallback in [False, True]:
        for hits in [[], HITS]:
            for extra in [{"history": [], "ref": None, "feedback": None, "focus": "", "kg": None, "forb": []},
                          {"history": HIST, "ref": REF, "feedback": ["P09 沒出處", "語氣"], "focus": "怎麼開口", "kg": KG, "forb": FORB}]:
                common = {"sys": "SYS", "skills": "SK", "tri": TRI, "hits": hits, "history": extra["history"], "ref": extra["ref"], "refWho": "園長",
                          "feedback": extra["feedback"], "focus": extra["focus"], "fallback": fallback, "kg": extra["kg"]}
                CASES.append({"fn": "full", "cfg": cfg, "forbidden": extra["forb"], "args": dict(common, caseText="原話")})
                CASES.append({"fn": "cont", "cfg": cfg, "forbidden": extra["forb"], "args": dict(common, prev=["前一句"], latest="這次", intent="要求特定協助")})
for cfg in [{}, ROLES]:
    for l5, rel, fb in [(True, True, False), (True, True, True), (True, False, False), ("FALSE", True, False), (True, False, True)]:
        CASES.append({"fn": "checks", "cfg": dict(cfg, enable_llm_check=l5, enable_relevance_check=rel), "args": {"caseText": "描述", "ans": {"reply": "好", "steps": [{"title": "預告", "how": "x", "refs": ["K1"]}]},
                      "hits": HITS, "range": [1, 3], "focus": "怎麼開口" if rel else "", "fallback": fb}})
CASES += [{"fn": "conservative", "cfg": ROLES, "args": {"refTable": t, "why": w}} for t in [{}, {"系統能力不足": {"who": "特教團隊"}}, {"系統能力不足": {"who": ""}}] for w in [None, "check"]]
CASES += [{"fn": "options", "cfg": {"continue_options": "- 換做法\n1. 詳細步驟\n・找誰\n\n  其他  "}}, {"fn": "options", "cfg": {}}]
CASES += [{"fn": "kg", "args": {"kg": k}} for k in [None, {"nodes": []}, KG]]


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(HERE / "parity" / "run_prompts_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def py(c):
    cfg, a = build_config(c.get("cfg") or {}), c.get("args", {})
    words = [f["rule"] for f in c.get("forbidden", []) if not f["regex"]]
    if c["fn"] == "triage":
        return P.triage_prompt(cfg, a["prev"], a["latest"], a["topics"], a["cats"], a["deidKnown"])
    if c["fn"] == "skill":
        return P.skill_text(cfg, a["pick"])
    if c["fn"] == "followup":
        return P.followup_prompt(cfg, a["sys"], a["skills"], a["missing"], a["caseText"])
    if c["fn"] in ("full", "cont"):
        tri = json.loads(json.dumps(a["tri"]))   # 與實際一樣是從 JSON 解析來的（1.0 會是 float）
        common = dict(sys=a["sys"], skills=a["skills"], hits=a["hits"], history=a["history"], ref=a["ref"], ref_who=a["refWho"], feedback=a["feedback"],
                      focus=a["focus"], fallback=a["fallback"], kg=a["kg"], forbidden_words=words)
        if c["fn"] == "full":
            return P.assemble_full(cfg, tri=tri, case_text=a["caseText"], **common)
        return P.assemble_continue(cfg, prev_user=a["prev"], latest=a["latest"], intent=a["intent"], **common)
    if c["fn"] == "checks":
        do_l5 = cfg.flag("enable_llm_check")
        do_rel = bool(a["focus"]) and cfg.flag("enable_relevance_check")
        return P.checks_prompt(cfg, case_text=a["caseText"], answer_json=P.js_json(a["ans"]), hits=a["hits"], focus=a["focus"], fallback=a["fallback"], do_l5=do_l5, do_rel=do_rel)
    if c["fn"] == "conservative":
        return P.conservative_msg(cfg, a["refTable"], a["why"])
    if c["fn"] == "options":
        return P.continue_options(cfg)
    return P.kg_prompt_text(a["kg"])


@pytest.mark.parametrize("i", range(len(CASES)), ids=[f"{c['fn']}{i}" for i, c in enumerate(CASES)])
def test_prompt_same_as_apps_script(gas, i):
    got, want = py(CASES[i]), gas[i]
    if got != want and isinstance(got, str) and isinstance(want, str):
        k = next(j for j in range(min(len(got), len(want)) + 1) if got[j:j + 1] != want[j:j + 1])
        pytest.fail(f"第 {k} 字開始不同\n  Apps Script: …{want[max(0, k - 40):k + 60]!r}\n  Python:      …{got[max(0, k - 40):k + 60]!r}")
    assert got == want
