"""docs/data-model.md 的 CREATE TABLE 與 db/models.py 必須一致（表名與欄位名）。不需要資料庫。"""
import os
import re

from db import models as m

DOC = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "data-model.md")
SKIP = {"PRIMARY", "UNIQUE", "CHECK", "CONSTRAINT", "FOREIGN"}


def doc_tables() -> dict[str, set[str]]:
    sql = "\n".join(re.findall(r"```sql\n(.*?)```", open(DOC, encoding="utf-8").read(), re.S))
    out = {}
    for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, re.S):
        cols = set()
        for line in body.splitlines():
            line = re.sub(r"--.*", "", line).strip()
            tok = line.split()[0] if line else ""
            if tok and tok.upper() not in SKIP and re.fullmatch(r"[a-z_][a-z0-9_]*", tok):
                cols.add(tok)
        out[name] = cols
    return out


def test_tables_match():
    doc, code = doc_tables(), {t.name: {c.name for c in t.columns} for t in m.Base.metadata.sorted_tables}
    assert set(doc) == set(code), f"文件多：{set(doc) - set(code)}；程式多：{set(code) - set(doc)}"
    diffs = {t: (doc[t] - code[t], code[t] - doc[t]) for t in code if doc[t] != code[t]}
    assert not diffs, "欄位不一致（文件多, 程式多）：" + repr(diffs)
