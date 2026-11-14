"""「用說的」設定：口語描述 → 結構化設定，以及反向。對應 apps-script/Admin.gs 的 adminDescribe、describeNormalize_、adminDescribeBack。

回傳驗證過的結果讓前端顯示，確認後才存；這裡不寫資料庫。
"""
from __future__ import annotations

import re
from typing import Any

from graph.prompts import js_json
from services.llm import LLMClient
from settings.loader import BotConfig, js_str

# 文字與 Apps Script 版逐字相同（tests/test_describe.py 比對）
KINDS: dict[str, dict[str, str]] = {
    'answer_schema': {"instr": '把下面的描述轉成「回答欄位」清單。每個欄位：label（顯示標題，可空）、hint（給回答模型看的一句說明）、type（text＝一段文字、list＝條列、items＝有出處的建議清單）、modes（full＝只在完整回答、continue＝只在延續回覆、both）、required、summary（是否放進對話摘要）、copy（是否提供複製鈕，通常是可以直接轉貼的訊息草稿）、items 另有 count（完整回答時的數量範圍如 "2-3"）與 count_continue。label_continue 是延續回覆時的標題（可省略）。順序依描述的先後。',
                 "format": '{"fields":[{"label":"","label_continue":"","hint":"","type":"text","modes":"full","required":true,"summary":false,"copy":false,"count":"2-3","count_continue":"0-2"}]}'},
    'intents': {"instr": '把下面的描述轉成四個意圖選項，順序固定：1 新問題（換了一個完全不同的狀況，只看這一則）、2 補充同一問題、3 要求特定協助（要具體東西，不追問）、4 與主題無關（拒答）。每個給 name（短名稱）與 desc（一句判斷說明）。描述沒提到的項目用合理的通用寫法。',
                 "format": '{"intents":[{"name":"","desc":""},{"name":"","desc":""},{"name":"","desc":""},{"name":"","desc":""}]}'},
    'check_items': {"instr": '把下面的描述轉成回答審查的檢查項目清單。每項：name（2–4 字的類型名）、desc（一句判斷說明：什麼情況算有問題、什麼不算）。另外給 severe：一句話說明什麼算「嚴重」（嚴重才會擋下回答重寫）。',
                 "format": '{"items":[{"name":"","desc":""}],"severe":""}'},
}


class DescribeError(ValueError):
    """訊息給使用者看"""


def describe_prompt(cfg: BotConfig, kind: str, text: str, current: str | None) -> str:
    k, r = KINDS[kind], cfg.roles()
    return (f"你在幫{r['domain']}的管理者把口語描述轉成系統設定，只輸出 JSON。提問者稱為「{r['user']}」、談論對象稱為「{r['subject']}」；說明文字裡提到他們時直接用這兩個詞。\n" +
            k["instr"] + "\n" + ("目前的設定（描述沒提到的部分可以沿用）：\n" + str(current)[:4000] + "\n" if current else "") +
            "格式：" + k["format"] + "\n\n描述：\n" + text)


def _concat(v) -> list:
    return v if isinstance(v, list) else ([] if v is None or v is False or v == "" or v == 0 else [v])


_RANGE = re.compile(r"^\d+-\d+$")
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize(kind: str, r: Any) -> dict:
    get = (lambda k: r.get(k)) if isinstance(r, dict) else (lambda k: None)
    if kind == "answer_schema":
        lst = [f for f in _concat(get("fields")) if isinstance(f, dict)]
        if not lst:
            raise DescribeError("沒有轉出任何欄位")
        used: set[str] = set()
        rows = []
        for i, f in enumerate(lst):
            t = f.get("type") if f.get("type") in ("text", "list", "items") else "text"
            key = js_str(f.get("key")).strip()
            if not _KEY.match(key) or key in used:
                key = f"{t}_{i + 1}"
                while key in used:
                    key += "_"
            used.add(key)
            o: dict[str, Any] = {"key": key, "type": t, "label": js_str(f.get("label")), "hint": js_str(f.get("hint")),
                                 "modes": f.get("modes") if f.get("modes") in ("continue", "both") else "full",
                                 "required": bool(f.get("required")), "summary": bool(f.get("summary")), "copy": bool(f.get("copy"))}
            if f.get("label_continue"):
                o["label_continue"] = js_str(f.get("label_continue"))
            if t == "items":
                o["count"] = js_str(f.get("count")) if _RANGE.match(js_str(f.get("count"))) else "2-3"
                o["count_continue"] = js_str(f.get("count_continue")) if _RANGE.match(js_str(f.get("count_continue"))) else "0-2"
            rows.append(o)
        warn = []
        if not any(x["type"] == "items" for x in rows):
            warn.append("沒有「有出處的建議」欄位：出處機制開著時會改用預設欄位")
        if not any(x["type"] != "items" for x in rows):
            warn.append("沒有任何文字欄位，回答會只有建議清單")
        return {"kind": kind, "rows": rows, "warn": warn, "json": "[\n" + ",\n".join(js_json(o) for o in rows) + "\n]"}
    if kind == "intents":
        lst = _concat(get("intents"))[:4]
        if len(lst) < 4:
            raise DescribeError(f"意圖要剛好四個，只轉出 {len(lst)} 個")
        rows = [{"name": re.sub(r"[＝=\n]", " ", js_str(_g(x, "name"))).strip(), "desc": js_str(_g(x, "desc")).replace("\n", " ").strip()} for x in lst]
        if any(not x["name"] for x in rows):
            raise DescribeError("有意圖沒有名稱")
        return {"kind": kind, "rows": rows, "warn": [], "text": "\n".join(x["name"] + "＝" + x["desc"] for x in rows)}
    if kind == "check_items":
        lst = [x for x in _concat(get("items")) if x and js_str(_g(x, "name")).strip()]
        if not lst:
            raise DescribeError("沒有轉出任何檢查項目")
        rows = [{"name": re.sub(r"[＝=\n|]", " ", js_str(_g(x, "name"))).strip(), "desc": js_str(_g(x, "desc")).replace("\n", " ").strip()} for x in lst]
        return {"kind": kind, "rows": rows, "warn": [], "text": "\n".join(x["name"] + "＝" + x["desc"] for x in rows), "severe": js_str(get("severe")).strip()}
    raise DescribeError("不支援：" + kind)


def _g(x, k):
    return x.get(k) if isinstance(x, dict) else None


async def describe(llm: LLMClient, cfg: BotConfig, kind: str, text: str, current: str | None = None) -> dict:
    if kind not in KINDS:
        raise DescribeError("不支援：" + kind)
    text = (text or "").strip()
    if not text:
        raise DescribeError("請先寫描述")
    r, _ = await llm.generate_json(cfg.str("model_answer"), describe_prompt(cfg, kind, text, current), temperature=0)
    return normalize(kind, r)


def back_source(cfg: BotConfig, kind: str) -> str:
    if kind == "answer_schema":
        return js_json([f.to_public() for f in cfg.schema()])
    if kind == "intents":
        return js_json(cfg.intents())
    if kind == "check_items":
        return js_json(cfg.check_items()) + "\n嚴重：" + cfg.text("check_severe")
    raise DescribeError("不支援：" + kind)


def back_prompt(src: str) -> str:
    return ("把下面的系統設定改寫成一段給一般人看的自然語言描述（繁體中文，150 字內，不要出現 key、type、JSON 這類字眼），讓人讀了知道目前的行為、也能直接改字再轉回設定。"
            '只輸出 JSON：{"text":""}\n\n設定：\n' + src)


async def describe_back(llm: LLMClient, cfg: BotConfig, kind: str) -> str:
    r, _ = await llm.generate_json(cfg.str("model_answer"), back_prompt(back_source(cfg, kind)), temperature=0)
    return js_str(r.get("text") if isinstance(r, dict) else None).strip()
