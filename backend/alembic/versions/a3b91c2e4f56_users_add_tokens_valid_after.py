"""users add tokens_valid_after

Revision ID: a3b91c2e4f56
Revises: f2a8c1d94e07
Create Date: 2026-07-18

err.md A9：JWT 无吊销——重置/修改密码后旧 token 在过期前（默认 7 天）仍有效。
新增 users.tokens_valid_after（epoch 秒）：JWT iat 早于此值即视为已吊销。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b91c2e4f56'
down_revision: Union[str, None] = 'f2a8c1d94e07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # 幂等：create_all 所建无版本/旧版本库可能已带该列（测试夹具 drop_all+create_all
    # 场景即如此），重复 ADD COLUMN 会报 duplicate column name
    cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    if "tokens_valid_after" in cols:
        return
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('tokens_valid_after', sa.Float(), server_default='0', nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('tokens_valid_after')
