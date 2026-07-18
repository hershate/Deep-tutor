"""应用配置（pydantic-settings，来源 plan/09-audit.md §十七 配置项目录）。

启动时校验必填项（validate_required），缺失 -> 报错退出（R27）。
.env 入 .gitignore；.env.example 提供模板。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # --- 认证（本地单用户）---
    auth_token: str = Field(default="", description="本地单用户 token（启动必填）")

    # --- 存储 ---
    db_path: str = Field(default="data/deep-tutor.db", description="SQLite 路径")
    data_dir: str = Field(default="data", description="文件存储根")
    skills_dir: str = Field(default="skills", description="Skill 目录")

    # --- 限额 ---
    max_upload_mb: int = Field(default=50, description="上传单文件上限 MB")
    max_tool_calls: int = Field(default=20, description="工具调用轮次上限/会话")
    max_regenerations: int = Field(default=2, description="重生成上限")
    context_token_threshold: int = Field(
        default=0, description="上下文溢出阈值；0=按模型自动"
    )

    # --- 预算（成本控制 Pillar 3）---
    budget_per_session_usd: float = Field(default=1.0, description="会话预算")
    daily_budget_usd: float = Field(default=10.0, description="每日预算")

    # --- 搜索 ---
    search_engine: str = Field(default="bing", description="搜索引擎")
    bing_api_key: str = Field(default="", description="Bing Search API key（search_resources 必填）")

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
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    # --- 目录与校验 ---
    def ensure_dirs(self) -> None:
        """创建运行时所需目录（DB 父目录 + 文件存储根 + skills 目录）。"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        Path(self.skills_dir).mkdir(parents=True, exist_ok=True)

    def validate_required(self) -> list[str]:
        """启动时校验必填项，返回缺失项列表。

        - AUTH_TOKEN：本地认证，结构性必填（启动即校验）。
        - ANTHROPIC_API_KEY / BING_API_KEY：仅对应功能（生成 / 搜索）调用时必填，
          启动时缺失仅告警，便于骨架/开发期先跑通 health 等非生成端点。
          （实现期可收紧为启动硬校验，见 stage.md 备注。）
        """
        missing: list[str] = []
        if not self.auth_token:
            missing.append("AUTH_TOKEN")
        return missing

    def validate_feature_keys(self) -> list[str]:
        """功能级必填 key 检查（调用对应功能前用），返回缺失项。"""
        missing: list[str] = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.bing_api_key:
            missing.append("BING_API_KEY")
        return missing


settings = Settings()
