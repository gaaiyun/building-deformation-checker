"""QThread 后台流水线执行器

把 src.core.pipeline.run_pipeline 包装成 QThread，
通过 pyqtSignal 把进度、日志、最终结果安全地推回 GUI 线程。

这是 Qt 处理长任务的标准模式，比 Streamlit 稳定得多：
- 主 UI 线程不会被阻塞，无论流水线跑多久（24 分钟也不卡顿）
- 用户切换 tab、最小化窗口、调整大小都不影响后台任务
- 信号槽通过 Qt 事件队列跨线程通信，天然线程安全
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from src.core import PipelineResult, RuntimeConfig, run_pipeline


class PipelineWorker(QObject):
    """QObject 上挂载 pyqtSignal，可被 moveToThread 到工作线程"""

    # 进度上报: (step_id, label, percent, detail)
    progress = Signal(str, str, int, str)
    # 单行日志
    log_line = Signal(str)
    # 完成 (PipelineResult)
    finished = Signal(object)

    def __init__(self, config: RuntimeConfig, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config = config
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """从主线程调用以请求取消"""
        self._cancel_event.set()

    def run(self) -> None:
        """在工作线程里执行流水线"""
        # 安装一个日志 handler 把所有 INFO 推到 log_line 信号
        log_handler = _SignalLogHandler(self.log_line)
        log_handler.setLevel(logging.INFO)
        log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)

        try:
            result: PipelineResult = run_pipeline(
                self._config,
                progress_callback=self._on_progress,
                cancel_event=self._cancel_event,
            )
            self.finished.emit(result)
        finally:
            root_logger.removeHandler(log_handler)

    def _on_progress(self, step_id: str, label: str, percent: int, detail: str) -> None:
        # 跨线程发射信号 - Qt 自动通过事件队列投递到主线程
        self.progress.emit(step_id, label, percent, detail)


class _SignalLogHandler(logging.Handler):
    """把日志记录通过 Qt 信号推送到主线程"""

    def __init__(self, signal: Signal):
        super().__init__()
        self._signal = signal

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._signal.emit(msg)
        except Exception:
            self.handleError(record)


def make_worker_thread(worker: PipelineWorker) -> QThread:
    """把 worker 移到一个 QThread 并连接生命周期信号"""
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread
