"""credential_profiles（API Key 凭证档案，iter 223）

Revision ID: d9e4b2c17f05
Revises: b7e3f1a92c48
Create Date: 2026-07-20

iter 223：设置页可视化管理全部 Key（Anthropic 回退主 Key / Bing 搜索 Key / 个人访问令牌），
多档案卡片切换。kind 区分三类用途；(user_id,kind,name) 命名复合唯一约束（SQLite 降级需名）。
api_key 仅存本机 SQLite，API 层不回显明文（仅掩码末 4 位）。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e4b2c17f05'
down_revision: Union[str, None] = 'b7e3f1a92c48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'credential_profiles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('api_key', sa.String(length=256), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'kind', 'name', name='uq_credential_user_kind_name'),
    )
    op.create_index('ix_credential_profiles_user_id', 'credential_profiles', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_credential_profiles_user_id', table_name='credential_profiles')
    op.drop_table('credential_profiles')
