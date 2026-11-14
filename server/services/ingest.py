"""文件匯入：上傳 → 解析 → 切段 → 段落類型 → 向量 → 寫入（docs/ingest.md）。

一份文件的段落在一個交易裡整批替換：要嘛全部在，要嘛一段都沒有。
分類失敗的段落留空（不受配額限制）；向量失敗的段落 embedding 為 NULL，文件照樣進 pending，之後可「修復」補算。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import BotKnowledge, Chunk, Document, DocumentFile, Job, KnowledgeBase
from services import parsers
from services.chunking import Piece, chunk_units
from services.llm import LLMClient, LLMError
from services.retrieval import CHUNK_TYPES
from settings.loader import ROLE_DEFAULTS, load_config

CLASSIFY_BATCH = 20
EMBED_BATCH = 100
EMBED_TRIES = 3
MSG_MODEL = "向量或分類服務暫時失敗，已處理的部分保留，可稍後按「修復」"


class Duplicate(Exception):
    def __init__(self, doc: Document):
        super().__init__("同一個知識庫已有相同的檔案")
        self.doc = doc


# ───────── 上傳 ─────────

def create_document(session: Session, kb: KnowledgeBase, *, filename: str = "", data: bytes | None = None, text: str | None = None,
                    title: str = "", source_note: str = "", evidence: str = "", user_id: uuid.UUID | None = None, force: bool = False) -> Document:
    """檢查格式、大小、重複後建立文件（status=processing）與原始檔；真正的處理在 process_document"""
    if data is None:
        raw = (text or "").encode("utf-8")
        if len(text or "") > parsers.MAX_PASTE:
            raise parsers.ParseError(f"貼上的文字超過 {parsers.MAX_PASTE:,} 字")
        if not (text or "").strip():
            raise parsers.ParseError("沒有讀到任何文字")
        filename, mime = filename or "pasted.txt", "text/plain"
    else:
        raw = data
        mime = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "txt": "text/plain", "md": "text/markdown"}[parsers.detect(filename, data)]
    sha = hashlib.sha256(raw).hexdigest()
    if not force:
        old = session.scalar(select(Document).where(Document.kb_id == kb.id, Document.sha256 == sha))
        if old is not None:
            raise Duplicate(old)
    doc = Document(kb_id=kb.id, title=(title or filename.rsplit(".", 1)[0]).strip()[:300], source_note=source_note, evidence=evidence, filename=filename,
                   mime=mime, size_bytes=len(raw), sha256=sha, status="processing", uploaded_by=user_id)
    session.add(doc)
    session.flush()
    session.add(DocumentFile(doc_id=doc.id, content=raw))
    session.flush()
    return doc


# ───────── 段落類型（對應 Knowledge.gs 的 classifyChunks_） ─────────

def classify_prompt(role_user: str, group: list[str]) -> str:
    return ("把每一段知識分成一種類型，只輸出 JSON，types 陣列順序與段落相同。類型：" + json.dumps(list(CHUNK_TYPES), ensure_ascii=False, separators=(",", ":")) +
            "。具體做法＝" + role_user + "可以直接照做的步驟或策略；案例＝具體情境的處理實例；溝通範例＝可直接使用的說法或訊息；原則／背景＝理論、原則、概念說明；轉介資訊＝找誰、流程、資源。\n"
            '格式：{"types":["具體做法","原則／背景",…]}\n\n' + "\n\n".join(f"【段落{j + 1}】\n" + str(t)[:700] for j, t in enumerate(group)))


def types_from(r) -> list:
    """模型回覆可能是陣列、{"types":[…]}，或任何含陣列的物件"""
    if isinstance(r, list):
        return r
    if isinstance(r, dict):
        if r.get("types"):
            return r["types"] if isinstance(r["types"], list) else []
        return next((v for v in r.values() if isinstance(v, list)), [])
    return []


async def classify(llm: LLMClient, model: str, role_user: str, texts: list[str], batch: int = CLASSIFY_BATCH) -> tuple[list[str], int]:
    """回傳（每段的類型，失敗的批數）"""
    out = [""] * len(texts)
    failed = 0
    for i in range(0, len(texts), batch):
        group = texts[i:i + batch]
        try:
            r, _ = await llm.generate_json(model, classify_prompt(role_user, group), temperature=0, no_think=True)
            for j, t in enumerate(types_from(r)[:len(group)]):
                if isinstance(t, str) and t in CHUNK_TYPES:
                    out[i + j] = t
        except Exception:   # 分類失敗不擋匯入
            failed += 1
    return out, failed


# ───────── 向量 ─────────

def embed_text(title: str, page: str, text: str) -> str:
    """讓短段落也有上下文；chunks.text 只存段落本身"""
    return "｜".join(x for x in (title, page) if x) + ("｜" if title or page else "") + text


async def embed_all(llm: LLMClient, model: str, dim: int, texts: list[str], sleep=asyncio.sleep) -> list[list[float] | None]:
    out: list[list[float] | None] = [None] * len(texts)
    for i in range(0, len(texts), EMBED_BATCH):
        group = texts[i:i + EMBED_BATCH]
        for attempt in range(EMBED_TRIES):
            try:
                vecs = await llm.embed(model, group, dim=dim, task="document")
                out[i:i + len(group)] = vecs
                break
            except LLMError:
                if attempt < EMBED_TRIES - 1:
                    await sleep(2 ** attempt)
    return out


# ───────── 處理 ─────────

@dataclass
class Result:
    status: str
    chunks: int = 0
    missing_vectors: int = 0
    unclassified_batches: int = 0
    error: str = ""


def _bot_context(session: Session, kb_id: uuid.UUID) -> tuple[str, str]:
    """分類用的模型與提問者稱呼：取掛載這個知識庫的第一個 Bot；沒有就用預設"""
    bot_id = session.scalar(select(BotKnowledge.bot_id).where(BotKnowledge.kb_id == kb_id).order_by(BotKnowledge.sort).limit(1))
    if bot_id is None:
        from settings.loader import build_config
        cfg = build_config({})
        return cfg.str("model_triage"), ROLE_DEFAULTS["user"]
    cfg = load_config(session, bot_id)
    return cfg.str("model_triage"), cfg.roles()["user"]


def _fail(session: Session, doc: Document, msg: str) -> Result:
    session.execute(delete(Chunk).where(Chunk.doc_id == doc.id))
    doc.status, doc.error, doc.chunk_count = "failed", msg[:500], 0
    session.flush()
    return Result("failed", error=doc.error)


async def process_document(session: Session, llm: LLMClient, doc_id: uuid.UUID, progress=None) -> Result:
    """解析原始檔並（重新）產生段落；呼叫端負責 commit"""
    doc = session.get(Document, doc_id)
    if doc is None:
        raise LookupError("找不到文件")
    kb = session.get(KnowledgeBase, doc.kb_id)
    f = session.get(DocumentFile, doc.id)
    step = progress or (lambda p, m: None)
    try:
        parsed = parsers.parse(doc.filename, f.content)
    except parsers.ParseError as e:
        return _fail(session, doc, str(e))
    pieces: list[Piece] = chunk_units(parsed.units, kb.chunk_size, kb.chunk_overlap)
    if not pieces:
        return _fail(session, doc, "沒有讀到任何文字")
    step(0.3, f"切成 {len(pieces)} 段")
    model, role_user = _bot_context(session, kb.id)
    types, bad_batches = await classify(llm, model, role_user, [p.text for p in pieces])
    step(0.6, "段落類型完成")
    try:
        vecs = await embed_all(llm, kb.embed_model, kb.embed_dim, [embed_text(doc.title, p.page, p.text) for p in pieces])
    except Exception as e:   # 設定錯誤（缺金鑰、維度不符）等：整份失敗
        return _fail(session, doc, MSG_MODEL + "（" + str(e)[:120] + "）")
    step(0.9, "向量完成")
    missing = sum(1 for v in vecs if v is None)
    session.execute(delete(Chunk).where(Chunk.doc_id == doc.id))
    session.flush()
    session.add_all([Chunk(kb_id=kb.id, doc_id=doc.id, seq=i + 1, text=p.text, page=p.page, ctype=types[i], embed_dim=kb.embed_dim, embedding=vecs[i])
                     for i, p in enumerate(pieces)])
    notes = list(parsed.warnings)
    if missing:
        notes.append(f"{missing} 段缺向量，可按「修復」補算")
    if bad_batches:
        notes.append("部分段落未分類，可在後台重做")
    doc.status, doc.chunk_count, doc.error = "pending", len(pieces), "；".join(notes)[:500]
    session.flush()
    return Result("pending", len(pieces), missing, bad_batches, doc.error)


async def repair_vectors(session: Session, llm: LLMClient, kb_id: uuid.UUID) -> int:
    """補算缺向量的段落；回傳補上的段數"""
    kb = session.get(KnowledgeBase, kb_id)
    rows = session.execute(select(Chunk, Document.title).join(Document, Document.id == Chunk.doc_id)
                           .where(Chunk.kb_id == kb_id, Chunk.embedding.is_(None)).order_by(Chunk.doc_id, Chunk.seq)).all()
    if not rows:
        return 0
    vecs = await embed_all(llm, kb.embed_model, kb.embed_dim, [embed_text(t, c.page, c.text) for c, t in rows])
    n = 0
    for (c, _), v in zip(rows, vecs):
        if v is not None:
            c.embedding = v
            n += 1
    session.flush()
    return n


# ───────── 背景任務 ─────────

async def run_ingest_job(session_factory, llm: LLMClient, job_id: uuid.UUID) -> None:
    """一個獨立 session 跑一份文件；進度與結果寫 jobs。例外一律收斂成 failed，不讓背景任務靜默消失"""
    with session_factory() as s:
        job = s.get(Job, job_id)
        job.status = "running"
        s.commit()

        def progress(p, m):
            job.progress, job.message = p, m
            s.commit()

        try:
            r = await process_document(s, llm, uuid.UUID(job.target), progress)
            job.status = "done" if r.status == "pending" else "failed"
            job.result = {"status": r.status, "chunks": r.chunks, "missingVectors": r.missing_vectors, "error": r.error}
            job.message = r.error or f"完成，{r.chunks} 段"
        except Exception as e:
            s.rollback()
            job = s.get(Job, job_id)
            doc = s.get(Document, uuid.UUID(job.target))
            if doc is not None:
                _fail(s, doc, "處理時發生錯誤，請重新處理")
            job.status, job.message = "failed", "處理時發生錯誤：" + type(e).__name__
        job.progress = 1.0
        job.finished_at = datetime.now(timezone.utc)
        s.commit()
