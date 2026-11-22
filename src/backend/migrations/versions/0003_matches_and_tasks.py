"""matches and tasks

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('payload_json', sa.Text(), nullable=False),
    sa.Column('key', sa.String(length=200), nullable=True),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('dead', sa.Boolean(), nullable=False),
    sa.Column('last_error', sa.String(length=1000), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tasks_run_after'), ['run_after'], unique=False)

    op.create_table('matches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('posting_id', sa.Integer(), nullable=False),
    sa.Column('dedupe_key', sa.String(length=1000), nullable=False),
    sa.Column('state', sa.String(length=30), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('gap_analysis', sa.Text(), nullable=False),
    sa.Column('selection_notes_json', sa.Text(), nullable=True),
    sa.Column('latex_source', sa.Text(), nullable=True),
    sa.Column('pdf', sa.LargeBinary(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=False),
    sa.Column('tailor_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['posting_id'], ['postings.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'dedupe_key', name='uq_matches_account_dedupe')
    )
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.create_index('ix_matches_account_state', ['account_id', 'state'], unique=False)
        batch_op.create_index(batch_op.f('ix_matches_posting_id'), ['posting_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_matches_tailor_started_at'), ['tailor_started_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('matches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_matches_tailor_started_at'))
        batch_op.drop_index(batch_op.f('ix_matches_posting_id'))
        batch_op.drop_index('ix_matches_account_state')

    op.drop_table('matches')
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tasks_run_after'))

    op.drop_table('tasks')
