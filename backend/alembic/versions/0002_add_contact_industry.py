"""add industry to contacts table

Revision ID: 0002_add_contact_industry
Revises: 0001_create_all_tables
Create Date: 2026-10-09

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = '0002_add_contact_industry'
down_revision: Union[str, Sequence[str], None] = '0001_create_all_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add industry column if it does not already exist
    op.execute("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS industry VARCHAR(120);")


def downgrade() -> None:
    op.drop_column('contacts', 'industry')
