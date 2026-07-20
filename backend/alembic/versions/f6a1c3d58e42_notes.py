"""notes（独立自由笔记，iter 227）

Revision ID: f6a1c3d58e42
Revises: e5f7a3c82d19
Create Date: 2026-07-21

iter 227：笔记系统解耦方案——新增 notes 表（标题+正文的自由笔记本，仅挂用户），
与 node_notes（知识点批注，挂节点）并存；此前笔记只能在方案的知识节点上添加。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a1c3d58e42'
down_revision: Union[str, None] = 'e5f7a3c82d19'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notes_user_id', 'notes', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_notes_user_id', table_name='notes')
    op.drop_table('notes')
