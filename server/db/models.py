"""資料表定義。規格與理由見 docs/data-model.md；兩邊不一致時以這裡為準並回頭修文件。

向量欄位 chunks.embedding 不定維度，HNSW 索引是「依維度的部分運算式索引」（WHERE embed_dim = N AND active），
只在 Alembic 遷移裡建立（SQLAlchemy 無法比對運算式索引，env.py 的 include_object 會略過 chunks_emb_*）。
chunks.active 由觸發器跟著 documents.status 同步，也只在遷移裡定義。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Computed, DateTime, ForeignKey, Identity, Index, Integer,
    REAL, LargeBinary, MetaData, PrimaryKeyConstraint, SmallInteger, Text, UniqueConstraint,
)
from sqlalchemy import func
from sqlalchemy import text as sql   # 不直接用 text：chunks 有個欄位叫 text，類別內會被遮蔽
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NOW = sql("now()")
UUID_PK = dict(primary_key=True, server_default=sql("gen_random_uuid()"))


def uid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), **UUID_PK)


def fk(target: str, ondelete: str, nullable: bool = False) -> Mapped:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target, ondelete=ondelete), nullable=nullable)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW)


def s(default: str = "") -> Mapped[str]:
    """非空字串欄位，預設空字串（與試算表一致：沒有值＝空字串，不用 NULL）"""
    return mapped_column(Text, nullable=False, server_default=sql(f"'{default}'"))


def status_check(col: str, values: tuple[str, ...]) -> CheckConstraint:
    return CheckConstraint(f"{col} IN ({', '.join(repr(v) for v in values)})", name=col)


# 固定的約束命名規則：遷移產生的名稱可預期，之後改約束才找得到
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


# ───────────────────────── 1. 租戶與帳號 ─────────────────────────

class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = uid()
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uid()
    login: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = s()
    pw_hash: Mapped[str] = mapped_column(Text, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    created_at: Mapped[datetime] = created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", "bot_id", postgresql_nulls_not_distinct=True),
        status_check("role", ("owner", "admin", "reviewer", "analyst")),
        # owner 只能掛在 Workspace 層
        CheckConstraint("role <> 'owner' OR bot_id IS NULL", name="owner_workspace_level"),
        Index("ix_memberships_ws_user", "workspace_id", "user_id"),
    )
    id: Mapped[uuid.UUID] = uid()
    user_id: Mapped[uuid.UUID] = fk("users.id", "CASCADE")
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id", "CASCADE")
    bot_id: Mapped[uuid.UUID | None] = fk("bots.id", "CASCADE", nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_ws_at", "workspace_id", sql("at DESC")),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id", "CASCADE")
    bot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))   # 不設 FK：Bot 刪了紀錄還在
    kb_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = s()
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sql("'{}'::jsonb"))
    at: Mapped[datetime] = created()


# ───────────────────────── 2. 機器人與流程 ─────────────────────────

class Bot(Base):
    __tablename__ = "bots"
    id: Mapped[uuid.UUID] = uid()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id", "CASCADE")
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)   # 全站唯一，聊天頁 ?bot=<slug>
    name: Mapped[str] = mapped_column(Text, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    created_at: Mapped[datetime] = created()


class Flow(Base):
    __tablename__ = "flows"
    __table_args__ = (
        status_check("kind", ("chat", "collect")),
        Index("uq_flows_default", "bot_id", unique=True, postgresql_where=sql("is_default")),
    )
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    kind: Mapped[str] = s("chat")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    created_at: Mapped[datetime] = created()


class BotSetting(Base):
    __tablename__ = "bot_settings"
    bot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = s()
    updated_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    updated_at: Mapped[datetime] = created()


class FlowSetting(Base):
    __tablename__ = "flow_settings"
    flow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("flows.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    updated_at: Mapped[datetime] = created()


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("bot_id", "skill_key"), UniqueConstraint("bot_id", "name"))
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    skill_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sql("'{}'"))
    always_on: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    content: Mapped[str] = s()
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("1"))
    updated_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    updated_at: Mapped[datetime] = created()


class SkillVersion(Base):
    __tablename__ = "skill_versions"
    skill_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    always_on: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Synonym(Base):
    __tablename__ = "synonyms"
    __table_args__ = (UniqueConstraint("bot_id", "word", "concept"),)
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    word: Mapped[str] = mapped_column(Text, nullable=False)
    concept: Mapped[str] = mapped_column(Text, nullable=False)


class ForbiddenRule(Base):
    __tablename__ = "forbidden_rules"
    __table_args__ = (UniqueConstraint("bot_id", "rule"), status_check("kind", ("word", "regex")))
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    rule: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = s("word")
    note: Mapped[str] = s()


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (UniqueConstraint("bot_id", "category"),)
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    category: Mapped[str] = mapped_column(Text, nullable=False)
    who: Mapped[str] = s()
    contact: Mapped[str] = s()
    now_action: Mapped[str] = s()
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))


class InviteCode(Base):
    __tablename__ = "invite_codes"
    __table_args__ = (UniqueConstraint("bot_id", "code"),)
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    code: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = s()
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("true"))
    trace: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


# ───────────────────────── 3. 知識庫 ─────────────────────────

class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"), status_check("visibility", ("workspace", "restricted")))
    id: Mapped[uuid.UUID] = uid()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id", "CASCADE")
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = s()
    visibility: Mapped[str] = s("workspace")
    embed_model: Mapped[str] = mapped_column(Text, nullable=False)
    embed_dim: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("500"))
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("80"))
    auto_kg_tag: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    created_at: Mapped[datetime] = created()


class KbAllowedBot(Base):
    __tablename__ = "kb_allowed_bots"
    kb_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)
    bot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True)


class BotKnowledge(Base):
    """掛載。kb_id 是 RESTRICT：知識庫被掛著就不能刪"""
    __tablename__ = "bot_knowledge"
    bot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True)
    kb_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="RESTRICT"), primary_key=True)
    label: Mapped[str] = s()
    min_hits: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql("0"))
    weight: Mapped[float] = mapped_column(REAL, nullable=False, server_default=sql("1.0"))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("true"))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))


DOC_STATUS = ("processing", "pending", "approved", "disabled", "failed")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (status_check("status", DOC_STATUS), Index("ix_documents_kb_status", "kb_id", "status"))
    id: Mapped[uuid.UUID] = uid()
    kb_id: Mapped[uuid.UUID] = fk("knowledge_bases.id", "CASCADE")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_note: Mapped[str] = s()
    evidence: Mapped[str] = s()
    filename: Mapped[str] = s()
    mime: Mapped[str] = s()
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    sha256: Mapped[str] = s()
    status: Mapped[str] = s("processing")
    error: Mapped[str] = s()
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    uploaded_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    uploaded_at: Mapped[datetime] = created()
    reviewed_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentFile(Base):
    __tablename__ = "document_files"
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)   # 上限 50 MB（應用層檢查）


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("doc_id", "seq"),
        Index("ix_chunks_kb", "kb_id"),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )
    id: Mapped[uuid.UUID] = uid()
    kb_id: Mapped[uuid.UUID] = fk("knowledge_bases.id", "CASCADE")
    doc_id: Mapped[uuid.UUID] = fk("documents.id", "CASCADE")
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[str] = s()
    ctype: Mapped[str] = s()
    audience: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sql("'{}'"))
    age_range: Mapped[str] = s()
    context: Mapped[str] = s()
    # 文件核可狀態的冗餘：向量查詢不用 join documents 才能走 HNSW 索引。由資料庫觸發器維護（見遷移），程式不要直接寫
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    embed_dim: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, Computed("to_tsvector('simple'::regconfig, text)", persisted=True))


# ───────────────────────── 4. 知識圖譜 ─────────────────────────

class KgDef(Base):
    __tablename__ = "kg_defs"
    __table_args__ = (PrimaryKeyConstraint("kb_id", "kind", "def_key"), status_check("kind", ("type", "relation", "audience")))
    kb_id: Mapped[uuid.UUID] = fk("knowledge_bases.id", "CASCADE")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    def_key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str] = s()


class KgSource(Base):
    __tablename__ = "kg_sources"
    __table_args__ = (PrimaryKeyConstraint("kb_id", "source_key"),)
    kb_id: Mapped[uuid.UUID] = fk("knowledge_bases.id", "CASCADE")
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = s()
    url: Mapped[str] = s()
    scope: Mapped[str] = s()


class KgNode(Base):
    __tablename__ = "kg_nodes"
    __table_args__ = (
        UniqueConstraint("kb_id", "node_key"),
        status_check("status", ("active", "disabled")),
        Index("ix_kg_nodes_kb_status", "kb_id", "status"),
    )
    id: Mapped[uuid.UUID] = uid()
    kb_id: Mapped[uuid.UUID] = fk("knowledge_bases.id", "CASCADE")
    node_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ntype: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = fk("kg_nodes.id", "SET NULL", nullable=True)
    path: Mapped[str] = s()
    definition: Mapped[str] = s()
    tips: Mapped[str] = s()
    source_key: Mapped[str] = s()
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sql("'{}'"))
    status: Mapped[str] = s("active")
    origin: Mapped[str] = s("import")
    created_at: Mapped[datetime] = created()


class KgEdge(Base):
    __tablename__ = "kg_edges"
    __table_args__ = (PrimaryKeyConstraint("subj_id", "rel", "obj_id"), Index("ix_kg_edges_kb_obj", "kb_id", "obj_id"))
    kb_id: Mapped[uuid.UUID] = fk("knowledge_bases.id", "CASCADE")
    subj_id: Mapped[uuid.UUID] = fk("kg_nodes.id", "CASCADE")
    rel: Mapped[str] = mapped_column(Text, nullable=False)
    obj_id: Mapped[uuid.UUID] = fk("kg_nodes.id", "CASCADE")
    note: Mapped[str] = s()
    origin: Mapped[str] = s("import")


class KgLink(Base):
    __tablename__ = "kg_links"
    __table_args__ = (PrimaryKeyConstraint("chunk_id", "node_id"), status_check("tag", ("primary", "secondary")), Index("ix_kg_links_node", "node_id"))
    chunk_id: Mapped[uuid.UUID] = fk("chunks.id", "CASCADE")
    node_id: Mapped[uuid.UUID] = fk("kg_nodes.id", "CASCADE")
    tag: Mapped[str] = s("primary")
    method: Mapped[str] = s("ai")
    created_at: Mapped[datetime] = created()


class KgCandidate(Base):
    __tablename__ = "kg_candidates"
    __table_args__ = (status_check("status", ("pending", "accepted", "merged", "rejected")), Index("ix_kg_cands_kb_status", "kb_id", "status"))
    id: Mapped[uuid.UUID] = uid()
    kb_id: Mapped[uuid.UUID] = fk("knowledge_bases.id", "CASCADE")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str] = s()
    ntype: Mapped[str] = s()
    parent_hint: Mapped[str] = s()
    rel_hint: Mapped[str] = s()
    doc_id: Mapped[uuid.UUID | None] = fk("documents.id", "SET NULL", nullable=True)
    chunk_id: Mapped[uuid.UUID | None] = fk("chunks.id", "SET NULL", nullable=True)
    hits: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("1"))
    reason: Mapped[str] = s()
    status: Mapped[str] = s("pending")
    result: Mapped[str] = s()
    reviewed_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ───────────────────────── 5. 對話紀錄與用量 ─────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("bot_id", "session", "turn"),
        Index("ix_conv_bot_at_id", "bot_id", sql("created_at DESC"), sql("id DESC")),     # 紀錄列表從上一頁最後一筆往下讀
        Index("ix_conv_bot_type_at", "bot_id", "rtype", sql("created_at DESC")),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    flow_id: Mapped[uuid.UUID | None] = fk("flows.id", "SET NULL", nullable=True)
    session: Mapped[str] = mapped_column(Text, nullable=False)
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    invite_code_id: Mapped[uuid.UUID | None] = fk("invite_codes.id", "SET NULL", nullable=True)
    unit: Mapped[str] = s()
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    rtype: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = s()
    triage: Mapped[dict | None] = mapped_column(JSONB)
    referral: Mapped[dict | None] = mapped_column(JSONB)
    answer: Mapped[dict | None] = mapped_column(JSONB)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sql("'[]'::jsonb"))
    checks: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=sql("'[]'::jsonb"))
    rewritten: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    skills: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sql("'{}'"))
    hits: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql("0"))
    top_score: Mapped[float] = mapped_column(REAL, nullable=False, server_default=sql("0"))
    cited: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql("0"))
    ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    tokens: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sql("'{}'::jsonb"))
    trace: Mapped[list | None] = mapped_column(JSONB)
    stability: Mapped[dict | None] = mapped_column(JSONB)
    quality: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created()


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (Index("ix_feedback_bot_at", "bot_id", sql("created_at DESC")), Index("ix_feedback_bot_turn", "bot_id", "session", "turn"))
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    session: Mapped[str] = mapped_column(Text, nullable=False)
    turn: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = s()
    created_at: Mapped[datetime] = created()


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    bucket: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ───────────────────────── 6. 評測 ─────────────────────────

class EvalQuestion(Base):
    __tablename__ = "eval_questions"
    __table_args__ = (
        status_check("kind", ("answer", "retrieval")),
        status_check("source", ("ai", "real", "expert")),
        status_check("status", ("draft", "active", "retired")),
        Index("ix_evalq_bot_kind_status", "bot_id", "kind", "status"),
    )
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    qtype: Mapped[str] = s()
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expect_chunks: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, server_default=sql("'{}'"))
    expect_docs: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, server_default=sql("'{}'"))
    key_points: Mapped[str] = s()
    expect_referral: Mapped[str] = s()
    expect_rtype: Mapped[str] = s()
    status: Mapped[str] = s("draft")
    expert_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql("false"))
    bank_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("1"))
    created_at: Mapped[datetime] = created()


RUN_STATUS = ("queued", "running", "done", "failed", "cancelled")


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (status_check("status", RUN_STATUS), Index("ix_evalrun_bot_at", "bot_id", sql("started_at DESC")))
    id: Mapped[uuid.UUID] = uid()
    bot_id: Mapped[uuid.UUID] = fk("bots.id", "CASCADE")
    flow_id: Mapped[uuid.UUID | None] = fk("flows.id", "SET NULL", nullable=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    bank_version: Mapped[int] = mapped_column(Integer, nullable=False)
    settings_override: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sql("'{}'::jsonb"))
    repeat: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql("1"))
    status: Mapped[str] = s("queued")
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=sql("'{}'::jsonb"))
    started_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    started_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvalResult(Base):
    __tablename__ = "eval_results"
    __table_args__ = (PrimaryKeyConstraint("run_id", "question_id", "attempt"),)
    run_id: Mapped[uuid.UUID] = fk("eval_runs.id", "CASCADE")
    question_id: Mapped[uuid.UUID] = fk("eval_questions.id", "CASCADE")
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql("1"))
    rtype: Mapped[str] = s()
    found: Mapped[bool | None] = mapped_column(Boolean)
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    on_topic: Mapped[float | None] = mapped_column(REAL)
    coverage: Mapped[float | None] = mapped_column(REAL)
    grounded: Mapped[float | None] = mapped_column(REAL)
    safe: Mapped[float | None] = mapped_column(REAL)
    safe_note: Mapped[str] = s()
    format_issues: Mapped[str] = s()
    answer_model: Mapped[str] = s()
    judge_model: Mapped[str] = s()
    judge_note: Mapped[str] = s()
    answer_summary: Mapped[str] = s()
    ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql("0"))
    expert_check: Mapped[str] = s()
    expert_note: Mapped[str] = s()


# ───────────────────────── 7. 背景任務 ─────────────────────────

class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (status_check("status", RUN_STATUS), Index("ix_jobs_ws_at", "workspace_id", sql("created_at DESC")))
    id: Mapped[uuid.UUID] = uid()
    workspace_id: Mapped[uuid.UUID] = fk("workspaces.id", "CASCADE")
    bot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kb_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = s()
    status: Mapped[str] = s("queued")
    progress: Mapped[float] = mapped_column(REAL, nullable=False, server_default=sql("0"))
    message: Mapped[str] = s()
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = fk("users.id", "SET NULL", nullable=True)
    created_at: Mapped[datetime] = created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 哪個伺服器執行個體建立／執行（主機:主行程 pid:啟動時間），與最後一次進度或心跳的時間：多開時判斷工作是否還有人在做（services/jobs.py）
    worker: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql("''"), default=lambda: _job_worker())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=NOW, onupdate=func.now())


def _job_worker() -> str:
    from services.jobs import INSTANCE
    return INSTANCE


# 每種向量維度一條 HNSW 部分運算式索引；新增維度要寫遷移補一條（見 docs/data-model.md）
SUPPORTED_EMBED_DIMS = (768, 1536)   # HNSW 上限 2000 維；3072 要改用 halfvec，另行評估
