"""提示詞組裝。逐字對應 apps-script/Chat.gs 的 triagePrompt_、追問、assemble_、assembleContinue_、checks_ 的 prompt、
conservativeMsg_、continueOptions_，以及 Graph.gs 的 kgPromptText_。測試會逐字比對 Apps Script 版的輸出。

所有函式都是純函式：輸入明確傳入（設定、段落、禁止用語…），不碰資料庫與網路。
段落 hit 的格式：{"ref": "K1", "title": "…", "page": "…", "text": "…"}。
"""
from __future__ import annotations

import json
import re
from typing import Any

from services.deid import instructions as deid_instructions
from settings.loader import BotConfig, js_str


def js_json(v: Any) -> str:
    """與 JavaScript JSON.stringify 相同的輸出：緊湊、不跳脫中文、整數值的浮點數寫成整數"""
    def fix(x):
        if isinstance(x, float) and x.is_integer():
            return int(x)
        if isinstance(x, dict):
            return {k: fix(val) for k, val in x.items()}
        if isinstance(x, list):
            return [fix(i) for i in x]
        return x
    return json.dumps(fix(v), ensure_ascii=False, separators=(",", ":"))


def _range_text(cfg: BotConfig, mode: str) -> str:
    a, b = cfg.items_range(mode)
    return f"{a}–{b}"


def continue_options(cfg: BotConfig) -> list[str]:
    return [o for o in (re.sub(r"^[-・\d.、\s]+", "", s).strip() for s in re.split(r"\n+", cfg.text("continue_options"))) if o]


def kg_prompt_text(kg: dict | None) -> str:
    if not kg or not kg.get("nodes"):
        return ""
    return "\n".join("- " + n["name"] + (f"（{n['type']}）" if n.get("type") else "") + (f"：{n['def']}" if n.get("def") else "") +
                     (f"\n  實務要點：{n['tips']}" if n.get("tips") else "") for n in kg["nodes"])


def triage_prompt(cfg: BotConfig, prev_user: list[str], latest: str, topics: list[str], categories: list[str], deid_known: list[str] | None) -> str:
    r, ins = cfg.roles(), cfg.intents()
    with_deid = deid_known is not None
    return (f"你是{r['domain']}的分流器，只輸出 JSON。依下列規則判斷：\n" + cfg.text("triage_rules") + "\n\n"
            "欄位：\n"
            f"- intent：{r['user']}「這一則訊息」的意圖，從 " + js_json([i["name"] for i in ins]) + " 選一個。" + "；".join(i["name"] + "＝" + i["desc"] for i in ins) + "\n"
            "- referral.category：從 " + js_json(categories) + " 選一個，或 \"none\"\n"
            "- referral.level：\"緊急\"、\"需要專人\"、\"建議諮詢\" 或 \"none\"；referral.reason 一句話\n"
            "- topics：從 " + js_json(topics) + " 選出相關的（可多選）\n"
            "- in_scope：是否屬於服務範圍\n"
            "- info_enough：整個個案的資訊是否足夠\n"
            "- missing：缺少的關鍵資訊（最多 2 項）\n"
            f"- focus：用一句話寫出{r['user']}「這一則」真正想要的東西（" + cfg.text("triage_focus_hint") + "），補充或回答追問時寫整個個案目前要解決的事\n"
            + ("- deid：" + deid_instructions(r, deid_known) if with_deid else "") +
            "格式：{" + ('"deid":[{"text":"","type":"subject"}],' if with_deid else "") + '"intent":"' + ins[0]["name"] +
            '","focus":"","referral":{"category":"none","level":"none","reason":""},"topics":[],"in_scope":true,"info_enough":true,"missing":[]}\n\n'
            + (f"{r['user']}先前說過：\n" + "\n".join(prev_user) + "\n\n" if prev_user else "") +
            f"{r['user']}這一則訊息：\n" + latest)


def skill_text(cfg: BotConfig, picked: list[dict]) -> str:
    """picked：[{name, content, always}]；沒有主題 Skill 時加一句提示"""
    no_topic = not any(not s["always"] for s in picked)
    return ("\n\n".join(f"【{s['name']}】\n{s['content']}" for s in picked) +
            (("\n\n" if picked else "") + f"（這題沒有對應的主題 Skill：依{cfg.roles()['user']}的問題本身與檢索到的知識直接回答，不必套用特定框架。）" if no_topic else ""))


def followup_prompt(cfg: BotConfig, sys: str, skills: str, missing: list[str], case_text: str) -> str:
    return (sys + "\n\n" + skills + "\n\n" + cfg.text("followup_prompt") + "\n目前缺少：" + ("、".join(missing) or "情境細節") +
            '\n只輸出 JSON：{"empathy":"","questions":["",""]}\n\n' + cfg.roles()["user"] + "的描述：\n" + case_text)


def _hist(cfg: BotConfig, history: list[dict]) -> str:
    u = cfg.roles()["user"]
    return "\n".join((u + "：" if h.get("role") == "teacher" else "助手：") + js_str(h.get("text")) for h in history)


def _kn(hits: list[dict]) -> str:
    return "\n\n".join(h["ref"] + "｜" + h["title"] + (f"（第{h['page']}頁）" if h.get("page") else "") + "\n" + h["text"] for h in hits)


def _kg_block(kg) -> str:
    t = kg_prompt_text(kg)
    return "【知識圖譜背景】（與問題相關的概念與做法方向，只能當理解背景，不能當出處）\n" + t if t else ""


def assemble_full(cfg: BotConfig, *, sys: str, skills: str, tri: dict, case_text: str, hits: list[dict], history: list[dict], ref: dict | None,
                  ref_who: str, feedback: list[str] | None, focus: str, fallback: bool, kg: dict | None, forbidden_words: list[str]) -> str:
    r = cfg.roles()
    hist, kn = _hist(cfg, history), _kn(hits)
    ref_line = '"referral":' + (f'"依「{ref["level"]}」說明為什麼需要、該找誰（可找：{ref_who}）"' if ref else "null")
    parts = [
        "【1 固定 system prompt】\n" + sys,
        "【2 主題 Skill】\n" + skills,
        _kg_block(kg),
        "【3 情境資訊】\n分流結果：" + js_json(tri) + "\n" + r["user"] + "原話：\n" + case_text +
        (f"\n\n【{r['user']}這次想要的】{focus}\n回答要先直接回應這一點；策略要具體到這個{r['subject']}與情境（做什麼、什麼時候、怎麼說），不要只寫一般原則。" if focus else ""),
        ("【4 知識庫內容不足】" + ("以下只是相關的背景或原則，可以引用（編號 K1…），但沒有直接針對這個情況的做法：\n" + kn if kn else "知識庫沒有相關內容。") +
         "\n這次可以用一般實務知識補足：" + cfg.text("fallback_rules")) if fallback else "【4 檢索到的知識（只能引用這些，編號 K1…）】\n" + kn,
        "【5 對話歷史】\n" + hist if hist else "",
        "【重寫指示】上一版沒有通過檢查：" + "；".join(feedback) + "。請修正後重寫。" if feedback else "",
        "輸出格式（只輸出 JSON）：" + cfg.schema_format("full", ['"knowledge_gap":null', ref_line]) + "\n" +
        ("" if fallback else "若上面的知識沒有在談這個問題的對象或情況，不要硬套：knowledge_gap 填一句話說明缺少什麼，有出處的建議給空陣列；知識有涵蓋時 knowledge_gap 為 null。\n") +
        "規則：" + ("有出處的建議 " + _range_text(cfg, "full") + " 個；引用背景知識時 refs 只能用上面提供的 K 編號，來自一般知識時 refs 為空陣列；不得推測診斷或使用標籤化用語；保留去識別化的代稱。"
                   if fallback else cfg.text("answer_rules")) +
        ("回答中不得出現這些詞（即使是否定句也不要寫）：" + "、".join(forbidden_words) + "。" if forbidden_words else ""),
    ]
    return "\n\n".join(p for p in parts if p)


def assemble_continue(cfg: BotConfig, *, sys: str, skills: str, prev_user: list[str], latest: str, hits: list[dict], history: list[dict], ref: dict | None,
                      ref_who: str, intent: str, feedback: list[str] | None, focus: str, fallback: bool, kg: dict | None, forbidden_words: list[str]) -> str:
    r = cfg.roles()
    hist, kn = _hist(cfg, history), _kn(hits)
    ref_line = '"referral":' + (f'"依「{ref["level"]}」簡短說明該找誰（可找：{ref_who}）"' if ref else "null")
    parts = [
        "【1 固定 system prompt】\n" + sys,
        "【2 主題 Skill】\n" + skills,
        _kg_block(kg),
        "【3 對話到目前為止】\n" + (hist or "（無）"),
        f"【4 {r['user']}這次說的】（{intent}）\n" + latest + (f"\n{r['user']}想要的：{focus}" if focus else ""),
        ("【5 知識庫內容不足】" + ("以下知識沒有直接針對這次的問題，只能當背景引用（編號 K1…）：\n" + kn + "\n" if kn else "") +
         "這次可以用一般實務知識補足：" + cfg.text("fallback_rules")) if fallback else
        ("【5 檢索到的知識（只能引用這些，編號 K1…）】\n" + kn if kn else "【5 檢索到的知識】這次沒有找到新的相關知識，不要給新的策略。"),
        "【延續回覆的做法】\n" + cfg.text("continue_prompt") + "\n可提供的下一步選項（挑 2–4 個最適合的，可改寫成符合情境的說法）：\n" + "\n".join("- " + o for o in continue_options(cfg)),
        "【重寫指示】上一版沒有通過檢查：" + "；".join(feedback) + "。請修正後重寫。" if feedback else "",
        "輸出格式（只輸出 JSON，沒有內容的欄位填 null 或空陣列）：" + cfg.schema_format("continue", [ref_line, '"options":["下一步選項"]', '"knowledge_gap":null']) + "\n" +
        ("" if fallback else "若這次的問題換了對象或主題，而上面的知識沒有在談它，不要硬套：knowledge_gap 填一句話說明缺少什麼；知識有涵蓋時為 null。\n") +
        "規則：有出處的建議 " + _range_text(cfg, "continue") + " 個，只放之前沒給過的新做法，" + ("有引用知識才填 refs；" if fallback else "每個都要有 refs；") +
        "不要重複之前回答過的內容；不得推測診斷；保留去識別化的代稱；refs 編號不要寫進文字裡。" +
        ("不得出現這些詞：" + "、".join(forbidden_words) + "。" if forbidden_words else ""),
    ]
    return "\n\n".join(p for p in parts if p)


def checks_prompt(cfg: BotConfig, *, case_text: str, answer_json: str, hits: list[dict], focus: str, fallback: bool, do_l5: bool, do_rel: bool) -> str:
    r = cfg.roles()
    ci = cfg.check_items() if do_l5 else []
    kn = "\n".join(h["ref"] + "：" + h["text"] for h in hits)
    return (f"你是{r['domain']}回答的審查器，只輸出 JSON。\n" +
            ("逐項檢查回答：\n" + "\n".join(f"({i + 1}) {it['name']}：{it['desc']}" for i, it in enumerate(ci)) +
             "\n嚴重（會擋下回答）只用在：" + cfg.text("check_severe") + "\n每個問題的 severe：符合「嚴重」的定義為 true，其他為 false。\n" if do_l5 else "") +
            ("這次知識庫內容不足，refs 為空的策略來自一般實務知識：「依據」改為檢查是否符合下列補足規則：" + cfg.text("fallback_rules") + "\n越界或可能造成傷害才算嚴重。\n" if do_l5 and fallback else "") +
            (f"另外判斷回應度：回答是否直接回應「{r['user']}想要的」，策略是否具體到這個{r['subject']}與情境（不是只有一般原則）。addressed 為 true／false，note 說明缺了什麼；"
             "addressed 為 false 時，cause 填「知識不足」（提供的知識沒有在談這個問題的對象或情況，重寫也無法改善）或「偏題」（知識有涵蓋，但回答沒用好）。\n" if do_rel else "") +
            "格式：{" + ('"issues":[{"type":"' + "|".join(it["name"] for it in ci) + '","severe":false,"note":"一句話說明問題"}]' if do_l5 else "") + ("," if do_l5 and do_rel else "") +
            ('"relevance":{"addressed":true,"cause":null,"note":""}' if do_rel else "") + "}" + ("，沒有問題時 issues 為空陣列" if do_l5 else "") + "\n\n" +
            (f"{r['user']}想要的：{focus}\n\n" if do_rel else "") + f"{r['user']}描述：\n" + case_text + "\n\n知識：\n" + kn + "\n\n回答：\n" + answer_json)


def conservative_msg(cfg: BotConfig, ref_table: dict[str, dict], why: str | None = None) -> str:
    who = (ref_table.get("系統能力不足") or {}).get("who") or cfg.roles()["escalate"]
    return cfg.text("msg_check_failed" if why == "check" else "msg_no_knowledge").replace("{who}", who)
