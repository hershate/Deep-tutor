"""应用配置（pydantic-settings，来源 plan/09-audit.md §十七 配置项目录）。

启动时校验必填项（validate_required），缺失 -> 报错退出（R27）。
.env 入 .gitignore；.env.example 提供模板。
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 相对路径锚定点：仓库根（backend/app/core/settings.py -> 上三级）。
# err.md C7：相对路径此前依赖启动 cwd（非仓库根启动会读不到 .env、在错误位置
# 新建空库，表现为「数据丢失」）；统一锚定后任意 cwd 行为一致。
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _anchor(p: str) -> str:
    """相对路径锚定到仓库根；空串/绝对路径原样返回。"""
    if not p or Path(p).is_absolute():
        return p
    return str(_REPO_ROOT / p)


# .env.example 历史模板占位值（iter 89 之前的 scripts/start.* 会在无 .env 时自动
# 复制模板生成 .env）。占位值不是有效凭证：若按「已配置」处理，AUTH_TOKEN 占位会
# 关闭开放本机模式、ANTHROPIC_API_KEY 占位会标记 LLM 已配而不进首次设置向导——
# 两者叠加把用户锁死在无法登录页（iter 90 实证）。统一经下方派生属性视为未配置。
TEMPLATE_AUTH_TOKEN = "change-me-please"
TEMPLATE_ANTHROPIC_API_KEY = "sk-ant-xxxxxxxx"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),  # err.md C7：.env 同样锚定，不依赖 cwd
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM / Anthropic ---
    anthropic_api_key: str = Field(default="", description="Claude API key（生成时必填）")
    anthropic_model: str = Field(
        default="claude-sonnet-5",
        description="模型（Haiku/Sonnet/Opus，影响成本/上下文/质量）",
    )
    # iter 355：LLM 单次输出 token 上限。默认拉高到 100k 防 generate_quiz 等大输出工具被截断
    # （max_tokens 是输出上限非目标，不会增加常规调用成本）。用户可在设置页按模型实际输出上限
    # 调节（过高如超模型输出上限会触发 400）。用户级覆盖见 PUT /settings/prefs。
    llm_max_tokens: int = Field(default=100_000, description="LLM 单次输出 token 上限（防截断）")

    # --- 认证（本地单用户 token；P4 多用户 JWT）---
    auth_token: str = Field(default="", description="本地单用户 token（可选；P4 起作 JWT 兼容回退，未设任何密码时进入开放本机模式 iter 89）")
    admin_username: str = Field(default="admin", description="P4 管理员登录名（启动自动创建/收敛）")
    admin_password: str = Field(default="", description="P4 管理员初始密码（空 -> 不创建登录账号，仅 token 回退）")
    jwt_secret: str = Field(default="", description="P4 JWT HMAC secret（空 -> 回退 AUTH_TOKEN；再无 -> 每启动随机）")
    jwt_expire_days: int = Field(default=7, description="P4 JWT 有效期（天）")

    # --- 存储 ---
    db_path: str = Field(default="data/deep-tutor.db", description="SQLite 路径（相对路径锚定仓库根）")
    database_url: str = Field(
        default="",
        description="完整 SQLAlchemy 数据库 URL（err.md C10：未来切换 Postgres 等；空 -> 由 DB_PATH 拼 sqlite URL）",
    )
    data_dir: str = Field(default="data", description="文件存储根（相对路径锚定仓库根）")
    skills_dir: str = Field(default="skills", description="Skill 目录（相对路径锚定仓库根）")
    plugins_dir: str = Field(default="plugins", description="后端插件目录（相对路径锚定仓库根；API.md「后端插件 API」）")
    frontend_dist: str = Field(
        default="",
        description="前端构建产物目录（设置后由后端托管 SPA + /api 同端口，免 nginx；空=不托管）",
    )

    # --- 限额 ---
    max_upload_mb: int = Field(default=50, description="上传单文件上限 MB")
    max_tool_calls: int = Field(default=20, description="工具调用轮次上限/会话")
    max_regenerations: int = Field(default=2, description="重生成上限")

    # --- 并发与数据库连接（多用户/高负载稳定性）---
    # SQLite 为单写者库：WAL 让读并发，但写仍串行。高负载下两类故障：
    # ① 连接池耗尽（每个 SSE 长连接经 get_db 钉住一条连接直至流结束）-> QueuePool TimeoutError；
    # ② 写-写争用超 busy_timeout -> database is locked 500。下方参数兜底二者。
    db_busy_timeout_ms: int = Field(
        default=10_000,
        description="SQLite busy_timeout（毫秒）：写-写争用时等待上限，超此报 database is locked。默认 10s（多用户负载下高于旧硬编 5s，减少瞬态 500）",
    )
    db_pool_size: int = Field(default=20, description="SQLAlchemy 连接池常驻大小（QueuePool）")
    db_max_overflow: int = Field(default=80, description="连接池溢出上限（pool_size + max_overflow = 总连接上限）")
    db_pool_timeout: int = Field(default=30, description="从连接池获取连接的等待上限（秒）；超此 QueuePool TimeoutError")
    # 任务级并发配额（原硬编码：生成全局 2 / 对话全局 2，100 用户场景下 98% 被拒）。
    # 提为可配，部署方可按数据库后端与 LLM 速率限额调整；配合全局 LLM 信号量（max_concurrent_llm_calls）兜底。
    max_concurrent_generations: int = Field(default=8, description="全局同时进行的方案生成任务数上限（超额 503 server_busy）")
    max_concurrent_generations_per_user: int = Field(default=1, description="单用户同时进行的方案生成任务数上限")
    max_concurrent_chat_runs: int = Field(default=8, description="全局同时进行的 AI 对话 run 数上限（超额 503 chat_run_busy）")
    max_running_chat_per_thread: int = Field(default=1, description="单会话同时进行的 AI 对话 run 数上限")
    max_concurrent_llm_calls: int = Field(
        default=16,
        description="全局并发 LLM 调用上限（provider 级信号量）：防止超额触发模型供应商速率限制与内存膨胀。0=不限",
    )
    max_plugin_event_threads: int = Field(
        default=32,
        description="插件协程事件监听器（同步上下文 emit，如 webhook 投递）的后台线程数上限；"
        "超额丢弃事件（best-effort，防 OS 线程无界膨胀 OOM）",
    )
    max_concurrent_webhook_deliveries: int = Field(
        default=16,
        description="插件 Webhook 并发投递上限（httpx 出站连接数）；超额排队，防 100 用户 × webhook "
        "同时触发打满出站连接/文件描述符。0=不限",
    )
    # iter 345 记忆系统：把方案生成旁挂为 run(kind=plan_generation) 进统一 Run 模型
    # （side-channel overlay，不动 Orchestrator/SSE 既有行为；默认关闭，验证后再开）
    agent_runs_enabled: bool = Field(default=False, description="方案生成旁挂 Run 模型（默认关闭）")

    # --- 预算（成本控制 Pillar 3）---
    budget_per_session_usd: float = Field(default=1.0, description="会话预算")
    daily_budget_usd: float = Field(default=10.0, description="每日预算")

    # --- 搜索 ---
    search_engine: str = Field(
        default="auto",
        description="搜索引擎：auto（有 BING_API_KEY 用付费 API，无 key 用免 Key 网页版）"
        " | bing（仅付费 API，无 key 时检索降级为关键词建议） | bing_web（仅免 Key 网页版）",
    )
    bing_api_key: str = Field(
        default="", description="Bing Search API key（可选；配置后 auto 模式用付费 API，未配置用 Bing 网页版）"
    )

    # --- 多模态（P3；R9：依赖缺失则对应入口禁用 + 提示安装）---
    ocr_languages: str = Field(default="eng+chi_sim", description="图片 OCR 语言（tesseract -l）")
    whisper_model: str = Field(default="base", description="音视频转写模型（faster-whisper 模型名/路径）")
    media_max_minutes: int = Field(default=30, description="音视频转写时长上限（分钟，ffprobe 可得时强制）")

    # --- 杂项 ---
    log_level: str = Field(default="info", description="日志级别")
    cors_origins: str = Field(
        default="http://localhost:5173", description="允许来源（逗号分隔）"
    )

    # --- 派生属性 ---
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth_token_configured(self) -> bool:
        """AUTH_TOKEN 是否为有效配置（历史模板占位值视为未配置，见模块头注释）。"""
        return bool(self.auth_token) and self.auth_token != TEMPLATE_AUTH_TOKEN

    @property
    def anthropic_key_configured(self) -> bool:
        """ANTHROPIC_API_KEY 是否为有效配置（历史模板占位值视为未配置，见模块头注释）。"""
        return bool(self.anthropic_api_key) and self.anthropic_api_key != TEMPLATE_ANTHROPIC_API_KEY

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def sqlalchemy_url(self) -> str:
        """实际使用的数据库 URL（err.md C10：DATABASE_URL 优先，空 -> SQLite）。"""
        return self.database_url or self.sqlite_url

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @model_validator(mode="after")
    def _anchor_paths(self) -> "Settings":
        """err.md C7：相对路径统一锚定仓库根（不依赖启动 cwd）。"""
        self.db_path = _anchor(self.db_path)
        self.data_dir = _anchor(self.data_dir)
        self.skills_dir = _anchor(self.skills_dir)
        self.plugins_dir = _anchor(self.plugins_dir)
        self.frontend_dist = _anchor(self.frontend_dist)
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    # --- 目录与校验 ---
    def ensure_dirs(self) -> None:
        """创建运行时所需目录（DB 父目录 + 文件存储根 + skills/插件目录）。"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.skills_dir).mkdir(parents=True, exist_ok=True)
        Path(self.plugins_dir).mkdir(parents=True, exist_ok=True)

    def validate_required(self) -> list[str]:
        """启动时校验必填项，返回缺失项列表。

        零配置开箱即用（iter 89）：无任何启动硬必填——无 .env 也可启动，
        认证缺失时以后端「开放本机模式」运行（见 app/core/security.py
        passwordless_open），首次设置引导用户在前端完成。
        R27 语义收敛为「功能级 key 调用前校验」（validate_feature_keys）。
        """
        return []

    def validate_feature_keys(self) -> list[str]:
        """功能级必填 key 检查（调用对应功能前用），返回缺失项。

        BING_API_KEY 自 iter 222 起为可选项（不配时 auto 模式用 Bing 网页抓取兜底），
        不再计入缺失——搜索功能无 key 亦可用（R9 可选依赖降级哲学）。
        """
        missing: list[str] = []
        if not self.anthropic_key_configured:
            missing.append("ANTHROPIC_API_KEY")
        return missing


# --- 搜索引擎决策（iter 222：BING_API_KEY 可选化）---
# 纯函数放 core（tools/api 共用，避免 tools <-> api 循环依赖）；调用时读取 settings 单例，
# 支持运行时 monkeypatch 与测试隔离。

SEARCH_ENGINES = ("auto", "bing", "bing_web")  # 合法值；新增引擎在此追加 + 工具层 dispatch 加分支


def normalize_search_engine(value: str) -> str:
    """SEARCH_ENGINE 归一化：strip/lower；非法值 -> "auto" + 告警（稳定优先，不崩启动）。"""
    v = (value or "").strip().lower()
    if v not in SEARCH_ENGINES:
        logging.getLogger("deep_tutor.settings").warning(
            "非法 SEARCH_ENGINE=%r，按 auto 处理（合法值: %s）", value, SEARCH_ENGINES
        )
        return "auto"
    return v


def effective_search_engine(bing_key: str | None = None) -> str:
    """实际生效的检索引擎 -> "bing_api" | "bing_web" | "none"。

    矩阵：auto+key -> bing_api；auto/bing_web 无 key -> bing_web；
    bing 无 key -> bing_web（隐式回退免 Key 网页兜底 + WARNING，iter 346 修正）。
    旧实现 bing+无 key -> "none" 会致 search_resources 静默返回空 resources
    （编排器只对 error 中断、repaired 不中断），用户感知「联网搜索失败」却无报错。
    现与 auto 的免 Key 兜底语义对齐；"none" 仅保留给未来显式禁用检索的开关
    （search_resources 的 none 分支为防御性降级路径，不删除以保完全性）。
    bing_key 缺省 -> settings.bing_api_key；iter 223 起调用方可传入
    凭证档案解析后的有效 key（app.core.credentials.resolve_bing_api_key）。
    """
    engine = normalize_search_engine(settings.search_engine)
    if engine == "bing_web":
        return "bing_web"
    key = settings.bing_api_key if bing_key is None else bing_key
    if key:
        return "bing_api"
    # 无 key：auto/bing 均回退免 Key 网页抓取（旧 bing->none 致静默空 resources，iter 346 修正）
    if engine == "bing":
        logging.getLogger("deep_tutor.settings").warning(
            "SEARCH_ENGINE=bing 但未配置 BING_API_KEY，回退免 Key 网页检索（bing_web）；"
            "若需付费 API 质量请在设置页/.env 配置 BING_API_KEY，或改 SEARCH_ENGINE=auto"
        )
    return "bing_web"


# err.md C14：环境变量解析错误给可读提示而非裸 ValidationError traceback
try:
    settings = Settings()
except ValidationError as exc:  # pragma: no cover - 仅配置错误时触发
    raise SystemExit(
        f"配置解析失败（请检查 .env 与环境变量类型，如 JWT_EXPIRE_DAYS 须为整数）:\n{exc}"
    ) from exc
