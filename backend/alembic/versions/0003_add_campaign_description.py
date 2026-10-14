"""add description to campaigns table

Revision ID: 0003_add_campaign_description
Revises: 0002_add_contact_industry
Create Date: 2026-10-14

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = '0003_add_campaign_description'
down_revision: Union[str, Sequence[str], None] = '0002_add_contact_industry'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS description TEXT;")


def downgrade() -> None:
    op.drop_column('campaigns', 'description')
