"""kb_chat_records（知识库问答持久化，iter 220）

Revision ID: b7e3f1a92c48
Revises: 5226d813e363
Create Date: 2026-07-20

iter 220：/kb/chat 问答记录落表，便于日后参考（取代 iter 216「聊天不持久化」的临时决策）。
citations/omitted_titles/doc_ids 为 JSON 快照——被引文档日后可删，记录须自包含可读。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e3f1a92c48'
down_revision: Union[str, None] = '5226d813e363'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'kb_chat_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('citations', sa.JSON(), nullable=False),
        sa.Column('omitted_titles', sa.JSON(), nullable=False),
        sa.Column('doc_ids', sa.JSON(), nullable=False),
        sa.Column('context_docs', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_kb_chat_records_user_id', 'kb_chat_records', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_kb_chat_records_user_id', table_name='kb_chat_records')
    op.drop_table('kb_chat_records')
