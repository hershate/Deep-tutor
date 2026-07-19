"""后端插件系统测试（API.md「后端插件 API」）。

覆盖：装载（单文件/包插件/_ 前缀停用/确定性排序）、隔离（导入失败/register 异常/
缺入口/非法 id/重复 id/min_api 不兼容均记 failed 且不阻塞其余插件）、
路由挂载（默认认证/公开可选）、事件总线（订阅/退订/异常隔离/自定义事件）、
工具注册（汇入 default_registry）、状态端点（/api/plugins 管理员）、
lifespan 集成（启动加载 + startup/shutdown 事件）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

# --- 测试插件源码 ---

PING_PLUGIN = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "ping", "name": "Ping", "version": "0.1.0"}

def register(api):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"pong": True, "plugin": api.id, "api_version": api.version}

    api.routes.mount(router)
'''

PUBLIC_PLUGIN = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "webhook"}

def register(api):
    router = APIRouter()

    @router.post("/hook")
    def hook():
        return {"ok": True}

    api.routes.mount(router, auth=False)
'''

EVENT_PLUGIN = '''
import json

PLUGIN_META = {"id": "ev"}

def register(api):
    def record(payload):
        (api.data_dir / "got.json").write_text(json.dumps(payload), encoding="utf-8")

    def boom(payload):
        raise RuntimeError("监听器故意炸掉")

    api.events.on("ev.custom", boom)      # 先订阅会炸的：验证隔离不影响后订阅者
    api.events.on("ev.custom", record)
'''

TOOL_PLUGIN = '''
PLUGIN_META = {"id": "toolsy"}

def register(api):
    async def echo(inp, ctx):
        return {"echo": inp.args}

    api.tools.register_python("toolsy.echo", echo)
'''

BOOT_PLUGIN = '''
PLUGIN_META = {"id": "boot"}

def register(api):
    api.events.on("startup", lambda payload: (api.data_dir / "started.txt").write_text("1", encoding="utf-8"))
    api.events.on("shutdown", lambda payload: (api.data_dir / "stopped.txt").write_text("1", encoding="utf-8"))
'''


# --- 辅助 ---


def _write(directory: Path, name: str, source: str) -> Path:
    p = directory / name
    p.write_text(source, encoding="utf-8")
    return p


def _fresh_app():
    from app.main import create_app

    return create_app()


def _load(app, directory: Path):
    from app.plugins.loader import load_plugins

    return load_plugins(app, directory)


def _status_map():
    from app.plugins.loader import get_plugin_statuses

    return {s.id: s for s in get_plugin_statuses()}


# --- 装载与隔离 ---


def test_load_single_file_plugin(tmp_path):
    statuses = _load(_fresh_app(), tmp_path)
    assert statuses == []  # 空目录 -> 空台账
    _write(tmp_path, "ping.py", PING_PLUGIN)
    statuses = _load(_fresh_app(), tmp_path)
    assert len(statuses) == 1
    s = statuses[0]
    assert s.id == "ping" and s.state == "loaded" and s.version == "0.1.0"
    assert s.routes == ["/api/plugins/ping"]


def test_load_package_plugin_with_relative_import(tmp_path):
    pkg = tmp_path / "pkgp"
    pkg.mkdir()
    _write(pkg, "helper.py", "VALUE = 42\n")
    _write(
        pkg,
        "plugin.py",
        'from . import helper\n\nPLUGIN_META = {"id": "pkgp"}\n\n'
        "def register(api):\n"
        '    assert helper.VALUE == 42\n',
    )
    statuses = _load(_fresh_app(), tmp_path)
    assert len(statuses) == 1
    assert statuses[0].id == "pkgp" and statuses[0].state == "loaded"


def test_underscore_prefixed_skipped(tmp_path):
    _write(tmp_path, "_disabled.py", PING_PLUGIN)
    pkg = tmp_path / "_off"
    pkg.mkdir()
    _write(pkg, "plugin.py", PING_PLUGIN)
    assert _load(_fresh_app(), tmp_path) == []


def test_broken_import_isolated(tmp_path):
    _write(tmp_path, "aaa_broken.py", "def nope(:\n")  # 语法错误，排序在前
    _write(tmp_path, "ping.py", PING_PLUGIN)
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["aaa_broken"].state == "failed"
    assert statuses["aaa_broken"].error.startswith("import:")
    assert statuses["ping"].state == "loaded"  # 坏插件不阻塞好插件


def _status_map_after(app, directory: Path):
    from app.plugins.loader import load_plugins

    load_plugins(app, directory)
    return _status_map()


def test_register_raises_isolated(tmp_path):
    _write(tmp_path, "bad.py", 'PLUGIN_META = {"id": "bad"}\n\ndef register(api):\n    raise RuntimeError("boom")\n')
    _write(tmp_path, "ping.py", PING_PLUGIN)
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["bad"].state == "failed"
    assert statuses["bad"].error.startswith("register: boom")
    assert statuses["ping"].state == "loaded"


def test_missing_register_failed(tmp_path):
    _write(tmp_path, "noreg.py", 'PLUGIN_META = {"id": "noreg"}\n')
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["noreg"].state == "failed"
    assert "register" in statuses["noreg"].error


def test_invalid_id_failed(tmp_path):
    _write(tmp_path, "upper.py", 'PLUGIN_META = {"id": "UpperCase"}\n\ndef register(api):\n    pass\n')
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["UpperCase"].state == "failed"
    assert "非法" in statuses["UpperCase"].error


def test_duplicate_id_failed(tmp_path):
    _write(tmp_path, "a.py", 'PLUGIN_META = {"id": "dup"}\n\ndef register(api):\n    pass\n')
    _write(tmp_path, "b.py", 'PLUGIN_META = {"id": "dup"}\n\ndef register(api):\n    pass\n')
    statuses = _load(_fresh_app(), tmp_path)  # 重复 id 在台账中是两条记录，不能用 map
    dups = [s for s in statuses if s.id == "dup"]
    assert len(dups) == 2
    assert sorted(s.state for s in dups) == ["failed", "loaded"]  # 先加载者成功，后者记重复失败
    assert "重复" in next(s for s in dups if s.state == "failed").error


def test_min_api_compatibility(tmp_path):
    _write(tmp_path, "toonew.py", 'PLUGIN_META = {"id": "toonew", "min_api": "99.0"}\n\ndef register(api):\n    pass\n')
    _write(tmp_path, "okver.py", 'PLUGIN_META = {"id": "okver", "min_api": "1.0"}\n\ndef register(api):\n    pass\n')
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["toonew"].state == "failed"
    assert "不兼容" in statuses["toonew"].error
    assert statuses["okver"].state == "loaded"


# --- 路由挂载 ---


def test_route_mount_requires_auth_by_default(tmp_path, auth_headers):
    _write(tmp_path, "ping.py", PING_PLUGIN)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)
    r = client.get("/api/plugins/ping/ping", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"pong": True, "plugin": "ping", "api_version": "1.2.0"}
    # 默认认证：无凭证 -> 401（与宿主端点语义一致）
    assert client.get("/api/plugins/ping/ping").status_code == 401


def test_route_public_opt_out(tmp_path):
    _write(tmp_path, "webhook.py", PUBLIC_PLUGIN)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)
    r = client.post("/api/plugins/webhook/hook")
    assert r.status_code == 200 and r.json() == {"ok": True}


# --- 事件总线 ---


def test_events_subscribe_emit_and_isolation(tmp_path):
    _write(tmp_path, "ev.py", EVENT_PLUGIN)
    app = _fresh_app()
    statuses = _load(app, tmp_path)
    assert statuses[0].state == "loaded"
    assert statuses[0].events == ["ev.custom", "ev.custom"]
    from app.plugins import bus

    bus.emit("ev.custom", {"x": 1})
    from app.core.settings import settings

    data_file = Path(settings.data_dir) / "plugins" / "ev" / "got.json"
    assert data_file.exists()  # 先订阅的炸监听器被隔离，后订阅者仍收到
    assert json.loads(data_file.read_text(encoding="utf-8")) == {"x": 1}


def test_events_unsubscribe():
    from app.plugins import bus

    calls = []
    off = bus.on("t.x", lambda p: calls.append(p))
    bus.emit("t.x", {"a": 1})
    off()
    bus.emit("t.x", {"a": 2})
    assert calls == [{"a": 1}]


def test_events_unknown_event_subscription_is_noop():
    from app.plugins import bus

    calls = []
    bus.on("future.event", lambda p: calls.append(p))  # 订阅不存在的事件不报错
    bus.emit("other.event", {})
    assert calls == []


# --- 工具注册 ---


def test_plugin_tools_join_default_registry(tmp_path):
    _write(tmp_path, "toolsy.py", TOOL_PLUGIN)
    _load(_fresh_app(), tmp_path)
    from app.tools import default_registry

    reg = default_registry()
    assert reg.has("toolsy.echo")
    tool = reg.get("toolsy.echo")
    assert tool.llm is False and tool.func is not None


# --- 状态端点与 lifespan 集成 ---


def test_lifespan_loads_plugins_and_fires_lifecycle_events(auth_headers):
    from app.core.security import hash_password
    from app.core.settings import settings
    from app.db.base import SessionLocal
    from app.db.models import User

    # require_admin 需要管理员：与 test_api_users 同一模式，直接落库一个管理员
    # （env token 认证解析为首个管理员）
    db = SessionLocal()
    try:
        db.add(User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.commit()
    finally:
        db.close()

    pdir = Path(settings.plugins_dir)
    _write(pdir, "boot.py", BOOT_PLUGIN)
    data_dir = Path(settings.data_dir) / "plugins" / "boot"

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/plugins", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["api_version"] == "1.2.0"
        rows = {p["id"]: p for p in body["plugins"]}
        assert rows["boot"]["state"] == "loaded"
        assert "startup" in body["subscribed_events"]
        assert (data_dir / "started.txt").exists()  # startup 事件已在启动时广播
    assert (data_dir / "stopped.txt").exists()  # 退出 lifespan 广播 shutdown


def test_status_endpoint_requires_admin(client):
    # 无任何凭证 -> 401（require_admin 先经 get_current_user 认证）
    assert client.get("/api/plugins").status_code == 401


# --- 核心事件埋点（API.md §8.5 事件清单必须与宿主行为一致）---


def test_materials_completed_event_emitted(client, auth_headers, monkeypatch):
    """材料 SSE 生成完成 -> materials_completed 广播（v1.1.0；payload 契约见 API.md §8.5）。"""
    from test_api_plans_materials import _make_plan
    from test_api_plans_materials_sse import _UnitProvider, _unit_resp

    from app.api import plans as plans_mod
    from app.plugins import bus

    async def _no_search(query, ctx):  # 防 .env BING_API_KEY 致真实联网（同材料 SSE 套件）
        return []

    monkeypatch.setattr(plans_mod, "search_unit_resources", _no_search)

    captured = []
    bus.on("materials_completed", captured.append)

    plan_id = _make_plan(client, auth_headers)
    provider = _UnitProvider([_unit_resp("### 一"), _unit_resp("### 二"), _unit_resp("### 三")])
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)

    headers = {**auth_headers, "Accept": "text/event-stream"}
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=headers)
    assert r.status_code == 200, r.text

    assert len(captured) == 1  # 全量生成一次广播（幂等重渲染不广播）
    payload = captured[0]
    assert payload["plan_id"] == plan_id
    assert isinstance(payload["session_id"], int)
    assert payload["unit_count"] == 3
    assert payload["degraded_count"] == 0
    assert payload["partial"] is False

    # 幂等命中（已持久化未 refresh -> JSON 重渲染）：不再广播
    r2 = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=auth_headers)
    assert r2.status_code == 200
    assert len(captured) == 1


def test_learning_log_added_event_emitted(client, auth_headers):
    """学习打卡 -> learning_log_added 广播（v1.2.0；payload 契约见 API.md §8.5）。"""
    from test_api_plans_materials import _make_plan

    from app.plugins import bus

    captured = []
    bus.on("learning_log_added", captured.append)

    plan_id = _make_plan(client, auth_headers)
    r = client.post(
        "/api/progress/logs",
        json={"plan_id": plan_id, "minutes": 45, "note": "学了第一章"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    log_id = r.json()["id"]

    assert len(captured) == 1
    payload = captured[0]
    assert payload["plan_id"] == plan_id
    assert payload["log_id"] == log_id
    assert payload["minutes"] == 45
    assert payload["note"] == "学了第一章"
    assert isinstance(payload["user_id"], int)
    assert payload["date"]  # ISO 日期非空


# --- v1.0.0 首批三事件 payload 契约回填（iter 208：此前仅事件名有守卫，payload 无断言）---


def test_plan_created_event_payload(client, auth_headers):
    """方案生成落库 -> plan_created 广播（v1.0.0；payload 契约见 API.md §8.5）。"""
    from test_api_plans_materials import _make_plan

    from app.plugins import bus

    captured = []
    bus.on("plan_created", captured.append)

    plan_id = _make_plan(client, auth_headers)

    assert len(captured) == 1
    payload = captured[0]
    assert payload["plan_id"] == plan_id
    assert isinstance(payload["session_id"], int)
    assert payload["version"] >= 1  # 生成/重生成/重试统一收口，版本号递增
    assert payload["topic"] == "React"  # 罐头 intent topic


def test_progress_updated_event_payload(client, auth_headers):
    """单节点进度更新 -> progress_updated 广播（v1.0.0；payload 契约见 API.md §8.5）。"""
    from test_api_plans_materials import _make_plan

    from app.plugins import bus

    captured = []
    bus.on("progress_updated", captured.append)

    plan_id = _make_plan(client, auth_headers)
    r = client.post(
        "/api/progress",
        json={"plan_id": plan_id, "node_id": "k1", "status": "done"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    assert len(captured) == 1
    payload = captured[0]
    assert payload["plan_id"] == plan_id
    assert payload["node_id"] == "k1"  # 对外暴露 sid 而非 DB 主键
    assert payload["status"] == "done"
    assert isinstance(payload["user_id"], int)
    assert isinstance(payload["mastery"], float)


def test_quiz_submitted_event_payload(client, auth_headers, monkeypatch):
    """测验作答提交 -> quiz_submitted 广播（v1.0.0；payload 契约见 API.md §8.5）。"""
    from test_api_quizzes import _QuizProvider, _gen_quiz, _make_plan, _quizzes_of

    from app.plugins import bus

    captured = []
    bus.on("quiz_submitted", captured.append)

    plan_id = _make_plan(client, auth_headers, monkeypatch)
    _gen_quiz(client, auth_headers, monkeypatch, plan_id, _QuizProvider())
    quiz = next(q for q in _quizzes_of(plan_id) if q.type and q.type.value == "choice")

    r = client.post(
        f"/api/quizzes/{quiz.id}/submit",
        json={"user_answer": {"selected": "const"}},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    assert len(captured) == 1
    payload = captured[0]
    assert payload["quiz_id"] == quiz.id
    assert payload["plan_id"] == plan_id
    assert payload["type"] == "choice"
    assert payload["correct"] is True  # selected 命中罐头答案
    assert isinstance(payload["user_id"], int)


# --- 仓库自带示例插件防腐（API.md §8.7 指向的示例必须始终可加载/可运行）---


def test_bundled_example_plugin_loads_and_works(tmp_path, auth_headers):
    import shutil

    example = Path(__file__).resolve().parents[2] / "plugins" / "example_hello.py.disabled"
    assert example.is_file(), "仓库自带示例插件缺失"
    shutil.copy(example, tmp_path / "example_hello.py")

    app = _fresh_app()
    statuses = _load(app, tmp_path)
    assert len(statuses) == 1 and statuses[0].state == "loaded", statuses[0].error
    assert statuses[0].id == "example_hello"

    client = TestClient(app)
    r = client.get("/api/plugins/example_hello/hello", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plugin"] == "example_hello" and body["api_version"] == "1.2.0"
    assert "message" in body and "model" in body

    # 示例注册的工具已汇入注册表
    from app.tools import default_registry

    assert default_registry().has("example_hello.word_count")

    # 示例订阅的 progress_updated 事件：广播后写入插件数据目录日志
    from app.plugins import bus

    bus.emit("progress_updated", {"user_id": 1, "plan_id": 2, "node_id": "n1", "status": "done"})
    from app.core.settings import settings

    log_file = Path(settings.data_dir) / "plugins" / "example_hello" / "progress.log"
    assert log_file.exists()
    assert "plan=2" in log_file.read_text(encoding="utf-8")


# --- API.md 文档 ↔ 实现漂移守卫（版本号/插槽单一事实源在代码，文档声明必须同步）---


def test_api_doc_versions_match_implementation():
    """API.md 声明的「当前版本」必须与代码 API_VERSION 一致（防 §8.6 示例式腐化）。"""
    import re

    from app.plugins.api import API_VERSION as BACKEND_VERSION

    root = Path(__file__).resolve().parents[2]
    doc = (root / "API.md").read_text(encoding="utf-8")
    fe_src = (root / "frontend" / "src" / "lib" / "plugins.ts").read_text(encoding="utf-8")
    m = re.search(r"export const API_VERSION = '([^']+)'", fe_src)
    assert m, "前端 plugins.ts 缺少 API_VERSION 导出"
    fe_version = m.group(1)

    # 文首版本线表：两条当前版本行
    assert re.search(rf"\| 前端插件 API \| `{re.escape(fe_version)}` \|", doc), "版本线表前端行过期"
    assert re.search(rf"\| 后端插件 API \| `{re.escape(BACKEND_VERSION)}` \|", doc), "版本线表后端行过期"
    # §八「API 版本」行
    assert re.search(rf"\*\*API 版本\*\*：`{re.escape(BACKEND_VERSION)}`", doc), "§八 API 版本行过期"
    # §8.6 状态端点示例 JSON（全文恰好一处）
    examples = re.findall(r'"api_version":\s*"([^"]+)"', doc)
    assert examples == [BACKEND_VERSION], f"§8.6 示例 api_version 过期: {examples}"


def test_api_doc_widget_slots_match_implementation():
    """代码 WIDGET_SLOTS 每个插槽都必须出现在 API.md（新增插槽忘同步文档即红）。"""
    import re

    root = Path(__file__).resolve().parents[2]
    doc = (root / "API.md").read_text(encoding="utf-8")
    fe_src = (root / "frontend" / "src" / "lib" / "plugins.ts").read_text(encoding="utf-8")
    m = re.search(r"WIDGET_SLOTS = \[([^\]]+)\]", fe_src)
    assert m, "前端 plugins.ts 缺少 WIDGET_SLOTS"
    for slot in re.findall(r"'([^']+)'", m.group(1)):
        assert slot in doc, f"插槽 {slot} 未写入 API.md"


def _doc_event_table(doc: str, start: str, end: str) -> set[str]:
    """提取 API.md 指定区间事件表首列（`| `name` |` 行）的事件名集合。"""
    import re

    i = doc.index(start)
    section = doc[i : doc.index(end, i)]
    return set(re.findall(r"^\| `(\w+)`", section, flags=re.M))


def test_api_doc_backend_events_match_emit_sites():
    """API.md §8.5 事件清单 == 宿主实际发射的核心事件（双向集合相等：

    新增发射点忘同步文档 -> 红（漏文档）；文档写了但代码不发射 -> 红（虚假承诺）。
    扫描以 `bus.emit/emit_async` 调用为事实源（orchestrator 的 `self._emit` 是 SSE 流事件，不匹配）。
    """
    import re

    root = Path(__file__).resolve().parents[2]
    emitted: set[str] = set()
    for py in (root / "backend" / "app").rglob("*.py"):
        emitted.update(re.findall(r'bus\.emit(?:_async)?\(\s*"([^"]+)"', py.read_text(encoding="utf-8")))
    assert emitted, "扫描不到任何插件事件发射点（正则失效？）"
    doc = (root / "API.md").read_text(encoding="utf-8")
    documented = _doc_event_table(doc, "### 8.5", "### 8.6")
    assert emitted == documented, f"发射点={sorted(emitted)} 文档={sorted(documented)}"


def test_api_doc_frontend_events_match_event_map():
    """API.md §四事件清单 == 前端 DeepTutorEventMap 键（双向集合相等）。"""
    import re

    root = Path(__file__).resolve().parents[2]
    fe_src = (root / "frontend" / "src" / "lib" / "plugins.ts").read_text(encoding="utf-8")
    m = re.search(r"export interface DeepTutorEventMap \{(.*?)\n\}", fe_src, flags=re.S)
    assert m, "前端 plugins.ts 缺少 DeepTutorEventMap"
    declared = set(re.findall(r"^\s{2}(\w+):", m.group(1), flags=re.M))
    assert declared, "DeepTutorEventMap 键提取失败（正则失效？）"
    doc = (root / "API.md").read_text(encoding="utf-8")
    documented = _doc_event_table(doc, "### 事件清单", "### 方法")
    assert declared == documented, f"事件映射={sorted(declared)} 文档={sorted(documented)}"
