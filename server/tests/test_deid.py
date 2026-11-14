"""去識別化與 Apps Script 原版比對（需要 node），加上 AI 模式的退回行為。"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from services import deid
from services.llm import LLMClient
from settings.loader import build_config

HERE = Path(__file__).parent
ROLES = {"role_user": "老師", "role_subject": "孩子"}
TEXT = "今天小宇又把積木丟向王小明，小明哭了。陳媽媽打 0912-345-678 和 (02)2345-6789 來，信箱 a.b+c@x-y.com.tw，身分證 A123456789。快樂幼兒園的小朋友都看到 𠀋𠀋。"
CASES = [
    {"fn": "scrub", "text": TEXT},
    {"fn": "scrub", "text": "全形０９１２３４５６７８ 不算，ＡＢ@ｃ.com 不算，a1234567890 不算，B223456789 算"},
    {"fn": "rule", "text": TEXT, "map": {}, "cfg": ROLES},
    {"fn": "rule", "text": "小宇和小花在小組裡，小宇說小心", "map": {"小宇": "孩子A"}, "cfg": ROLES},
    {"fn": "rule", "text": "小明小華", "map": {}, "cfg": {"role_subject": "學生", "deid_labels": "subject＝生"}},
    {"fn": "items", "text": TEXT, "map": {}, "cfg": ROLES, "items": [
        {"text": "小宇", "type": "subject"}, {"text": "王小明", "type": "subject"}, {"text": "小明", "type": "subject"},
        {"text": "陳", "type": "family"}, {"text": "快樂幼兒園", "type": "org"}, {"text": "小朋友", "type": "other"},
        {"text": "不存在的人", "type": "subject"}, {"text": "𠀋𠀋", "type": "weird"}, {"text": "  ", "type": "subject"}, "壞", {"text": 3}]},
    {"fn": "items", "text": "小宇今天好多了，王小明也沒事", "map": {"小宇": "孩子A", "王小明": "孩子B", "小明": "孩子B"}, "cfg": ROLES, "items": [{"text": "王小明", "type": "subject"}]},
    {"fn": "items", "text": "張三李四", "map": {}, "cfg": {}, "items": [{"text": "張三", "type": "staff"}, {"text": "李四", "type": "staff"}]},
    {"fn": "items", "text": "x", "map": {}, "cfg": {}, "items": None},
    {"fn": "items", "text": "A" * 30, "map": {}, "cfg": {}, "items": [{"text": "A" * 21, "type": "other"}, {"text": "A" * 20, "type": "other"}]},
    # 對照表裡短名字排在前面：一定要先換長的，否則「王小明」會變成「王孩子A」
    {"fn": "items", "text": "王小明和小明", "map": {"小明": "孩子A", "王小明": "孩子B"}, "cfg": ROLES, "items": []},
    {"fn": "instr", "known": [], "cfg": ROLES},
    {"fn": "instr", "known": ["小宇", "王小明"], "cfg": {}},
]


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(HERE / "parity" / "run_deid_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def py(c):
    cfg = build_config(c.get("cfg") or {})
    if c["fn"] == "scrub":
        return deid.scrub(c["text"])
    if c["fn"] == "rule":
        r = deid.rule(c["text"], c["map"], cfg.deid_labels()); return {"text": r.text, "map": r.map, "found": r.found}
    if c["fn"] == "items":
        r = deid.from_items(c["text"], c["map"], c["items"], cfg.deid_labels()); return {"text": r.text, "map": r.map, "found": r.found}
    return deid.instructions(cfg.roles(), c["known"])


@pytest.mark.parametrize("i", range(len(CASES)), ids=[f"{c['fn']}{i}" for i, c in enumerate(CASES)])
def test_same_as_apps_script(gas, i):
    assert py(CASES[i]) == gas[i]


def test_map_order_preserved_for_frontend():
    r = deid.from_items("甲乙丙", {}, [{"text": "甲", "type": "subject"}, {"text": "丙", "type": "subject"}], build_config({}).deid_labels())
    assert list(r.map) == ["甲", "丙"] and list(r.map.values()) == ["對方A", "對方B"]


def fake_llm(content):
    def h(req): return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return LLMClient(gemini_key="g", openai_key="o", transport=httpx.MockTransport(h))


def test_ai_mode_and_fallback_to_rule():
    cfg = build_config({**ROLES, "model_triage": "gpt-5-mini"})
    r = asyncio.run(deid.deidentify(fake_llm('{"items":[{"text":"阿明","type":"subject"}]}'), cfg, "阿明今天哭", {}))
    assert r.text == "孩子A今天哭" and r.fallback is None
    r = asyncio.run(deid.deidentify(fake_llm('{"nope":1}'), cfg, "小宇今天哭", {}))
    assert r.text == "孩子A今天哭" and "items" in r.fallback          # 格式錯→退回規則（小＋一字）
    r = asyncio.run(deid.deidentify(fake_llm("not json"), cfg, "小宇今天哭", {}))
    assert r.fallback and r.text == "孩子A今天哭"
    cfg_rule = build_config({**ROLES, "deid_mode": "rule"})
    assert asyncio.run(deid.deidentify(fake_llm("x"), cfg_rule, "阿明和小宇", {})).text == "阿明和孩子A"   # 規則模式不呼叫模型
