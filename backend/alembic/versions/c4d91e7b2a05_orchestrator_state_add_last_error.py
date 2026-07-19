"""orchestrator_state add last_error

Revision ID: c4d91e7b2a05
Revises: b4e19f0c7a25
Create Date: 2026-07-19

iter 184 B4：失败态持久化——终态错误详情 {code, message} 落库，
供会话列表/终态回放/重试端点展示与判定（此前 error 分支只有状态无详情，
回放只能笼统 previous_failed）。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d91e7b2a05'
down_revision: Union[str, None] = 'b4e19f0c7a25'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # 幂等：create_all 所建无版本/旧版本库可能已带该列（同 a3b91c2e4f56 模式）
    cols = {c["name"] for c in sa.inspect(bind).get_columns("orchestrator_state")}
    if "last_error" in cols:
        return
    # models 为 nullable=False 且无 server_default（与 partial_plan 同构）；
    # 既有行需默认值才能加 NOT NULL 列——先带默认加列，再第二批重建去掉默认
    # （SQLite batch 模式=整表重建，两步后最终 schema 与 models 零漂移）
    with op.batch_alter_table('orchestrator_state', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_error', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    with op.batch_alter_table('orchestrator_state', schema=None) as batch_op:
        batch_op.alter_column('last_error', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('orchestrator_state', schema=None) as batch_op:
        batch_op.drop_column('last_error')
