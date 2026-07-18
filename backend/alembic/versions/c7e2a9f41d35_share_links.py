"""share_links（P5 协作分享：方案只读分享链接）

Revision ID: c7e2a9f41d35
Revises: a1c4f7e20b58
Create Date: 2026-07-18 13:20:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2a9f41d35'
down_revision: Union[str, None] = 'a1c4f7e20b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'share_links',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_id', name='uq_share_links_plan'),
    )
    # token 唯一性用唯一索引表达，与 models.ShareLink（unique=True, index=True）保持一致，
    # 避免 create_all（测试库）与 alembic（部署库）产出两套 schema 造成 autogenerate 漂移
    op.create_index('ix_share_links_token', 'share_links', ['token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_share_links_token', table_name='share_links')
    op.drop_table('share_links')
