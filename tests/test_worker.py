"""gui_desktop.worker 单元测试

只依赖 PySide6（CI 环境可用），不依赖 pytest-qt。
为避免在 headless 环境真正启动事件循环，所有信号断言都基于直接连接的
Python 回调（Qt::DirectConnection 默认行为，同步触发），不需要 QApplication.exec_()。

如果 PySide6 在当前环境无法初始化 QCoreApplication（极少数 headless 场景），
对应测试会被跳过并保留诊断信息。
"""

from __future__ import annotations

import logging
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

    _PYSIDE_AVAILABLE = True
    _PYSIDE_ERROR = None
except Exception as exc:  # pragma: no cover - environment-dependent
    _PYSIDE_AVAILABLE = False
    _PYSIDE_ERROR = exc


# 共享一个 QCoreApplication 实例（Qt 要求进程内最多一个）
_qapp = None
if _PYSIDE_AVAILABLE:
    try:
        _qapp = QCoreApplication.instance() or QCoreApplication(sys.argv[:1] or [""])
    except Exception as exc:  # pragma: no cover
        _PYSIDE_AVAILABLE = False
        _PYSIDE_ERROR = exc


@unittest.skipUnless(
    _PYSIDE_AVAILABLE,
    f"PySide6 不可用，跳过 worker 测试: {_PYSIDE_ERROR}",
)
class TestPipelineWorker(unittest.TestCase):
    """PipelineWorker 的取消事件 + 生命周期信号"""

    def _build_worker(self):
        from src.core import RuntimeConfig
        from gui_desktop.worker import PipelineWorker

        cfg = RuntimeConfig(pdf_path="/no/such/file_missing.pdf")
        return PipelineWorker(cfg)

    def test_cancel_sets_internal_event(self):
        worker = self._build_worker()
        # 取消前事件未被设置
        self.assertFalse(worker._cancel_event.is_set())
        worker.cancel()
        self.assertTrue(worker._cancel_event.is_set())

    def test_cancel_event_is_threading_event(self):
        worker = self._build_worker()
        self.assertIsInstance(worker._cancel_event, threading.Event)

    def test_worker_has_progress_signal(self):
        worker = self._build_worker()
        self.assertTrue(hasattr(worker, "progress"))
        self.assertTrue(hasattr(worker, "log_line"))
        self.assertTrue(hasattr(worker, "finished"))

    def test_on_progress_emits_progress_signal(self):
        worker = self._build_worker()
        captured: list[tuple] = []

        worker.progress.connect(
            lambda step, label, pct, detail: captured.append((step, label, pct, detail))
        )
        worker._on_progress("stepX", "标签", 42, "细节信息")

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], ("stepX", "标签", 42, "细节信息"))


@unittest.skipUnless(
    _PYSIDE_AVAILABLE,
    f"PySide6 不可用，跳过 worker 日志信号测试: {_PYSIDE_ERROR}",
)
class TestSignalLogHandler(unittest.TestCase):
    """_SignalLogHandler 应把 LogRecord 通过信号发出"""

    def test_handler_emits_via_signal(self):
        from gui_desktop.worker import _SignalLogHandler

        # 创建一个挂载信号的 QObject 宿主
        class Host(QObject):
            sig = Signal(str)

        host = Host()
        captured: list[str] = []
        host.sig.connect(lambda s: captured.append(s))

        handler = _SignalLogHandler(host.sig)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello from logger",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        self.assertEqual(len(captured), 1)
        self.assertIn("hello from logger", captured[0])

    def test_handler_is_logging_handler_subclass(self):
        from gui_desktop.worker import _SignalLogHandler

        self.assertTrue(issubclass(_SignalLogHandler, logging.Handler))

    def test_handler_handles_broken_signal_gracefully(self):
        """如果 signal 抛异常，handler 应走 handleError 不让进程崩"""
        from gui_desktop.worker import _SignalLogHandler

        class BrokenSignal:
            def emit(self, *a, **kw):
                raise RuntimeError("signal broken")

        handler = _SignalLogHandler(BrokenSignal())
        handler.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="t",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="x",
            args=(),
            exc_info=None,
        )
        # 不应向外抛异常
        handler.emit(record)


@unittest.skipUnless(
    _PYSIDE_AVAILABLE,
    f"PySide6 不可用，跳过 make_worker_thread 测试: {_PYSIDE_ERROR}",
)
class TestMakeWorkerThread(unittest.TestCase):
    """make_worker_thread 应把 worker 挂到 QThread 并连接生命周期信号"""

    def test_returns_qthread_with_worker_moved(self):
        from src.core import RuntimeConfig
        from gui_desktop.worker import PipelineWorker, make_worker_thread

        cfg = RuntimeConfig(pdf_path="/no/such/file.pdf")
        worker = PipelineWorker(cfg)
        thread = make_worker_thread(worker)

        try:
            self.assertIsInstance(thread, QThread)
            # worker 的 thread() 应已切换到新线程
            self.assertIs(worker.thread(), thread)
        finally:
            # 清理：不能让 thread 残留
            if thread.isRunning():
                thread.quit()
                thread.wait(1000)

    def test_thread_not_started_by_factory(self):
        """make_worker_thread 只接线不启动，调用者负责 thread.start()"""
        from src.core import RuntimeConfig
        from gui_desktop.worker import PipelineWorker, make_worker_thread

        cfg = RuntimeConfig(pdf_path="/no/such/file.pdf")
        worker = PipelineWorker(cfg)
        thread = make_worker_thread(worker)

        try:
            self.assertFalse(thread.isRunning())
        finally:
            if thread.isRunning():
                thread.quit()
                thread.wait(1000)


if __name__ == "__main__":
    unittest.main()
