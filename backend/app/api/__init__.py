"""API 路由层（plan/03 REST/SSE）。

health/sessions/materials/plans/export/usage/settings/progress/quizzes 已接入。
"""

from fastapi import APIRouter

from app.api import auth, backup, deps, exports, health, kb, knowledge, materials, plans, plugins, progress, providers, quizzes, sessions, settings, setup, share, usage, users

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(health.router)
api_router.include_router(sessions.router)
api_router.include_router(materials.router)
api_router.include_router(plans.router)
api_router.include_router(exports.router)
api_router.include_router(usage.router)
api_router.include_router(settings.router)
api_router.include_router(progress.router)
api_router.include_router(quizzes.router)
api_router.include_router(providers.router)
api_router.include_router(users.router)
api_router.include_router(share.router)
api_router.include_router(share.public_router)  # P5：无认证的只读分享访问
api_router.include_router(setup.router)  # iter 89：首次设置向导（公开端点）
api_router.include_router(backup.router)  # iter 93：数据备份导出/导入
api_router.include_router(knowledge.router)  # iter 100：个人知识库聚合（Wiki 页）
api_router.include_router(kb.router)  # iter 213：知识库独立文档库（/kb）
api_router.include_router(plugins.router)  # 后端插件状态（管理员）


__all__ = ["api_router", "auth", "backup", "deps", "exports", "health", "kb", "knowledge", "materials", "plans", "plugins", "progress", "providers", "quizzes", "sessions", "settings", "setup", "share", "usage", "users"]
