"""從 Apps Script 試算表搬過來：工作表與標題直接從 apps-script/Setup.gs 讀，確保跟真的試算表一致。"""
import asyncio
import io
import json
import re
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from db import models as m
from services import apps_script_import as I
from test_ingest import FakeLLM

SETUP = (Path(__file__).parents[2] / "apps-script" / "Setup.gs").read_text(encoding="utf-8")
HEADERS = {k: json.loads("[" + h.replace("'", '"') + "]") for k, h in re.findall(r"make\(SHEETS\.(\w+), \[([^\]]*)\]", SETUP)}


def test_sheet_names_and_headers_match_apps_script():
    store = (Path(__file__).parents[2] / "apps-script" / "Store.gs").read_text(encoding="utf-8")
    for k, v in I.SHEETS.items():
        assert re.search(rf"\b{k}: '{v}'", store), k
        assert k in HEADERS, k


def book(sheets: dict) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for key, rows in sheets.items():
        ws = wb.create_sheet(I.SHEETS[key])
        head = HEADERS[key]
        ws.append(head)
        for r in rows:
            assert set(r) <= set(head), (key, set(r) - set(head))
            ws.append([r.get(h) for h in head])
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


SHEETS = {
    "settings": [{"設定": "bot_name", "值": "舊助手"}, {"設定": "enable_triage", "值": True}, {"設定": "top_k", "值": 5}, {"設定": "min_score", "值": "abc"},
                 {"設定": "chunk_size", "值": 400}, {"設定": "model_embed", "值": "gemini-embedding-001"}, {"設定": "embed_dim", "值": 768}, {"設定": "老設定", "值": 1},
                 {"設定": "intents", "值": "新問題＝a\n補充同一問題＝b\n要求特定協助＝c\n與主題無關＝d"}],
    "skills": [{"Skill ID": "S01", "名稱": "理解行為", "對應主題": "情緒,行為", "一律載入": False, "內容": "v2", "啟用": True, "版本": 2}, {"Skill ID": "S02", "名稱": "", "內容": "x"}],
    "skillHist": [{"Skill ID": "S01", "版本": 1, "名稱": "理解行為", "對應主題": "情緒", "內容": "v1"}],
    "docs": [{"文件ID": "D1", "標題": "收拾手冊", "出處": "出版社", "狀態": "已核可", "上傳時間": datetime(2026, 1, 2, 3, 4)}, {"文件ID": "D2", "標題": "草稿", "狀態": "待審核"}],
    "chunks": [{"段落ID": "C1", "文件ID": "D1", "序號": 1, "內容": "五分鐘前預告", "頁碼": "3", "向量": "AAAA", "類型": "具體做法", "讀者對象": "老師、家長"},
               {"段落ID": "C2", "文件ID": "D1", "序號": 2, "內容": "用計時器", "類型": "具體做法"}, {"段落ID": "C3", "文件ID": "D2", "序號": 1, "內容": "草稿內容"},
               {"段落ID": "C9", "文件ID": "DX", "序號": 1, "內容": "孤兒"}],
    "synonyms": [{"使用者用語": "丟", "對應概念": "攻擊"}, {"使用者用語": "丟", "對應概念": "攻擊"}],
    "forbidden": [{"詞或規則": "過動", "類型": "字詞"}, {"詞或規則": "\\d{4}", "類型": "正規式"}],
    "referral": [{"類別": "兒少保護", "該找誰": "社工", "聯絡方式": "113", "現在要做的事": "通報"}],
    "codes": [{"邀請碼": "abc123", "單位代號": "甲園", "啟用": True, "觀測": True}, {"邀請碼": "OFF1", "啟用": False}],
    "logs": [{"時間": datetime(2026, 3, 1, 9, 0), "Session": "s1", "單位": "甲園", "輪次": 1, "使用者訊息": "孩子A丟積木", "分流": '{"intent":"新問題"}', "回應類型": "answer",
              "回答": '{"reply":"先預告"}', "引用": "[]", "是否重寫": "是", "耗時ms": 3000, "檢索段數": 2, "最高相似度": 0.8, "被引用段數": 1, "載入Skill": "理解行為",
              "流程紀錄": "[]", "穩定度": "壞掉的 JSON {"}],
    "feedback": [{"時間": datetime(2026, 3, 1, 9, 1), "Session": "s1", "輪次": 1, "類型": "有幫助"}],
    "kgDefs": [{"種類": "節點類型", "ID": "concept", "名稱": "概念", "定義": "名詞"}, {"種類": "關係", "ID": "hasStrategy", "名稱": "可用策略"}],
    "kgNodes": [{"節點ID": "n1", "名稱": "轉銜", "類型": "概念", "同義說法": "收拾時間、轉換", "狀態": "啟用", "來源": "知識圖譜匯入 v1"},
                {"節點ID": "n2", "名稱": "預告", "類型": "實務策略", "上層": "n1", "狀態": "停用", "來源": "文件提案"}],
    "kgEdges": [{"主詞": "n1", "關係": "hasStrategy", "受詞": "n2", "來源": "知識圖譜匯入 v1"}, {"主詞": "n1", "關係": "hasStrategy", "受詞": "zz"}],
    "kgSources": [{"來源ID": "S1", "標題": "手冊"}],
    "kgLinks": [{"段落ID": "C1", "節點ID": "n1", "標籤": "主要"}, {"段落ID": "C1", "節點ID": "n1", "標籤": "次要"}, {"段落ID": "C2", "節點ID": "n2", "標籤": "次要"}, {"段落ID": "CX", "節點ID": "n1"}],
    "kgCands": [{"候選ID": "x", "名稱": "等待", "類型": "概念", "文件ID": "D1", "段落ID": "C2;C1", "次數": 3, "狀態": "待審核"}],
    "evalQs": [{"題目ID": "Q1", "來源": "AI 出題", "類型": "一般", "題目": "收拾時丟積木", "應命中段落ID": "C1;CX", "應命中文件ID": "D1", "應包含重點": "預告", "期望回應": "answer", "狀態": "啟用"},
               {"題目ID": "Q2", "來源": "專家", "類型": "超出範圍", "題目": "天氣", "狀態": "停用", "專家確認": "是"}],
}


@pytest.fixture
def ws(db):
    w = m.Workspace(slug="main", name="主要"); db.add(w); db.flush()
    return db, w


def run(db, data, **kw):
    return asyncio.run(I.import_workbook(db, FakeLLM(), data, workspace_slug="main", bot_slug="old", progress=lambda *a: None, **kw))


def test_full_import(ws):
    db, w = ws
    rep = run(db, book(SHEETS), with_logs=True)
    assert (rep["settings"], rep["skills"], rep["synonyms"], rep["forbidden"], rep["referral"], rep["invite_codes"]) == (4, 1, 1, 2, 1, 2)
    assert (rep["documents"], rep["chunks"], rep["missing_vectors"], rep["eval_questions"], rep["logs"], rep["feedback"]) == (2, 3, 0, 2, 1, 1)
    assert rep["kg"] == {"defs": 2, "nodes": 2, "edges": 1, "sources": 1, "links": 2, "candidates": 1}
    assert any("min_score" in x for x in rep["skipped"]) and any("老設定" in x for x in rep["skipped"]) and any("1 個段落" in x for x in rep["skipped"])
    bot = db.query(m.Bot).filter_by(slug="old").one()
    kb = db.query(m.KnowledgeBase).filter_by(slug="old").one()
    assert bot.name == "舊助手" and kb.chunk_size == 400 and db.query(m.Flow).filter_by(bot_id=bot.id, is_default=True).count() == 1
    vals = {r.key: r.value for r in db.query(m.BotSetting).filter_by(bot_id=bot.id)}
    assert vals["enable_triage"] == "TRUE" and vals["top_k"] == "5" and "chunk_size" not in vals and vals["intents"].startswith("新問題＝a")
    sk = db.query(m.Skill).filter_by(bot_id=bot.id).one()
    assert sk.topics == ["情緒", "行為"] and sk.version == 2 and [v.content for v in db.query(m.SkillVersion).filter_by(skill_id=sk.id).order_by(m.SkillVersion.version)] == ["v1", "v2"]
    assert {c.code: (c.unit, c.enabled, c.trace) for c in db.query(m.InviteCode)} == {"ABC123": ("甲園", True, True), "OFF1": ("", False, False)}
    assert db.query(m.ForbiddenRule).filter_by(rule="\\d{4}").one().kind == "regex"
    d1 = db.query(m.Document).filter_by(kb_id=kb.id, title="收拾手冊").one()
    assert d1.status == "approved" and d1.source_note == "出版社" and d1.uploaded_at.year == 2026
    c1 = db.query(m.Chunk).filter_by(doc_id=d1.id, seq=1).one()
    assert c1.active and c1.embedding is not None and c1.audience == ["老師", "家長"] and c1.page == "3"
    assert not db.query(m.Chunk).join(m.Document).filter(m.Document.title == "草稿").one().active      # 待審核不進檢索
    n1, n2 = (db.query(m.KgNode).filter_by(kb_id=kb.id, node_key=k).one() for k in ("n1", "n2"))
    assert n1.aliases == ["收拾時間", "轉換"] and n1.origin == "import" and n2.status == "disabled" and n2.origin == "candidate" and n2.parent_id == n1.id
    assert db.query(m.KgLink).filter_by(chunk_id=c1.id, node_id=n1.id).one().tag == "primary"
    cand = db.query(m.KgCandidate).filter_by(kb_id=kb.id).one()
    assert cand.hits == 3 and cand.chunk_id is not None and cand.status == "pending"
    q1 = db.query(m.EvalQuestion).filter_by(bot_id=bot.id, question="收拾時丟積木").one()
    assert q1.source == "ai" and q1.expect_chunks == [c1.id] and q1.expect_docs == [d1.id]
    q2 = db.query(m.EvalQuestion).filter_by(bot_id=bot.id, question="天氣").one()
    assert q2.status == "retired" and q2.expert_ok and q2.expect_rtype == "out_of_scope"
    conv = db.query(m.Conversation).filter_by(bot_id=bot.id).one()
    assert conv.rewritten and conv.answer == {"reply": "先預告"} and conv.stability is None and conv.created_at.month == 3 and conv.skills == ["理解行為"]
    assert db.query(m.BotKnowledge).filter_by(bot_id=bot.id, kb_id=kb.id).count() == 1


def test_without_logs_and_no_embed(ws):
    db, w = ws
    rep = run(db, book(SHEETS), embed=False)
    assert rep["logs"] == 0 and rep["missing_vectors"] == 3
    assert db.query(m.Conversation).count() == 0 and db.query(m.Chunk).filter(m.Chunk.embedding.is_(None)).count() == 3


def test_errors(ws):
    db, w = ws
    with pytest.raises(I.ImportError_, match="xlsx"):
        run(db, b"not a zip")
    with pytest.raises(I.ImportError_, match="設定"):
        run(db, book({"skills": []}))
    run(db, book({"settings": [], "docs": []}))
    with pytest.raises(I.ImportError_, match="已被使用"):
        run(db, book({"settings": [], "docs": []}))
    with pytest.raises(I.ImportError_, match="工作區"):
        asyncio.run(I.import_workbook(db, FakeLLM(), book({"settings": [], "docs": []}), workspace_slug="nope", bot_slug="x", progress=lambda *a: None))


def test_old_headers_and_bad_embed_model(ws):
    """舊版標題（園所、老師用語）照樣讀；不支援的向量模型改用預設"""
    db, w = ws
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    s1 = wb.create_sheet("設定"); s1.append(["設定", "值", "說明"]); s1.append(["model_embed", "某種舊模型", ""]); s1.append(["embed_dim", 1536, ""])
    s2 = wb.create_sheet("文件"); s2.append(HEADERS["docs"])
    s3 = wb.create_sheet("同義詞"); s3.append(["老師用語", "對應概念"]); s3.append(["推", "攻擊"])
    buf = io.BytesIO(); wb.save(buf)
    rep = run(db, buf.getvalue())
    assert rep["synonyms"] == 1 and any("不支援" in x for x in rep["skipped"]) and db.query(m.KnowledgeBase).filter_by(slug="old").one().embed_dim == 768
