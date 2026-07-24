"""llm_provider_profiles 加每卡计价列（iter 354 BUG2）

Revision ID: d8a2f5c91b34
Revises: f0a2c5d839b1
Create Date: 2026-07-24

iter 354 BUG2：定价绑定具体供应商卡片。每档案 4 个价目字段（USD / 1M tokens），
NULL -> 该字段回退模型族内置默认价（pricing.py）。计费按「启用档案」价目合成 Pricing
（pricing_for_profile），替代旧的族级 UserPref 覆盖。既有档案 4 字段皆 NULL ->
行为同旧（用内置默认价），零回归。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8a2f5c91b34'
down_revision: Union[str, None] = 'f0a2c5d839b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite ALTER TABLE ADD COLUMN（nullable，无 server_default——既有行得 NULL = 回退默认价）
    op.add_column('llm_provider_profiles', sa.Column('price_input_per_mtok', sa.Float(), nullable=True))
    op.add_column('llm_provider_profiles', sa.Column('price_output_per_mtok', sa.Float(), nullable=True))
    op.add_column('llm_provider_profiles', sa.Column('price_cache_read_per_mtok', sa.Float(), nullable=True))
    op.add_column('llm_provider_profiles', sa.Column('price_cache_write_per_mtok', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('llm_provider_profiles', 'price_cache_write_per_mtok')
    op.drop_column('llm_provider_profiles', 'price_cache_read_per_mtok')
    op.drop_column('llm_provider_profiles', 'price_output_per_mtok')
    op.drop_column('llm_provider_profiles', 'price_input_per_mtok')
