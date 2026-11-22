"""sources and postings

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('slug', sa.String(length=200), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('etag', sa.String(length=300), nullable=True),
    sa.Column('last_modified', sa.String(length=100), nullable=True),
    sa.Column('last_polled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('next_poll_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('baseline_done', sa.Boolean(), nullable=False),
    sa.Column('consecutive_failures', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kind', 'slug', name='uq_sources_kind_slug')
    )
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sources_next_poll_at'), ['next_poll_at'], unique=False)

    op.create_table('account_sources',
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('account_id', 'source_id')
    )
    with op.batch_alter_table('account_sources', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_account_sources_source_id'), ['source_id'], unique=False)

    op.create_table('postings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_id', sa.Integer(), nullable=False),
    sa.Column('external_id', sa.String(length=200), nullable=False),
    sa.Column('dedupe_key', sa.String(length=1000), nullable=False),
    sa.Column('company', sa.String(length=300), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('location', sa.String(length=1000), nullable=False),
    sa.Column('pay', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('canonical_url', sa.String(length=2000), nullable=False),
    sa.Column('apply_url', sa.String(length=2000), nullable=False),
    sa.Column('extra_json', sa.Text(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_baseline', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_postings_source_external')
    )
    with op.batch_alter_table('postings', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_postings_dedupe_key'), ['dedupe_key'], unique=False)
        batch_op.create_index('ix_postings_source_first_seen', ['source_id', 'first_seen_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('postings', schema=None) as batch_op:
        batch_op.drop_index('ix_postings_source_first_seen')
        batch_op.drop_index(batch_op.f('ix_postings_dedupe_key'))

    op.drop_table('postings')
    with op.batch_alter_table('account_sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_account_sources_source_id'))

    op.drop_table('account_sources')
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sources_next_poll_at'))

    op.drop_table('sources')
