"""Set storage_retention default to 180d and migrate all existing users

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-04-24
"""
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # unlimited 사용자는 유지, 나머지 전원 180d로 일괄 변경
    op.execute(
        "UPDATE users SET storage_retention = '180d' "
        "WHERE storage_retention != 'unlimited'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE users SET storage_retention = '30d' "
        "WHERE storage_retention = '180d'"
    )
