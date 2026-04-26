"""Add terminate_reason to terminal_sessions

Revision ID: g7h8i9j0k1l2
Revises: b9d8936113f0
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1l2"
down_revision = "b9d8936113f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "terminal_sessions",
        sa.Column("terminate_reason", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("terminal_sessions", "terminate_reason")
