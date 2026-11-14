"""請求組裝要與 apps-script/Llm.gs 一致（需要 node）。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from services.llm import build_embed_requests, build_gen_request, gemini_version, model_id, provider_of

HERE = Path(__file__).parent
GEN = [(m, o) for m in ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-3.1-pro-preview",
                        "gpt-6-luna", "gpt-6.1-terra", "gpt-6-sol", "gpt-5-mini", "o4-mini"]
       for o in [{}, {"json": True, "noThink": True}, {"temperature": 0, "maxTokens": 500}, {"json": True, "temperature": 0.7, "noThink": True, "maxTokens": 100}]]
EMB = [("gemini-embedding-001", 768, "RETRIEVAL_DOCUMENT", 3), ("gemini-embedding-001", 1536, "RETRIEVAL_QUERY", 1),
       ("text-embedding-3-small", 1536, "RETRIEVAL_DOCUMENT", 150), ("text-embedding-3-large", 768, "RETRIEVAL_QUERY", 2)]


@pytest.fixture(scope="module")
def gas():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    cases = [{"kind": "gen", "model": m, "opts": o} for m, o in GEN] + \
            [{"kind": "emb", "model": m, "dim": d, "task": t, "texts": [f"文字{i}" for i in range(n)]} for m, d, t, n in EMB]
    r = subprocess.run(["node", str(HERE / "parity" / "run_llm_gas.js")], input=json.dumps(cases), capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


@pytest.mark.parametrize("i", range(len(GEN)), ids=[f"{m}-{'-'.join(o) or 'plain'}" for m, o in GEN])
def test_gen_request_same_as_gas(gas, i):
    m, o = GEN[i]
    req = build_gen_request(m, "P", json_mode=bool(o.get("json")), temperature=o.get("temperature"), max_tokens=o.get("maxTokens"), no_think=bool(o.get("noThink")))
    assert {"url": req.url, "body": req.body} == gas[i]


@pytest.mark.parametrize("j", range(len(EMB)), ids=[f"{m}-{d}" for m, d, *_ in EMB])
def test_embed_requests_same_as_gas(gas, j):
    m, d, t, n = EMB[j]
    reqs = build_embed_requests(m, [f"文字{i}" for i in range(n)], dim=d, task="query" if t == "RETRIEVAL_QUERY" else "document")
    assert [{"url": r.url, "body": r.body} for r in reqs] == gas[len(GEN) + j]


def test_provider_detection():
    assert [provider_of(x) for x in ["gpt-6-luna", "o4-mini", "chatgpt-4o", "text-embedding-3-small", "gemini-3.8-flash", "gemini-embedding-001"]] == \
           ["openai", "openai", "openai", "openai", "gemini", "gemini"]
    # Gemini 舊模型名稱會被當成 OpenAI，用前綴明確指定
    assert provider_of("text-embedding-004") == "openai" and provider_of("gemini:text-embedding-004") == "gemini"
    assert model_id("gemini:text-embedding-004") == "text-embedding-004" and gemini_version("gemini-3.1-pro-preview") == 3.1


def test_ada_002_has_no_dimensions_param():
    (r,) = build_embed_requests("text-embedding-ada-002", ["a"], dim=1536, task="document")
    assert "dimensions" not in r.body
