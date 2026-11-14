"""切段。chunk_text 逐行對應 apps-script/Knowledge.gs 的 chunkText_；chunk_units 加上結構邊界（docs/ingest.md §4）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PARA = re.compile(r"\n\s*\n|\n(?=\[\[第\d+頁\]\])")
_PAGE = re.compile(r"^\[\[第(\d+)頁\]\]\s*")
MIN_UNIT = 100        # 結構單位短於這個字數就與下一個合併


@dataclass
class Unit:
    """解析器的輸出：一個結構單位（PDF 一頁、Word 一個標題段、PPT 一張、Excel 一個工作表）"""
    loc: str
    text: str
    rows: list[tuple[int, str]] = field(default_factory=list)   # Excel：(列號, 「欄名：值；…」)，有值時忽略 text
    sheet: str = ""


@dataclass
class Piece:
    text: str
    page: str


def chunk_text(text: str, size: int = 500, overlap: int = 80) -> list[Piece]:
    """依空行分段落，累積到約 size 字收一段，下一段帶上一段末尾 overlap 字；單段超過 1.6 倍硬切。保留 [[第N頁]] 頁碼"""
    paras = [s.strip() for s in _PARA.split(str(text).replace("\r", ""))]
    paras = [p for p in paras if p]
    out: list[Piece] = []
    buf, page, buf_page = "", "", ""

    def flush():
        if buf.strip():
            out.append(Piece(buf.strip(), buf_page or page))

    for p in paras:
        m = _PAGE.match(p)
        if m:
            page = m.group(1)
            p = p[m.end():].strip()
            if not p:
                continue
        if not buf:
            buf_page = page
        if len(buf + "\n" + p) > size and buf:
            flush()
            buf = (buf[-overlap:] if overlap else "") + "\n" + p
            buf_page = page
        else:
            buf = buf + "\n" + p if buf else p
        while len(buf) > size * 1.6:
            out.append(Piece(buf[:size], buf_page or page))
            buf = buf[size - overlap:]
    flush()
    return out


def merge_loc(a: str, b: str) -> str:
    """p.3＋p.4 → p.3–4；第 5 張＋第 6 張 → 第 5–6 張；其他 → a–b"""
    if a == b or not b:
        return a
    if not a:
        return b
    ma, mb = re.fullmatch(r"(.*?)(\d+)(\D*)", a), re.fullmatch(r"(.*?)(\d+)(\D*)", b)
    if ma and mb and ma.group(1) == mb.group(1) and ma.group(3) == mb.group(3):
        return f"{ma.group(1)}{ma.group(2)}–{mb.group(2)}{ma.group(3)}"
    return f"{a}–{b}"


def _rows_chunks(u: Unit, size: int) -> list[Piece]:
    """Excel：整列不切開，一段放滿為止"""
    out, cur = [], []
    for r in u.rows:
        if cur and len("\n".join(x[1] for x in cur + [r])) > size:
            out.append(cur)
            cur = []
        cur.append(r)
    if cur:
        out.append(cur)
    return [Piece("\n".join(x[1] for x in g), f"{u.sheet} 第 {g[0][0]}–{g[-1][0]} 列" if g[0][0] != g[-1][0] else f"{u.sheet} 第 {g[0][0]} 列") for g in out]


def chunk_units(units: list[Unit], size: int = 500, overlap: int = 80) -> list[Piece]:
    """段落不跨結構單位；單位太短（< 100 字）才與下一個合併，位置標成範圍"""
    merged: list[Unit] = []
    for u in units:
        if u.rows:
            merged.append(u)
            continue
        if not u.text.strip():
            continue
        prev = merged[-1] if merged else None
        if prev is not None and not prev.rows and len(prev.text.strip()) < MIN_UNIT:
            merged[-1] = Unit(merge_loc(prev.loc.split("–")[0], u.loc), prev.text.rstrip() + "\n\n" + u.text)
        else:
            merged.append(Unit(u.loc, u.text))
    out: list[Piece] = []
    for u in merged:
        if u.rows:
            out.extend(_rows_chunks(u, size))
        else:
            out.extend(Piece(p.text, u.loc) for p in chunk_text(u.text, size, overlap))
    return out
