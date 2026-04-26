"""merge three heads into single head

Revision ID: b9d8936113f0
Revises: 9933bc68d530, a1b2c3d4e5f6, e6f7a8b9c0d1
Create Date: 2026-04-26 09:09:14.370150

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b9d8936113f0'
down_revision: Union[str, None] = ('9933bc68d530', 'a1b2c3d4e5f6', 'e6f7a8b9c0d1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
