"""Phase 0 누락 테이블 추가 — Alembic migration baseline.

5개 신규 테이블 생성:
  - sqlcipher_keys          (SQLCipherKey)
  - app_likes               (AppLike)
  - announcements           (Announcement)
  - guides                  (Guide)
  - moderation_violations   (ModerationViolation)

Revision ID: b2c3d4e5f6a7
Revises: a1f2c3d4e5f6
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1f2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Phase 0 누락 테이블 생성."""

    # ── sqlcipher_keys ─────────────────────────────────────────────────────────
    op.create_table(
        "sqlcipher_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("db_name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sqlcipher_keys_username", "sqlcipher_keys", ["username"])

    # ── app_likes ──────────────────────────────────────────────────────────────
    op.create_table(
        "app_likes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("app_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_app_likes_app_user",
        "app_likes",
        ["app_id", "user_id"],
        unique=True,
    )

    # ── announcements ──────────────────────────────────────────────────────────
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author_username", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── guides ─────────────────────────────────────────────────────────────────
    op.create_table(
        "guides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("author_username", sa.String(50), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── moderation_violations ──────────────────────────────────────────────────
    op.create_table(
        "moderation_violations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("violation_category", sa.String(50), nullable=True),
        sa.Column("violation_reason", sa.Text(), nullable=True),
        sa.Column("app_name", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_moderation_violations_username",
        "moderation_violations",
        ["username"],
    )


def downgrade() -> None:
    """Phase 0 신규 테이블 제거."""
    op.drop_index("ix_moderation_violations_username", table_name="moderation_violations")
    op.drop_table("moderation_violations")
    op.drop_table("guides")
    op.drop_table("announcements")
    op.drop_index("ix_app_likes_app_user", table_name="app_likes")
    op.drop_table("app_likes")
    op.drop_index("ix_sqlcipher_keys_username", table_name="sqlcipher_keys")
    op.drop_table("sqlcipher_keys")
