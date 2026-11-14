"""文件解析：測試檔在測試裡現做（PDF 用 reportlab＋系統中文字型，其餘用各自的套件）。"""
import io

import pytest

from services import parsers as P
from services.chunking import chunk_units



def make_pdf(pages, header="園所內部手冊", footer=True, password=None):
    rl = pytest.importorskip("reportlab")
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))   # reportlab 內建的繁中字型，不需要字型檔
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, lines in enumerate(pages, 1):
        c.setFont("MSung-Light", 11)
        if lines is not None:
            if header:
                c.drawString(72, 800, header)
            y = 760
            for ln in lines:
                c.drawString(72, y, ln); y -= 16
            if footer:
                c.drawString(280, 40, f"- {i} -")
        c.showPage()
    c.save()
    data = buf.getvalue()
    if password:
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
        w.encrypt(password)
        out = io.BytesIO(); w.write(out); data = out.getvalue()
    return data


def test_pdf_pages_headers_and_line_joins():
    data = make_pdf([
        ["第一章 收拾時間", "孩子在收拾時間常常把積木丟出去，老師可以先", "預告五分鐘，並且用計時器。", "1. 先預告", "2. 再提醒"],
        ["第二頁的內容很重要。"],
        None,                                    # 空白頁
        ["English words are hyphen-", "ated across lines and joined", "with spaces."],
    ])
    r = P.parse("a.pdf", data)
    assert r.kind == "pdf" and [u.loc for u in r.units] == ["p.1", "p.2", "p.4"]
    t1 = r.units[0].text
    assert "園所內部手冊" not in t1 and "- 1 -" not in t1                      # 頁首頁尾去掉
    assert "老師可以先預告五分鐘，並且用計時器。" in t1                          # 中文斷行接回
    assert "\n\n1. 先預告\n\n2. 再提醒" in t1                                  # 條列各自成段
    assert "hyphenated across lines and joined with spaces." in r.units[2].text
    assert r.warnings and "p.3" in r.warnings[0]


def test_pdf_scanned_and_encrypted():
    with pytest.raises(P.ParseError, match="掃描檔"):
        P.parse("s.pdf", make_pdf([None, None]))
    with pytest.raises(P.ParseError, match="密碼"):
        P.parse("e.pdf", make_pdf([["秘密內容"]], password="pw"))
    with pytest.raises(P.ParseError, match="無法讀取"):
        P.parse("b.pdf", b"%PDF-1.4 garbage")


def make_docx():
    import docx
    d = docx.Document()
    d.add_paragraph("前言文字。")
    d.add_heading("第二章 轉銜", level=1)
    d.add_paragraph("轉銜時段的做法。")
    d.add_heading("2.1 預告", level=2)
    d.add_paragraph("先說再做。")
    t = d.add_table(rows=4, cols=2)
    for i, (a, b) in enumerate([("情境", "做法"), ("收拾", "計時器"), ("排隊", "")]):
        t.cell(i, 0).text, t.cell(i, 1).text = a, b
    t.cell(3, 0).merge(t.cell(3, 1)).text = "合併說明"        # 合併儲存格只算一次
    d.add_heading("第三章 溝通", level=1)
    d.add_paragraph("和家長說明。")
    buf = io.BytesIO(); d.save(buf)
    return buf.getvalue()


def test_docx_heading_paths_and_tables():
    r = P.parse("a.docx", make_docx())
    assert [(u.loc, u.text) for u in r.units] == [
        ("", "前言文字。"),
        ("第二章 轉銜", "轉銜時段的做法。"),
        ("第二章 轉銜 > 2.1 預告", "先說再做。\n\n情境：收拾；做法：計時器\n情境：排隊\n情境：合併說明"),
        ("第三章 溝通", "和家長說明。"),
    ]


def test_pptx_slides_notes_and_tables():
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "收拾時間"
    s.placeholders[1].text = "先預告"
    s.notes_slide.notes_text_frame.text = "這張要強調計時器"
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "對照表"
    tb = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    tb.cell(0, 0).text, tb.cell(0, 1).text, tb.cell(1, 0).text, tb.cell(1, 1).text = "情境", "做法", "排隊", "唱歌"
    prs.slides.add_slide(prs.slide_layouts[6])            # 空白投影片
    buf = io.BytesIO(); prs.save(buf)
    r = P.parse("a.pptx", buf.getvalue())
    assert [u.loc for u in r.units] == ["第 1 張", "第 2 張"]
    assert r.units[0].text == "收拾時間\n\n先預告\n\n備忘稿：這張要強調計時器"
    assert r.units[1].text == "對照表\n\n情境：排隊；做法：唱歌"
    assert [p.page for p in chunk_units(r.units)] == ["第 1–2 張"]          # 兩張都很短→合併


def test_xlsx_rows_hidden_sheets_and_limit(monkeypatch):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "資源"
    ws.append([]); ws.append(["名稱", "電話", "備註"])
    ws.append(["早療中心", 1234, None]); ws.append([None, None, None]); ws.append(["社福", "5678", "週一"])
    h = wb.create_sheet("隱藏"); h.append(["x"]); h.append(["y"]); h.sheet_state = "hidden"
    big = wb.create_sheet("大")
    big.append(["編號"])
    for i in range(12):
        big.append([i])
    buf = io.BytesIO(); wb.save(buf)
    monkeypatch.setattr(P, "MAX_ROWS", 10)
    r = P.parse("a.xlsx", buf.getvalue())
    assert [u.sheet for u in r.units] == ["資源", "大"]
    assert r.units[0].rows == [(3, "名稱：早療中心；電話：1234"), (5, "名稱：社福；電話：5678；備註：週一")]
    assert len(r.units[1].rows) == 10 and "超過 10 列" in r.warnings[0]
    assert chunk_units(r.units[:1])[0].page == "資源 第 3–5 列"


def test_text_markdown_and_big5():
    r = P.parse("a.md", "# 標題一\n內容一\n## 小節\n內容二\n# 標題二\n內容三".encode())
    assert [(u.loc, u.text) for u in r.units] == [("標題一", "內容一"), ("標題一 > 小節", "內容二"), ("標題二", "內容三")]
    big5 = ("老師可以先預告五分鐘，並且用計時器讓孩子知道接下來要做什麼。" * 3).encode("big5")
    assert "計時器" in P.parse("b.txt", big5).units[0].text
    assert P.parse("c.txt", "﻿有 BOM".encode("utf-8")).units[0].text == "有 BOM"
    with pytest.raises(P.ParseError, match="超過"):
        P.parse_pasted("字" * (P.MAX_PASTE + 1))


def test_detect_rejects():
    cases = [("a.doc", b"\xd0\xcf\x11\xe0rest", "另存成 .docx"), ("a.docx", b"\xd0\xcf\x11\xe0rest", "密碼"), ("a.exe", b"MZ..", "不支援"),
             ("a.txt", b"ab\x00cd", "不支援"), ("a.docx", make_docx()[:0] + b"PK\x03\x04broken", "不支援"), ("x.pdf.docx", b"%PDF-1.4", None)]
    for name, data, msg in cases:
        if msg is None:
            assert P.detect(name, data) == "pdf"        # 以檔頭為準
            continue
        with pytest.raises(P.ParseError, match=msg):
            P.detect(name, data)
    with pytest.raises(P.ParseError, match="不支援"):
        P.detect("renamed.xlsx", make_docx())          # Word 檔改副檔名
    with pytest.raises(P.ParseError, match="50 MB"):
        P.detect("a.pdf", b"%PDF-" + b"0" * P.MAX_BYTES)
