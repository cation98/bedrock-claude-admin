"""Phase 2 E — Skills N-of-M 승인 정책 테이블.

skill_approval_policies: category별 required_approvals 수 정의.
seed: 기본 카테고리(skill, claude-md, prompt, snippet, slash_command, workflow)를
required_approvals=1로 삽입. 운영 중 category별 상향 조정 가능.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


_DEFAULT_CATEGORIES = [
    ("skill", 1, "일반 스킬 — 기본 1인 승인"),
    ("claude-md", 1, "CLAUDE.md 스니펫 — 기본 1인 승인"),
    ("prompt", 1, "프롬프트 템플릿 — 기본 1인 승인"),
    ("snippet", 1, "코드 스니펫 — 기본 1인 승인"),
    ("slash_command", 1, "슬래시 커맨드 — 기본 1인 승인"),
    ("workflow", 1, "워크플로우 — 기본 1인 승인"),
]


def upgrade() -> None:
    op.create_table(
        "skill_approval_policies",
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("category"),
    )

    # 기본 카테고리 seed
    policies = sa.table(
        "skill_approval_policies",
        sa.column("category", sa.String),
        sa.column("required_approvals", sa.Integer),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        policies,
        [
            {"category": c, "required_approvals": n, "description": d}
            for c, n, d in _DEFAULT_CATEGORIES
        ],
    )


def downgrade() -> None:
    op.drop_table("skill_approval_policies")
