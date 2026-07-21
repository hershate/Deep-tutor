"""高频过滤外键列补索引（iter 243：SQLite FK 不自动建索引）

sessions.user_id / materials.session_id / plans.session_id /
knowledge_nodes.plan_id / tool_calls.session_id / api_usage.user_id /
study_units.plan_id —— 列表按用户、按会话/方案级联查询、日预算按用户
聚合均走这些列的全表扫描，数据量增长后线性变慢。

Revision ID: a7f3e5c91d02
Revises: f6a1c3d58e42
Create Date: 2026-07-21 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7f3e5c91d02'
down_revision: Union[str, None] = 'f6a1c3d58e42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ('ix_sessions_user_id', 'sessions', ['user_id']),
    ('ix_materials_session_id', 'materials', ['session_id']),
    ('ix_plans_session_id', 'plans', ['session_id']),
    ('ix_knowledge_nodes_plan_id', 'knowledge_nodes', ['plan_id']),
    ('ix_tool_calls_session_id', 'tool_calls', ['session_id']),
    ('ix_api_usage_user_id', 'api_usage', ['user_id']),
    ('ix_study_units_plan_id', 'study_units', ['plan_id']),
]


def upgrade() -> None:
    # if_not_exists：对「create_all 已建索引但 alembic_version 被 stamp 到上一代」
    # 的库（测试夹具流/手动建库流）幂等；旧库无索引则正常创建
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, if_not_exists=True)


def downgrade() -> None:
    for name, table, _ in _INDEXES:
        op.drop_index(name, table_name=table, if_exists=True)
