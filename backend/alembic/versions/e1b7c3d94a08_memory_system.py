"""memory_system（AI 对话记忆 + 完整 Agent，iter 345）

Revision ID: e1b7c3d94a08
Revises: c0f2a4b18d33
Create Date: 2026-07-23

iter 345：新增记忆系统 6 张表（threads/messages/runs/run_steps/run_artifacts/memories，
仿 OpenAI Assistants Thread/Message/Run/RunStep），并在 tool_calls 加可空 run_id 逻辑列。
全部 additive——不动既有表语义；plan-gen 仍用 sessions + orchestrator_state，
runs(kind=plan_generation) 为后续 side-channel overlay（phase I 默认关闭）。
枚举列以 String 落库（同 materials.status 等既有 plain-string status 风格，避 batch 模式
enum 命名/check 约束复杂度；ORM Enum 仍按 .value 读写）。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1b7c3d94a08'
down_revision: Union[str, None] = 'c0f2a4b18d33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 顺序按 FK 依赖：threads -> runs -> 其余
    op.create_table(
        'threads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('kind', sa.String(length=32), server_default='chat', nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=True),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_threads_user_id', 'threads', ['user_id'])

    op.create_table(
        'runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Enum('chat', 'plan_generation', name='runkind'), nullable=False),
        sa.Column('status', sa.Enum('running', 'done', 'error', 'interrupted', 'awaiting_approval', 'cancelled', name='runstatus'), nullable=False),
        sa.Column('step_count', sa.Integer(), nullable=False),
        sa.Column('budget_used', sa.Float(), nullable=False),
        sa.Column('workspace_dir', sa.String(length=512), nullable=False),
        sa.Column('workspace_committed', sa.Boolean(), nullable=False),
        sa.Column('trace_id', sa.String(length=64), nullable=False),
        sa.Column('last_event_id', sa.String(length=64), nullable=True),
        sa.Column('error', sa.JSON(), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('started_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('ended_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_runs_thread_id', 'runs', ['thread_id'])
    op.create_index('ix_runs_user_id', 'runs', ['user_id'])

    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.Enum('user', 'assistant', 'system', name='messagerole'), nullable=False),
        sa.Column('content', sa.JSON(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id']),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_messages_thread_id', 'messages', ['thread_id'])
    op.create_index('ix_messages_run_id', 'messages', ['run_id'])

    op.create_table(
        'run_steps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('step_index', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('output', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('stop_reason', sa.String(length=32), nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=False),
        sa.Column('tokens_out', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Float(), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_run_steps_run_id', 'run_steps', ['run_id'])

    op.create_table(
        'run_artifacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=True),
        sa.Column('thread_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('path', sa.String(length=512), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('promoted_to', sa.String(length=64), nullable=True),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_run_artifacts_run_id', 'run_artifacts', ['run_id'])
    op.create_index('ix_run_artifacts_thread_id', 'run_artifacts', ['thread_id'])

    op.create_table(
        'memories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('thread_id', sa.Integer(), nullable=True),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['thread_id'], ['threads.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_memories_user_id', 'memories', ['user_id'])
    op.create_index('ix_memories_thread_id', 'memories', ['thread_id'])

    # tool_calls 加可空 run_id（逻辑外键 runs.id，不加 DB 约束——同 kind 列加列风格）
    with op.batch_alter_table('tool_calls') as batch_op:
        batch_op.add_column(sa.Column('run_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tool_calls') as batch_op:
        batch_op.drop_column('run_id')
    op.drop_index('ix_memories_thread_id', table_name='memories')
    op.drop_index('ix_memories_user_id', table_name='memories')
    op.drop_table('memories')
    op.drop_index('ix_run_artifacts_thread_id', table_name='run_artifacts')
    op.drop_index('ix_run_artifacts_run_id', table_name='run_artifacts')
    op.drop_table('run_artifacts')
    op.drop_index('ix_run_steps_run_id', table_name='run_steps')
    op.drop_table('run_steps')
    op.drop_index('ix_messages_run_id', table_name='messages')
    op.drop_index('ix_messages_thread_id', table_name='messages')
    op.drop_table('messages')
    op.drop_index('ix_runs_user_id', table_name='runs')
    op.drop_index('ix_runs_thread_id', table_name='runs')
    op.drop_table('runs')
    op.drop_index('ix_threads_user_id', table_name='threads')
    op.drop_table('threads')
