"""后端插件 API 门面——插件 register(api) 收到的对象（契约见 API.md「后端插件 API」）。

设计原则：插件只依赖本门面，不 import 宿主内部模块路径——宿主内部重构/模块搬家
不破坏已发布插件。门面语义化版本（api.version）只增不改；新增能力以新命名空间/
新方法追加，既有方法签名与行为保持向后兼容。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from app.plugins import bus, exporters, parsers, providers, runtime

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from app.agent.registry import Tool
    from app.plugins.runtime import PluginStatus

# 后端插件 API 语义化版本（与前端 window.DeepTutor 版本相互独立，见 API.md §版本线）
# v1.3.0：新增 api.parsers 文件解析扩展点（插件为私有/小众格式注册解析器）。
# v1.4.0：新增 api.exporters 导出格式扩展点（插件为私有/小众格式注册导出器）。
# v1.5.0：新增 api.llm 宿主 LLM 能力调用（插件复用用户已配置的供应商，预算守卫+计量同宿主）。
# v1.6.0：新增 api.providers LLM Provider 扩展点（插件注册新协议 Provider，api.llm.complete
#         经 provider_kind= 分发，预算守卫+计量同宿主——非 Anthropic 协议不再须 DIY httpx 绕开预算）。
# v1.7.0：新增 api.tools.call 工具调用（插件调用任意已注册工具——核心 parse/search/quiz/understand
#         等 + 插件工具，组合成"新工作流"，无需重实现解析/检索/出题；按用户解析 provider+预算+计量）。
# v1.8.0：新增 api.tools.list 工具发现（列出全部已注册工具元数据 name/llm/requires/description，
#         含核心 14 工具 + 插件工具）+ GET /api/tools 发现端点——插件/外部客户端动态发现可组合工具。
# v1.9.0：新增 api.mcp MCP 客户端（连接外部 MCP server Streamable HTTP，list_tools/call_tool）——
#         "扩展后端类 Claude Code 可以调用的工具和 MCP"（用户点名）。插件据此接入任意外部 MCP server 工具。
# v1.10.0：新增 api.scheduler 后台任务（every 周期 / once 延迟）——插件可做定时同步/每日摘要/周期轮询
#         （此前只能路由触发/事件触发，无周期任务能力）。fn async/sync，错误隔离，reset 统一取消。
# v1.11.0：新增 api.storage 持久化 KV（每插件私有 JSON，原子写 tmp+rename，JSON 序列化校验）——
#         数据密集型插件的结构化持久化（替代裸 data_dir 文件 I/O 的并发损坏风险）。
# v1.12.0：api.mcp 加 connect_stdio——MCP stdio 传输（spawn 子进程 server，stdin/stdout JSON-RPC）。
#         stdio 是 MCP server 常见部署，补齐 iter 319 HTTP-only 的实用闭环。
# v1.13.0：Tool.description 字段 + register_python(description=)——插件工具可自带一行描述，
#         api.tools.list / GET /api/tools 发现视图展示（核心工具回退 TOOL_DESCRIPTIONS）。
# v1.14.0：api.llm.stream 流式补全——逐 token yield 文本增量（Anthropic 真流式 / 插件 Provider
#         回退非流式），日预算守卫 + api_usage 计量同 complete。
# v1.15.0：api.webhooks.register——事件→外部 URL 非阻塞推送（bus 协程监听器 + httpx + HMAC 签名），
#         一行替代 events.on+httpx+签名 ~20 行模板。
# v1.16.0：api.webhooks.register(events=None) 全事件支持（subscribe_all sync sink + create_task）；
#         新增 4 个 widget 插槽（learn/notes/usage/wiki）。
# v1.17.0：新增 api.middleware——插件注册 HTTP 中间件（全请求拦截：限流/IP/审计/安全头），
#         用户 /goal "后端可引入额外安全防护插件"；BaseHTTPMiddleware + error isolation + rollback。
# v1.18.0：后端插件热重启——unload/reload/enable/disable + REST 端点 + 持久化禁用状态。
#         无需重启服务器即可在 /plugins 页热管理插件。
API_VERSION = "1.20.0"


class ToolsNamespace:
    """Agent 工具注册与调用：插件工具汇入每次请求新建的工具注册表（default_registry）。

    注意：内置生成 PIPELINE 步骤固定，不会自动调用新工具——新工具主要供插件路由/
    其他插件经注册表显式调用（v1.7.0 起 call 可调用任意已注册工具，含核心 parse/search/
    quiz/understand 等，组合成"新工作流"，无需重实现）；与核心工具同名会覆盖核心实现
    （注册表告警），属高级用法，见 API.md 开发规范。
    """

    def __init__(self, plugin_id: str, status: PluginStatus, rollbacks: list) -> None:
        self._pid = plugin_id
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

    def register_python(self, name: str, func: Any, *, description: str = "") -> None:
        """便捷注册 Python 工具：async (inp, ctx) -> dict（data 字典）。

        inp/ctx 契约与宿主工具一致（app/tools/base.py 的 ToolInput；ctx 含 db/registry 等）。
        description（v1.13.0）：人类可读一行描述，在 api.tools.list / GET /api/tools 发现视图展示。
        """
        from app.agent.registry import Tool

        self.register(Tool(name=name, llm=False, func=func, description=description))

    def list(self) -> list[dict[str, Any]]:
        """发现全部已注册工具的元数据（v1.8.0）：``[{name, llm, requires, description}, ...]``。

        含核心工具（parse_material/understand_intent/build_knowledge_map/design_stages/
        search_resources/verify_url/schedule_plan/validate_plan/export_plan/
        build_plan_slides/generate_study_materials/generate_unit_content/generate_quiz/
        kb_answer）+ 插件工具。插件据此**动态组合工作流**（先 list 探查可用工具再 call），
        无需硬编码工具名/查文档；``llm`` 标志哪些工具耗 LLM（预算意识），``requires`` 标上游依赖。
        """
        from app.tools import default_registry, tool_metadata

        return tool_metadata(default_registry())

    async def call(
        self,
        name: str,
        args: dict[str, Any],
        *,
        user_id: int,
        upstream: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调用任意已注册工具（核心或插件工具，v1.7.0），返回结果信封。

        返回 ``{"status": "ok"|"repaired"|"error", "data": {...}, "errors": [...]}``。
        插件据此组合**新工作流**——用 parse_material 解析 / search_resources 检索 /
        generate_quiz 出题 / understand_intent 理解 等，无需重实现。

        - ``args``：工具入参（按该工具契约，如 parse_material 的 {kind, source}）。
        - ``upstream``：上游工具输出（按需，如 build_knowledge_map 需要 intent/parsed）。
        - LLM 工具按 ``user_id`` 解析供应商 + 日预算守卫 + ``api_usage`` 计量（同生成口径）；
          用户未配置供应商 -> ``ApiError(no_llm_provider, 503)``。
        - 未知工具 -> ``ValueError``；工具执行失败 -> 信封 ``status="error"``（不抛，交插件处理）。
        """
        import uuid

        from app.agent.context import ExecContext
        from app.agent.cost import record_metadata_usage
        from app.agent.loop import run_tool
        from app.api.deps import check_daily_budget
        from app.api.errors import ApiError
        from app.core.credentials import resolve_bing_api_key
        from app.db.base import SessionLocal
        from app.llm import get_provider
        from app.skills.loader import get_skill_registry
        from app.tools import default_registry
        from app.tools.base import ToolInput

        if not isinstance(name, str) or not name:
            raise ValueError("name 必须是非空字符串")
        if not isinstance(args, dict):
            raise TypeError("args 必须是 dict")
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id 必须是正整数")

        registry = default_registry()
        if not registry.has(name):
            raise ValueError(f"未注册的工具: {name}")
        tool = registry.get(name)

        provider = None
        if tool.llm:
            try:
                provider = get_provider(user_id)
            except Exception as exc:  # noqa: BLE001
                raise ApiError("no_llm_provider", f"用户 {user_id} 未配置 LLM 供应商") from exc
            check_daily_budget(None, user_id)  # 日预算守卫（同生成/api.llm 端点）

        sid = f"plugin_call:{self._pid}:{uuid.uuid4().hex[:8]}"
        tid = f"pt-{uuid.uuid4().hex[:12]}"
        ctx = ExecContext(
            session_id=sid,
            trace_id=tid,
            registry=registry,
            llm_provider=provider,
            skill_registry=get_skill_registry() or None,  # 空 -> loop 走 inline_prompt/默认
            bing_api_key=resolve_bing_api_key(user_id),  # search_resources 等按用户解析
        )
        inp = ToolInput(tool=name, session_id=sid, args=args, upstream=upstream or {})
        out = await run_tool(name, inp, ctx)

        # 用量计量（LLM 工具有 cost；Python 工具 metadata 空 -> record 内部容错不记）
        if tool.llm:
            db = SessionLocal()
            try:
                await asyncio.to_thread(record_metadata_usage, db, out.metadata, user_id)  # iter 370：commit 出循环
            finally:
                db.close()

        return {
            "status": out.status,
            "data": out.data,
            "errors": [e.model_dump() for e in out.errors],
        }


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
        provider_kind: str | None = None,
    ) -> dict:
        """以该用户的供应商配置发起一次 LLM 补全。

        返回 dict：{"content": str, "model": str, "stop_reason": str | None,
        "usage": {"input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens"}}（只增不改）。max_tokens 收敛到 [1, 8192]。

        provider_kind（v1.6.0）：指定时改为分发到该 kind 的**插件 Provider**（api.providers
        注册的新协议 handler，如 'openai'、'ollama'）——凭证由插件自理，日预算守卫 + api_usage
        计量与默认路径同口径（非 Anthropic 协议不再须 DIY httpx 绕开预算/计量）。未知 kind ->
        no_llm_provider。
        """
        from app.api.deps import check_daily_budget
        from app.api.errors import ApiError
        from app.db.base import SessionLocal
        from app.llm.base import TransientLLMError

        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id 必须是正整数")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt 必须是非空字符串")
        if not isinstance(system, str):
            raise TypeError("system 必须是字符串")
        max_tokens = max(1, min(int(max_tokens), self._MAX_TOKENS_CAP))

        if provider_kind is not None:
            from app.llm.plugin_provider import PluginProvider

            from app.plugins import providers as plugin_providers

            entry = plugin_providers.find_provider(provider_kind)
            if entry is None:
                raise ApiError("no_llm_provider", f"未注册的 provider kind: {provider_kind}")
            check_daily_budget(None, user_id)  # 日预算守卫同默认路径
            provider = PluginProvider(entry, model=entry.default_model or "")
        else:
            from app.llm import get_provider

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

        metadata = build_metadata(resp.usage, provider=getattr(provider, "kind", "anthropic"))  # iter 370
        db = SessionLocal()
        try:
            await asyncio.to_thread(record_metadata_usage, db, metadata, user_id)  # iter 370：commit 出循环
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

    async def stream(
        self,
        user_id: int,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 2048,
    ):  # -> AsyncIterator[str]
        """流式补全（v1.14.0）：逐 token yield 文本增量——插件做 chat / 实时标注等 live-typing UI。

        与 complete 同口径：按 user_id 解析供应商 + 日预算守卫 + api_usage 计量（usage 在流末尾
        摄取）。Anthropic 真流式（逐 token）；插件 Provider 回退非流式（一次性 yield 全文）。
        错误映射同 complete（no_llm_provider/server_busy/bad_gateway）。max_tokens 收敛 [1, 8192]。

        用法：``async for text in api.llm.stream(user.id, "总结这段", system="你是助教"): ...``
        """
        from app.agent.cost import build_metadata, record_metadata_usage
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
        check_daily_budget(None, user_id)

        captured_usage = None
        try:
            async for chunk in provider.stream(system, prompt, max_tokens=max_tokens):
                if chunk.usage is not None:
                    captured_usage = chunk.usage
                if chunk.text:
                    yield chunk.text
        except TransientLLMError as exc:
            raise ApiError("server_busy", f"LLM 服务暂时不可用（可退避重试）: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiError("bad_gateway", f"LLM 流式调用失败: {exc}") from exc
        finally:
            if captured_usage is not None:
                metadata = build_metadata(captured_usage, provider=getattr(provider, "kind", "anthropic"))  # iter 370
                db = SessionLocal()
                try:
                    await asyncio.to_thread(record_metadata_usage, db, metadata, user_id)  # iter 370：commit 出循环
                finally:
                    db.close()


class ProvidersNamespace:
    """LLM Provider 扩展点（v1.6.0 扩展点，契约见 API.md「§LLM Provider 扩展点」）。

    插件为内置仅支持 Anthropic 协议的 LLM 层注册**新协议 Provider**（OpenAI
    /v1/chat/completions、Ollama、DeepSeek 原生、Gemini 等），经
    ``api.llm.complete(..., provider_kind=...)`` 调用——带宿主日预算守卫 + api_usage 计量
    （与宿主端点同口径），无需 DIY httpx 绕开预算/计量（"暗花费"）。

    handler 契约（async 或 sync，签名相同）：
        (system: str, user: str, *, model: str, max_tokens: int) -> dict
    返回 dict：{"content": str, "model": str, "stop_reason"?: str,
    "usage": {"input_tokens": int, "output_tokens": int,
    "cache_read_input_tokens"?: int, "cache_creation_input_tokens"?: int}}。
    凭证由插件自理（handler 不接收 api_key/base_url——从 api.data_dir/环境变量/自有设置读写）。

    异常约定：handler 抛异常 -> 经分类——在异常上设 ``transient = True``（或抛常见网络瞬态
    类型）标记瞬态，宿主据此退避重试（api.llm -> server_busy 503）；其余非瞬态（含 4xx/契约
    不合规）-> bad_gateway 502。

    安全默认：'anthropic' 为内置保留协议名，注册时拒绝（ValueError）——插件请注册新协议名。
    """

    def __init__(self, plugin_id: str, status: "PluginStatus", rollbacks: list) -> None:
        self._pid = plugin_id
        self._status = status
        self._rollbacks = rollbacks

    def register(
        self,
        kind: str,
        handler: Callable,
        *,
        default_model: str = "",
        label: str = "",
    ) -> None:
        """注册 LLM Provider。

        - kind：协议名（如 "openai"、"ollama"）；自动规范化为小写，字符集 [a-z0-9][a-z0-9_-]{0,31}；
          'anthropic' 为内置保留，注册拒绝。
        - handler：补全函数，async 或 sync，签名 (system, user, *, model, max_tokens) -> dict（见类 docstring）。
        - default_model：该 kind 的默认模型名（api.llm 未显式传 model 时用），可选。
        - label：人类可读协议名（GET /providers/kinds 展示用），可选。
        """
        if not callable(handler):
            raise TypeError(
                "handler 必须是 callable（async 或 sync，签名 (system, user, *, model, max_tokens) -> dict）"
            )
        norm = providers.normalize_kind(kind)
        if not providers.valid_kind_name(norm):
            raise ValueError(f"provider kind 非法: {kind!r}（需匹配 [a-z0-9][a-z0-9_-]{{0,31}}）")
        entry = providers.ProviderEntry(
            plugin_id=self._pid,
            kind=norm,
            handler=handler,
            default_model=default_model or "",
            label=label or "",
        )
        providers.register(entry)  # 'anthropic' 在此抛 ValueError
        self._status.providers.append(norm)
        self._rollbacks.append(lambda entry=entry: providers.unregister(entry))

    def kinds(self) -> list[str]:
        """本插件已注册的协议名（调试/状态展示用）。"""
        return [e.kind for e in providers.all_entries() if e.plugin_id == self._pid]


class McpNamespace:
    """MCP（Model Context Protocol）客户端（v1.9.0 扩展点，契约见 API.md「§MCP 客户端」）。

    连接外部 MCP server（Streamable HTTP 传输）——列出并调用其工具，把任意外部工具 server
    接入后端（"扩展后端类 Claude Code 可以调用的工具和 MCP"，用户点名需求）。插件据此在自有
    路由调用 MCP server 工具，或包成宿主工具经 api.tools.call/list 复用。

    用法：``client = await api.mcp.connect(url, headers={...})`` -> 已 initialize 的客户端；
    ``await client.list_tools()`` / ``await client.call_tool(name, arguments)``；用完 ``await client.close()``。

    安全：客户端仅插件显式调用（非热路径，server 故障错误隔离在插件调用内，不影响宿主稳定）；
    出站到可信 URL（admin/插件配置，同供应商 base_url 信任模型）；httpx 超时；JSON-RPC error -> RuntimeError。
    范围（v1）：Streamable HTTP 传输；stdio（子进程）+ 真实 server 互操作验证为后续轮。
    """

    def __init__(self, plugin_id: str) -> None:
        self._pid = plugin_id

    async def connect(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        """连接 MCP server（Streamable HTTP）：创建客户端 + initialize 握手，返回就绪的客户端。

        - url：MCP server 的 HTTP 端点（http(s)://；可信配置——server 可在 localhost）。
        - headers：额外请求头（如 Authorization），可选。
        - timeout：每次请求超时秒（缺省 30）。
        返回 McpHttpClient（list_tools / call_tool / close）。握手失败 -> RuntimeError。
        """
        if not isinstance(url, str) or not url.strip():
            raise ValueError("url 必须是非空字符串")
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("MCP server url 须以 http:// 或 https:// 开头")
        from app.plugins.mcp import McpHttpClient

        client = McpHttpClient(url, headers=headers, timeout=timeout)
        await client.initialize()  # 握手失败抛 RuntimeError（插件处理）
        return client

    async def connect_stdio(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        read_timeout: float = 30.0,
    ) -> Any:
        """连接 MCP server（**stdio 传输**，v1.12.0）：spawn 子进程 + initialize 握手，返回就绪客户端。

        stdio 是 MCP server 的常见部署（Claude Code 启动本地子进程 server）。与 connect()（HTTP）同接口
        （list_tools / call_tool / close）。

        - command：启动 server 的 argv（如 ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/data"]），
          须非空字符串列表；可信配置（同插件代码信任——宿主执行之）。
        - env：子进程环境变量（可选；默认继承宿主环境）。
        - read_timeout：每次读响应超时秒（防 server 挂起）。
        返回 McpStdioClient。握手失败 -> RuntimeError；用完须 close()（terminate 子进程）。
        """
        if not isinstance(command, (list, tuple)) or not command or not all(isinstance(c, str) for c in command):
            raise ValueError("command 必须是非空字符串列表（argv）")
        from app.plugins.mcp import McpStdioClient

        client = McpStdioClient(list(command), env=env, read_timeout=read_timeout)
        await client.initialize()  # spawn + 握手；失败抛 RuntimeError（插件处理）
        return client


class SchedulerNamespace:
    """后台任务调度（v1.10.0 扩展点，契约见 API.md「§后台任务调度」）。

    插件注册周期/延迟后台任务——every(周期) / once(延迟)。填补"插件无法做周期工作"的缺口
    （定时同步 / 每日摘要 / 周期轮询 MCP 等；此前只有路由触发与事件触发）。

    任务跑在宿主事件循环；fn 可 async（await）或 sync（须非阻塞，重活 fn 内 to_thread）。
    fn 异常隔离（记日志不杀循环）。返回 TaskHandle.cancel() 单独取消；插件卸载（reset_plugins）
    统一取消全部。任务不持久化——进程重启即失（插件 register/startup 重新注册）。
    须在运行中的事件循环内调用（register 期间 / async 路由内）。
    """

    def __init__(self, plugin_id: str, rollbacks: list) -> None:
        self._pid = plugin_id
        self._rollbacks = rollbacks

    def every(self, interval_s: float, fn: Callable, *, run_immediately: bool = False) -> Any:
        """注册周期任务：每 interval_s 秒跑一次 fn（须 > 0）。返回 TaskHandle（cancel 取消）。"""
        from app.plugins import scheduler

        handle = scheduler.every(interval_s, fn, run_immediately=run_immediately)
        self._rollbacks.append(handle.cancel)  # register 中途失败回滚：取消该任务
        return handle

    def once(self, delay_s: float, fn: Callable) -> Any:
        """注册一次性延迟任务：delay_s 秒后跑一次 fn（须 >= 0）。返回 TaskHandle（cancel 取消则不跑）。"""
        from app.plugins import scheduler

        handle = scheduler.once(delay_s, fn)
        self._rollbacks.append(handle.cancel)
        return handle


class StorageNamespace:
    """持久化 KV 存储（v1.11.0 扩展点，契约见 API.md「§持久化 KV 存储」）。

    每插件私有键值存储（JSON 文件于插件 data_dir），替代裸 data_dir 文件 I/O——提供原子写
    （tmp + os.replace，防并发请求损坏）、JSON 序列化校验、损坏容错（损坏文件静默重置）与统一 API。
    数据密集型插件（习惯追踪 / 分析缓存 / 自定义实体）的结构化持久化。

    值须 JSON 可序列化（str/int/float/bool/list/dict/None）；非可序列化 -> ValueError。
    键须字符串。懒加载（首次访问读盘）+ 内存缓存。按插件 data_dir 天然命名空间隔离。
    """

    def __init__(self, data_dir: Path) -> None:
        self._path = Path(data_dir) / "storage.json"
        self._data: dict[str, Any] = {}
        self._loaded = False
        # iter 369：实例级锁——sync 插件路由跑 anyio 线程池、事件监听器经 bus._guard_to_thread
        # 也在线程池；并发 get/set 的读-改-写会交错丢更新，两线程并发 _save 写同一固定
        # .tmp 名会交错损坏（Linux 下 replace 后对旧 inode 继续写 / Windows 下
        # PermissionError 逃出 set()），损坏 JSON 下次启动被静默重置 {}——插件全部数据丢失
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self._path.is_file():
                raw = self._path.read_text(encoding="utf-8")
                obj = json.loads(raw) if raw.strip() else {}
                if isinstance(obj, dict):
                    self._data = obj
        except (OSError, ValueError):  # noqa: BLE001  损坏/不可读 -> 静默重置（不抛、不丢功能）
            self._data = {}

    def _save(self) -> None:
        """原子写：唯一 tmp 文件 + os.replace（防并发写中途崩溃留下半写文件；
        调用方持 _lock）。tmp 名带 uuid 防两线程写同一中间路径。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        finally:
            # 异常路径（写失败/replace 失败）不留 tmp 残留
            try:
                tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def _check_key(key: str) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("key 必须是非空字符串")

    @staticmethod
    def _check_value(value: Any) -> None:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"value 非 JSON 可序列化: {exc}") from exc

    def get(self, key: str, default: Any = None) -> Any:
        self._check_key(key)
        with self._lock:
            self._ensure_loaded()
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._check_key(key)
        self._check_value(value)
        with self._lock:
            self._ensure_loaded()
            self._data[key] = value
            self._save()

    def delete(self, key: str) -> bool:
        """删除键；返回是否确实删除了一个。"""
        self._check_key(key)
        with self._lock:
            self._ensure_loaded()
            if key in self._data:
                del self._data[key]
                self._save()
                return True
            return False

    def has(self, key: str) -> bool:
        self._check_key(key)
        with self._lock:
            self._ensure_loaded()
            return key in self._data

    def keys(self) -> list[str]:
        with self._lock:
            self._ensure_loaded()
            return list(self._data.keys())

    def clear(self) -> None:
        """清空本插件全部 KV。"""
        with self._lock:
            self._ensure_loaded()
            self._data = {}
            self._save()


class WebhooksNamespace:
    """事件→外部 URL 投递（v1.15.0 扩展点，契约见 API.md「§Webhook 投递」）。

    一行调用 ``api.webhooks.register(url, events=['plan_created'], secret='xxx')`` 即把指定事件
    非阻塞推送到外部 Webhook（Slack/Discord/自动化）——标准载荷 + 可选 HMAC-SHA256 签名 + 10s 超时 +
    错误隔离。替代插件自行 events.on + httpx + 签名 + 错误处理 ~20 行模板。

    bus 协程监听器自动非阻塞 dispatch（create_task + _guard_await）；bus.reset（reset_plugins）清除
    全部监听器——webhook 监听器随之清理，无需单独 reset。
    """

    def __init__(self, plugin_id: str, status: "PluginStatus", rollbacks: list) -> None:
        self._pid = plugin_id
        self._status = status
        self._rollbacks = rollbacks

    def register(
        self,
        url: str,
        events: list[str] | None = None,
        *,
        secret: str = "",
    ) -> Any:
        """注册 Webhook：指定事件触发时非阻塞 POST 到 url。

        - url：外部 Webhook URL（http(s)://；可信配置——同插件代码信任）。
        - events：要推送的事件名列表（如 ['plan_created', 'progress_updated']）。
          None（v1.16.0）= 全部事件（经 subscribe_all sync sink + create_task）。
        - secret：HMAC-SHA256 签名密钥（可选；非空时加 X-Webhook-Signature 头）。
        返回 WebhookHandle（unregister 停止推送）。
        """
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            raise ValueError("url 须以 http:// 或 https:// 开头")
        from app.plugins import bus, webhooks

        offs: list[Callable] = []
        if events is None:
            # v1.16.0：全事件（subscribe_all sync sink → create_task 非阻塞投递）
            sink = webhooks.make_all_events_sink(url, secret=secret)
            off = bus.subscribe_all(sink)
            offs.append(off)
        else:
            if not isinstance(events, (list, tuple)) or not events or not all(isinstance(e, str) and e for e in events):
                raise ValueError("events 为非 None 时必须是非空字符串列表")
            for event_name in events:
                listener = webhooks.make_listener(url, event_name, secret=secret)
                off = bus.on(event_name, listener)
                offs.append(off)

        self._status.webhooks.append(url)

        class _Handle:
            def __init__(self_inner) -> None:
                self_inner._done = False

            def unregister(self_inner) -> None:
                if self_inner._done:
                    return
                self_inner._done = True
                for off in offs:
                    off()
                # iter 369：同步清台账与回滚闭包——插件改配置（unregister 旧 handle 再
                # register 新）时，_rollbacks 与 status.webhooks 此前单调增长（每次配置
                # 变更多一条失效闭包 + 一条重复 URL）
                if url in self._status.webhooks:
                    self._status.webhooks.remove(url)
                try:
                    self._rollbacks.remove(handle.unregister)
                except ValueError:
                    pass

        handle = _Handle()
        self._rollbacks.append(handle.unregister)
        return handle


class MiddlewareNamespace:
    """HTTP 中间件注册（v1.17.0 扩展点，契约见 API.md「§HTTP 中间件」）。

    插件注册全局请求中间件——全请求拦截（限流 / IP 过滤 / 审计日志 / 安全响应头 / 内容过滤）。
    用户 /goal "后端可引入额外安全防护插件"。

    fn 签名：``async (request: Request, call_next) -> Response``（Starlette BaseHTTPMiddleware dispatch 语义）。
    **错误隔离**：fn 异常 -> 透传原请求（不阻断全部端点，同事件监听器隔离语义）。
    中间件在插件 register() 期间添加（lifespan startup 内，首次 HTTP 请求前——build_middleware_stack 仅包路由器，无 finalization）。
    """

    def __init__(self, app: "FastAPI", plugin_id: str, status: "PluginStatus", rollbacks: list) -> None:
        self._app = app
        self._pid = plugin_id
        self._status = status
        self._rollbacks = rollbacks

    def register(self, fn: Callable) -> None:
        """注册 HTTP 中间件。fn: async (request, call_next) -> Response。

        - fn 异常 -> 透传原请求（不阻断——记日志后 call_next(request)）。
        - 多次注册叠加（LIFO 顺序：后注册先执行，同 Starlette user_middleware insert(0)）。
        """
        if not callable(fn):
            raise TypeError("fn 必须是 callable（async (request, call_next) -> Response）")
        from starlette.middleware import Middleware
        from starlette.middleware.base import BaseHTTPMiddleware

        pid = self._pid
        plugin_log = logging.getLogger(f"deep_tutor.plugin.{pid}")

        async def _safe_dispatch(request, call_next):
            try:
                return await fn(request, call_next)
            except Exception:  # noqa: BLE001  中间件异常隔离：透传不阻断
                plugin_log.exception("插件中间件异常 -> 透传原请求")
                return await call_next(request)

        class _PluginMW(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                return await _safe_dispatch(request, call_next)

        entry = Middleware(_PluginMW)
        self._app.user_middleware.insert(0, entry)
        self._app.middleware_stack = self._app.build_middleware_stack()
        self._status.middleware.append(pid)

        def _rollback() -> None:
            try:
                self._app.user_middleware.remove(entry)
                self._app.middleware_stack = self._app.build_middleware_stack()
            except (ValueError, Exception):  # noqa: BLE001  rollback 尽力而为
                pass

        self._rollbacks.append(_rollback)


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
        self._app.include_router(router, prefix=full, dependencies=dependencies, tags=[f"plugin:{self._pid}"])
        added = tuple(self._app.router.routes[before:])
        self._status.routes.append(full)

        def _unmount(added: tuple = added) -> None:
            # iter 247：register 中途失败的回滚——按对象身份摘除本次挂载加入的条目
            # （不用 ==：_IncludedRouter 是 dataclass，字段相等会误删他人的同构挂载）
            self._app.router.routes[:] = [r for r in self._app.router.routes if all(r is not a for a in added)]

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
    """只读安全配置视图：名称含 key/secret/password/token/dsn/conn/url 的字段一律不暴露。

    插件与宿主同进程运行、本身即受信代码（可直接 import app.core.settings），
    本视图只是防「顺手误读机密并外发」的便利层，不是安全边界。
    iter 368：_DENY 补 url/dsn/conn——内网服务地址（SMB/数据库 DSN/内部 base_url）
    对「可被诱导外发的插件」同样是敏感侦察信息。
    """

    _DENY = ("key", "secret", "password", "token", "dsn", "conn", "url")

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


class MemoryNamespace:
    """记忆系统 CRUD 门面（v1.19.0，契约见 API.md「§记忆系统」）。

    让后端插件对**会话（Thread）/消息（Message）/记忆库（Memory）**做完整增删查改——
    运行于宿主进程内（无 HTTP 往返），所有操作按 ``user_id`` 归属校验（他人资源返 None/空，
    不泄露存在性）。插件通常从路由 ``Depends(api.deps.get_current_user)`` 取 user_id。

    Agent 对话 run 本身经 REST ``POST /api/threads/{id}/runs``（SSE）触发；插件若需启动 run
    可挂自有路由转发，或用 ``api.tools.call`` 调用已注册的 agent 工具（workspace_*/memory_* 等）。
    """

    @staticmethod
    def _db():
        from app.db.base import SessionLocal

        return SessionLocal()

    def list_threads(self, user_id: int, *, limit: int = 50) -> list[dict]:
        from app.db import models

        limit = max(1, min(int(limit), 500))  # iter 363：上界防无界查询拖垮宿主
        db = self._db()
        try:
            rows = (
                db.query(models.Thread)
                .filter_by(user_id=user_id)
                .order_by(models.Thread.updated_at.desc())
                .limit(limit)
                .all()
            )
            return [{"id": t.id, "title": t.title, "kind": t.kind, "session_id": t.session_id} for t in rows]
        finally:
            db.close()

    _MAX_THREADS_PER_USER = 500  # 与 REST /threads 配额一致（iter 370 对齐）

    def create_thread(
        self,
        user_id: int,
        *,
        title: str = "",
        kind: str = "chat",
        session_id: int | None = None,
        model: str = "",
    ) -> dict:
        from app.db import models

        # iter 370：与 REST /threads 硬化对齐——kind 白名单/长度上限/配额/频控/session 归属，
        # 防插件以门面身份绕过校验写入越权或超长数据（String 列不限长，超长 title 随列表全量回传）
        from app.core.ratelimit import limiter

        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id 必须是正整数")
        if kind not in ("chat", "plan_generation"):
            raise ValueError(f"kind 非法: {kind!r}（需 chat/plan_generation）")
        if len(title) > 256:
            raise ValueError("title 长度须 ≤256")
        if len(model) > 128:
            raise ValueError("model 长度须 ≤128")
        if not limiter.check(f"thread_write:{user_id}", 30):
            raise RuntimeError("rate_limited: thread_write")
        db = self._db()
        try:
            if db.query(models.Thread).filter_by(user_id=user_id).count() >= self._MAX_THREADS_PER_USER:
                raise RuntimeError("threads 配额已满（500/用户）")
            if session_id is not None:
                sess = db.get(models.Session, session_id)
                if sess is None or sess.user_id != user_id:
                    raise ValueError("session_id 不存在或不属于该用户")
            t = models.Thread(user_id=user_id, title=title, kind=kind, session_id=session_id, model=model)
            db.add(t)
            db.commit()
            db.refresh(t)
            return {"id": t.id, "title": t.title, "kind": t.kind}
        finally:
            db.close()

    def get_thread(self, thread_id: int, user_id: int) -> dict | None:
        from app.db import models

        db = self._db()
        try:
            t = db.get(models.Thread, thread_id)
            if t is None or t.user_id != user_id:
                return None
            return {"id": t.id, "title": t.title, "kind": t.kind, "session_id": t.session_id}
        finally:
            db.close()

    def delete_thread(self, thread_id: int, user_id: int) -> bool:
        from app.db import models

        db = self._db()
        try:
            t = db.get(models.Thread, thread_id)
            if t is None or t.user_id != user_id:
                return False
            db.delete(t)
            db.commit()
            return True
        finally:
            db.close()

    def list_messages(self, thread_id: int, user_id: int, *, limit: int = 100) -> list[dict]:
        from app.db import models

        limit = max(1, min(int(limit), 500))  # iter 363：上界防无界查询拖垮宿主
        db = self._db()
        try:
            t = db.get(models.Thread, thread_id)
            if t is None or t.user_id != user_id:
                return []
            rows = (
                db.query(models.Message)
                .filter_by(thread_id=thread_id)
                .order_by(models.Message.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {"id": m.id, "role": m.role.value, "content": m.content, "run_id": m.run_id} for m in reversed(rows)
            ]
        finally:
            db.close()

    _MAX_MESSAGE_CHARS = 200_000  # 与 REST RunIn.message 上限一致
    _MAX_MESSAGE_BLOCKS = 64

    def add_message(self, thread_id: int, user_id: int, role: str, content) -> dict | None:
        """追加一条消息（role: user/assistant/system；content 为 Anthropic block list 或纯文本）。

        iter 370：频控（message_write 60/分钟）与长度上限（纯文本 200k 字符 / block list
        ≤64 块且每块须 dict）——此前无界，失控插件可无限刷 messages 行。
        """
        from app.db import models

        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id 必须是正整数")
        from app.core.ratelimit import limiter

        if not limiter.check(f"message_write:{user_id}", 60):
            raise RuntimeError("rate_limited: message_write")
        if isinstance(content, str):
            if len(content) > self._MAX_MESSAGE_CHARS:
                raise ValueError(f"纯文本 content 须 ≤{self._MAX_MESSAGE_CHARS} 字符")
            content = [{"type": "text", "text": content}]
        elif isinstance(content, list):
            if len(content) > self._MAX_MESSAGE_BLOCKS or not all(isinstance(b, dict) for b in content):
                raise ValueError(f"content block list 须 ≤{self._MAX_MESSAGE_BLOCKS} 块且每块为 dict")
        else:
            raise ValueError("content 须为纯文本字符串或 block list")
        db = self._db()
        try:
            t = db.get(models.Thread, thread_id)
            if t is None or t.user_id != user_id:
                return None
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            m = models.Message(thread_id=thread_id, role=models.MessageRole(role), content=content)
            db.add(m)
            db.commit()
            db.refresh(m)
            return {"id": m.id, "role": m.role.value}
        finally:
            db.close()

    _MAX_MEMORIES_PER_USER = 2000  # 与 REST /memory 配额一致（iter 370 对齐）
    _MAX_MEMORY_CHARS = 20000

    def save_memory(
        self, user_id: int, content: str, *, kind: str = "note", scope: str = "user", thread_id: int | None = None
    ) -> dict:
        from app.db import models

        # iter 363：scope 校验 + thread_id 一致性——此前 scope="user" 传 thread_id 静默丢、
        # scope="thread" 缺 thread_id 仍以 scope="thread"+thread_id=None 入库（孤儿记忆，被 user
        # 级召回混入）。改为显式校验：scope="thread" 必须有 thread_id，否则报错（不静默退化）。
        if not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("user_id 必须是正整数")
        if scope not in ("user", "thread"):
            raise ValueError(f"scope 必须是 'user' 或 'thread'，得到 {scope!r}")
        if scope == "thread" and thread_id is None:
            raise ValueError("scope='thread' 需提供 thread_id")
        # iter 370：与 REST /memory 硬化对齐——长度/频控/配额；并补 thread 归属校验
        # （此前可把记忆挂到他人 thread 上，victim 的 thread 记忆列表会混入攻击者内容）
        if not isinstance(content, str) or not (1 <= len(content) <= self._MAX_MEMORY_CHARS):
            raise ValueError(f"content 须为 1-{self._MAX_MEMORY_CHARS} 字符")
        if len(kind) > 32:
            raise ValueError("kind 长度须 ≤32")
        from app.core.ratelimit import limiter

        if not limiter.check(f"memory_write:{user_id}", 60):
            raise RuntimeError("rate_limited: memory_write")
        if scope == "user":
            thread_id = None  # user 级记忆 thread_id 无意义，明确置空（不混入会话级）
        db = self._db()
        try:
            if db.query(models.Memory).filter_by(user_id=user_id).count() >= self._MAX_MEMORIES_PER_USER:
                raise RuntimeError("memories 配额已满（2000/用户）")
            if thread_id is not None:
                th = db.get(models.Thread, thread_id)
                if th is None or th.user_id != user_id:
                    raise ValueError("thread_id 不存在或不属于该用户")

            m = models.Memory(
                user_id=user_id,
                thread_id=thread_id,
                scope=scope,
                kind=kind,
                content=content,
            )
            db.add(m)
            db.commit()
            db.refresh(m)
            return {"id": m.id, "scope": m.scope}
        finally:
            db.close()

    def recall_memories(
        self, user_id: int, *, q: str = "", scope: str | None = None, thread_id: int | None = None, limit: int = 10
    ) -> list[dict]:
        from app.db import models

        limit = max(1, min(int(limit), 500))  # iter 363：上界防无界查询拖垮宿主
        db = self._db()
        try:
            qry = db.query(models.Memory).filter(models.Memory.user_id == user_id)
            if scope == "thread" and thread_id:
                qry = qry.filter(models.Memory.thread_id == thread_id)
            elif scope == "user":
                qry = qry.filter(models.Memory.thread_id.is_(None))
            if q:
                qry = qry.filter(models.Memory.content.contains(q))
            rows = qry.order_by(models.Memory.updated_at.desc()).limit(limit).all()
            return [{"id": r.id, "kind": r.kind, "scope": r.scope, "content": r.content} for r in rows]
        finally:
            db.close()

    def delete_memory(self, user_id: int, memory_id: int) -> bool:
        from app.db import models

        db = self._db()
        try:
            m = db.query(models.Memory).filter_by(id=memory_id, user_id=user_id).first()
            if m is None:
                return False
            db.delete(m)
            db.commit()
            return True
        finally:
            db.close()


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
        self.tools = ToolsNamespace(plugin_id, status, self._rollbacks)
        self.parsers = ParsersNamespace(plugin_id, status, self._rollbacks)
        self.exporters = ExportersNamespace(plugin_id, status, self._rollbacks)
        self.providers = ProvidersNamespace(plugin_id, status, self._rollbacks)
        self.llm = LlmNamespace(plugin_id)
        self.mcp = McpNamespace(plugin_id)
        self.scheduler = SchedulerNamespace(plugin_id, self._rollbacks)
        self.storage = StorageNamespace(data_dir)
        self.webhooks = WebhooksNamespace(plugin_id, status, self._rollbacks)
        self.middleware = MiddlewareNamespace(app, plugin_id, status, self._rollbacks)
        self.routes = RoutesNamespace(app, plugin_id, status, self._rollbacks)
        self.events = EventsNamespace(status, self._rollbacks)
        self.settings = SettingsView()
        self.deps = DepsNamespace()
        self.memory = MemoryNamespace()  # v1.19.0 记忆系统 CRUD 门面（threads/messages/memories）
        self.threads = self.memory  # 别名（语义等价）

    def _rollback(self) -> None:
        """逆序撤销 register 期间的全部注册（路由/事件/工具）；供 loader 在
        register 抛异常时调用。各回滚回调自身异常隔离（尽力而为，不掩盖原异常）。"""
        for rb in reversed(self._rollbacks):
            try:
                rb()
            except Exception:  # noqa: BLE001
                self.log.exception("插件注册回滚失败（忽略，继续其余回滚）")
        self._rollbacks.clear()
