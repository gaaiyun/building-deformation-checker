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
    """后台流水线工人，挂载 Qt 信号供跨线程通信。

    生命周期:
        1. 主线程：``worker = PipelineWorker(cfg)``
        2. 主线程：``thread = make_worker_thread(worker)``（自动 moveToThread）
        3. 主线程连接信号：``worker.progress.connect(...)`` 等
        4. 主线程：``thread.start()`` → 工作线程触发 ``worker.run()``
        5. 工作线程：``run_pipeline()`` 执行 8 步流水线
        6. 工作线程：发射 ``finished`` 信号 → Qt 队列 → 主线程处理

    线程安全:
        - 所有信号通过 Qt 的队列连接（QueuedConnection）传递，跨线程安全
        - ``cancel()`` 写 ``threading.Event`` 是原子的，可从任意线程调用
        - 不要直接访问 worker 的内部字段（如 ``_config``），仅通过信号交互
    """

    # ─── 信号定义 ──────────────────────────────────
    progress = Signal(str, str, int, str)
    """进度上报信号。
    参数:
        step_id (str): 步骤标识，如 ``"step1"``、``"step2.5"``、``"done"``
        label (str): 显示给用户的步骤名，如 ``"Step 1/8 · PDF 提取"``
        percent (int): 总进度百分比 0-100
        detail (str): 当前步骤的详细描述（单行）
    """

    log_line = Signal(str)
    """单行日志信号（来自 Python logging 系统的格式化字符串）"""

    finished = Signal(object)
    """完成信号，参数为 ``PipelineResult``。无论成功/失败/取消都会发射。"""

    def __init__(self, config: RuntimeConfig, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._config = config
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """请求取消（线程安全）。

        从主线程或任意线程调用都安全。流水线在下一个 ``_check_cancel``
        检查点会抛出 ``CancelledError``，被 ``run_pipeline`` 捕获并写入
        ``PipelineResult.cancelled = True``。
        """
        self._cancel_event.set()

    def run(self) -> None:
        """在工作线程里执行流水线，捕获所有底层 logger 输出。"""
        # 连接到 ROOT logger（而非 __name__ logger）是有意的：
        # pdf_extractor / llm_parser / self_verifier 等所有下游模块的日志
        # 都需要透出到 GUI 日志窗口。用 root logger 是 Python 推荐的"全局
        # 接管"方式，比逐个 module 注册简单且不易漏。
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
