"""紀錄列表翻頁與回饋查詢用的索引：conversations 加 id 讓翻頁可以從上一頁最後一筆接著讀；feedback 依 (bot, session, turn) 查

Revision ID: 5b1e0c7d9a21
Revises: 78cd006ea160
Create Date: 2026-11-14 18:00:00
"""
from alembic import op


revision = '5b1e0c7d9a21'
down_revision = '78cd006ea160'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_conv_bot_at_id ON conversations (bot_id, created_at DESC, id DESC)")
    op.execute("DROP INDEX IF EXISTS ix_conv_bot_at")
    op.execute("CREATE INDEX ix_feedback_bot_turn ON feedback (bot_id, session, turn)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_feedback_bot_turn")
    op.execute("CREATE INDEX ix_conv_bot_at ON conversations (bot_id, created_at DESC)")
    op.execute("DROP INDEX IF EXISTS ix_conv_bot_at_id")
