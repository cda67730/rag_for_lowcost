"""「用說的」設定：prompt 與結果整理與 Apps Script 逐項比對（需要 node）。"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from services import describe as D
from settings.loader import build_config

HERE = Path(__file__).parent
ROLES = {"role_user": "老師", "role_subject": "孩子", "domain_desc": "學前融合"}
SCHEMA_R = [
    {"fields": [{"key": "reply", "type": "text", "label": "回覆", "hint": "直接回答", "required": True, "modes": "both"},
                {"key": "reply", "type": "items", "label": "建議", "count": "1-2", "count_continue": "x", "label_continue": "再試"},
                {"key": "1bad", "type": "zzz", "copy": 1}, "字串", None,
                {"type": "list", "label": ["a", "b"], "modes": "continue", "summary": "yes"}]},
    {"fields": {"key": "only", "type": "items"}},
    {"fields": []}, {"fields": "x"}, [], None, {"fields": [{"type": "text"}, {"type": "text", "key": "text_1"}]}, {"fields": [{"key": "text_2"}, {"type": "text"}]},
]
INT_R = [{"intents": [{"name": "新的＝x", "desc": "a\nb"}, {"name": "補充", "desc": ""}, {"name": "要", "desc": 3}, {"name": "閒聊", "desc": "d"}, {"name": "多的"}]},
         {"intents": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}, {"intents": [{"name": "a"}, {"name": ""}, {"name": "c"}, {"name": "d"}]}, {}]
CHK_R = [{"items": [{"name": "依據|來源", "desc": "要有\n出處"}, {"name": ""}, "s", {"name": "語氣"}], "severe": " 傷害 "}, {"items": []}, {"items": {"name": "單一"}}]
CASES = []
for cfg in [{}, ROLES]:
    for r in SCHEMA_R:
        CASES.append({"cfg": cfg, "kind": "answer_schema", "text": " 要有回覆和建議 ", "current": None, "resp": r})
    for r in INT_R:
        CASES.append({"cfg": cfg, "kind": "intents", "text": "四種", "current": "目前" * 2100, "resp": r})
    for r in CHK_R:
        CASES.append({"cfg": cfg, "kind": "check_items", "text": "檢查", "current": "", "resp": r})
    CASES.append({"cfg": cfg, "kind": "nope", "text": "x", "current": None, "resp": {}})
    CASES.append({"cfg": cfg, "kind": "intents", "text": "  ", "current": None, "resp": {}})
    for k in ("answer_schema", "intents", "check_items"):
        CASES.append({"cfg": cfg, "kind": k, "back": True, "resp": {"text": " 目前會回覆 "}})


class L:
    def __init__(self, resp):
        self.resp, self.prompt = resp, None

    async def generate_json(self, model, prompt, **kw):
        self.prompt = prompt
        return json.loads(json.dumps(self.resp)), None


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(HERE / "parity" / "run_describe_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


@pytest.mark.parametrize("i", range(len(CASES)))
def test_same_as_apps_script(gas, i):
    c, want = CASES[i], gas[i]
    llm, cfg = L(c["resp"]), build_config(c["cfg"])
    try:
        out = asyncio.run(D.describe_back(llm, cfg, c["kind"]) if c.get("back") else D.describe(llm, cfg, c["kind"], c["text"], c["current"]))
        got = {"out": out, "prompt": llm.prompt}
    except D.DescribeError as e:
        got = {"error": str(e), "prompt": llm.prompt}
    assert got == want, json.dumps(c, ensure_ascii=False)[:200]
