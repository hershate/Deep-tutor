"""provider profiles & api_usage add user_id（P4 按用户隔离，iter 58）

- llm_provider_profiles：新增 user_id（回填首个用户）；name 全局唯一（旧迁移未命名约束，
  SQLite 无法按名 drop）改为 (user_id, name) 复合唯一 -> 手工重建表（确定性，避免 batch 反射未命名约束）。
- api_usage：新增 user_id（回填首个用户），usage 端点按用户统计。

Revision ID: a1c4f7e20b58
Revises: 169af26b09e7
Create Date: 2026-07-18

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4f7e20b58'
down_revision: Union[str, None] = '169af26b09e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 迁移前旧数据归属：单用户时代数据归首个用户（与 ensure_admin_user 收敛的管理员一致）
_BACKFILL_UID = "(SELECT MIN(id) FROM users)"


def upgrade() -> None:
    # --- llm_provider_profiles：重建表（变更唯一约束 + 加 user_id）---
    op.execute(
        """
        CREATE TABLE llm_provider_profiles_new (
            id INTEGER NOT NULL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            name VARCHAR(64) NOT NULL,
            base_url VARCHAR(512) NOT NULL,
            api_key VARCHAR(256) NOT NULL,
            model VARCHAR(128) NOT NULL,
            is_active BOOLEAN NOT NULL,
            created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
            CONSTRAINT uq_provider_user_name UNIQUE (user_id, name)
        )
        """
    )
    op.execute(
        f"""
        INSERT INTO llm_provider_profiles_new (id, user_id, name, base_url, api_key, model, is_active, created_at)
        SELECT id, {_BACKFILL_UID}, name, base_url, api_key, model, is_active, created_at
        FROM llm_provider_profiles
        """
    )
    op.execute("DROP TABLE llm_provider_profiles")
    op.execute("ALTER TABLE llm_provider_profiles_new RENAME TO llm_provider_profiles")

    # --- api_usage：仅加列（无约束变更，batch 即可）---
    with op.batch_alter_table('api_usage', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_api_usage_user', 'users', ['user_id'], ['id'])
    op.execute(f"UPDATE api_usage SET user_id = {_BACKFILL_UID} WHERE user_id IS NULL")


def downgrade() -> None:
    with op.batch_alter_table('api_usage', schema=None) as batch_op:
        batch_op.drop_constraint('fk_api_usage_user', type_='foreignkey')
        batch_op.drop_column('user_id')

    op.execute(
        """
        CREATE TABLE llm_provider_profiles_old (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(64) NOT NULL,
            base_url VARCHAR(512) NOT NULL,
            api_key VARCHAR(256) NOT NULL,
            model VARCHAR(128) NOT NULL,
            is_active BOOLEAN NOT NULL,
            created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
            CONSTRAINT uq_provider_name UNIQUE (name)
        )
        """
    )
    op.execute(
        """
        INSERT INTO llm_provider_profiles_old (id, name, base_url, api_key, model, is_active, created_at)
        SELECT id, name, base_url, api_key, model, is_active, created_at
        FROM llm_provider_profiles
        """
    )
    op.execute("DROP TABLE llm_provider_profiles")
    op.execute("ALTER TABLE llm_provider_profiles_old RENAME TO llm_provider_profiles")
