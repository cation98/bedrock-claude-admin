"""Add can_send_sms to users + seed N1001063

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-16
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_send_sms",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # 1차 릴리스 정책: 정병오(N1001063)님만 초기 허용
    op.execute("UPDATE users SET can_send_sms = true WHERE username = 'N1001063'")


def downgrade() -> None:
    op.drop_column("users", "can_send_sms")
