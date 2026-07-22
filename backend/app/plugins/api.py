"""后端插件 API 门面——插件 register(api) 收到的对象（契约见 API.md「后端插件 API」）。

设计原则：插件只依赖本门面，不 import 宿主内部模块路径——宿主内部重构/模块搬家
不破坏已发布插件。门面语义化版本（api.version）只增不改；新增能力以新命名空间/
新方法追加，既有方法签名与行为保持向后兼容。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from app.plugins import bus, exporters, parsers, runtime

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from app.agent.registry import Tool
    from app.plugins.runtime import PluginStatus

# 后端插件 API 语义化版本（与前端 window.DeepTutor 版本相互独立，见 API.md §版本线）
# v1.3.0：新增 api.parsers 文件解析扩展点（插件为私有/小众格式注册解析器）。
# v1.4.0：新增 api.exporters 导出格式扩展点（插件为私有/小众格式注册导出器）。
# v1.5.0：新增 api.llm 宿主 LLM 能力调用（插件复用用户已配置的供应商，预算守卫+计量同宿主）。
API_VERSION = "1.5.0"


class ToolsNamespace:
    """Agent 工具注册：插件工具汇入每次请求新建的工具注册表（default_registry）。

    注意：内置生成 PIPELINE 步骤固定，不会自动调用新工具——新工具主要供插件路由/
    其他插件经注册表显式调用；与核心工具同名会覆盖核心实现（注册表告警），
    属高级用法，见 API.md 开发规范。
    """

    def __init__(self, status: PluginStatus, rollbacks: list) -> None:
        self._status = status
        self._rollbacks = rollbacks

    def register(self, tool: Tool) -> None:
        """注册完整 Tool（LLM 工具或 Python 工具，契约同宿主工具，见 app/agent/registry.py）。"""
        from app.agent.registry import Tool

        if not isinstance(tool, Tool):
            raise TypeError("tool 必须是 app.agent.registry.Tool 实例")
        runtime.add_tool(tool)
        self._status.tools.append(tool.name)
        self._rollbacks.append(lambda tool=tool: runtime.remove_tool(tool))

    def register_python(self, name: str, func: Any) -> None:
        """便捷注册 Python 工具：async (inp, ctx) -> dict（data 字典）。

        inp/ctx 契约与宿主工具一致（app/tools/base.py 的 ToolInput；ctx 含 db/registry 等）。
        """
        from app.agent.registry import Tool

        self.register(Tool(name=name, llm=False, func=func))


class ParsersNamespace:
    """文件解析器注册（v1.3.0 扩展点，契约见 API.md「§文件解析扩展点」）。

    插件为内置 parse_material 不支持的文件格式（私有/小众格式）注册解析器，上传校验
    与解析链路自动识别并委托——无需改宿主代码即可"让后端支持新格式"（用户点名需求）。

    handler 契约与内置 _parse_xxx 一致：(path: pathlib.Path, parse_mode: str)
    -> tuple[str, dict]（解析文本, 元数据）。async 直接 await；sync 在线程池执行
    （与内置同步解析器经 to_thread 同口径，不阻塞事件循环）。成功返回 (text, meta)；
    返回空文本 -> 上游 empty_content；抛任何异常 -> 上游 parse_failed（消息经服务器
    路径清洗）。parse_mode 取值与内置一致（"full"/"fast"），插件可忽略。

    安全默认（内置优先）：对内置已支持的 ext 注册解析器默认不生效（内置处理），除非
    override=True（启动告警，高级用法——替换内置解析器请充分测试）。
    """

    def __init__(self, plugin_id: str, status: "PluginStatus", rollbacks: list) -> None:
        self._pid = plugin_id
        self._status = status
        self._rollbacks = rollbacks

    def register(
        self,
        extensions: list[str],
        handler: Callable,
        *,
        label: str = "",
        magic: bytes | None = None,
        override: bool = False,
    ) -> None:
        """注册文件解析器。

        - extensions：扩展名列表（如 [".epub"]）；自动规范化为小写带点。
        - handler：解析函数，async 或 sync，签名 (path, parse_mode) -> (text, meta)。
        - label：人类可读格式名（GET /materials/formats 展示与替代前端格式发现用），可选。
        - magic：上传 magic-bytes 校验签名（如 b"PK"，校验文件内容与扩展名一致），可选。
        - override：True 则即便对内置格式也接管（高级用法，启动告警）；默认 False 内置优先。
        """
        if not isinstance(extensions, (list, tuple)) or not extensions:
            raise ValueError("extensions 必须是非空列表")
        if not callable(handler):
            raise TypeError("handler 必须是 callable（async 或 sync，签名 (path, parse_mode) -> (text, meta)）")
        norm = tuple(parsers.normalize_ext(e) for e in extensions)
        entry = parsers.ParserEntry(
            plugin_id=self._pid,
            extensions=norm,
            handler=handler,
            override=bool(override),
            magic=magic,
            label=label or "",
        )
        parsers.register(entry)
        self._status.parsers.extend(norm)
        self._rollbacks.append(lambda entry=entry: parsers.unregister(entry))

    def extensions(self) -> list[str]:
        """本插件已注册的扩展名（调试/状态展示用）。"""
        return [e for e in parsers.registered_extensions()]


class ExportersNamespace:
    """导出格式注册（v1.4.0 扩展点，契约见 API.md「§导出格式扩展点」）。

    插件为内置导出（md/pdf/slides/ics/anki）不支持的格式（私有/小众格式）注册导出器，
    POST /api/export/{plan_id} 的 format 校验与分发自动识别并委托——无需改宿主代码即可
    "让后端支持新导出格式"（与 api.parsers 对称的出口侧）。

    renderer 契约：async 或 sync (plan: dict, ctx: dict) -> dict。
    - plan：方案完整内容字典（intent/schedule/quizzes 等，与 GET /plans/{id} 的 content 一致）。
    - ctx：{"plan_id": int, "user_id": int, "wrong_only": bool}。wrong_only 仅对 override
      接管内置 anki 的插件有意义（内置约定 wrong_only 为 anki 专属）；普通插件格式收到
      wrong_only=true 时端点已在分发前 400 拒绝，插件可忽略该键。
      注意：sync renderer 在线程池执行（与内置同步导出经 to_thread 同口径）——需要
      额外查库的插件请自行新建会话（勿跨线程共享宿主请求会话，SQLAlchemy Session
      非线程安全）。
    - 返回 dict：必须含 "filename"（str，仅取 basename 防路径注入）；必须含
      "content"（str，文本产物随响应返回）或 "data"（bytes，二进制产物 base64 为
      bytes_b64 返回，上限 exporters.MAX_BYTES）其一。其余键原样并入响应
      （保留键 file_url/format/filename/content/bytes_b64/data 除外，由宿主控制）。
    - 抛任何异常 -> 上游 export_failed（不静默降级——私有格式降级无意义且危险）。

    安全默认（内置优先）：对内置格式注册导出器默认不生效（内置处理），除非
    override=True（启动告警，高级用法——替换内置导出请充分测试）。
    """

    def __init__(self, plugin_id: str, status: "PluginStatus", rollbacks: list) -> None:
        self._pid = plugin_id
        self._status = status
        self._rollbacks = rollbacks

    def register(
        self,
        format: str,
        handler: Callable,
        *,
        label: str = "",
        override: bool = False,
    ) -> None:
        """注册导出器。

        - format：格式名（如 "epub"）；自动规范化为小写，字符集 [a-z0-9_-]。
        - handler：渲染函数，async 或 sync，签名 (plan, ctx) -> dict（见类 docstring）。
        - label：人类可读格式名（GET /export/formats 展示与替代前端格式发现用），可选。
        - override：True 则即便对内置格式也接管（高级用法，启动告警）；默认 False 内置优先。
        """
        if not callable(handler):
            raise TypeError("handler 必须是 callable（async 或 sync，签名 (plan, ctx) -> dict）")
        fmt = exporters.normalize_format(format)
        if not exporters.valid_format_name(fmt):
            raise ValueError(f"格式名非法: {fmt!r}（需匹配 [a-z0-9][a-z0-9_-]{{0,31}}）")
        entry = exporters.ExporterEntry(
            plugin_id=self._pid,
            format=fmt,
            handler=handler,
            override=bool(override),
            label=label or "",
        )
        exporters.register(entry)
        self._status.exporters.append(fmt)
        self._rollbacks.append(lambda entry=entry: exporters.unregister(entry))

    def formats(self) -> list[str]:
        """本插件已注册的格式名（调试/状态展示用）。"""
        return [e.format for e in exporters.all_entries() if e.plugin_id == self._pid]


class LlmNamespace:
    """宿主 LLM 能力调用（v1.5.0 扩展点，契约见 API.md「§LLM 能力」）。

    插件做 AI 功能（自定义总结/批改/翻译/问答等）直接复用宿主已配置的供应商——
    用户无需为插件重复配 key；按 user_id 三级回退解析（DB 供应商档案 -> Key 凭证
    档案 -> .env，与宿主端点同一口径）。调用前经 check_daily_budget 日预算守卫
    （与生成/幻灯片端点同规），用量计入 api_usage（用量页/日预算统计口径完整）。

    错误映射：无配置 -> no_llm_provider(503)；日预算耗尽 -> budget_exceeded(402)；
    瞬态上游错误（限流/5xx/连接失败）-> server_busy(503)，插件可自行退避重试；
    其他 -> bad_gateway(502)。均为 ApiError——插件路由内直接上抛即得宿主统一错误格式。

    注意：v1 为非流式单次补全；多轮对话/工具调用请自行用返回文本编排（或经
    api.tools 注册工具融入 Agent 管线）。
    """

    _MAX_TOKENS_CAP = 8192  # 单次 max_tokens 上限（防失控插件天价调用；日预算守卫是底线）

    def __init__(self, plugin_id: str) -> None:
        self._pid = plugin_id

    def is_configured(self, user_id: int) -> bool:
        """该用户是否已配置任一 LLM 供应商（与 complete 的解析口径严格一致）。

        插件据此在"无 key"时优雅降级（如显示配置引导），而非调用时才撞 503。
        """
        from app.llm import get_provider

        try:
            get_provider(user_id)
        except Exception:  # noqa: BLE001  探测语义：任何解析失败（含 DB 异常）都视为未配置
            return False
        return True

    async def complete(
        self,
        user_id: int,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 2048,
    ) -> dict:
        """以该用户的供应商配置发起一次 LLM 补全。

        返回 dict：{"content": str, "model": str, "stop_reason": str | None,
        "usage": {"input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens"}}（只增不改）。max_tokens 收敛到 [1, 8192]。
        """
        from app.api.deps import check_daily_budget
        from app.api.errors import ApiError
        from app.db.base import SessionLocal
        from app.llm import get_provider
        from app.llm.base import TransientLLMError

        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id 必须是正整数")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt 必须是非空字符串")
        if not isinstance(system, str):
            raise TypeError("system 必须是字符串")
        max_tokens = max(1, min(int(max_tokens), self._MAX_TOKENS_CAP))

        try:
            provider = get_provider(user_id)
        except RuntimeError as exc:
            raise ApiError("no_llm_provider", f"用户 {user_id} 未配置 LLM 供应商") from exc
        check_daily_budget(None, user_id)  # 日预算守卫（err.md A6，同生成/slides 端点）
        try:
            resp = await provider.chat(system, prompt, max_tokens=max_tokens)
        except TransientLLMError as exc:
            raise ApiError("server_busy", f"LLM 服务暂时不可用（可退避重试）: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiError("bad_gateway", f"LLM 调用失败: {exc}") from exc

        # 用量计量（err.md A6：与宿主端点同口径；record 内部容错，计量失败不阻断业务）
        from app.agent.cost import build_metadata, record_metadata_usage

        metadata = build_metadata(resp.usage)
        db = SessionLocal()
        try:
            record_metadata_usage(db, metadata, user_id)
        finally:
            db.close()

        return {
            "content": resp.content,
            "model": resp.usage.model,
            "stop_reason": resp.stop_reason,
            "usage": {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "cache_read_input_tokens": resp.usage.cache_read_input_tokens,
                "cache_creation_input_tokens": resp.usage.cache_creation_input_tokens,
            },
        }


class RoutesNamespace:
    """REST 路由挂载：插件路由统一隔离在 /api/plugins/<插件id>/ 命名空间下。

    auth=True（默认）经宿主同一认证机制（JWT / AUTH_TOKEN / 开放本机模式）保护，
    未认证 401 与宿主端点语义一致；auth=False 用于公开 webhook 等场景（需自行验签，
    见 API.md 开发规范 §安全）。
    """

    def __init__(self, app: FastAPI, plugin_id: str, status: PluginStatus, rollbacks: list) -> None:
        self._app = app
        self._pid = plugin_id
        self._status = status
        self._rollbacks = rollbacks

    def mount(self, router: APIRouter, *, auth: bool = True, prefix: str = "") -> str:
        """挂载 APIRouter，返回实际前缀 /api/plugins/<pid>[prefix]（幂等追加，可多次挂载）。

        路由自动带 OpenAPI 标签 `plugin:<id>`（/docs 中按插件分组，便于发现与调试）。
        """
        from fastapi import Depends

        from app.api.deps import require_auth

        if prefix and not prefix.startswith("/"):
            raise ValueError("prefix 需以 / 开头")
        full = f"/api/plugins/{self._pid}{prefix}"
        dependencies = [Depends(require_auth)] if auth else []
        # FastAPI 0.139 起 include_router 惰性挂载（路由表条目为 _IncludedRouter 包装，
        # 无 .path 属性）——回滚不能按路径前缀过滤，改为记录本次挂载追加的对象按身份摘除
        before = len(self._app.router.routes)
        self._app.include_router(
            router, prefix=full, dependencies=dependencies, tags=[f"plugin:{self._pid}"]
        )
        added = tuple(self._app.router.routes[before:])
        self._status.routes.append(full)

        def _unmount(added: tuple = added) -> None:
            # iter 247：register 中途失败的回滚——按对象身份摘除本次挂载加入的条目
            #（不用 ==：_IncludedRouter 是 dataclass，字段相等会误删他人的同构挂载）
            self._app.router.routes[:] = [
                r for r in self._app.router.routes if all(r is not a for a in added)
            ]

        self._rollbacks.append(_unmount)
        return full

    @property
    def base_path(self) -> str:
        """本插件路由命名空间前缀（拼接绝对 URL 用）。"""
        return f"/api/plugins/{self._pid}"


class EventsNamespace:
    """事件订阅/发布（总线语义与隔离保证见 app/plugins/bus.py 模块头）。"""

    def __init__(self, status: PluginStatus, rollbacks: list) -> None:
        self._status = status
        self._rollbacks = rollbacks

    def on(self, event: str, fn: Any) -> Any:
        """订阅事件，返回退订函数。监听器可为同步函数或协程函数。"""
        self._status.events.append(event)
        off = bus.on(event, fn)
        self._rollbacks.append(off)  # iter 247：register 失败回滚退订
        return off

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """发布自定义事件（建议以插件 id 作命名空间，如 "myplugin.xxx"）。"""
        bus.emit(event, payload)


class SettingsView:
    """只读安全配置视图：名称含 key/secret/password/token 的字段一律不暴露。

    插件与宿主同进程运行、本身即受信代码（可直接 import app.core.settings），
    本视图只是防「顺手误读机密并外发」的便利层，不是安全边界。
    """

    _DENY = ("key", "secret", "password", "token")

    def get(self, name: str, default: Any = None) -> Any:
        from app.core.settings import settings

        if any(d in name.lower() for d in self._DENY):
            return default
        return getattr(settings, name, default)

    @property
    def data_dir(self) -> str:
        from app.core.settings import settings

        return settings.data_dir

    @property
    def plugins_dir(self) -> str:
        from app.core.settings import settings

        return settings.plugins_dir


class DepsNamespace:
    """宿主 FastAPI 依赖的 stable 引用（路由 DI 用）。

    插件经 Depends(api.deps.get_current_user) 等方式获得与宿主一致的认证/数据
    归属语义，而不 import 宿主内部路径（内部重构不破坏插件）。
    """

    @property
    def get_db(self) -> Any:
        from app.api.deps import get_db

        return get_db

    @property
    def get_current_user(self) -> Any:
        from app.api.deps import get_current_user

        return get_current_user

    @property
    def require_auth(self) -> Any:
        from app.api.deps import require_auth

        return require_auth

    @property
    def require_admin(self) -> Any:
        from app.api.deps import require_admin

        return require_admin

    @property
    def get_owned_session(self) -> Any:
        from app.api.deps import get_owned_session

        return get_owned_session

    @property
    def get_owned_plan(self) -> Any:
        from app.api.deps import get_owned_plan

        return get_owned_plan


class BackendPluginAPI:
    """register(api) 收到的门面对象（属性一览见 API.md §api 门面）。"""

    def __init__(self, app: FastAPI, plugin_id: str, data_dir: Path, status: PluginStatus) -> None:
        self.version = API_VERSION
        self.id = plugin_id
        self.log = logging.getLogger(f"deep_tutor.plugin.{plugin_id}")
        self.data_dir = data_dir  # 插件私有数据目录（已自动创建），持久化插件自身状态用
        # iter 247：register 中途失败的注册回滚台账——此前失败仅记 state=failed，
        # 但失败前已挂载的路由/已订阅的事件/已注册的工具仍然存活，状态台账与
        # 真实运行态不一致（坏插件代码继续在宿主内执行）；loader 在 register
        # 异常时调用 _rollback() 逆序撤销
        self._rollbacks: list = []
        self.tools = ToolsNamespace(status, self._rollbacks)
        self.parsers = ParsersNamespace(plugin_id, status, self._rollbacks)
        self.exporters = ExportersNamespace(plugin_id, status, self._rollbacks)
        self.llm = LlmNamespace(plugin_id)
        self.routes = RoutesNamespace(app, plugin_id, status, self._rollbacks)
        self.events = EventsNamespace(status, self._rollbacks)
        self.settings = SettingsView()
        self.deps = DepsNamespace()

    def _rollback(self) -> None:
        """逆序撤销 register 期间的全部注册（路由/事件/工具）；供 loader 在
        register 抛异常时调用。各回滚回调自身异常隔离（尽力而为，不掩盖原异常）。"""
        for rb in reversed(self._rollbacks):
            try:
                rb()
            except Exception:  # noqa: BLE001
                self.log.exception("插件注册回滚失败（忽略，继续其余回滚）")
        self._rollbacks.clear()
