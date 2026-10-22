"""add prompt_template to agents table

Revision ID: 0005_add_agent_prompt_template
Revises: 0004_add_missing_columns
Create Date: 2026-10-22

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0005_add_agent_prompt_template'
down_revision: Union[str, Sequence[str], None] = '0004_add_missing_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS prompt_template TEXT;")


def downgrade() -> None:
    op.drop_column('agents', 'prompt_template')
