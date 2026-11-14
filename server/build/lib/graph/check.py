"""回答的驗證、清理與檢查。逐一對應 apps-script/Chat.gs 的 validateAnswer_、stripRefs_、cleanAnswer_、checks_。

模型回的 JSON 型別不可靠（refs 給成字串、list 給成文字、issues 給成字串陣列），這裡的容錯與 Apps Script 版一致；
差別只有一處：禁止用語的正規式寫錯時 Apps Script 會整個回答失敗，這裡改成略過那條規則並記一筆設定錯誤。
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ForbiddenRule
from graph.prompts import checks_prompt, js_json
from services.llm import LLMClient, Notes
from settings.loader import BotConfig, js_str, js_truthy

_REFS_INLINE = re.compile(r"\s*[（(\[【]\s*K[0-9]+(\s*[,，、]\s*K[0-9]+)*\s*[)）\]】]")
_REFS_TAIL = re.compile(r"\s*K[0-9]+(\s*[,，、]\s*K[0-9]+)*\s*$")


def js_string(v: Any) -> str:
    """JavaScript 的 String(v)：null→'null'、陣列用逗號接、物件→'[object Object]'"""
    if v is None:
        return "null"
    if isinstance(v, list):
        return ",".join("" if x is None else js_string(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return js_str(v)


def u16slice(s: str, n: int) -> str:
    """JavaScript 的 s.slice(0, n)（以 UTF-16 單位計）"""
    b = s.encode("utf-16-le")[: n * 2]
    return b.decode("utf-16-le", errors="ignore")


def _get(x: Any, k: str) -> Any:
    return x.get(k) if isinstance(x, dict) else None


def _concat(v: Any) -> list:
    """[].concat(v)"""
    return list(v) if isinstance(v, list) else [v]


def _note(notes: Notes | None, kind: str, msg: str) -> None:
    if notes:
        notes(kind, msg)


def strip_refs(t: Any) -> str:
    """去掉模型自己寫進內文的 (K1)、【K1、K2】與句尾的 K3"""
    return _REFS_TAIL.sub("", _REFS_INLINE.sub("", js_str(t))).strip()


def validate_answer(cfg: BotConfig, a: Any, mode: str, notes: Notes | None) -> None:
    """只記穩定度，不改內容"""
    if not isinstance(a, dict):
        return _note(notes, "型別錯誤", "回答不是物件")
    for f in cfg.schema_fields(mode):
        if f.key not in a:
            if f.required:
                _note(notes, "缺欄位", f.key)
            continue
        v = a[f.key]
        if f.type == "text":
            if v is not None and not isinstance(v, str):
                _note(notes, "型別錯誤", f.key + " 不是文字")
        elif v is not None and not isinstance(v, list):
            _note(notes, "型別錯誤", f.key + " 不是陣列")
        if f.type == "items":
            for i, s in enumerate(v if isinstance(v, list) else []):
                if not js_truthy(s) or not isinstance(s, (dict, list)):
                    _note(notes, "型別錯誤", f"{f.key}{i + 1} 不是物件")
                    continue
                for k in ("title", "how", "refs"):
                    if not (isinstance(s, dict) and k in s):
                        _note(notes, "缺欄位", f"{f.key}{i + 1}.{k}")
                if isinstance(s, dict) and "refs" in s and not isinstance(s["refs"], list):
                    _note(notes, "型別錯誤", f"{f.key}{i + 1}.refs 不是陣列")
    if "referral" not in a:
        _note(notes, "缺欄位", "referral")


def clean_answer(cfg: BotConfig, a: Any, mode: str = "full") -> dict:
    """只留這個模式的欄位＋系統欄位，一律轉成文字並去掉內文的出處標記"""
    a = a if isinstance(a, dict) else {}
    out: dict[str, Any] = {}
    for f in cfg.schema_fields(mode or "full"):
        v = a.get(f.key)
        if f.type == "items":
            out[f.key] = [{"title": strip_refs(_get(s, "title")), "how": strip_refs(_get(s, "how")),
                           "refs": [r for r in (js_string(x) for x in _concat(_get(s, "refs") if js_truthy(_get(s, "refs")) else [])) if r]}
                          for s in _concat(v if js_truthy(v) else []) if js_truthy(s) and isinstance(s, (dict, list))]
        elif f.type == "list":
            out[f.key] = [t for t in (strip_refs(x) for x in _concat([] if v is None or v == "null" else v)) if t]
        else:
            out[f.key] = None if v is None or v == "null" else (strip_refs(v) or None)
    r = a.get("referral")
    out["referral"] = None if r is None or r == "null" else (strip_refs(r) or None)
    if js_truthy(a.get("options")):
        out["options"] = [t for t in (js_str(x) for x in _concat(a["options"])) if t]
    return out


def load_forbidden(session: Session, bot_id) -> list[dict]:
    rows = session.scalars(select(ForbiddenRule).where(ForbiddenRule.bot_id == bot_id).order_by(ForbiddenRule.kind, ForbiddenRule.rule))
    return [{"rule": r.rule.strip(), "regex": r.kind == "regex", "note": r.note or ""} for r in rows if r.rule.strip()]


def _find_forbidden(forbidden: list[dict], s: str, notes: Notes | None) -> dict | None:
    for f in forbidden:
        if f["regex"]:
            try:
                if re.search(f["rule"], s):
                    return f
            except re.error as e:
                _note(notes, "設定錯誤", f"禁止用語正規式無效：{f['rule'][:40]}（{e}）")
        elif f["rule"] in s:
            return f
    return None


async def run_checks(cfg: BotConfig, llm: LLMClient | None, *, case_text: str, ans: dict, hits: list[dict], forbidden: list[dict],
                     rng: tuple[int, int] | list[int] | None = None, focus: str = "", fallback: bool = False, notes: Notes | None = None) -> dict:
    """P08 禁止用語、P09 出處、L5 與回應度（後兩者合併成一次模型呼叫）"""
    rng = rng or (2, 3)
    r = cfg.roles()
    items: list[dict] = []
    reasons: list[str] = []
    l5_issues: list[dict] = []
    rel_fail = None
    s = js_json(ans)
    bad = _find_forbidden(forbidden, s, notes)
    items.append({"name": "P08 規則檢查", "pass": not bad, "note": f"出現禁止用語「{bad['rule']}」" if bad else "沒有禁止用語"})
    if bad:
        reasons.append(f"不可使用「{bad['rule']}」" + (f"（{bad['note']}）" if bad["note"] else ""))

    ok = {js_string(h["ref"]) for h in hits}
    st = cfg.answer_items(ans)
    p9: list[str] = []
    if cfg.citation_on():
        if len(st) < rng[0] or len(st) > rng[1]:
            p9.append(f"建議數量 {len(st)} 個，應為 {rng[0]}–{rng[1]} 個")
        for x in st:
            refs = _get(x, "refs")
            refs = _concat(refs) if js_truthy(refs) and not isinstance(refs, list) else (refs or [])
            if not fallback and not refs:
                p9.append(f"「{js_string(_get(x, 'title')) if isinstance(x, dict) and 'title' in x else 'undefined'}」沒有出處")
            for ref in refs:
                if js_string(ref) not in ok:
                    p9.append(js_string(ref) + " 不在檢索結果中")
        items.append({"name": "P09 出處核對", "pass": not p9,
                      "note": "；".join(p9) or ("知識不足，部分建議來自 AI 一般知識（已標示）" if fallback else "每條建議都有出處" if st else "沒有新建議")})
        reasons.extend(p9)
    else:
        items.append({"name": "P09 出處核對", "pass": True, "note": "已關閉（enable_citation）"})

    do_l5 = cfg.flag("enable_llm_check")
    do_rel = bool(focus) and cfg.flag("enable_relevance_check")
    if do_l5 or do_rel:
        try:
            if llm is None:
                raise RuntimeError("沒有模型")
            ci = cfg.check_items() if do_l5 else []
            prompt = checks_prompt(cfg, case_text=case_text, answer_json=s, hits=hits, focus=focus, fallback=fallback, do_l5=do_l5, do_rel=do_rel)
            l5, _ = await llm.generate_json(cfg.str("model_check"), prompt, temperature=0, no_think=True, notes=notes)
            if l5 is None:
                raise TypeError("檢查結果是 null")
            if not isinstance(l5, dict):
                l5 = {}   # 陣列、文字、數字在 JS 讀屬性都是 undefined
            issues_raw = l5.get("issues")
            if do_l5 and not isinstance(issues_raw, list):
                _note(notes, "缺欄位" if issues_raw is None else "型別錯誤", "issues")
            if do_l5 and isinstance(issues_raw, list) and any(isinstance(x, str) for x in issues_raw):
                _note(notes, "型別錯誤", "issues 項目是文字不是物件")
            rel = l5.get("relevance")
            if do_rel and (not js_truthy(rel) or not isinstance(_get(rel, "addressed"), bool)):
                _note(notes, "型別錯誤" if js_truthy(rel) else "缺欄位", "relevance.addressed")
            if do_l5:
                if js_truthy(issues_raw) and not isinstance(issues_raw, list):
                    raise TypeError("issues 不是陣列")
                issues = []
                for x in issues_raw or []:
                    if isinstance(x, str):
                        issues.append({"type": "", "severe": True, "note": x})
                        continue
                    if x is None:
                        raise TypeError("issues 項目是 null")
                    note = next((v for v in (_get(x, k) for k in ("note", "issue", "reason", "description")) if js_truthy(v)), None)
                    issues.append({"type": _get(x, "type") if js_truthy(_get(x, "type")) else "", "severe": _get(x, "severe") is not False,
                                   "note": note if note is not None else js_json(x)})
                l5_issues = [{"type": i["type"] if js_truthy(i["type"]) else "其他", "severe": js_truthy(i["severe"]),
                              "note": u16slice(js_string(i["note"]), 160)} for i in issues]
                severe = [i for i in issues if i["severe"]]
                txt = lambda i: (js_string(i["type"]) + "：" if js_truthy(i["type"]) else "") + js_string(i["note"])
                passed = not severe
                items.append({"name": "L5 檢查", "pass": passed,
                              "note": "、".join(it["name"] for it in ci) + "都沒有問題" if not issues else
                              ("" if not passed else "小問題（不擋）：") + "；".join(("【嚴重】" if i["severe"] else "") + txt(i) for i in issues)})
                if not passed:
                    reasons.extend([txt(i) for i in severe] or ["檢查有問題"])
            if do_rel:
                rel = rel if isinstance(rel, dict) else {}
                ok2 = rel.get("addressed") is not False
                rnote = js_str(rel.get("note"))
                if not ok2:
                    rel_fail = {"focus": focus, "note": u16slice(rnote, 160), "cause": "知識不足" if rel.get("cause") == "知識不足" else "偏題"}
                items.append({"name": "回應度", "pass": ok2,
                              "note": "有直接回應：" + focus if ok2 else f"【嚴重】沒有回應到「{focus}」" + ("：" + rnote if js_truthy(rel.get("note")) else "")})
                if not ok2:
                    reasons.append(f"沒有直接回應{r['user']}想要的「{focus}」" + (f"（{rnote}）" if js_truthy(rel.get("note")) else "") + "，請先直接回應這一點，策略要具體")
        except Exception as e:   # 檢查本身失敗不擋回答
            items.append({"name": "L5 檢查" if do_l5 else "回應度", "pass": True, "note": "檢查未完成（略過）：" + str(e)})
    return {"pass": all(i["pass"] for i in items), "items": items, "reasons": reasons, "l5": l5_issues, "relFail": rel_fail}
