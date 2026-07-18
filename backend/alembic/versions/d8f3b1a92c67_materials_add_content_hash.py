"""materials 表增加 content_hash（plan/11 §九 重复资料按内容哈希去重）

Revision ID: d8f3b1a92c67
Revises: c7e2a9f41d35
Create Date: 2026-07-18 16:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8f3b1a92c67'
down_revision: Union[str, None] = 'c7e2a9f41d35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('materials', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_materials_content_hash', 'materials', ['content_hash'])


def downgrade() -> None:
    op.drop_index('ix_materials_content_hash', table_name='materials')
    op.drop_column('materials', 'content_hash')
