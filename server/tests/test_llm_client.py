"""LLMClient 行為：參數不相容重送、429／5xx 重試、回應解析、JSON 補救、向量分批與順序、維度檢查、金鑰不外洩。不呼叫真的 API。"""
import asyncio
import json
import math

import httpx
import pytest

from services.llm import LLMClient, LLMError, parse_json, validate_embed_config


class Fake:
    """依序回應預先排好的 (status, body, headers)；記錄每個請求"""
    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        st, body, hd = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
        body = body(request) if callable(body) else body
        return httpx.Response(st, json=body if not isinstance(body, str) else None, text=body if isinstance(body, str) else None, headers=hd or {})

    def body(self, i):
        return json.loads(self.calls[i].content)


def client(fake, **kw):
    sleeps = []
    async def sleep(s): sleeps.append(s)
    c = LLMClient(gemini_key="AIzaSyTESTKEYTESTKEYTESTKEY12345", openai_key="sk-testkeytestkeytestkey123456", transport=httpx.MockTransport(fake), sleep=sleep, **kw)
    c.sleeps = sleeps
    return c


def run(coro):
    return asyncio.run(coro)


GEM_OK = {"candidates": [{"content": {"parts": [{"text": "思考中", "thought": True}, {"text": " 答案 "}]}}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3}}
OA_OK = {"choices": [{"message": {"content": " hi "}}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}


def test_gemini_text_skips_thoughts_and_reads_usage():
    f = Fake((200, GEM_OK, None)); c = client(f)
    r = run(c.generate("gemini-2.5-flash", "q"))
    assert (r.text, r.input_tokens, r.output_tokens) == ("答案", 10, 3)
    assert f.calls[0].headers["x-goog-api-key"].startswith("AIza") and "key=" not in str(f.calls[0].url)   # 金鑰放標頭，不放網址


def test_openai_text_and_auth_header():
    f = Fake((200, OA_OK, None)); c = client(f)
    r = run(c.generate("gpt-6-luna", "q", no_think=True))
    assert r.text == "hi" and f.calls[0].headers["authorization"].startswith("Bearer sk-")
    assert f.body(0)["reasoning_effort"] == "none" and "temperature" not in f.body(0)


def test_unsupported_param_retried_once_without_it():
    notes = []
    f = Fake((400, {"error": {"message": "Unsupported parameter: thinking_config"}}, None), (200, GEM_OK, None)); c = client(f)
    r = run(c.generate("gemini-3.8-flash", "q", no_think=True, notes=lambda k, n: notes.append(k)))
    assert r.text == "答案" and len(f.calls) == 2
    assert "thinkingConfig" in f.body(0)["generationConfig"] and "thinkingConfig" not in f.body(1)["generationConfig"]
    assert notes == ["參數不相容"] and r.retried == ["參數不相容"]


def test_other_400_not_retried_and_key_redacted():
    f = Fake((400, {"error": {"message": "API key AIzaSyTESTKEYTESTKEYTESTKEY12345 not valid"}}, None)); c = client(f)
    with pytest.raises(LLMError) as e:
        run(c.generate("gemini-2.5-flash", "q"))
    assert len(f.calls) == 1 and "AIza" not in str(e.value) and "[已遮蔽]" in str(e.value)


def test_429_then_ok_honors_retry_after():
    f = Fake((429, {"error": "rate"}, {"retry-after": "7"}), (503, "busy", None), (200, OA_OK, None)); c = client(f)
    assert run(c.generate("gpt-5-mini", "q")).text == "hi"
    assert len(f.calls) == 3 and c.sleeps == [7.0, 2]


def test_retries_exhausted_raises():
    f = Fake((503, "down", None)); c = client(f, max_retries=2)
    with pytest.raises(LLMError, match="503"):
        run(c.generate("gpt-5-mini", "q"))
    assert len(f.calls) == 3


def test_missing_key_message():
    c = LLMClient(gemini_key="", openai_key="", transport=httpx.MockTransport(Fake((200, OA_OK, None))))
    with pytest.raises(LLMError, match="OPENAI_API_KEY 未設定"):
        run(c.generate("gpt-5-mini", "q"))
    with pytest.raises(LLMError, match="GEMINI_API_KEY 未設定"):
        run(c.embed("gemini-embedding-001", ["a"], dim=768))


def test_parse_json_salvage_and_failure():
    notes = []
    assert parse_json('好的：```json\n{"a": 1}\n```', lambda k, n: notes.append(k)) == {"a": 1} and notes == ["JSON補救"]
    with pytest.raises(LLMError):
        parse_json("沒有 JSON", lambda k, n: notes.append(k))
    assert notes[-1] == "JSON失敗"


def test_generate_json():
    f = Fake((200, {"choices": [{"message": {"content": '{"intent":"新問題"}'}}]}, None)); c = client(f)
    obj, _ = run(c.generate_json("gpt-5-mini", "q"))
    assert obj == {"intent": "新問題"} and f.body(0)["response_format"] == {"type": "json_object"}


# ── 向量 ──

def gem_embed(dim):
    return lambda req: {"embeddings": [{"values": [float(i + 1)] + [0.0] * (dim - 1)} for i, _ in enumerate(json.loads(req.content)["requests"])]}


def oa_embed(dim, shuffle=True):
    def h(req):
        n = len(json.loads(req.content)["input"])
        data = [{"index": i, "embedding": [float(i + 1), 1.0] + [0.0] * (dim - 2)} for i in range(n)]
        return {"data": list(reversed(data)) if shuffle else data}
    return h


def test_gemini_embed_batches_and_normalizes():
    f = Fake((200, gem_embed(768), None)); c = client(f)
    v = run(c.embed("gemini-embedding-001", [f"t{i}" for i in range(250)], dim=768, task="query"))
    assert len(f.calls) == 3 and [len(json.loads(x.content)["requests"]) for x in f.calls] == [100, 100, 50]
    assert all(json.loads(x.content)["requests"][0]["taskType"] == "RETRIEVAL_QUERY" for x in f.calls)
    assert len(v) == 250 and all(abs(math.sqrt(sum(a * a for a in x)) - 1) < 1e-9 for x in v)


def test_openai_embed_keeps_order_and_sends_dimensions():
    f = Fake((200, oa_embed(1536), None)); c = client(f)
    v = run(c.embed("text-embedding-3-small", ["a", "b", "c"], dim=1536))
    assert f.body(0)["dimensions"] == 1536 and f.body(0)["input"] == ["a", "b", "c"]
    # 回應順序打亂，仍依 index 排回：第 i 筆的第一個分量最大值依序遞增
    assert [round(x[0] / x[1], 6) for x in v] == [1.0, 2.0, 3.0]


def test_embed_dimension_mismatch():
    f = Fake((200, gem_embed(3072), None)); c = client(f)
    with pytest.raises(LLMError, match="維度不符"):
        run(c.embed("gemini-embedding-001", ["a"], dim=768))


def test_embed_empty_no_call():
    f = Fake((200, OA_OK, None)); c = client(f)
    assert run(c.embed("text-embedding-3-small", [], dim=768)) == [] and f.calls == []


def test_validate_embed_config():
    validate_embed_config("text-embedding-3-small", 1536, (768, 1536))
    validate_embed_config("gemini-embedding-001", 768, (768, 1536))
    with pytest.raises(ValueError, match="資料庫目前支援"):
        validate_embed_config("gemini-embedding-001", 3072, (768, 1536))
    with pytest.raises(ValueError, match="可用的維度"):
        validate_embed_config("text-embedding-ada-002", 768, (768, 1536))
