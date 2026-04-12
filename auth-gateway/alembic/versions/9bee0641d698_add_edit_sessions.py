"""add edit_sessions for OnlyOffice edit mode

Revision ID: 9bee0641d698
Revises: 8c798042e646
Create Date: 2026-04-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9bee0641d698'
down_revision: Union[str, None] = '8c798042e646'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'edit_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('document_key', sa.String(length=128), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('owner_username', sa.String(length=50), nullable=False),
        sa.Column('is_shared', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('mount_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='editing'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_key', name='uq_edit_sessions_document_key'),
    )
    op.create_index(op.f('ix_edit_sessions_document_key'), 'edit_sessions', ['document_key'], unique=True)
    op.create_index(op.f('ix_edit_sessions_owner_username'), 'edit_sessions', ['owner_username'], unique=False)
    op.create_index(op.f('ix_edit_sessions_status'), 'edit_sessions', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_edit_sessions_status'), table_name='edit_sessions')
    op.drop_index(op.f('ix_edit_sessions_owner_username'), table_name='edit_sessions')
    op.drop_index(op.f('ix_edit_sessions_document_key'), table_name='edit_sessions')
    op.drop_table('edit_sessions')
