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
    bing 无 key -> none（走 no_search_key 兼容降级路径）。
    bing_key 缺省 -> settings.bing_api_key；iter 223 起调用方可传入
    凭证档案解析后的有效 key（app.core.credentials.resolve_bing_api_key）。
    """
    engine = normalize_search_engine(settings.search_engine)
    if engine == "bing_web":
        return "bing_web"
    key = settings.bing_api_key if bing_key is None else bing_key
    if key:
        return "bing_api"
    return "none" if engine == "bing" else "bing_web"


# err.md C14：环境变量解析错误给可读提示而非裸 ValidationError traceback
try:
    settings = Settings()
except ValidationError as exc:  # pragma: no cover - 仅配置错误时触发
    raise SystemExit(
        f"配置解析失败（请检查 .env 与环境变量类型，如 JWT_EXPIRE_DAYS 须为整数）:\n{exc}"
    ) from exc
