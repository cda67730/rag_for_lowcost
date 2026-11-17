"""api_keys（外部呼叫金鑰，只存雜湊）、bot_integrations（外部資料設定，不存資料本身）

Revision ID: 4d2b8e6f1a73
Revises: 7a1c5e9b2d34
Create Date: 2026-11-17 16:00:00
"""
from alembic import op


revision = '4d2b8e6f1a73'
down_revision = '7a1c5e9b2d34'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE api_keys (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      bot_id uuid NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
      name text NOT NULL DEFAULT '',
      prefix text NOT NULL DEFAULT '',
      key_hash text NOT NULL UNIQUE,
      enabled boolean NOT NULL DEFAULT true,
      last_used_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX api_keys_bot ON api_keys(bot_id);
    CREATE TABLE bot_integrations (
      bot_id uuid PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
      context_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
      sources jsonb NOT NULL DEFAULT '[]'::jsonb,
      updated_at timestamptz NOT NULL DEFAULT now()
    );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE bot_integrations; DROP TABLE api_keys;")
