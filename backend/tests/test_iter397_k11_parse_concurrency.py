"""iter 397 K11：解析并发槽 + faster-whisper 模型单例。

背景
----
解析是全站最重的本地计算：marker（PDF + OCR）、faster-whisper（音视频转写）、
tesseract（OCR）都吃满 CPU 与内存。此前两处缺口：

1. **并发无上界**：N 个用户的 N 份解析同时跑，互相拖慢到全部超时，且每路都
   持有一份模型与中间产物。
2. **whisper 模型每次新建**：``WhisModel(...)`` 每次解析都把权重重新载入内存
   （base 档数百 MB）——单用户连续解析反复付加载代价，并发解析则是 N 份模型
   同时驻留。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types

from app.core import concurrency
from app.tools import parse_material


class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeWhisperModel:
    """替身模型：记录构造次数与转写时的并发重叠。"""

    instances = 0
    live = 0
    peak = 0
    _lock = threading.Lock()

    def __init__(self, name, device=None, compute_type=None):  # noqa: ANN001
        type(self).instances += 1
        self.name = name

    def transcribe(self, path):  # noqa: ANN001
        with type(self)._lock:
            type(self).live += 1
            type(self).peak = max(type(self).peak, type(self).live)
        try:
            time.sleep(0.05)
            return iter([_FakeSegment("你好")]), types.SimpleNamespace(language="zh")
        finally:
            with type(self)._lock:
                type(self).live -= 1


def _install_fake_whisper(monkeypatch) -> None:
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    parse_material.reset_whisper_model()
    _FakeWhisperModel.instances = 0
    _FakeWhisperModel.live = 0
    _FakeWhisperModel.peak = 0


# ---------------------------------------------------------------------------
# 模型单例
# ---------------------------------------------------------------------------


def test_whisper_model_is_built_once(monkeypatch):
    """两次取模型必须是同一个实例，且只构造一次。"""
    _install_fake_whisper(monkeypatch)
    first = parse_material._whisper_model()
    second = parse_material._whisper_model()
    assert first is second
    assert _FakeWhisperModel.instances == 1
    parse_material.reset_whisper_model()


def test_whisper_model_is_rebuilt_when_the_name_changes(monkeypatch):
    """设置改了模型名要重建（否则仍在用旧模型）。"""
    _install_fake_whisper(monkeypatch)
    parse_material._whisper_model()
    monkeypatch.setattr(parse_material.settings, "whisper_model", "small", raising=False)
    rebuilt = parse_material._whisper_model()
    assert _FakeWhisperModel.instances == 2
    assert rebuilt.name == "small"
    parse_material.reset_whisper_model()


def test_whisper_model_is_rebuilt_when_the_implementation_changes(monkeypatch):
    """依赖实现被替换 -> 旧实例不得复用（缓存键含实现类 identity）。

    回归：此前缓存键只有模型名，注入的新替身永远不生效（替身调用数恒为 0），
    用例断言「空转写」却拿到前序用例残留的模型 -> 结果 ok 而非 empty_content。
    """
    _install_fake_whisper(monkeypatch)
    first = parse_material._whisper_model()
    assert first is parse_material._whisper_model()
    assert _FakeWhisperModel.instances == 1

    class _OtherWhisperModel(_FakeWhisperModel):
        pass

    other_module = types.ModuleType("faster_whisper")
    other_module.WhisperModel = _OtherWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", other_module)

    rebuilt = parse_material._whisper_model()
    assert rebuilt is not first
    assert isinstance(rebuilt, _OtherWhisperModel)
    assert _FakeWhisperModel.instances == 1  # 旧实现不再被构造
    parse_material.reset_whisper_model()


def test_transcriptions_do_not_overlap(monkeypatch):
    """同一个模型实例不能并发转写：转写走串行锁。"""
    _install_fake_whisper(monkeypatch)
    # 时长探测不可用 -> 跳过时长守卫。
    monkeypatch.setattr(parse_material, "_media_duration_seconds", lambda path: None)
    path = types.SimpleNamespace(suffix=".mp3")
    path = parse_material.Path(str(path))
    results = []

    def worker():
        results.append(parse_material._parse_media(path))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3
    assert _FakeWhisperModel.peak == 1, f"转写并发重叠：peak={_FakeWhisperModel.peak}"
    assert _FakeWhisperModel.instances == 1
    parse_material.reset_whisper_model()


# ---------------------------------------------------------------------------
# 解析并发槽
# ---------------------------------------------------------------------------


def test_parse_slot_bounds_the_concurrency(monkeypatch, tmp_path):
    """并发的文件解析不得超过 max_concurrent_parses。"""
    concurrency.reset_llm_semaphore()
    monkeypatch.setattr(parse_material.settings, "max_concurrent_parses", 2, raising=False)

    live = 0
    peak = 0
    lock = threading.Lock()

    def fake_parse_file(source, parse_mode="full"):  # noqa: ANN001
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            time.sleep(0.05)
            return "文本", {"source_type": "file"}
        finally:
            with lock:
                live -= 1

    monkeypatch.setattr(parse_material, "_parse_file", fake_parse_file)
    doc = tmp_path / "a.md"
    doc.write_text("x", encoding="utf-8")

    async def storm():
        await asyncio.gather(
            *[parse_material._parse_file_dispatch(str(doc)) for _ in range(6)]
        )

    asyncio.run(storm())
    assert peak <= 2, f"解析并发越界：peak={peak}"
    concurrency.reset_llm_semaphore()


def test_parse_slot_is_shared_across_calls(monkeypatch, tmp_path):
    """槽是进程级的：两次分开的调用共用同一个上界。"""
    concurrency.reset_llm_semaphore()
    monkeypatch.setattr(parse_material.settings, "max_concurrent_parses", 1, raising=False)

    live = 0
    peak = 0
    lock = threading.Lock()

    def fake_parse_file(source, parse_mode="full"):  # noqa: ANN001
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            time.sleep(0.05)
            return "文本", {"source_type": "file"}
        finally:
            with lock:
                live -= 1

    monkeypatch.setattr(parse_material, "_parse_file", fake_parse_file)
    doc = tmp_path / "a.md"
    doc.write_text("x", encoding="utf-8")

    async def storm():
        await asyncio.gather(
            *[parse_material._parse_file_dispatch(str(doc)) for _ in range(4)]
        )

    asyncio.run(storm())
    assert peak == 1, f"上限 1 时仍有并发：peak={peak}"
    concurrency.reset_llm_semaphore()


def test_nested_parse_does_not_deadlock(monkeypatch, tmp_path):
    """插件解析器回调宿主 parse_material 时不得自死锁。

    回归：信号量不可重入。插件注册的解析器可经 ``api.tools.call("parse_material")``
    回调宿主解析（API.md §8.4 允许插件组合核心工具）——重入同一信号量时，
    上界为 1 即**永久挂起**（外层持槽等内层，内层等外层）；上界为 N 时，
    N 路重入请求即可占满全部槽位，此后全站解析请求排队卡死。
    """
    concurrency.reset_llm_semaphore()
    monkeypatch.setattr(parse_material.settings, "max_concurrent_parses", 1, raising=False)

    def fake_parse_file(source, parse_mode="full"):  # noqa: ANN001
        return "文本", {"source_type": "file"}

    monkeypatch.setattr(parse_material, "_parse_file", fake_parse_file)
    doc = tmp_path / "a.md"
    doc.write_text("x", encoding="utf-8")

    async def outer():
        # 外层持槽 -> 内层重入（模拟插件解析器回调宿主解析）
        async with concurrency.parse_slot():
            await parse_material._parse_file_dispatch(str(doc))

    # 超时兜底：真死锁时挂起，这里 5s 未完成即判定失败
    asyncio.run(asyncio.wait_for(outer(), timeout=5.0))
    concurrency.reset_llm_semaphore()


def test_sibling_parses_are_not_treated_as_reentrant(monkeypatch, tmp_path):
    """同一父任务 gather 出的兄弟解析不得被误判为「重入」而绕过并发上界。

    回归：重入判定若用 ContextVar 深度，子任务会继承父上下文（asyncio.gather
    拷贝父上下文），兄弟解析全部被当成重入 -> 并发上界形同虚设。
    """
    concurrency.reset_llm_semaphore()
    monkeypatch.setattr(parse_material.settings, "max_concurrent_parses", 2, raising=False)

    live = 0
    peak = 0
    lock = threading.Lock()

    def fake_parse_file(source, parse_mode="full"):  # noqa: ANN001
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            time.sleep(0.05)
            return "文本", {"source_type": "file"}
        finally:
            with lock:
                live -= 1

    monkeypatch.setattr(parse_material, "_parse_file", fake_parse_file)
    doc = tmp_path / "a.md"
    doc.write_text("x", encoding="utf-8")

    async def storm():
        async with concurrency.parse_slot():
            await asyncio.gather(
                *[parse_material._parse_file_dispatch(str(doc)) for _ in range(4)]
            )

    asyncio.run(storm())
    # 上界 2：父任务已持 1 槽 -> 剩余 1 槽 -> 兄弟解析最多并发 1。
    # 若兄弟被误判为「重入」而绕过信号量，peak 会是 4。
    assert peak == 1, f"兄弟解析绕过并发上界：peak={peak}（期望 1）"
    concurrency.reset_llm_semaphore()
