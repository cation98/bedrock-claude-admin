"""Add model_tier to users — P2 Haiku routing policy

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-04-25
"""
import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "model_tier",
            sa.String(20),
            nullable=False,
            server_default="sonnet",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "model_tier")
