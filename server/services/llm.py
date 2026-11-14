"""模型呼叫：Gemini 與 OpenAI 的文字生成與向量，統一介面。

規則與 apps-script/Llm.gs 相同：
- 依模型 ID 判斷供應商（gpt-／o數字／chatgpt-／text-embedding- 開頭是 OpenAI，其他是 Gemini）；
  也可以寫 "openai:xxx"／"gemini:xxx" 明確指定（例如 Gemini 舊模型 text-embedding-004）。
- Gemini 3 以上與 OpenAI 不送溫度；不需要思考的任務（no_think）降低思考強度。
- 模型不接受思考／溫度參數而回 400 時，拿掉這些參數重送一次，並記一筆穩定度紀錄。
向量一律回傳正規化後的向量，並檢查維度。金鑰只從環境變數讀，不會出現在錯誤訊息裡。
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/"
OPENAI_BASE = "https://api.openai.com/v1/"
EMBED_BATCH = 100
EMBED_CONCURRENCY = 3
MAX_TEXT = 8000
RETRY_STATUS = {429, 500, 502, 503, 504}
_OPENAI_RE = re.compile(r"^(gpt-|o\d|chatgpt-|text-embedding-)", re.I)
_FIX_RE = re.compile(r"thinking|temperature|top_?p|top_?k|reasoning|unsupported", re.I)
_KEY_RE = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z_\-]{20,})")

# 向量模型：供應商、可選維度、能不能指定維度。知識庫建立時用 validate_embed_config 檢查
EMBED_MODELS = {
    "gemini-embedding-001": {"dims": (768, 1536, 3072), "settable": True},
    "text-embedding-3-small": {"dims": (512, 768, 1536), "settable": True},
    "text-embedding-3-large": {"dims": (768, 1536, 3072), "settable": True},
    "text-embedding-ada-002": {"dims": (1536,), "settable": False},
}


class LLMError(RuntimeError):
    pass


Notes = Callable[[str, str], None]   # (類型, 說明) → 寫進穩定度紀錄


def provider_of(model: str) -> str:
    m = (model or "").strip()
    if m.startswith("openai:"):
        return "openai"
    if m.startswith("gemini:"):
        return "gemini"
    return "openai" if _OPENAI_RE.match(m) else "gemini"


def model_id(model: str) -> str:
    return re.sub(r"^(openai|gemini):", "", (model or "").strip())


def gemini_version(model: str) -> float:
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", model_id(model))
    return float(m.group(1)) if m else 0.0


def redact(s: str) -> str:
    return _KEY_RE.sub("[已遮蔽]", s)


def normalize(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def validate_embed_config(model: str, dim: int, supported_dims: Sequence[int]) -> None:
    """建知識庫前檢查：這個模型能產生這個維度、資料庫有這個維度的向量索引"""
    mid = model_id(model)
    if dim not in supported_dims:
        raise ValueError(f"資料庫目前支援的向量維度：{', '.join(map(str, supported_dims))}")
    info = EMBED_MODELS.get(mid)
    if info and dim not in info["dims"]:
        raise ValueError(f"{mid} 可用的維度：{', '.join(map(str, info['dims']))}")


# ───────── 請求組裝（純函式，方便測試與比對 Apps Script 版） ─────────

@dataclass
class Request:
    url: str
    body: dict
    headers: dict


def build_gen_request(model: str, prompt: str, *, json_mode: bool = False, temperature: float | None = None,
                      max_tokens: int | None = None, no_think: bool = False, keys: dict | None = None) -> Request:
    keys = keys or {}
    mid = model_id(model)
    if provider_of(model) == "openai":
        body: dict[str, Any] = {"model": mid, "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if max_tokens:
            body["max_completion_tokens"] = max_tokens
        if no_think:
            body["reasoning_effort"] = "none" if re.search(r"luna|terra|^gpt-6-sol$", mid) else "low"
        return Request(OPENAI_BASE + "chat/completions", body, {"Authorization": "Bearer " + keys.get("openai", "")})
    v3 = gemini_version(model) >= 3
    gc: dict[str, Any] = {}
    if not v3:
        gc["temperature"] = 0.3 if temperature is None else temperature
    if json_mode:
        gc["responseMimeType"] = "application/json"
    if max_tokens:
        gc["maxOutputTokens"] = max_tokens
    if no_think and v3:
        gc["thinkingConfig"] = {"thinkingLevel": "minimal" if re.search(r"3\.1-flash-lite", mid) else "low"}
    elif no_think and re.search(r"2\.5-flash", mid):
        gc["thinkingConfig"] = {"thinkingBudget": 0}
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gc}
    return Request(GEMINI_BASE + mid + ":generateContent", body, {"x-goog-api-key": keys.get("gemini", "")})


def strip_unsupported(body: dict) -> dict:
    """對應 genFix_：拿掉思考與溫度相關參數"""
    b = json.loads(json.dumps(body))
    for k in ("thinkingConfig", "temperature", "topP", "topK"):
        b.get("generationConfig", {}).pop(k, None)
    for k in ("reasoning_effort", "temperature", "top_p"):
        b.pop(k, None)
    return b


def build_embed_requests(model: str, texts: Sequence[str], *, dim: int, task: str, keys: dict | None = None) -> list[Request]:
    keys = keys or {}
    mid = model_id(model)
    reqs = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [str(t)[:MAX_TEXT] for t in texts[i:i + EMBED_BATCH]]
        if provider_of(model) == "openai":
            body: dict[str, Any] = {"model": mid, "input": batch}
            if EMBED_MODELS.get(mid, {"settable": True})["settable"]:
                body["dimensions"] = dim
            reqs.append(Request(OPENAI_BASE + "embeddings", body, {"Authorization": "Bearer " + keys.get("openai", "")}))
        else:
            tt = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
            body = {"requests": [{"model": "models/" + mid, "content": {"parts": [{"text": t}]}, "taskType": tt, "outputDimensionality": dim} for t in batch]}
            reqs.append(Request(GEMINI_BASE + mid + ":batchEmbedContents", body, {"x-goog-api-key": keys.get("gemini", "")}))
    return reqs


# ───────── 回應解析 ─────────

@dataclass
class GenResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    retried: list[str] = field(default_factory=list)


def parse_gen_response(model: str, d: dict) -> GenResult:
    if "choices" in d:
        msg = ((d.get("choices") or [{}])[0] or {}).get("message") or {}
        u = d.get("usage") or {}
        return GenResult(str(msg.get("content") or "").strip(), model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    parts = ((((d.get("candidates") or [{}])[0] or {}).get("content")) or {}).get("parts") or []
    u = d.get("usageMetadata") or {}
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()   # 略過思考摘要
    return GenResult(text, model, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0))


def parse_embed_response(d: dict) -> list[list[float]]:
    if "data" in d:
        return [e["embedding"] for e in sorted(d["data"], key=lambda e: e["index"])]
    return [e["values"] for e in d["embeddings"]]


def parse_json(text: str, notes: Notes | None = None) -> Any:
    """對應 parseJson_：直接解析；失敗時擷取第一段 JSON 並記一筆；仍失敗丟錯"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r"[\[{][\s\S]*[\]}]", str(text))
    if m:
        try:
            v = json.loads(m.group(0))
            notes and notes("JSON補救", "回覆夾雜非 JSON 文字，已擷取")
            return v
        except json.JSONDecodeError:
            pass
    notes and notes("JSON失敗", str(text)[:80])
    raise LLMError("模型回覆不是有效的 JSON")


# ───────── 用戶端 ─────────

class LLMClient:
    def __init__(self, *, gemini_key: str | None = None, openai_key: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 60.0, max_retries: int = 3, sleep: Callable[[float], Any] = asyncio.sleep):
        self.keys = {"gemini": gemini_key if gemini_key is not None else os.environ.get("GEMINI_API_KEY", ""),
                     "openai": openai_key if openai_key is not None else os.environ.get("OPENAI_API_KEY", "")}
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)
        self.max_retries = max_retries
        self._sleep = sleep

    async def aclose(self):
        await self._http.aclose()

    def _need_key(self, model: str):
        p = provider_of(model)
        if not self.keys[p]:
            raise LLMError(("OPENAI_API_KEY" if p == "openai" else "GEMINI_API_KEY") + " 未設定")

    async def _post(self, req: Request) -> httpx.Response:
        """送出；429／5xx 依 Retry-After 或指數退避重試"""
        for attempt in range(self.max_retries + 1):
            try:
                r = await self._http.post(req.url, json=req.body, headers=req.headers)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise LLMError(f"連不到模型 API：{type(e).__name__}") from None
                await self._sleep(2 ** attempt)
                continue
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                ra = r.headers.get("retry-after")
                await self._sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2 ** attempt)
                continue
            return r
        raise LLMError("重試次數用完")   # 理論上到不了

    @staticmethod
    def _fail(kind: str, r: httpx.Response) -> LLMError:
        return LLMError(redact(f"{kind} {r.status_code}: {r.text[:200]}"))

    async def generate(self, model: str, prompt: str, *, json_mode: bool = False, temperature: float | None = None,
                       max_tokens: int | None = None, no_think: bool = False, notes: Notes | None = None) -> GenResult:
        self._need_key(model)
        req = build_gen_request(model, prompt, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens, no_think=no_think, keys=self.keys)
        r = await self._post(req)
        retried = []
        if r.status_code == 400 and _FIX_RE.search(r.text):
            req = Request(req.url, strip_unsupported(req.body), req.headers)
            notes and notes("參數不相容", "已拿掉思考／溫度設定重送")
            retried.append("參數不相容")
            r = await self._post(req)
        if r.status_code != 200:
            raise self._fail("模型 API", r)
        res = parse_gen_response(model, r.json())
        res.retried = retried
        return res

    async def generate_json(self, model: str, prompt: str, *, notes: Notes | None = None, **kw) -> tuple[Any, GenResult]:
        res = await self.generate(model, prompt, json_mode=True, notes=notes, **kw)
        return parse_json(res.text, notes), res

    async def embed(self, model: str, texts: Sequence[str], *, dim: int, task: str = "document") -> list[list[float]]:
        """回傳與 texts 同順序、已正規化的向量；task＝document（建庫）或 query（查詢）"""
        if not texts:
            return []
        self._need_key(model)
        reqs = build_embed_requests(model, texts, dim=dim, task=task, keys=self.keys)
        sem = asyncio.Semaphore(EMBED_CONCURRENCY)

        async def one(req: Request) -> list[list[float]]:
            async with sem:
                r = await self._post(req)
            if r.status_code != 200:
                raise self._fail("向量 API", r)
            return parse_embed_response(r.json())

        out: list[list[float]] = []
        for vecs in await asyncio.gather(*(one(q) for q in reqs)):
            out.extend(vecs)
        if len(out) != len(texts):
            raise LLMError(f"向量數量不符：送 {len(texts)} 筆、回 {len(out)} 筆")
        bad = next((len(v) for v in out if len(v) != dim), None)
        if bad is not None:
            raise LLMError(f"向量維度不符：預期 {dim}、實際 {bad}（模型 {model_id(model)} 可能不支援這個維度）")
        return [normalize(v) for v in out]
