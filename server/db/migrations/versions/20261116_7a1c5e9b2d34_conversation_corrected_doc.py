"""conversations.corrected_doc_id：專家從對話紀錄修正問答、存進知識庫的文件

Revision ID: 7a1c5e9b2d34
Revises: 3e8b6d2f4c10
Create Date: 2026-11-16 11:00:00
"""
from alembic import op


revision = '7a1c5e9b2d34'
down_revision = '3e8b6d2f4c10'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE conversations ADD COLUMN corrected_doc_id uuid REFERENCES documents(id) ON DELETE SET NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE conversations DROP COLUMN corrected_doc_id")
