"""add ai_reports

Revision ID: c3f1a8d4e7b2
Revises: b7c19e4a52d1
Create Date: 2026-06-11 17:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3f1a8d4e7b2'
down_revision: Union[str, None] = 'b7c19e4a52d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ai_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=True),
        sa.Column('cluster_name', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ai_reports_cluster_id', 'ai_reports', ['cluster_id'])


def downgrade() -> None:
    op.drop_index('ix_ai_reports_cluster_id', table_name='ai_reports')
    op.drop_table('ai_reports')
