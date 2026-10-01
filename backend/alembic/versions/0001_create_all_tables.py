"""create all tables from scratch

Revision ID: 0001_create_all_tables
Revises:
Create Date: 2026-10-01

This is the true initial migration for a fresh database.
All subsequent migrations depend on this one.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001_create_all_tables'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DO $$ BEGIN CREATE TYPE campaignstatus AS ENUM ('draft','scheduled','active','paused','completed','cancelled'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE contactstatus AS ENUM ('new','contacted','interested','not_interested','no_answer','callback','converted','do_not_call'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE campaigncontactstatus AS ENUM ('pending','calling','completed','failed','skipped'); EXCEPTION WHEN duplicate_object THEN null; END $$;")

    op.create_table(
        'users',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(), nullable=False),
    )

    op.create_table(
        'businesses',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('industry', sa.String(100), nullable=True),
        sa.Column('default_language', sa.String(10), nullable=True),
        sa.UniqueConstraint('user_id', name='uq_businesses_user_id'),
    )

    op.create_table(
        'agents',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('voice_id', sa.String(100), nullable=True),
        sa.Column('language', sa.String(50), nullable=True),
        sa.Column('stability', sa.Float(), nullable=True),
        sa.Column('similarity_boost', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'campaigns',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id'), nullable=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('language', sa.String(50), nullable=True),
        sa.Column('objective', sa.String(255), nullable=True),
        sa.Column('status', sa.Enum('draft', 'scheduled', 'active', 'paused', 'completed', 'cancelled', name='campaignstatus', create_type=False), nullable=False, server_default='draft'),
        sa.Column('ai_prompt', sa.Text(), nullable=True),
        sa.Column('ai_voice', sa.String(100), nullable=True),
        sa.Column('max_retries', sa.Integer(), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'contacts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('businesses.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('phone_number', sa.String(50), nullable=False),
        sa.Column('email', sa.String(255), nullable=True),
        sa.Column('company', sa.String(255), nullable=True),
        sa.Column('tags', sa.String(500), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('status', sa.Enum('new', 'contacted', 'interested', 'not_interested', 'no_answer', 'callback', 'converted', 'do_not_call', name='contactstatus', create_type=False), nullable=False, server_default='new'),
        sa.Column('lead_score', sa.Integer(), nullable=True),
        sa.Column('pipeline_stage', sa.String(100), nullable=True),
        sa.Column('ai_insights', postgresql.JSONB(), nullable=True),
        sa.Column('custom_fields', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'campaign_contacts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('contact_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('contacts.id'), nullable=False),
        sa.Column('call_status', sa.Enum('pending', 'calling', 'completed', 'failed', 'skipped', name='campaigncontactstatus', create_type=False), nullable=False, server_default='pending'),
        sa.Column('called_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('campaign_id', 'contact_id', name='uq_campaign_contacts'),
    )

    op.create_table(
        'call_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('contact_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('contacts.id'), nullable=True),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('campaigns.id'), nullable=True),
        sa.Column('business_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('businesses.id'), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('transcript', sa.String(), nullable=True),
        sa.Column('duration', sa.Integer(), nullable=True),
        sa.Column('recording_url', sa.String(), nullable=True),
        sa.Column('outcome', sa.String(32), nullable=True),
        sa.Column('summary', sa.String(), nullable=True),
        sa.Column('follow_up', sa.String(), nullable=True),
        sa.Column('call_sid', sa.String(64), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('call_logs')
    op.drop_table('campaign_contacts')
    op.drop_table('contacts')
    op.drop_table('campaigns')
    op.drop_table('agents')
    op.drop_table('businesses')
    op.drop_table('users')
    op.execute('DROP TYPE IF EXISTS campaigncontactstatus')
    op.execute('DROP TYPE IF EXISTS contactstatus')
    op.execute('DROP TYPE IF EXISTS campaignstatus')
