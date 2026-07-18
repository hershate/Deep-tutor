"""progress add unique (plan_id, node_id)

Revision ID: f2a8c1d94e07
Revises: d8f3b1a92c67
Create Date: 2026-07-18

err.md A11/C19：Progress 先查后插竞态可产生重复行，此后 .one_or_none() 抛
MultipleResultsFound 使该节点更新端点持续 500。迁移先去重（保留每组 id 最大行），
再补唯一约束；已存在等价唯一索引的库（create_all 新库）跳过约束创建，保证幂等。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a8c1d94e07'
down_revision: Union[str, None] = 'd8f3b1a92c67'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_unique_plan_node(bind) -> bool:
    """progress 表是否已有覆盖 (plan_id, node_id) 的唯一索引（含 create_all 建的约束）。"""
    for row in bind.execute(sa.text("PRAGMA index_list('progress')")).fetchall():
        # row: (seq, name, unique, origin, partial)
        if not row[2]:
            continue
        cols = {
            r[2]
            for r in bind.execute(sa.text(f"PRAGMA index_info('{row[1]}')")).fetchall()
        }
        if cols == {"plan_id", "node_id"}:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()
    # 去重：每组 (plan_id, node_id) 仅保留 id 最大（最新）的一行
    bind.execute(
        sa.text(
            "DELETE FROM progress WHERE id NOT IN "
            "(SELECT MAX(id) FROM progress GROUP BY plan_id, node_id)"
        )
    )
    if not _has_unique_plan_node(bind):
        with op.batch_alter_table('progress', schema=None) as batch_op:
            batch_op.create_unique_constraint('uq_progress_plan_node', ['plan_id', 'node_id'])


def downgrade() -> None:
    with op.batch_alter_table('progress', schema=None) as batch_op:
        batch_op.drop_constraint('uq_progress_plan_node', type_='unique')
