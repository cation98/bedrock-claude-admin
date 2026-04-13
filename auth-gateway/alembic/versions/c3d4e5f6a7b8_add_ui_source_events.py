"""UI 소스 이벤트 테이블 추가 — T23 ui-source 엔드포인트 지원.

webchat / console 사용률 추적용 테이블.
Admin Dashboard /analytics/ui-split 페이지의 주간/월간 집계 기반.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_source_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ui_source_events_username_recorded",
        "ui_source_events",
        ["username", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ui_source_events_username_recorded", table_name="ui_source_events")
    op.drop_table("ui_source_events")
