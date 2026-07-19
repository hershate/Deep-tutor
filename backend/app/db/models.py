"""ORM 模型（全部表），字段对齐 plan/01-architecture.md §四 数据模型。

枚举值对齐 plan/08-data-exchange-format.md §2.4 全局枚举。
大内容（解析文本/自学材料/导出/大方案 JSON）入文件系统，DB 存路径（见 01 §五）。
"""

from __future__ import annotations

import enum
from datetime import datetime, date

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.db.base import Base


# ---------------------------------------------------------------------------
# 枚举（plan/08 §2.4）
# ---------------------------------------------------------------------------


class OrchestratorStatus(str, enum.Enum):
    idle = "idle"
    running = "running"
    paused_clarify = "paused_clarify"
    done = "done"
    error = "error"
    interrupted = "interrupted"


class MaterialKind(str, enum.Enum):
    file = "file"
    text = "text"
    url = "url"
    image = "image"
    audio = "audio"
    video = "video"


class ResourceType(str, enum.Enum):
    tutorial = "tutorial"
    video = "video"
    doc = "doc"
    book = "book"


class VerifyLabel(str, enum.Enum):
    ok = "ok"
    uncertain = "uncertain"
    invalid = "invalid"


class QuizType(str, enum.Enum):
    choice = "choice"
    fill = "fill"
    short = "short"
    essay = "essay"


class ProgressStatus(str, enum.Enum):
    not_started = "not_started"
    learning = "learning"
    done = "done"


class ToolCallStatus(str, enum.Enum):
    ok = "ok"
    error = "error"
    repaired = "repaired"


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    # 命名唯一约束：SQLite 下 alembic 降级需约束名（未命名约束 batch 模式无法 DROP）
    __table_args__ = (UniqueConstraint("username", name="uq_users_username"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # P4 多用户（iter 57）：username 唯一登录名（旧数据可为 NULL，启动时收敛为管理员）；
    # password_hash 为 pbkdf2$iterations$salt$hash（app/core/security.py）；is_admin 管理员标记。
    username: Mapped[str | None] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # err.md A9：改密时间（epoch 秒）；JWT iat 早于此值即视为已吊销（改密后旧 token 失效）
    tokens_valid_after: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 删用户时级联删其会话（err.md C3：PRAGMA foreign_keys=ON 下无级联会 FK 阻断）；
    # ORM 级级联（先删子行），不依赖 FK ondelete，迁移库与 create_all 库行为一致。
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    api_usages: Mapped[list[ApiUsage]] = relationship(cascade="all, delete-orphan")
    provider_profiles: Mapped[list[LlmProviderProfile]] = relationship(
        cascade="all, delete-orphan"
    )
    node_notes: Mapped[list[NodeNote]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    prefs: Mapped[list[UserPref]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="sessions")
    materials: Mapped[list[Material]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    plans: Mapped[list[Plan]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    orchestrator_state: Mapped[OrchestratorState | None] = relationship(
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class OrchestratorState(Base):
    """编排器状态（1:1 与 session，plan/10 §十一 状态机）。"""

    __tablename__ = "orchestrator_state"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id"), primary_key=True
    )
    status: Mapped[OrchestratorStatus] = mapped_column(
        Enum(OrchestratorStatus), default=OrchestratorStatus.idle, nullable=False
    )
    current_tool: Mapped[str | None] = mapped_column(String(64))
    partial_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    # iter 184 B4：终态错误详情 {code, message}（done 清空 {}；列表/回放/重试展示用）
    last_error: Mapped[dict] = mapped_column(JSON, default=dict)
    tool_call_ids: Mapped[list] = mapped_column(JSON, default=list)
    budget_used: Mapped[float] = mapped_column(Float, default=0.0)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    last_event_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    session: Mapped[Session] = relationship(back_populates="orchestrator_state")


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    kind: Mapped[MaterialKind] = mapped_column(Enum(MaterialKind), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_text_path: Mapped[str | None] = mapped_column(String(512))
    parsed_chars: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    # 来源内容哈希（sha256，plan/11 §九：重复资料按 content hash 去重、复用已解析结果）
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="materials")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    content_path: Mapped[str | None] = mapped_column(String(512))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="plans")
    knowledge_nodes: Mapped[list[KnowledgeNode]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    quizzes: Mapped[list[Quiz]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    progress: Mapped[list[Progress]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    learning_logs: Mapped[list[LearningLog]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    study_units: Mapped[list[StudyUnit]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class KnowledgeNode(Base):
    __tablename__ = "knowledge_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    sid: Mapped[str] = mapped_column(String(64), default="")  # plan 内容 knowledge_map 中的原始字符串 id（如 "k1"）
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, default=1)  # 1-5
    must_learn: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))

    plan: Mapped[Plan] = relationship(back_populates="knowledge_nodes")
    parent: Mapped[KnowledgeNode | None] = relationship(
        remote_side="KnowledgeNode.id", back_populates="children"
    )
    # err.md C11：删父节点时级联删子节点（否则 FK 阻断 ORM 删除）
    children: Mapped[list[KnowledgeNode]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    resources: Mapped[list[Resource]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )
    notes: Mapped[list[NodeNote]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(512), default="")
    type: Mapped[ResourceType] = mapped_column(Enum(ResourceType), default=ResourceType.doc)
    verified: Mapped[VerifyLabel] = mapped_column(
        Enum(VerifyLabel), default=VerifyLabel.uncertain
    )
    note: Mapped[str] = mapped_column(Text, default="")

    node: Mapped[KnowledgeNode | None] = relationship(back_populates="resources")


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))
    type: Mapped[QuizType] = mapped_column(Enum(QuizType), default=QuizType.choice)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 题干/选项/答案/解析

    plan: Mapped[Plan] = relationship(back_populates="quizzes")
    attempts: Mapped[list[QuizAttempt]] = relationship(
        back_populates="quiz", cascade="all, delete-orphan"
    )


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"), nullable=False)
    user_answer: Mapped[dict] = mapped_column(JSON, default=dict)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    quiz: Mapped[Quiz] = relationship(back_populates="attempts")


class Progress(Base):
    __tablename__ = "progress"
    # err.md A11/C19：每 (plan_id, node_id) 至多一行；先查后插竞态由唯一约束兜底
    __table_args__ = (UniqueConstraint("plan_id", "node_id", name="uq_progress_plan_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    node_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id"), nullable=False)
    status: Mapped[ProgressStatus] = mapped_column(
        Enum(ProgressStatus), default=ProgressStatus.not_started
    )
    mastery: Mapped[float] = mapped_column(Float, default=0.0)  # 0-1
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    plan: Mapped[Plan] = relationship(back_populates="progress")


class LearningLog(Base):
    __tablename__ = "learning_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)

    plan: Mapped[Plan] = relationship(back_populates="learning_logs")


class NodeNote(Base):
    """知识点笔记（iter 105 个人 Wiki 增强）：每用户每节点一条，空内容即删行。

    归属双 FK：node_id 定位知识点（随节点级联清理），user_id 归属用户（P4 隔离 +
    删用户级联）。导出/导入走 backup.py（按节点 sid 重关联，随备份版本 1 兼容：
    旧备份无 note 字段则不还原笔记）。
    """

    __tablename__ = "node_notes"
    __table_args__ = (UniqueConstraint("user_id", "node_id", name="uq_node_notes_user_node"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    node_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="node_notes")
    node: Mapped[KnowledgeNode] = relationship(back_populates="notes")


class UserPref(Base):
    """用户级偏好 KV（iter 176 参数配置：日预算等运行时覆盖项）。

    通用 key/value 文本表，为后续偏好项预留（免每加一项跑一次迁移）；删除某键
    即回退全局默认。删用户 ORM 级联（同 node_notes）。
    """

    __tablename__ = "user_prefs"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_prefs_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="prefs")


class ToolCall(Base):
    """Agent 审计 + 成本归集（plan/01 §四 tool_calls）。"""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[ToolCallStatus] = mapped_column(
        Enum(ToolCallStatus), default=ToolCallStatus.ok
    )
    error: Mapped[str | None] = mapped_column(Text)
    llm_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="tool_calls")


class ApiUsage(Base):
    """原始 API 用量（成本计量 Pillar 3）。

    P4 多用户（iter 58）：user_id 归属用户（按用户统计用量）；
    迁移前旧行回填至首个用户（见 alembic 迁移），新行由 _persist_tool_calls 写入。
    """

    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # P4 iter 58 按用户隔离
    provider: Mapped[str] = mapped_column(String(32), default="anthropic")
    model: Mapped[str] = mapped_column(String(64), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cache_read: Mapped[int] = mapped_column(Integer, default=0)
    cache_write: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StudyUnit(Base):
    """自学材料单元（P2，plan/01 §四 study_units）。"""

    __tablename__ = "study_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_nodes.id"))
    stage_index: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    prerequisites: Mapped[list] = mapped_column(JSON, default=list)
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    estimated_time: Mapped[str] = mapped_column(String(64), default="")
    order: Mapped[int] = mapped_column(Integer, default=0)
    done_at: Mapped[datetime | None] = mapped_column(DateTime)  # 标记已学时间（plan/07 §六 标记已学），None=未学

    plan: Mapped[Plan] = relationship(back_populates="study_units")
    materials: Mapped[list[StudyMaterial]] = relationship(
        back_populates="unit", cascade="all, delete-orphan"
    )


class StudyMaterial(Base):
    """单元内容（P2，plan/01 §四 study_materials）。"""

    __tablename__ = "study_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("study_units.id"), nullable=False)
    content_md_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_html_path: Mapped[str | None] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(Integer, default=0)
    resources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    unit: Mapped[StudyUnit] = relationship(back_populates="materials")


class LlmProviderProfile(Base):
    """LLM 供应商档案（用户自定义供应商，类 CC-switch，iter 50）。

    多档案切换：is_active 唯一生效（API 层保证同一用户内同时至多一个）；
    get_provider(user_id) 优先取该用户 active 档案，否则回退 .env（ANTHROPIC_API_KEY）。
    api_key 仅存本机 SQLite，API 层不回显明文（仅掩码末 4 位）。
    base_url 为空 -> Anthropic 官方端点；model 为空 -> settings.anthropic_model。
    P4 多用户（iter 58）：档案按用户隔离（user_id + (user_id,name) 唯一）；
    迁移前旧行回填至首个用户（见 alembic 迁移）。
    """

    __tablename__ = "llm_provider_profiles"
    # 命名复合唯一约束：同名档案仅在同用户内冲突；命名便于 SQLite alembic 降级
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_provider_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # P4 iter 58 按用户隔离
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), default="")
    api_key: Mapped[str] = mapped_column(String(256), nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ShareLink(Base):
    """方案只读分享链接（P5 协作分享）。

    每个方案至多一条有效链接（plan_id 唯一）；token 为 secrets.token_urlsafe 随机串，
    知道链接即可只读访问方案内容（不含用户/会话/资料信息）。撤销 = 删行。
    """

    __tablename__ = "share_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False, unique=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Plan 删除时联动删链接（session/user 级联删 plan 路径），防 FK 约束阻断
    plan: Mapped[Plan] = relationship(
        backref=backref("share_link", cascade="all, delete-orphan", uselist=False)
    )
