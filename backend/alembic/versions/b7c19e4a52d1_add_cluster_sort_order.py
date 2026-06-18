"""add cluster.sort_order

Revision ID: b7c19e4a52d1
Revises: f30cb007a222
Create Date: 2026-06-11 16:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c19e4a52d1'
down_revision: Union[str, None] = 'f30cb007a222'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('clusters', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    with op.batch_alter_table('clusters', schema=None) as batch_op:
        batch_op.drop_column('sort_order')
