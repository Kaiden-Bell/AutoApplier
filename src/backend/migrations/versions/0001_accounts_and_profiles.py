"""accounts and profiles

Revision ID: 0001
Revises: 
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('api_token_hash', sa.String(length=64), nullable=False),
    sa.Column('daily_llm_cap', sa.Integer(), nullable=False),
    sa.Column('automation_enabled', sa.Boolean(), nullable=False),
    sa.Column('disabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_accounts_api_token_hash'), ['api_token_hash'], unique=True)

    op.create_table('profiles',
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('notify_email_enc', sa.LargeBinary(), nullable=True),
    sa.Column('master_cv_enc', sa.LargeBinary(), nullable=True),
    sa.Column('application_profile_enc', sa.LargeBinary(), nullable=True),
    sa.Column('criteria_json', sa.String(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('account_id')
    )


def downgrade() -> None:
    op.drop_table('profiles')
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_accounts_api_token_hash'))

    op.drop_table('accounts')
