"""移除 llm_provider_profiles 每卡计价列（iter 356）

Revision ID: c5d7e9a13b28
Revises: d8a2f5c91b34
Create Date: 2026-07-24

iter 356：计费改为「设置页维护的模型计费表」（UserPref{model_pricing}，模型名 -> 4
单价字段，¥/1M tokens），不再绑定 LLM 供应商卡片。故移除 iter 354 加的 4 个每卡
计价列。既有用户若在卡片上设过价目会丢失（计费改由设置页模型计费表独立配置）。

代际探测（migrate.py）仍以「price_input_per_mtok 列存在」识别旧库 -> stamp 到
d8a2f5c91b34 -> upgrade 本迁移删列，链路自洽。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5d7e9a13b28'
down_revision: Union[str, None] = 'd8a2f5c91b34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite ALTER TABLE DROP COLUMN（3.35+ 支持 batch 模式更稳，但直接 drop_column 亦可）
    with op.batch_alter_table('llm_provider_profiles') as batch_op:
        batch_op.drop_column('price_cache_write_per_mtok')
        batch_op.drop_column('price_cache_read_per_mtok')
        batch_op.drop_column('price_output_per_mtok')
        batch_op.drop_column('price_input_per_mtok')


def downgrade() -> None:
    # 回滚：恢复 4 列（NULL -> 回退族默认，同 iter 354 语义）
    with op.batch_alter_table('llm_provider_profiles') as batch_op:
        batch_op.add_column(sa.Column('price_input_per_mtok', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('price_output_per_mtok', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('price_cache_read_per_mtok', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('price_cache_write_per_mtok', sa.Float(), nullable=True))
