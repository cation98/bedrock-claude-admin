"""add first_editor_username to edit_sessions

Revision ID: a1f2c3d4e5f6
Revises: 9bee0641d698
Create Date: 2026-04-12 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1f2c3d4e5f6'
down_revision: Union[str, None] = '9bee0641d698'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'edit_sessions',
        sa.Column('first_editor_username', sa.String(length=50), nullable=True),
    )
    op.create_index(
        op.f('ix_edit_sessions_first_editor_username'),
        'edit_sessions',
        ['first_editor_username'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_edit_sessions_first_editor_username'), table_name='edit_sessions')
    op.drop_column('edit_sessions', 'first_editor_username')
