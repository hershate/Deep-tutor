"""api_usage 补 (user_id, created_at) 复合索引（iter 369）

日预算守卫（check_daily_budget，每个 LLM 计费请求执行）与 /usage 系列端点均按
(user_id, created_at >= since) 过滤；此前仅有 user_id 单列索引（a7f3e5c91d02），
取索引后仍逐行过滤日期，用量行数随使用线性增长（chat 每轮 1 行、生成每次 5-15 行）
后守卫请求路径线性变慢。守卫谓词已同步改为可索引形式（>= 本地 0 点换算 UTC）。

Revision ID: e8c4a1f63b02
Revises: c5d7e9a13b28
Create Date: 2026-10-17 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e8c4a1f63b02"
down_revision: Union[str, None] = "c5d7e9a13b28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # if_not_exists：对「create_all 已按新 models 建索引但 alembic_version 未及」
    # 的库（测试夹具流/手动建库流）幂等（同 a7f3e5c91d02 口径）
    op.create_index("ix_api_usage_user_created", "api_usage", ["user_id", "created_at"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_api_usage_user_created", table_name="api_usage", if_exists=True)
