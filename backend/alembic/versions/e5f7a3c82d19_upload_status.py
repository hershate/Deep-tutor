"""kb_documents/materials 处理状态列（上传确认流程，iter 225）

Revision ID: e5f7a3c82d19
Revises: d9e4b2c17f05
Create Date: 2026-07-20

iter 225：上传流程改为「上传仅落原件（pending 暂存待确认）-> 用户确认（confirmed）->
按需解析（parsed）」。kb_documents 与 materials 各加 status 列；存量行全部已解析，
server_default 'parsed' 直接回填。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f7a3c82d19'
down_revision: Union[str, None] = 'd9e4b2c17f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'kb_documents',
        sa.Column('status', sa.String(length=16), server_default='parsed', nullable=False),
    )
    op.add_column(
        'materials',
        sa.Column('status', sa.String(length=16), server_default='parsed', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('materials', 'status')
    op.drop_column('kb_documents', 'status')
