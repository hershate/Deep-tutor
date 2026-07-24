"""materials_node_sids（自学材料知识点驱动切分，iter 348）

Revision ID: f0a2c5d839b1
Revises: e1b7c3d94a08
Create Date: 2026-07-24

iter 348：自学材料改为按知识点聚合切分（每单元覆盖 1-3 个知识点），study_units 加
node_sids（JSON，知识点 sid 列表）记录单元覆盖的知识点，供 quiz/进度/局部重生成按
知识点定位。additive——既有 node_id（单节点 FK，历史路径多留空）不动。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0a2c5d839b1'
down_revision: Union[str, None] = 'e1b7c3d94a08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # additive：study_units 加 node_sids（JSON 知识点 sid 列表）；既有行填 []
    op.add_column(
        'study_units',
        sa.Column('node_sids', sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('study_units', 'node_sids')
