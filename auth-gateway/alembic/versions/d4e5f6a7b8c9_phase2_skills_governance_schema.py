"""Phase 2 A+B — Skills governance 스키마 확장.

A. shared_skills 컬럼 추가:
   - approval_status (pending|approved|rejected, 기본 pending)
   - version (int, 기본 1)
   - rejected_by, rejected_at, rejection_reason

B. skill_governance_events 테이블 신설:
   - 승인/반려/삭제/버전 이력 감사 트레일
   - skill_id FK는 ON DELETE SET NULL — skill 삭제 후에도 이력 유지

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── A. shared_skills 컬럼 추가 ──
    op.add_column(
        "shared_skills",
        sa.Column(
            "approval_status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
    )
    op.add_column(
        "shared_skills",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column("shared_skills", sa.Column("rejected_by", sa.String(50), nullable=True))
    op.add_column("shared_skills", sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shared_skills", sa.Column("rejection_reason", sa.Text(), nullable=True))

    op.create_index(
        "ix_shared_skills_approval_status",
        "shared_skills",
        ["approval_status"],
    )

    # 기존 is_approved=True 레코드를 approval_status='approved'로 보정
    op.execute(
        "UPDATE shared_skills SET approval_status = 'approved' WHERE is_approved = true"
    )

    # ── B. skill_governance_events 신규 테이블 ──
    op.create_table(
        "skill_governance_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("actor_username", sa.String(50), nullable=False),
        sa.Column("actor_role", sa.String(20), server_default="admin", nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["shared_skills.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sge_skill_created",
        "skill_governance_events",
        ["skill_id", "created_at"],
    )
    op.create_index(
        "ix_sge_actor",
        "skill_governance_events",
        ["actor_username"],
    )
    op.create_index(
        "ix_sge_type",
        "skill_governance_events",
        ["event_type"],
    )


def downgrade() -> None:
    # B. drop audit table
    op.drop_index("ix_sge_type", table_name="skill_governance_events")
    op.drop_index("ix_sge_actor", table_name="skill_governance_events")
    op.drop_index("ix_sge_skill_created", table_name="skill_governance_events")
    op.drop_table("skill_governance_events")

    # A. shared_skills 컬럼 제거
    op.drop_index("ix_shared_skills_approval_status", table_name="shared_skills")
    op.drop_column("shared_skills", "rejection_reason")
    op.drop_column("shared_skills", "rejected_at")
    op.drop_column("shared_skills", "rejected_by")
    op.drop_column("shared_skills", "version")
    op.drop_column("shared_skills", "approval_status")
