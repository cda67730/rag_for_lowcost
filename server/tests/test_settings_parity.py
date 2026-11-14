"""settings/loader.py 與 Apps Script 原版對同樣輸入要得到同樣結果（需要 node；沒有就略過）。"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from settings.loader import build_config

HERE = Path(__file__).parent
CUSTOM_SCHEMA = json.dumps([
    {"key": "empathy", "type": "text", "hint": "先回應{user}", "modes": "full", "required": True, "summary": True},
    {"key": "steps", "type": "items", "label": "做法", "label_continue": "再試試", "modes": "both", "count": "1~4", "count_continue": "0-1", "summary": True},
    {"key": "notes", "type": "list", "label": "注意", "modes": "continue", "required": "false"},
    {"key": "bad key", "type": "text"}, {"key": "refs", "type": "text"}, {"key": "steps", "type": "list"},
    {"key": "msg", "type": "weird", "copy": 1, "label": "訊息"}, "not-an-object",
], ensure_ascii=False)
ANSWER = {"empathy": "辛苦了", "steps": [{"title": "預告", "how": "x", "refs": ["K1"]}, {"title": "示範"}], "notes": ["a", "b"], "reply": "好", "refs_items": [{"title": "T"}]}

CASES = [
    {"name": "預設", "cfg": {}},
    {"name": "角色與自訂欄位", "cfg": {"role_user": "老師", "role_subject": "孩子", "domain_desc": "學前融合", "answer_schema": CUSTOM_SCHEMA}},
    {"name": "壞 JSON", "cfg": {"answer_schema": "{broken"}},
    {"name": "沒有 items、出處開", "cfg": {"answer_schema": json.dumps([{"key": "reply", "type": "text", "modes": "both"}])}},
    {"name": "沒有 items、出處關", "cfg": {"enable_citation": False, "answer_schema": json.dumps([{"key": "reply", "type": "text", "modes": "both"}])}},
    {"name": "自由作答", "cfg": {"enable_answer_schema": "FALSE"}},
    {"name": "空陣列", "cfg": {"answer_schema": "[]"}},
    {"name": "意圖只填兩行", "cfg": {"intents": "換人=換了一個人\n\n補充＝補充細節＝含等號"}},
    {"name": "檢查項目空白", "cfg": {"check_items": "   "}},
    {"name": "檢查項目自訂", "cfg": {"check_items": "正確性＝與知識一致\n\n完整\n＝沒有名稱"}},
    {"name": "代稱", "cfg": {"role_subject": "學生", "deid_labels": "family＝監護人\n亂寫\norg=\nother＝路人"}},
    {"name": "介面", "cfg": {"ui_accent": "#123456", "ui_waiting": "", "ui_fb_up": "讚", "show_sources": "FALSE", "bot_name": "小幫手"}},
    {"name": "是", "cfg": {"show_sources": "是", "enable_citation": "true"}},
]


@pytest.fixture(scope="module")
def gas_results():
    if not shutil.which("node"):
        pytest.skip("沒有 node，略過與 Apps Script 的比對")
    payload = json.dumps([{"cfg": c["cfg"], "answer": ANSWER} for c in CASES], ensure_ascii=False)
    r = subprocess.run(["node", str(HERE / "parity" / "run_gas.js")], input=payload, capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def py_result(cfg: dict) -> dict:
    c = build_config(cfg)
    return {
        "roles": c.roles(), "intents": c.intents(), "check_items": c.check_items(), "deid_labels": c.deid_labels(), "ui": c.ui_config(),
        "schema": [f.to_public() for f in c.schema()],
        "range_full": list(c.items_range("full")), "range_continue": list(c.items_range("continue")),
        "format_full": c.schema_format("full", ['"knowledge_gap":null']), "format_continue": c.schema_format("continue", []),
        "summary_full": c.answer_summary(ANSWER, "answer"), "summary_continue": c.answer_summary(ANSWER, "continue"),
        "items_n": len(c.answer_items(ANSWER)),
        "text": {k: c.text(k) for k in ("check_items", "fallback_rules", "msg_ai_fallback", "welcome")},
        "intent_key_of_2nd": c.intent_key(c.intents()[1]["name"]), "intent_name_off": c.intent_name("off"),
        "notes": [list(x) for x in c.notes],
    }


@pytest.mark.parametrize("i", range(len(CASES)), ids=[c["name"] for c in CASES])
def test_same_as_apps_script(gas_results, i):
    want, got = gas_results[i], py_result(CASES[i]["cfg"])
    # JSON 解析失敗的訊息由各語言的解析器產生，措辭不同；只比固定的部分
    norm = lambda notes: [[t, re.sub(r"解析失敗：.*?，", "解析失敗：…，", n)] for t, n in notes]
    want, got = dict(want, notes=norm(want["notes"])), dict(got, notes=norm(got["notes"]))
    for k in want:
        assert got[k] == want[k], f"{CASES[i]['name']} / {k}\n  Apps Script: {want[k]!r}\n  Python:      {got[k]!r}"
