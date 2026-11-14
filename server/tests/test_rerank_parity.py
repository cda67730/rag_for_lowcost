"""重排與配額（rerank_select）與 Apps Script 的 rerankSelect_ 結果相同（需要 node）。"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from services.llm import LLMClient
from services.retrieval import rerank_select
from settings.loader import build_config

HERE = Path(__file__).parent
T = ["具體做法", "原則／背景", "案例", "原則／背景", "溝通範例", "原則／背景", "", "轉介資訊", "具體做法", "原則／背景"]
POOL = [{"id": f"c{i}", "title": f"文件{i}", "page": str(i), "type": T[i], "text": f"段落內容 {i} " * 3, "score": round(0.9 - i * 0.03, 3)} for i in range(10)]
CASES = []
for cfg in [{"model_rerank": "gpt-5-mini"}, {"model_rerank": "gpt-5-mini", "top_k": 4, "quota_min_actionable": 1, "quota_max_principle": 1},
            {"model_rerank": "", "model_check": "gpt-6-luna", "quota_min_actionable": 0, "quota_max_principle": 0, "role_user": "老師"}]:
    for resp in [{"value": {"scores": [3, 1, 2, 0, 3, 1, 2, 1, 0, 3]}}, {"value": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]}, {"value": {"scores": [3, "2", None, "x", "", True, 9, -1]}},
                 {"value": {"nope": 1}}, {"value": {"scores": ["x", None]}}, {"throw": "模型回覆不是有效的 JSON"}]:
        CASES.append({"cfg": cfg, "resp": resp, "focus": "收玩具", "caseText": "描述" * 900, "pool": POOL})
CASES.append({"cfg": {"enable_rerank": "FALSE"}, "resp": {"value": {"scores": [3] * 10}}, "focus": "f", "caseText": "c", "pool": POOL})
CASES.append({"cfg": {}, "resp": {"value": {"scores": []}}, "focus": "f", "caseText": "c", "pool": []})


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    r = subprocess.run(["node", str(HERE / "parity" / "run_rerank_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def run_py(c):
    sent = {}
    def h(req):
        body = json.loads(req.content); sent["model"] = body["model"]; sent["prompt"] = body["messages"][0]["content"][0]["text"]
        if "throw" in c["resp"]:
            return httpx.Response(200, json={"choices": [{"message": {"content": "不是 JSON"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(c["resp"]["value"])}}]})
    llm = LLMClient(gemini_key="g", openai_key="o", transport=httpx.MockTransport(h))
    r = asyncio.run(rerank_select(build_config(c["cfg"]), llm, c["focus"], c["caseText"], c["pool"]))
    return r, (sent or None)


@pytest.mark.parametrize("i", range(len(CASES)), ids=[str(i) for i in range(len(CASES))])
def test_rerank_same_as_apps_script(gas, i):
    want, (got, sent) = gas[i], run_py(CASES[i])
    w = want["r"]
    assert [(h["id"], h["ref"], h["rerank"]) for h in got["hits"]] == [(h["id"], h["ref"], h["rerank"]) for h in w["hits"]]
    assert (got["direct"], got["actionable"]) == (w["direct"], w["actionable"])
    assert {k: got["trace"][k] for k in ("used", "why", "model", "topK", "minAct", "maxPri")} == {k: w["trace"][k] for k in ("used", "why", "model", "topK", "minAct", "maxPri")}
    assert [(x["id"], x["rerank"], x["ref"]) for x in got["trace"]["items"]] == [(x["id"], x["rerank"], x["ref"]) for x in w["trace"]["items"]]
    if want["got"]:
        assert sent and sent["prompt"] == want["got"]["prompt"] and sent["model"] == want["got"]["model"]
    else:
        assert sent is None   # 沒有呼叫模型
