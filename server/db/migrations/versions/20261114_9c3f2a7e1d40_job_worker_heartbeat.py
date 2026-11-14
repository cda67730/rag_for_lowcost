"""jobs 加 worker（哪個伺服器執行個體負責）與 updated_at（最後一次進度或心跳）：多開程式時判斷工作是否還有人在做

Revision ID: 9c3f2a7e1d40
Revises: 5b1e0c7d9a21
Create Date: 2026-11-14 21:30:00
"""
from alembic import op


revision = '9c3f2a7e1d40'
down_revision = '5b1e0c7d9a21'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN worker text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE jobs ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP COLUMN updated_at")
    op.execute("ALTER TABLE jobs DROP COLUMN worker")
