"""kb_documents（知识库独立文档库，iter 212）

Revision ID: 5226d813e363
Revises: c4d91e7b2a05
Create Date: 2026-07-20

iter 212：LLMWiki 式独立知识库文档表——用户直接上传文档到知识库（不依赖学习方案），
库内可直接查看原件或提供给 AI（解析文本）。大内容入文件系统
（data/kb/<user_id>/{originals,parsed}/），本表存索引（同 plan/01 §五 存储策略）。
kind 复用 materialkind 枚举（与 materials 表同一套取值）。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5226d813e363'
down_revision: Union[str, None] = 'c4d91e7b2a05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'kb_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column(
            'kind',
            sa.Enum('file', 'text', 'url', 'image', 'audio', 'video', name='materialkind'),
            nullable=False,
        ),
        sa.Column('ext', sa.String(length=16), nullable=False),
        sa.Column('source', sa.String(length=1024), nullable=False),
        sa.Column('original_path', sa.String(length=512), nullable=True),
        sa.Column('parsed_text_path', sa.String(length=512), nullable=True),
        sa.Column('parsed_chars', sa.Integer(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_kb_documents_content_hash', 'kb_documents', ['content_hash'])


def downgrade() -> None:
    op.drop_index('ix_kb_documents_content_hash', table_name='kb_documents')
    op.drop_table('kb_documents')
