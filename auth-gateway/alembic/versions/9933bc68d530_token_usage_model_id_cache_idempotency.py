"""token_usage model_id cache idempotency

Revision ID: 9933bc68d530
Revises: a7b8c9d0e1f2
Create Date: 2026-04-25

Spec: docs/superpowers/specs/2026-04-25-bedrock-cost-measurement-foundation-design.md §4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9933bc68d530'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── 1. token_usage_daily 확장 ───────────────────────────────────────────
    op.add_column('token_usage_daily',
        sa.Column('model_id', sa.String(100), nullable=False,
                  server_default='legacy-aggregate'))
    op.add_column('token_usage_daily',
        sa.Column('cache_creation_input_tokens', sa.BigInteger(), server_default='0'))
    op.add_column('token_usage_daily',
        sa.Column('cache_read_input_tokens', sa.BigInteger(), server_default='0'))
    op.alter_column('token_usage_daily', 'cost_usd',
                    type_=sa.Numeric(12, 6),
                    existing_type=sa.Numeric(10, 4))

    # UniqueConstraint 교체
    # 기존 row의 model_id는 server_default='legacy-aggregate'로 통일됨 → 충돌 없음
    op.drop_constraint('token_usage_daily_username_usage_date_key',
                       'token_usage_daily', type_='unique')
    op.create_unique_constraint('uq_user_date_model', 'token_usage_daily',
                                ['username', 'usage_date', 'model_id'])
    op.create_index('ix_usage_date_model', 'token_usage_daily',
                    ['usage_date', 'model_id'])
    op.create_index('ix_username_usage_date', 'token_usage_daily',
                    ['username', 'usage_date'])

    # ─── 2. token_usage_hourly 확장 ──────────────────────────────────────────
    op.add_column('token_usage_hourly',
        sa.Column('model_id', sa.String(100), nullable=False,
                  server_default='legacy-aggregate'))
    op.add_column('token_usage_hourly',
        sa.Column('cache_creation_input_tokens', sa.BigInteger(), server_default='0'))
    op.add_column('token_usage_hourly',
        sa.Column('cache_read_input_tokens', sa.BigInteger(), server_default='0'))
    op.alter_column('token_usage_hourly', 'cost_usd',
                    type_=sa.Numeric(12, 6),
                    existing_type=sa.Numeric(10, 4))
    op.drop_constraint('uq_slot_user_date_slot', 'token_usage_hourly', type_='unique')
    op.create_unique_constraint('uq_slot_user_date_slot_model', 'token_usage_hourly',
                                ['username', 'usage_date', 'slot', 'model_id'])
    op.create_index('ix_hourly_usage_date_slot', 'token_usage_hourly',
                    ['usage_date', 'slot'])

    # ─── 3. token_usage_event 신규 ───────────────────────────────────────────
    op.create_table('token_usage_event',
        sa.Column('request_id', sa.String(36), primary_key=True),
        sa.Column('username', sa.String(50), nullable=False),
        sa.Column('model_id', sa.String(100), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(20)),
        sa.Column('input_tokens', sa.BigInteger(), server_default='0'),
        sa.Column('output_tokens', sa.BigInteger(), server_default='0'),
        sa.Column('cache_creation_input_tokens', sa.BigInteger(), server_default='0'),
        sa.Column('cache_read_input_tokens', sa.BigInteger(), server_default='0'),
        sa.Column('cost_usd', sa.Numeric(12, 6), server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('NOW()')),
    )
    op.create_index('ix_event_recorded_at', 'token_usage_event', ['recorded_at'])


def downgrade() -> None:
    # 역순 — token_usage_event 먼저, 그 다음 hourly, daily
    op.drop_index('ix_event_recorded_at', table_name='token_usage_event')
    op.drop_table('token_usage_event')

    op.drop_index('ix_hourly_usage_date_slot', table_name='token_usage_hourly')
    op.drop_constraint('uq_slot_user_date_slot_model', 'token_usage_hourly',
                       type_='unique')
    op.create_unique_constraint('uq_slot_user_date_slot', 'token_usage_hourly',
                                ['username', 'usage_date', 'slot'])
    op.alter_column('token_usage_hourly', 'cost_usd',
                    type_=sa.Numeric(10, 4),
                    existing_type=sa.Numeric(12, 6))
    op.drop_column('token_usage_hourly', 'cache_read_input_tokens')
    op.drop_column('token_usage_hourly', 'cache_creation_input_tokens')
    op.drop_column('token_usage_hourly', 'model_id')

    op.drop_index('ix_username_usage_date', table_name='token_usage_daily')
    op.drop_index('ix_usage_date_model', table_name='token_usage_daily')
    op.drop_constraint('uq_user_date_model', 'token_usage_daily', type_='unique')
    op.create_unique_constraint('token_usage_daily_username_usage_date_key',
                                'token_usage_daily', ['username', 'usage_date'])
    op.alter_column('token_usage_daily', 'cost_usd',
                    type_=sa.Numeric(10, 4),
                    existing_type=sa.Numeric(12, 6))
    op.drop_column('token_usage_daily', 'cache_read_input_tokens')
    op.drop_column('token_usage_daily', 'cache_creation_input_tokens')
    op.drop_column('token_usage_daily', 'model_id')
