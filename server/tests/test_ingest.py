"""文件匯入：段落類型與 Apps Script 比對；上傳、處理、修復、背景任務用真資料庫＋假模型。"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from db import models as m
from services import ingest as I
from services.llm import LLMError
from services.parsers import ParseError
from settings.store import save_bot_settings

HERE = Path(__file__).parent
TEXTS = [f"段落{i}：" + "老師可以先預告" * (i % 5 + 1) for i in range(30)]
RESPS = [
    [{"types": ["具體做法", "案例", "不存在", None, 3] + ["溝通範例"] * 20}, ["轉介資訊", "原則／背景"]],
    [["原則／背景"] * 25, {"其他": ["案例"]}],
    [{"__throw": "壞了"}, {"types": "不是陣列"}],
    [{"types": []}, {"x": 1}],
    [{"types": "", "其他": ["案例"]}, {"types": 0, "y": ["轉介資訊"]}],
]


def test_classify_same_as_apps_script():
    if not shutil.which("node"):
        pytest.skip("沒有 node")
    cases = [{"cfg": {"role_user": "老師"}, "texts": TEXTS, "resp": r} for r in RESPS]
    want = json.loads(subprocess.run(["node", str(HERE / "parity" / "run_classify_gas.js")], input=json.dumps(cases, ensure_ascii=False), capture_output=True, text=True, check=True).stdout)

    class L:
        def __init__(self, resp):
            self.resp, self.prompts = list(resp), []

        async def generate_json(self, model, prompt, **kw):
            self.prompts.append(prompt)
            r = self.resp[len(self.prompts) - 1]
            if isinstance(r, dict) and "__throw" in r:
                raise LLMError(r["__throw"])
            return r, None

    for r, w in zip(RESPS, want):
        llm = L(r)
        out, _ = asyncio.run(I.classify(llm, "m", "老師", TEXTS, batch=25))     # 原版一批 25 段；Python 預設 20
        assert out == w["out"] and llm.prompts == w["prompts"]
    # 原版回傳比段數多時會寫到下一批的位置；這裡只取這一批的長度
    llm = L([{"types": ["案例"] * 40}, {"types": []}])
    assert asyncio.run(I.classify(llm, "m", "老師", TEXTS, batch=25))[0] == ["案例"] * 25 + [""] * 5


# ── 資料庫 ──

class FakeLLM:
    def __init__(self, dim=768, fail_embed=0, bad_dim=False):
        self.dim, self.fail_embed, self.bad_dim = dim, fail_embed, bad_dim
        self.embedded: list[str] = []
        self.classify_calls = 0

    async def generate_json(self, model, prompt, **kw):
        self.classify_calls += 1
        n = prompt.count("【段落")
        return {"types": ["具體做法"] * n}, None

    async def embed(self, model, texts, *, dim, task="document"):
        assert task == "document"
        if self.fail_embed:
            self.fail_embed -= 1
            raise LLMError("429")
        if self.bad_dim:
            raise LLMError("向量維度不符")
        self.embedded += texts
        return [[1.0] + [0.0] * (dim - 1) for _ in texts]


@pytest.fixture
def kb(db):
    ws = m.Workspace(slug="w", name="w"); db.add(ws); db.flush()
    kb = m.KnowledgeBase(workspace_id=ws.id, slug="k", name="k", embed_model="gemini-embedding-001", embed_dim=768, chunk_size=200, chunk_overlap=20)
    db.add(kb); db.flush()
    return db, kb


def md(n=6):
    return "\n".join(f"# 第{i}章\n" + "\n\n".join(["孩子收拾時丟積木，老師先預告五分鐘。"] * 16) for i in range(n)).encode()


def run(coro):
    return asyncio.run(coro)


def test_upload_process_and_reprocess(kb, monkeypatch):
    db, k = kb
    monkeypatch.setattr(I, "embed_all", I.embed_all)
    doc = I.create_document(db, k, filename="手冊.md", data=md(), source_note="出處")
    assert doc.status == "processing" and doc.title == "手冊" and doc.mime == "text/markdown" and len(doc.sha256) == 64
    llm = FakeLLM()
    r = run(I.process_document(db, llm, doc.id))
    chunks = db.query(m.Chunk).filter_by(doc_id=doc.id).order_by(m.Chunk.seq).all()
    assert r.status == "pending" and doc.status == "pending" and doc.chunk_count == len(chunks) == r.chunks > 6
    assert [c.seq for c in chunks] == list(range(1, len(chunks) + 1)) and all(c.ctype == "具體做法" for c in chunks)
    assert chunks[0].page == "第0章" and llm.embedded[0].startswith("手冊｜第0章｜孩子") and not chunks[0].text.startswith("手冊")
    assert all(c.active is False for c in chunks)                       # 待審核不進檢索
    k.chunk_size = 2000; db.flush()                                     # 換參數重新處理：舊段落整批換掉
    r2 = run(I.process_document(db, FakeLLM(), doc.id))
    assert r2.chunks < r.chunks and db.query(m.Chunk).filter_by(doc_id=doc.id).count() == r2.chunks


def test_parsing_runs_off_event_loop(kb, monkeypatch):
    """解析與切段在執行緒裡跑：大檔案解析時，同一個程序裡的聊天請求不會被卡住"""
    import threading
    db, k = kb
    doc = I.create_document(db, k, filename="手冊.md", data=md())
    seen, orig = {}, I.parsers.parse

    def spy(name, data):
        seen["parse"] = threading.current_thread()
        return orig(name, data)
    monkeypatch.setattr(I.parsers, "parse", spy)

    async def go():
        seen["loop"] = threading.current_thread()
        return await I.process_document(db, FakeLLM(), doc.id)
    assert run(go()).status == "pending" and seen["parse"] is not seen["loop"]


def test_duplicates_and_bad_files(kb):
    db, k = kb
    d = I.create_document(db, k, filename="a.md", data=md())
    with pytest.raises(I.Duplicate) as e:
        I.create_document(db, k, filename="b.md", data=md())
    assert e.value.doc.id == d.id
    assert I.create_document(db, k, filename="b.md", data=md(), force=True).id != d.id
    with pytest.raises(ParseError):
        I.create_document(db, k, filename="a.exe", data=b"MZ")
    with pytest.raises(ParseError):
        I.create_document(db, k, text="   ")
    p = I.create_document(db, k, text="貼上的內容\n\n第二段", title="貼上")
    assert p.filename == "pasted.txt" and run(I.process_document(db, FakeLLM(), p.id)).status == "pending"


def test_parse_failure_marks_failed(kb):
    db, k = kb
    doc = I.create_document(db, k, filename="a.md", data=md())
    db.get(m.DocumentFile, doc.id).content = b"%PDF-1.4 broken"
    doc.filename = "a.pdf"; db.flush()
    r = run(I.process_document(db, FakeLLM(), doc.id))
    assert r.status == "failed" and doc.status == "failed" and "無法讀取" in doc.error and db.query(m.Chunk).filter_by(doc_id=doc.id).count() == 0


def test_embedding_retry_missing_and_repair(kb, monkeypatch):
    db, k = kb
    slept = []

    async def nosleep(s):
        slept.append(s)

    real = I.embed_all
    monkeypatch.setattr(I, "embed_all", lambda llm, model, dim, texts: real(llm, model, dim, texts, sleep=nosleep))
    doc = I.create_document(db, k, filename="a.md", data=md())
    r = run(I.process_document(db, FakeLLM(fail_embed=2), doc.id))          # 失敗兩次、第三次成功
    assert r.missing_vectors == 0 and slept == [1, 2]
    doc2 = I.create_document(db, k, filename="b.md", data=md(3) + b"x")
    r = run(I.process_document(db, FakeLLM(fail_embed=99), doc2.id))
    assert r.status == "pending" and r.missing_vectors == r.chunks and "缺向量" in doc2.error
    assert run(I.repair_vectors(db, FakeLLM(), k.id)) == r.chunks
    assert db.query(m.Chunk).filter(m.Chunk.kb_id == k.id, m.Chunk.embedding.is_(None)).count() == 0


def test_classify_uses_first_mounted_bot(kb):
    db, k = kb
    b = m.Bot(workspace_id=k.workspace_id, slug="b", name="b"); db.add(b); db.flush()
    save_bot_settings(db, b.id, {"role_user": "老師", "model_triage": "gemini-x"})
    db.add(m.BotKnowledge(bot_id=b.id, kb_id=k.id)); db.flush()
    assert I._bot_context(db, k.id) == ("gemini-x", "老師")


def test_background_job(kb, engine):
    db, k = kb
    # 背景任務用自己的 session：這裡用同一個連線的 savepoint 模擬，測完一起回滾
    doc = I.create_document(db, k, filename="a.md", data=md())
    job = m.Job(workspace_id=k.workspace_id, kb_id=k.id, kind="ingest", target=str(doc.id)); db.add(job); db.flush()
    factory = lambda: Session(bind=db.connection(), join_transaction_mode="create_savepoint")
    run(I.run_ingest_job(factory, FakeLLM(), job.id))
    db.expire_all()
    job = db.get(m.Job, job.id)
    assert job.status == "done" and job.progress == 1.0 and job.result["chunks"] > 0 and job.finished_at
    doc2 = I.create_document(db, k, filename="b.md", data=md(2) + b"y")
    job2 = m.Job(workspace_id=k.workspace_id, kb_id=k.id, kind="ingest", target=str(doc2.id)); db.add(job2); db.flush()
    run(I.run_ingest_job(factory, FakeLLM(bad_dim=True), job2.id))
    db.expire_all()
    assert db.get(m.Job, job2.id).status == "done" and db.get(m.Document, doc2.id).error   # 向量全失敗：文件仍進 pending、記缺向量
    doc3 = I.create_document(db, k, filename="c.md", data=md(2) + b"z")
    db.get(m.DocumentFile, doc3.id).content = b"%PDF-broken"; doc3.filename = "c.pdf"; db.flush()
    job3 = m.Job(workspace_id=k.workspace_id, kb_id=k.id, kind="ingest", target=str(doc3.id)); db.add(job3); db.flush()
    run(I.run_ingest_job(factory, FakeLLM(), job3.id))
    db.expire_all()
    assert db.get(m.Job, job3.id).status == "failed" and "無法讀取" in db.get(m.Job, job3.id).message


def test_auto_kg_tag(kb):
    db, k = kb
    k.auto_kg_tag = True
    db.add(m.KgNode(kb_id=k.id, node_key="n1", name="收拾", ntype="概念")); db.flush()

    class L(FakeLLM):
        async def generate_json(self, model, prompt, **kw):
            if "做標註" in prompt:
                return {"items": [{"chunk": 1, "main": ["n1"], "new": [{"name": "新概念"}]}]}, None
            return await super().generate_json(model, prompt, **kw)

    doc = I.create_document(db, k, filename="a.md", data=md())
    r = run(I.process_document(db, L(), doc.id))
    assert r.status == "pending" and "候選" in doc.error and db.query(m.KgLink).count() >= 1

    class Bad(FakeLLM):
        async def generate_json(self, model, prompt, **kw):
            if "做標註" in prompt:
                raise RuntimeError("x")
            return await super().generate_json(model, prompt, **kw)

    db.query(m.KgLink).delete(); db.flush()
    r = run(I.process_document(db, Bad(), doc.id))
    assert r.status == "pending" and db.query(m.KgLink).count() == 0       # 批次失敗只是沒標，不影響匯入
