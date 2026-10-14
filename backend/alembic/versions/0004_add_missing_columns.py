"""add notes to campaign_contacts and description to agents

Revision ID: 0004_add_missing_columns
Revises: 0003_add_campaign_description
Create Date: 2026-10-14

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = '0004_add_missing_columns'
down_revision: Union[str, Sequence[str], None] = '0003_add_campaign_description'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE campaign_contacts ADD COLUMN IF NOT EXISTS notes VARCHAR(500);")
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS description TEXT;")


def downgrade() -> None:
    op.drop_column('campaign_contacts', 'notes')
    op.drop_column('agents', 'description')
