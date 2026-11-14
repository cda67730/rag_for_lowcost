"""加 1536 維向量索引：OpenAI text-embedding-3-small 原生維度；Gemini／OpenAI 其他模型也可指定 1536

Revision ID: 78cd006ea160
Revises: 432c4b17728c
Create Date: 2026-11-14 10:22:43.808463
"""
from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


revision = '78cd006ea160'
down_revision = '432c4b17728c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX chunks_emb_1536 ON chunks USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE embed_dim = 1536 AND active")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS chunks_emb_1536")
