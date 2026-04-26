"""add_drm_encryption_columns

DRM Phase 2 — governed_files 테이블에 AES-256-GCM envelope encryption 관련
컬럼 4개와 백필 스캐너용 partial index를 추가한다.

Revision ID: 1a2b3c4d5e6f
Revises: a1b2c3d4e5f6
Create Date: 2026-04-25 00:01:00.000000

Upgrade:
  - vault_id         VARCHAR(500) NULL   — S3 오브젝트 키 (암호화 완료 후 채워짐)
  - encrypted_dek    TEXT NULL           — KMS 암호화된 DEK (base64)
  - encryption_state VARCHAR(20) NOT NULL DEFAULT 'plain'
                                         — FSM: plain/encrypting/encrypted/failed
  - backfill_completed_at TIMESTAMPTZ NULL — 백필 완료 시각

  - ix_governed_files_encryption_state_plain (partial index)
      WHERE encryption_state = 'plain'
      → 백필 스캐너가 대상 행을 O(미암호화 수)로 스캔할 수 있도록 최적화

Downgrade:
  - 위 index 및 컬럼 제거 (역순)

Production 배포 절차:
  1. alembic upgrade 1a2b3c4d5e6f  ← ADD COLUMN x4 + partial index
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1a2b3c4d5e6f"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "governed_files",
        sa.Column("vault_id", sa.String(500), nullable=True),
    )
    op.add_column(
        "governed_files",
        sa.Column("encrypted_dek", sa.Text(), nullable=True),
    )
    op.add_column(
        "governed_files",
        sa.Column(
            "encryption_state",
            sa.String(20),
            nullable=False,
            server_default="plain",
        ),
    )
    op.add_column(
        "governed_files",
        sa.Column("backfill_completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Partial index: 백필 스캐너가 plain 상태 행만 빠르게 찾을 수 있도록.
    # PostgreSQL 전용 (partial index는 표준 SQL 아님).
    op.create_index(
        "ix_governed_files_encryption_state_plain",
        "governed_files",
        ["encryption_state"],
        postgresql_where=sa.text("encryption_state = 'plain'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_governed_files_encryption_state_plain",
        table_name="governed_files",
    )
    op.drop_column("governed_files", "backfill_completed_at")
    op.drop_column("governed_files", "encryption_state")
    op.drop_column("governed_files", "encrypted_dek")
    op.drop_column("governed_files", "vault_id")
