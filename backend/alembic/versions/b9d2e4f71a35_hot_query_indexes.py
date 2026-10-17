"""热查询路径补五处索引（iter 371 R9）

文件级审计（R9）核对「模型列定义 × 实际查询模式」发现的线性劣化点——五轮功能
审计后残留的是**随数据量增长**才显现的负载面（单机小库无感，长期高负载线性变慢）：

- kb_documents(user_id)：知识库列表页主过滤列，无索引全表扫（上传配额 200/用户，
  多用户下千行级，每次列表全扫）
- quizzes(plan_id)：进度面板/测验页按方案取题，无索引
- quiz_attempts(quiz_id)：错题队列按 quiz_id 取最新 attempt（R5 B8 口径），全库增速
  最快的表（每次答题一行），无索引最劣
- learning_logs(plan_id, date)：打卡日历/今日分钟按 (plan, date) 取行
- user_prefs(user_id, key)：核查发现既有 UniqueConstraint 自带索引实现，无需另加

if_not_exists 幂等：对「create_all 已按新 models 建索引但版本未及」的库（同
e8c4a1f63b02 口径）。schema parity 守护测试（test_iter370_schema_parity）覆盖
create_all 与 upgrade head 两侧一致性。

Revision ID: b9d2e4f71a35
Revises: e8c4a1f63b02
Create Date: 2026-10-17 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b9d2e4f71a35"
down_revision: Union[str, None] = "e8c4a1f63b02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_kb_documents_user_id", "kb_documents", ["user_id"]),
    ("ix_quizzes_plan_id", "quizzes", ["plan_id"]),
    ("ix_quiz_attempts_quiz_id", "quiz_attempts", ["quiz_id"]),
    ("ix_learning_logs_plan_date", "learning_logs", ["plan_id", "date"]),
    # user_prefs(user_id, key)：核查发现既有 UniqueConstraint 自带索引实现，无需另加（冗余项剔除）
]


def upgrade() -> None:
    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, if_not_exists=True)


def downgrade() -> None:
    for name, table, _cols in _INDEXES:
        op.drop_index(name, table_name=table, if_exists=True)
