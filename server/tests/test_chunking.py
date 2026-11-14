"""切段：與 Apps Script chunkText_ 比對，以及結構邊界。"""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from services.chunking import Unit, chunk_text, chunk_units, merge_loc

HERE = Path(__file__).parent
R = random.Random(1)
WORDS = "孩子在收拾時間常常把積木丟出去老師可以先預告五分鐘並用計時器讓他知道接下來要做什麼"


def para(n):
    return "".join(R.choice(WORDS) for _ in range(n))


TEXTS = [
    "",
    "短短一段",
    "\n\n".join(para(R.randint(20, 300)) for _ in range(12)),
    "[[第1頁]]\n" + para(120) + "\n\n" + para(400) + "\n[[第2頁]]\n" + para(90) + "\n\n[[第3頁]]\n\n" + para(1200) + "\r\n\r\n" + para(10),
    para(3000),
    "a\n  \nb\n\n\n\nc" + "\n\n".join(para(499) for _ in range(3)),
    "[[第7頁]]",
]
CASES = [{"text": t, "size": s, "overlap": o} for t in TEXTS for s, o in [(500, 80), (200, 30), (120, 10), (1000, 200)]]   # JS 的 overlap＝0 會被當成未填（80），不比


def test_same_as_apps_script():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    want = json.loads(subprocess.run(["node", str(HERE / "parity" / "run_chunk_gas.js")], input=json.dumps(CASES, ensure_ascii=False), capture_output=True, text=True, check=True).stdout)
    for c, w in zip(CASES, want):
        got = [{"text": p.text, "page": p.page} for p in chunk_text(c["text"], c["size"], c["overlap"])]
        assert got == w, (c["size"], c["overlap"], c["text"][:30])


def test_merge_loc():
    assert merge_loc("p.3", "p.4") == "p.3–4"
    assert merge_loc("第 5 張", "第 6 張") == "第 5–6 張"
    assert merge_loc("第一章", "第二章 > 2.1") == "第一章–第二章 > 2.1"
    assert merge_loc("p.3", "p.3") == "p.3" and merge_loc("", "p.2") == "p.2"


def test_units_do_not_cross_and_short_ones_merge():
    units = [Unit("p.1", para(900)), Unit("p.2", "很短"), Unit("p.3", "也短"), Unit("p.4", para(300)), Unit("p.5", "  ")]
    out = chunk_units(units, 500, 80)
    assert [p.page for p in out] == ["p.1", "p.1", "p.2–4"]
    assert out[2].text.startswith("很短\n也短\n")
    assert all(len(p.text) <= 500 * 1.6 for p in out)


def test_excel_rows_never_split():
    rows = [(i, f"名稱：項目{i}；說明：" + para(70)) for i in range(2, 30)]
    out = chunk_units([Unit("", "", rows=rows, sheet="工作表1")], 300, 80)
    assert out[0].page.startswith("工作表1 第 2–")
    back = [line for p in out for line in p.text.split("\n")]
    assert back == [r[1] for r in rows]                 # 每列完整、不重疊
    big = chunk_units([Unit("", "", rows=[(5, "x" * 2000)], sheet="S")], 300, 80)
    assert big[0].page == "S 第 5 列" and len(big[0].text) == 2000


def test_zero_overlap_means_none():
    out = chunk_text("\n\n".join(["甲" * 300, "乙" * 300, "丙" * 300]), 500, 0)
    assert [p.text for p in out] == ["甲" * 300, "乙" * 300, "丙" * 300]
