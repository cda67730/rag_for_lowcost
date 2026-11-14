"""文件解析：PDF、Word、PowerPoint、Excel、純文字 → 結構單位（docs/ingest.md §2–3）。

不呼叫模型、不執行檔案內的巨集。所有解析器輸出 list[Unit]，位置寫進段落的 page 欄位。
"""
from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field

from services.chunking import Unit

MAX_BYTES = 50 * 1024 * 1024
MAX_PASTE = 200_000
MAX_ROWS = 5000

MSG_UNSUPPORTED = "目前不支援這種檔案；請轉成 PDF、Word、PowerPoint、Excel 或純文字"
MSG_SCANNED = "這份 PDF 沒有可抽取的文字，可能是掃描檔；目前版本不支援掃描檔"
MSG_ENCRYPTED = "檔案有密碼保護，請先解除後再上傳"
MSG_TOO_BIG = "檔案超過 50 MB"
MSG_OLD = {"doc": "舊版 Word（.doc）不支援，請另存成 .docx", "xls": "舊版 Excel（.xls）不支援，請另存成 .xlsx", "ppt": "舊版 PowerPoint（.ppt）不支援，請另存成 .pptx"}


class ParseError(ValueError):
    """訊息直接給使用者看"""


@dataclass
class Parsed:
    kind: str
    units: list[Unit]
    warnings: list[str] = field(default_factory=list)


# ───────── 類型判斷：副檔名＋檔頭 ─────────

def detect(filename: str, data: bytes) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if len(data) > MAX_BYTES:
        raise ParseError(MSG_TOO_BIG)
    if data[:4] == b"\xd0\xcf\x11\xe0":           # OLE：舊版 Office，或加了密碼的新版 Office
        if ext in MSG_OLD:
            raise ParseError(MSG_OLD[ext])
        if ext in ("docx", "xlsx", "pptx"):
            raise ParseError(MSG_ENCRYPTED)
        raise ParseError(MSG_UNSUPPORTED)
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            raise ParseError(MSG_UNSUPPORTED)
        for kind, prefix in (("docx", "word/"), ("pptx", "ppt/"), ("xlsx", "xl/")):
            if any(n.startswith(prefix) for n in names) and ext == kind:
                return kind
        raise ParseError(MSG_UNSUPPORTED)
    if ext in ("txt", "md", "markdown", "csv") and b"\x00" not in data[:8192]:
        return "md" if ext in ("md", "markdown") else "txt"
    raise ParseError(MSG_UNSUPPORTED)


def decode_text(data: bytes) -> str:
    """UTF-8（含 BOM）優先，不行再猜（Big5 等）"""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    from charset_normalizer import from_bytes
    best = from_bytes(data).best()
    if best is None:
        raise ParseError(MSG_UNSUPPORTED)
    return str(best)


# ───────── PDF ─────────

_END = re.compile(r"[。！？；：」』）)\]】.!?;:]$")
_START_LIST = re.compile(r"^([0-9０-９]+[.、．)]|[一二三四五六七八九十]+[、.．]|[（(][0-9一二三四五六七八九十]+[)）]|[•●■◆○\-*‧])")
_CJK = re.compile(r"[㐀-鿿豈-﫿]")


def _norm_line(s: str) -> str:
    return re.sub(r"\d+", "#", s.strip())


def _join_lines(lines: list[str]) -> str:
    """把版面換行接回段落：上一行沒以句末標點結束、下一行不是條列開頭 → 接起來；中文直接接，英文補空格，連字號斷字合併"""
    paras: list[str] = []
    cur = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            if cur:
                paras.append(cur)
            cur = ""
            continue
        if not cur:
            cur = line
        elif _END.search(cur) or _START_LIST.match(line):
            paras.append(cur)
            cur = line
        elif cur.endswith("-") and line[:1].isalpha():
            cur = cur[:-1] + line
        elif _CJK.match(cur[-1]) or _CJK.match(line[0]):
            cur += line
        else:
            cur += " " + line
    if cur:
        paras.append(cur)
    return "\n\n".join(paras)


def parse_pdf(data: bytes) -> Parsed:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            try:
                if not r.decrypt(""):
                    raise ParseError(MSG_ENCRYPTED)
            except ParseError:
                raise
            except Exception:
                raise ParseError(MSG_ENCRYPTED)
        pages = [(p.extract_text() or "") for p in r.pages]
    except ParseError:
        raise
    except (PdfReadError, Exception) as e:   # 壞檔
        raise ParseError("PDF 檔案無法讀取：" + type(e).__name__)
    lines = [[l for l in p.replace("\r", "").split("\n")] for p in pages]
    # 頁首頁尾：同一行（數字視為相同）出現在 ≥60% 的頁面，而且在頁面的前兩行或後兩行
    warnings: list[str] = []
    n = sum(1 for p in lines if any(l.strip() for l in p))
    if n >= 3:
        cnt: Counter = Counter()
        for p in lines:
            ne = [l for l in p if l.strip()]
            cnt.update({_norm_line(l) for l in ne[:2] + ne[-2:]})
        rep = {k for k, v in cnt.items() if v >= 0.6 * n}
        for i, p in enumerate(lines):
            ne_idx = [j for j, l in enumerate(p) if l.strip()]
            edge = set(ne_idx[:2] + ne_idx[-2:])
            lines[i] = [l for j, l in enumerate(p) if not (j in edge and _norm_line(l) in rep)]
    units = []
    empty = []
    for i, p in enumerate(lines):
        t = _join_lines(p)
        if t.strip():
            units.append(Unit(f"p.{i + 1}", t))
        else:
            empty.append(i + 1)
    if not units:
        raise ParseError(MSG_SCANNED)
    if empty:
        warnings.append("此頁無文字（可能是圖片或掃描頁）：" + "、".join(f"p.{x}" for x in empty[:20]) + ("…" if len(empty) > 20 else ""))
    return Parsed("pdf", units, warnings)


# ───────── Word ─────────

def _row_text(header: list[str], cells: list[str]) -> str:
    parts = []
    for i, v in enumerate(cells):
        v = v.strip()
        if not v:
            continue
        h = header[i].strip() if i < len(header) else ""
        parts.append(f"{h}：{v}" if h and h != v else v)
    return "；".join(parts)


def _table_lines(rows: list[list[str]]) -> list[str]:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    if len(rows) == 1:
        return ["；".join(c.strip() for c in rows[0] if c.strip())]
    return [t for t in (_row_text(rows[0], r) for r in rows[1:]) if t]


def parse_docx(data: bytes) -> Parsed:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ParseError("Word 檔案無法讀取：" + type(e).__name__)
    units: list[Unit] = []
    path: list[str] = []
    buf: list[str] = []

    def flush():
        if any(x.strip() for x in buf):
            units.append(Unit(" > ".join(path), "\n\n".join(x for x in buf if x.strip())))
        buf.clear()

    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            style = (p.style.name if p.style is not None else "") or ""
            m = re.match(r"(?:Heading|標題)\s*([1-3])$", style)
            text = p.text.strip()
            if m and text:
                flush()
                level = int(m.group(1))
                path[:] = path[: level - 1] + [text]
                continue
            if style == "Title" and text:
                flush()
                path[:] = [text]
                continue
            buf.append(text)
        elif tag == "tbl":
            t = Table(el, d)
            rows = []
            for row in t.rows:
                cells, last = [], None
                for c in row.cells:            # 合併儲存格會重複出現，去掉相鄰重複
                    if c._tc is last:
                        continue
                    last = c._tc
                    cells.append(c.text.replace("\n", " "))
                rows.append(cells)
            buf.append("\n".join(_table_lines(rows)))
    flush()
    if not units:
        raise ParseError("沒有讀到任何文字")
    return Parsed("docx", units)


# ───────── PowerPoint ─────────

def parse_pptx(data: bytes) -> Parsed:
    from pptx import Presentation
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as e:
        raise ParseError("PowerPoint 檔案無法讀取：" + type(e).__name__)
    units = []
    for i, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        parts: list[str] = []
        if title_shape is not None and title_shape.has_text_frame and title_shape.text_frame.text.strip():
            parts.append(title_shape.text_frame.text.strip())
        tid = title_shape.shape_id if title_shape is not None else None   # 每次取 shape 都是新物件，用 id 比
        shapes = sorted((s for s in slide.shapes if s.shape_id != tid), key=lambda s: ((s.top or 0), (s.left or 0)))
        for s in shapes:
            if s.has_text_frame:
                t = "\n".join(p.text for p in s.text_frame.paragraphs if p.text.strip())
                if t.strip():
                    parts.append(t.strip())
            elif getattr(s, "has_table", False) and s.has_table:
                rows = [[c.text.replace("\n", " ") for c in r.cells] for r in s.table.rows]
                parts.append("\n".join(_table_lines(rows)))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame is not None else ""
            if notes:
                parts.append("備忘稿：" + notes)
        text = "\n\n".join(p for p in parts if p)
        if text:
            units.append(Unit(f"第 {i} 張", text))
    if not units:
        raise ParseError("沒有讀到任何文字")
    return Parsed("pptx", units)


# ───────── Excel ─────────

def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "isoformat"):
        s = v.isoformat()
        return s[:10] if s.endswith("T00:00:00") else s.replace("T", " ")
    return str(v).strip()


def parse_xlsx(data: bytes) -> Parsed:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise ParseError("Excel 檔案無法讀取：" + type(e).__name__)
    units, warnings = [], []
    try:
        for ws in wb.worksheets:
            if ws.sheet_state != "visible":
                continue
            header: list[str] | None = None
            rows: list[tuple[int, str]] = []
            truncated = False
            for idx, row in enumerate(ws.iter_rows(values_only=True), 1):
                cells = [_cell(v) for v in row]
                if not any(cells):
                    continue
                if header is None:
                    header = cells
                    continue
                if len(rows) >= MAX_ROWS:
                    truncated = True
                    break
                t = _row_text(header, cells)
                if t:
                    rows.append((idx, t))
            if truncated:
                warnings.append(f"工作表「{ws.title}」超過 {MAX_ROWS} 列，只取前 {MAX_ROWS} 列")
            if rows:
                units.append(Unit("", "", rows=rows, sheet=ws.title))
            elif header:     # 只有一列：當成一般文字
                units.append(Unit(ws.title, "；".join(c for c in header if c)))
    finally:
        wb.close()
    if not units:
        raise ParseError("沒有讀到任何文字")
    return Parsed("xlsx", units, warnings)


# ───────── 純文字 ─────────

_MD_H = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def parse_text(text: str, markdown: bool = False) -> Parsed:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not markdown:
        if not text.strip():
            raise ParseError("沒有讀到任何文字")
        return Parsed("txt", [Unit("", text)])
    units, path, buf = [], [], []

    def flush():
        t = "\n".join(buf).strip()
        if t:
            units.append(Unit(" > ".join(path), t))
        buf.clear()

    for line in text.split("\n"):
        m = _MD_H.match(line)
        if m:
            flush()
            lv = len(m.group(1))
            path[:] = path[: lv - 1] + [m.group(2)]
        else:
            buf.append(line)
    flush()
    if not units:
        raise ParseError("沒有讀到任何文字")
    return Parsed("md", units)


def parse(filename: str, data: bytes) -> Parsed:
    kind = detect(filename, data)
    if kind == "pdf":
        return parse_pdf(data)
    if kind == "docx":
        return parse_docx(data)
    if kind == "pptx":
        return parse_pptx(data)
    if kind == "xlsx":
        return parse_xlsx(data)
    return parse_text(decode_text(data), markdown=kind == "md")


def parse_pasted(text: str) -> Parsed:
    if len(text) > MAX_PASTE:
        raise ParseError(f"貼上的文字超過 {MAX_PASTE:,} 字")
    return parse_text(text)
