"""去識別化。逐一對應 apps-script/Chat.gs 的 deidScrub_、deidApply_、deidNext_、deidRule_、deidInstr_、deidFromItems_、deid_。

注意與 JavaScript 一致的細節：正規式的 \\w \\d \\b 在 JS 只認 ASCII（這裡用 re.ASCII）；字串長度用 UTF-16 單位計算。
原名與代稱的對照表（name_map）只回給使用者端，不落地。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from services.llm import LLMClient, Notes
from settings.loader import BotConfig, js_str

NAME_SKIP = ("小朋", "小孩", "小班", "小組", "小時", "小心", "小事", "小動", "小便", "小肌", "小聲", "小小", "小點", "小任", "小步", "小卡",
             "小物", "小玩", "小型", "小學", "小姐", "小兒", "小禮", "小貼", "小椅", "小桌", "小籃", "小手", "小部", "小片", "小塊", "小幫")
DEID_CATS = ("subject", "family", "staff", "org", "other")
# 模型偶爾會把一般名詞當成人名，這些不換
DEID_GENERIC = ("小朋友", "孩子", "小孩", "幼兒", "老師", "家長", "媽媽", "爸爸", "園長", "主任", "同學", "班上", "園所", "學校", "阿公", "阿嬤",
                "奶奶", "爺爺", "哥哥", "姊姊", "姐姐", "弟弟", "妹妹")
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_MOBILE = re.compile(r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}", re.ASCII)
_PHONE = re.compile(r"\(0\d\)[-\s]?\d{3,4}[-\s]?\d{4}|0\d[-\s]?\d{3,4}[-\s]?\d{4}", re.ASCII)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", re.ASCII)
_ID = re.compile(r"\b[A-Z][12]\d{8}\b", re.ASCII)
_SMALL_NAME = re.compile("小[一-鿿]")


def u16len(s: str) -> int:
    """JavaScript 的 string.length（UTF-16 單位）"""
    return len(s.encode("utf-16-le")) // 2


@dataclass
class DeidResult:
    text: str
    map: dict[str, str]
    found: int = 0
    fallback: str | None = None   # AI 失敗改用規則時的原因


def scrub(text: str) -> str:
    t = _MOBILE.sub("（電話已刪除）", str(text))
    t = _PHONE.sub("（電話已刪除）", t)
    t = _EMAIL.sub("（Email 已刪除）", t)
    return _ID.sub("（證號已刪除）", t)


def apply(text: str, name_map: dict[str, str]) -> str:
    """依對照表替換：長的先換，避免「王小明」只換到「小明」"""
    out = str(text)
    for n in sorted(name_map, key=u16len, reverse=True):
        out = out.replace(n, name_map[n])
    return out


def next_label(name_map: dict[str, str], prefix: str) -> str:
    used = sum(1 for k in name_map if name_map[k].startswith(prefix))
    return prefix + LETTERS[used % 26]


def rule(text: str, name_map: dict[str, str] | None, labels: dict[str, str]) -> DeidResult:
    m = dict(name_map or {})

    def repl(mt: re.Match) -> str:
        w = mt.group(0)
        if w in NAME_SKIP:
            return w
        if not m.get(w):
            m[w] = next_label(m, labels["subject"])
        return m[w]

    out = scrub(_SMALL_NAME.sub(repl, str(text)))
    return DeidResult(apply(out, m), m, 0)


def instructions(roles: dict[str, str], known: list[str] | None) -> str:
    """給模型的「要找哪些個資」說明；單獨呼叫與併入 L4 時共用"""
    return ("找出訊息裡所有可以指認特定個人或機構的詞：\n"
            f"- subject：{roles['subject']}的姓名、小名、綽號（例如「小宇」「宇宇」「阿明」「王小明」）\n"
            "- family：家長或家人的姓名、稱呼（例如「宇宇媽媽」只標「宇宇」，「陳媽媽」標「陳」）\n"
            f"- staff：{roles['user']}或其他人員的姓名\n"
            "- org：園所、學校、機構、地址或可指認的地點名稱\n"
            "- other：其他人名\n"
            "只抓訊息裡「原樣出現」的字串，text 必須能在訊息中逐字找到；一般名詞（小朋友、老師、媽媽、班上）不算。沒有就給空陣列。\n"
            + ("這些已經處理過，不用再列：" + "、".join(known) + "\n" if known else ""))


def from_items(text: str, name_map: dict[str, str] | None, items: list | None, labels: dict[str, str]) -> DeidResult:
    """把模型找到的名字清單套成代稱"""
    m = dict(name_map or {})
    lst = [x for x in (items if isinstance(items, list) else ([items] if items else []))
           if isinstance(x, dict) and isinstance(x.get("text"), str) and u16len(x["text"].strip()) >= 1]
    for x in sorted(lst, key=lambda x: u16len(x["text"]), reverse=True):
        t = x["text"].strip()
        if m.get(t) or t not in str(text) or u16len(t) > 20 or t in DEID_GENERIC:
            continue
        longer = next((k for k in m if t in k and k != t), None)
        if longer:
            m[t] = m[longer]   # 「小明」是「王小明」的一部分：用同一個代稱
            continue
        cat = x.get("type") if x.get("type") in DEID_CATS else "other"
        m[t] = next_label(m, labels[cat])
    return DeidResult(apply(scrub(text), m), m, len(lst))


async def ai(llm: LLMClient, cfg: BotConfig, text: str, name_map: dict[str, str] | None, notes: Notes | None = None) -> DeidResult:
    prompt = ("你負責在送出前去除個人資訊，只輸出 JSON。" + instructions(cfg.roles(), list(name_map or {})) +
              '格式：{"items":[{"text":"","type":"subject"}]}\n\n訊息：\n' + text)
    obj, _ = await llm.generate_json(cfg.str("model_triage"), prompt, temperature=0, no_think=True, notes=notes)
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        raise ValueError("回覆沒有 items 陣列")
    return from_items(text, name_map, obj["items"], cfg.deid_labels())


async def deidentify(llm: LLMClient, cfg: BotConfig, text: str, name_map: dict[str, str] | None, notes: Notes | None = None) -> DeidResult:
    """單獨做一次去識別化（L4 關閉時用）；ai 模式失敗改用規則並記下原因"""
    if js_str(cfg.raw("deid_mode") or "ai") != "rule":
        try:
            return await ai(llm, cfg, text, name_map, notes)
        except Exception as e:   # 模型或格式錯誤都退回規則，不讓整個回答失敗
            d = rule(text, name_map, cfg.deid_labels())
            d.fallback = str(e)
            return d
    return rule(text, name_map, cfg.deid_labels())
