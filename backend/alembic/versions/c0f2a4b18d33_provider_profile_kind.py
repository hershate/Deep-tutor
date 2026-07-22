"""llm_provider_profiles 加 kind 列（协议选择，iter 313）

Revision ID: c0f2a4b18d33
Revises: a7f3e5c91d02
Create Date: 2026-07-22

iter 313：供应商档案加协议 kind 字段。NULL/空 -> 'anthropic'（get_provider 走内置
AnthropicProvider，既有档案零行为变化）；插件注册的协议名（api.providers，如 'openai'）
-> get_provider 分发到对应 PluginProvider（档案 api_key/base_url 作凭证传入 handler）。
插件 API v1.6.0 LLM Provider 扩展点的 profile 集成（iter 312 的跟进项）。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0f2a4b18d33'
down_revision: Union[str, None] = 'a7f3e5c91d02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite ALTER TABLE ADD COLUMN（nullable，无 server_default——既有行得 NULL，get_provider 视作 'anthropic'）
    op.add_column('llm_provider_profiles', sa.Column('kind', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('llm_provider_profiles', 'kind')
