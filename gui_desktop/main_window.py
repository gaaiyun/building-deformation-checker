"""PySide6 主窗 - 建筑变形监测报告核验台桌面版。

三态状态机 (QStackedWidget 切换)：
    ┌──────┐  upload   ┌──────────┐  done   ┌──────────┐
    │ Idle │──────────▶│ Running  │────────▶│ Results  │
    │      │           │ (QThread)│         │ (8 tabs) │
    └──────┘           └──────────┘         └─────┬────┘
       ▲                    │                     │
       └─── new task ───────┴─── cancel/fail ─────┘

设计要点（修复 v1 Streamlit 长任务三大 bug）：
    - QThread + Signal 跑后台流水线，主 UI 永远不卡，24 分钟长任务也稳
    - 完成态保持所有结果在 ResultsPanel 内存中，反复导出不会丢失
    - 拖拽 PDF 入窗口直接开始；也支持文件选择按钮 / 菜单栏 / 文件对话框
    - 配置持久化到 keyring + JSON（敏感字段走系统密钥环）
    - 内嵌 PDF 预览（QPdfView，PySide6 6.5+ 自带）

模块组织：
    - MainWindow: 主窗，挂载菜单、状态栏、三态切换
    - ConfigPanel: 左侧配置面板（LLM/OCR/流水线开关）
    - IdlePanel: 空闲态（拖拽提示 + 浏览按钮）
    - RunningPanel: 运行态（进度条 + 步骤清单 + 实时日志 + 取消按钮）
    - ResultsPanel: 完成态（8 个 tab + 导出按钮）

依赖关系：
    main_window → worker (QThread 包装)
                → settings_store (持久化)
                → src.core.run_pipeline (UI 无关核心)
"""

from __future__ import annotations

import html
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_desktop.settings_store import load_settings, save_settings
from gui_desktop.worker import PipelineWorker, make_worker_thread
from src.core import PipelineResult, RuntimeConfig

logger = logging.getLogger(__name__)


def resource_path(*parts: str) -> Path:
    """Return a project resource path, both from source and PyInstaller onefile."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parents[1]
    return base.joinpath(*parts)


APP_ICON_PATH = resource_path("assets", "city_safety_iot.ico")
APP_LOGO_PATH = resource_path("assets", "city_safety_iot_logo.png")
APP_MARK_PATH = resource_path("assets", "city_safety_iot_icon.png")
LEGACY_PADDLE_DEFAULT_MODELS = {"PaddleOCR-VL-1.5", "PaddleOCR-VL"}


def _normalize_paddle_model(model: str | None) -> str:
    model = (model or "").strip()
    if not model or model in LEGACY_PADDLE_DEFAULT_MODELS:
        return "PaddleOCR-VL-1.6"
    return model


PROFESSIONAL_STYLESHEET = """
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 12px;
}
QMainWindow#AppShell {
    background: #f4f7fb;
}
QWidget#Sidebar {
    background: #ffffff;
    border-right: 1px solid #d9e2ec;
}
QLabel#SidebarTitle {
    color: #152238;
    font-size: 18px;
    font-weight: 700;
    padding: 2px 2px 0 2px;
}
QLabel#BrandName {
    color: #0b4d93;
    font-size: 20px;
    font-weight: 800;
}
QLabel#BrandSubName {
    color: #0b4d93;
    font-size: 11px;
    font-weight: 700;
}
QLabel#ProductSubtitle {
    color: #475569;
    font-size: 12px;
}
QGroupBox#ConfigCard {
    background: #ffffff;
    border: 1px solid #d9e2ec;
    border-radius: 8px;
    margin-top: 14px;
    padding: 12px 10px 10px 10px;
    font-weight: 700;
    color: #1f2a44;
}
QGroupBox#ConfigCard::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QLineEdit, QComboBox, QSpinBox {
    min-height: 30px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px 8px;
    background: #ffffff;
    color: #172033;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #2563eb;
}
QPushButton {
    min-height: 30px;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px 12px;
    background: #ffffff;
    color: #172033;
}
QPushButton:hover {
    background: #eef4ff;
    border-color: #9db7f6;
}
QPushButton#PrimaryButton {
    background: #1d4ed8;
    border-color: #1d4ed8;
    color: #ffffff;
    font-weight: 700;
}
QPushButton#PrimaryButton:hover {
    background: #1e40af;
}
QProgressBar {
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    background: #edf2f7;
    text-align: center;
    color: #172033;
}
QProgressBar::chunk {
    border-radius: 7px;
    background: #16a34a;
}
QTabWidget::pane {
    border: 1px solid #d9e2ec;
    border-radius: 8px;
    background: #ffffff;
}
QTabBar::tab {
    padding: 8px 14px;
    margin-right: 2px;
    border: 1px solid #d9e2ec;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    background: #edf2f7;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #1d4ed8;
    font-weight: 700;
}
QStatusBar {
    background: #ffffff;
    border-top: 1px solid #d9e2ec;
    color: #475569;
}
"""


# ─── 配置面板（左侧栏）────────────────────────────────────
class ConfigPanel(QWidget):
    """API key + 模型 + OCR 设置面板，持久化到本地"""

    def __init__(self, settings: dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # LLM 设置
        llm_box = QGroupBox("LLM 设置")
        llm_box.setObjectName("ConfigCard")
        llm_form = QFormLayout(llm_box)
        # User convenience: saved secrets are read from the local OS keyring.
        # Packaged EXE/MSI never includes keys; each user's machine owns its keyring.
        self.llm_api_key = QLineEdit(settings.get("llm_api_key", ""))
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.llm_api_key.setPlaceholderText("请输入自己的 API Key（不会内置到安装包）")
        llm_form.addRow("API Key", self.llm_api_key)

        self.llm_base_url = QLineEdit(settings.get("llm_base_url") or "https://api.deepseek.com")
        llm_form.addRow("Base URL", self.llm_base_url)

        self.llm_model = QComboBox()
        self.llm_model.setEditable(True)
        self.llm_model.addItems([
            "deepseek-v4-flash",
            "deepseek-v4-pro",
            "deepseek-chat",
            "deepseek-reasoner",
            "MiniMax-M2.7-highspeed",
            "MiniMax-M2.7",
        ])
        saved_model = settings.get("llm_model") or "deepseek-v4-flash"
        if saved_model == "MiniMax-M2.7-highspeed":
            saved_model = "deepseek-v4-flash"
        self.llm_model.setCurrentText(saved_model)
        llm_form.addRow("模型", self.llm_model)

        layout.addWidget(llm_box)

        # PaddleOCR 设置
        ocr_box = QGroupBox("PaddleOCR 设置（可选）")
        ocr_box.setObjectName("ConfigCard")
        ocr_form = QFormLayout(ocr_box)
        self.paddle_ocr_token = QLineEdit(settings.get("paddle_ocr_token", ""))
        self.paddle_ocr_token.setEchoMode(QLineEdit.Password)
        self.paddle_ocr_token.setPlaceholderText("请输入自己的 PaddleOCR Token")
        ocr_form.addRow("Token", self.paddle_ocr_token)

        self.paddle_ocr_model = QComboBox()
        self.paddle_ocr_model.setEditable(True)
        self.paddle_ocr_model.addItems([
            "PaddleOCR-VL-1.6",
            "PaddleOCR-VL-1.5",
            "PaddleOCR-VL",
            "PP-StructureV3",
            "PP-OCRv5",
        ])
        saved_ocr_model = _normalize_paddle_model(settings.get("paddle_ocr_model", "PaddleOCR-VL-1.6"))
        self.paddle_ocr_model.setCurrentText(saved_ocr_model)
        ocr_form.addRow("模型", self.paddle_ocr_model)

        self.paddle_ocr_use_async = QCheckBox("使用异步 OCR API")
        self.paddle_ocr_use_async.setChecked(settings.get("paddle_ocr_use_async", True))
        ocr_form.addRow("", self.paddle_ocr_use_async)

        self.paddle_ocr_use_cache = QCheckBox("复用 OCR 缓存（强烈推荐）")
        self.paddle_ocr_use_cache.setChecked(settings.get("paddle_ocr_use_cache", True))
        ocr_form.addRow("", self.paddle_ocr_use_cache)

        layout.addWidget(ocr_box)

        # 流水线开关
        pipeline_box = QGroupBox("流水线选项")
        pipeline_box.setObjectName("ConfigCard")
        pipeline_layout = QVBoxLayout(pipeline_box)
        self.use_ocr = QCheckBox("强制优先 OCR（扫描件）")
        self.use_ocr.setChecked(settings.get("use_ocr", False))
        pipeline_layout.addWidget(self.use_ocr)

        self.skip_self_verify = QCheckBox("跳过 AI 自验证（步骤 6，快 30%）")
        self.skip_self_verify.setChecked(settings.get("skip_self_verify", False))
        pipeline_layout.addWidget(self.skip_self_verify)

        self.skip_ai_review = QCheckBox("跳过 AI 最终审核（步骤 7）")
        self.skip_ai_review.setChecked(settings.get("skip_ai_review", False))
        pipeline_layout.addWidget(self.skip_ai_review)

        parallel_row = QHBoxLayout()
        parallel_row.addWidget(QLabel("LLM 分块并发"))
        self.llm_parse_max_parallel = QSpinBox()
        self.llm_parse_max_parallel.setRange(1, 8)
        self.llm_parse_max_parallel.setValue(int(settings.get("llm_parse_max_parallel", 4) or 4))
        self.llm_parse_max_parallel.setToolTip("长 PDF 会拆成多段并行解析；DeepSeek 默认建议 4，遇到限流可调低。")
        parallel_row.addWidget(self.llm_parse_max_parallel)
        pipeline_layout.addLayout(parallel_row)

        layout.addWidget(pipeline_box)

        # 保存按钮
        save_btn = QPushButton("保存配置")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.persist)
        layout.addWidget(save_btn)

        clear_secret_btn = QPushButton("清空已保存密钥")
        clear_secret_btn.clicked.connect(self.clear_secrets)
        layout.addWidget(clear_secret_btn)

        layout.addStretch(1)

    def to_runtime_config(self, pdf_path: str) -> RuntimeConfig:
        return RuntimeConfig(
            pdf_path=pdf_path,
            llm_api_key=self.llm_api_key.text().strip(),
            llm_base_url=self.llm_base_url.text().strip(),
            llm_model=self.llm_model.currentText().strip(),
            paddle_ocr_token=self.paddle_ocr_token.text().strip(),
            paddle_ocr_model=self.paddle_ocr_model.currentText().strip(),
            paddle_ocr_use_async=self.paddle_ocr_use_async.isChecked(),
            paddle_ocr_use_cache=self.paddle_ocr_use_cache.isChecked(),
            llm_parse_max_parallel=self.llm_parse_max_parallel.value(),
            use_ocr=self.use_ocr.isChecked(),
            prefer_ocr=self.use_ocr.isChecked(),
            auto_fallback=True,
            skip_self_verify=self.skip_self_verify.isChecked(),
            skip_ai_review=self.skip_ai_review.isChecked(),
        )

    def persist(self) -> None:
        snapshot = {
            "llm_api_key": self.llm_api_key.text(),
            "llm_base_url": self.llm_base_url.text(),
            "llm_model": self.llm_model.currentText(),
            "paddle_ocr_token": self.paddle_ocr_token.text(),
            "paddle_ocr_model": self.paddle_ocr_model.currentText(),
            "paddle_ocr_use_async": self.paddle_ocr_use_async.isChecked(),
            "paddle_ocr_use_cache": self.paddle_ocr_use_cache.isChecked(),
            "llm_parse_max_parallel": self.llm_parse_max_parallel.value(),
            "use_ocr": self.use_ocr.isChecked(),
            "skip_self_verify": self.skip_self_verify.isChecked(),
            "skip_ai_review": self.skip_ai_review.isChecked(),
        }
        self._settings.update(snapshot)
        save_settings(self._settings)

    def clear_secrets(self) -> None:
        self.llm_api_key.clear()
        self.paddle_ocr_token.clear()
        snapshot = {
            **self._settings,
            "llm_api_key": "",
            "paddle_ocr_token": "",
            "llm_base_url": self.llm_base_url.text(),
            "llm_model": self.llm_model.currentText(),
            "paddle_ocr_model": self.paddle_ocr_model.currentText(),
            "paddle_ocr_use_async": self.paddle_ocr_use_async.isChecked(),
            "paddle_ocr_use_cache": self.paddle_ocr_use_cache.isChecked(),
            "llm_parse_max_parallel": self.llm_parse_max_parallel.value(),
            "use_ocr": self.use_ocr.isChecked(),
            "skip_self_verify": self.skip_self_verify.isChecked(),
            "skip_ai_review": self.skip_ai_review.isChecked(),
        }
        self._settings.update(snapshot)
        save_settings(self._settings)


# ─── 进度面板（运行态）────────────────────────────────────
class RunningPanel(QWidget):
    """8 步进度 + 实时日志"""

    cancel_requested = Signal()

    STEP_LABELS = [
        ("step1", "Step 1/8 · PDF 提取"),
        ("step2", "Step 2/8 · LLM 结构化解析"),
        ("step2.5", "Step 2.5/8 · 分析计划 (ReAct)"),
        ("step3", "Step 3/8 · 计算验证"),
        ("step4", "Step 4/8 · 统计验证"),
        ("step5", "Step 5/8 · 逻辑检查"),
        ("step6", "Step 6/8 · AI 自验证"),
        ("step7", "Step 7/8 · AI 最终审核"),
        ("step8", "Step 8/8 · 报告生成"),
    ]

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.current_step_label = QLabel("准备中...")
        f = self.current_step_label.font()
        f.setPointSize(14)
        f.setBold(True)
        self.current_step_label.setFont(f)
        layout.addWidget(self.current_step_label)

        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("color: #5b6472;")
        layout.addWidget(self.detail_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setMinimumHeight(28)
        layout.addWidget(self.progress_bar)

        # 步骤清单
        self.steps_list = QListWidget()
        for step_id, label in self.STEP_LABELS:
            item = QListWidgetItem(f"○ {label}")
            item.setData(Qt.UserRole, step_id)
            self.steps_list.addItem(item)
        layout.addWidget(self.steps_list)

        # 实时日志
        log_label = QLabel("实时日志")
        layout.addWidget(log_label)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.log_view, stretch=1)

        # 取消按钮
        cancel_row = QHBoxLayout()
        cancel_row.addStretch(1)
        self.cancel_btn = QPushButton("取消运行")
        self.cancel_btn.clicked.connect(self.cancel_requested)
        cancel_row.addWidget(self.cancel_btn)
        layout.addLayout(cancel_row)

    def reset(self) -> None:
        self.current_step_label.setText("准备中...")
        self.detail_label.setText("")
        self.progress_bar.setValue(0)
        self.log_view.clear()
        for i in range(self.steps_list.count()):
            item = self.steps_list.item(i)
            text = item.text()
            if text.startswith(("●", "✓", "✗")):
                item.setText("○" + text[1:])

    def on_progress(self, step_id: str, label: str, percent: int, detail: str) -> None:
        self.current_step_label.setText(label)
        self.detail_label.setText(detail)
        self.progress_bar.setValue(percent)
        # 标记步骤状态
        for i in range(self.steps_list.count()):
            item = self.steps_list.item(i)
            sid = item.data(Qt.UserRole)
            if sid == step_id:
                item.setText("● " + label)
            else:
                txt = item.text()
                # 已通过的步骤前缀替换为 ✓
                idx_target = next(
                    (idx for idx, (s, _) in enumerate(self.STEP_LABELS) if s == step_id),
                    -1,
                )
                if idx_target >= 0 and i < idx_target and txt.startswith("●"):
                    item.setText("✓" + txt[1:])

    def append_log(self, line: str) -> None:
        self.log_view.append(line)
        # 自动滚动到底部
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())


# ─── 完成面板（结果展示）──────────────────────────────────
class ResultsPanel(QWidget):
    """完成态：8 个标签页展示所有结果，下方导出按钮"""

    new_pdf_requested = Signal()
    export_md_requested = Signal()
    export_docx_requested = Signal()
    export_html_requested = Signal()
    export_xlsx_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        # 摘要 metrics
        metrics_row = QHBoxLayout()
        self.metric_errors = self._make_metric("错误", "#dc2626")
        self.metric_warnings = self._make_metric("警告", "#d97706")
        self.metric_infos = self._make_metric("提示", "#2563eb")
        self.metric_duration = self._make_metric("用时", "#374151")
        for w in (self.metric_errors, self.metric_warnings, self.metric_infos, self.metric_duration):
            metrics_row.addWidget(w)
        layout.addLayout(metrics_row)

        # Tabs
        self.tabs = QTabWidget()
        self.tab_summary = QTextBrowser()
        self.tab_summary.setOpenExternalLinks(True)
        self.tabs.addTab(self.tab_summary, "总览")

        self.tab_calc = QTreeWidget()
        self.tab_calc.setHeaderLabels(["表名 / 测点", "字段", "级别", "说明"])
        self.tabs.addTab(self.tab_calc, "计算验证")

        self.tab_stats = QTreeWidget()
        self.tab_stats.setHeaderLabels(["表名 / 测点", "字段", "级别", "说明"])
        self.tabs.addTab(self.tab_stats, "统计验证")

        self.tab_logic = QTreeWidget()
        self.tab_logic.setHeaderLabels(["表名 / 测点", "字段", "级别", "说明"])
        self.tabs.addTab(self.tab_logic, "逻辑检查")

        self.tab_plan = QTextBrowser()
        self.tabs.addTab(self.tab_plan, "分析计划 (ReAct)")

        self.tab_ai = QTextBrowser()
        self.tabs.addTab(self.tab_ai, "AI 最终审核")

        self.tab_md = QTextEdit()
        self.tab_md.setReadOnly(True)
        self.tab_md.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.tab_md, "Markdown 源")

        self.tab_logs = QTextEdit()
        self.tab_logs.setReadOnly(True)
        self.tab_logs.setFont(QFont("Consolas", 9))
        self.tabs.addTab(self.tab_logs, "运行日志")

        layout.addWidget(self.tabs, stretch=1)

        # 底部按钮栏
        btns = QHBoxLayout()
        self.btn_new = QPushButton("处理新 PDF")
        self.btn_new.clicked.connect(self.new_pdf_requested)
        btns.addWidget(self.btn_new)
        btns.addStretch(1)
        self.btn_md = QPushButton("导出 Markdown")
        self.btn_md.clicked.connect(self.export_md_requested)
        btns.addWidget(self.btn_md)
        self.btn_docx = QPushButton("导出 Word")
        self.btn_docx.clicked.connect(self.export_docx_requested)
        btns.addWidget(self.btn_docx)
        self.btn_html = QPushButton("导出 HTML")
        self.btn_html.clicked.connect(self.export_html_requested)
        btns.addWidget(self.btn_html)
        self.btn_xlsx = QPushButton("导出 Excel中间层")
        self.btn_xlsx.clicked.connect(self.export_xlsx_requested)
        btns.addWidget(self.btn_xlsx)
        layout.addLayout(btns)
        self._set_export_buttons_enabled(False)

    def _make_metric(self, label: str, color: str) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 6, 8, 6)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #5b6472; font-size: 11px;")
        lbl.setAlignment(Qt.AlignCenter)
        val = QLabel("-")
        val.setAlignment(Qt.AlignCenter)
        val.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: 700;")
        v.addWidget(lbl)
        v.addWidget(val)
        w.setStyleSheet("background:#ffffff;border:1px solid #d8dee9;border-radius:10px;")
        w._value_label = val
        return w

    @staticmethod
    def _set_metric_value(metric_widget: QWidget, value: str) -> None:
        metric_widget._value_label.setText(value)

    def _set_export_buttons_enabled(self, enabled: bool) -> None:
        for button in (self.btn_md, self.btn_docx, self.btn_html, self.btn_xlsx):
            button.setEnabled(enabled)

    def render(self, result: PipelineResult, log_lines: list[str]) -> None:
        report = result.report
        self._set_export_buttons_enabled(bool(result.report and result.final_md))
        self._set_metric_value(self.metric_errors, str(len(result.errors)))
        self._set_metric_value(self.metric_warnings, str(len(result.warnings)))
        self._set_metric_value(self.metric_infos, str(len(result.infos)))
        self._set_metric_value(self.metric_duration, f"{result.duration_sec:.0f}s")

        # 总览
        project_name = getattr(report, "project_name", "") or "-"
        company = getattr(report, "monitoring_company", "") or "-"
        date = getattr(report, "monitoring_date", "") or "-"
        report_number = getattr(report, "report_number", "") or "-"
        method = result.extraction_method or "-"
        profile = result.extraction_profile or "-"
        tables_count = len(getattr(report, "tables", []) or [])
        thresholds_count = len(getattr(report, "thresholds", []) or [])

        summary_html = f"""
        <h2>{project_name}</h2>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
            <tr><td><b>监测单位</b></td><td>{company}</td><td><b>报告编号</b></td><td>{report_number}</td></tr>
            <tr><td><b>监测日期</b></td><td>{date}</td><td><b>提取方式</b></td><td>{method} / {profile}</td></tr>
            <tr><td><b>数据表</b></td><td>{tables_count}</td><td><b>阈值项</b></td><td>{thresholds_count}</td></tr>
            <tr><td><b>错误</b></td><td>{len(result.errors)}</td><td><b>警告</b></td><td>{len(result.warnings)}</td></tr>
        </table>
        <h3>报告输出路径</h3>
        <p><code>{result.output_path or '-'}</code></p>
        """
        self.tab_summary.setHtml(summary_html)

        # 问题树
        self._fill_issue_tree(self.tab_calc, result.calc_issues)
        self._fill_issue_tree(self.tab_stats, result.stats_issues)
        self._fill_issue_tree(self.tab_logic, result.logic_issues)

        # 分析计划
        plan_md = self._format_analysis_plan(result.analysis_plan)
        self.tab_plan.setMarkdown(plan_md)

        # AI 审核
        if result.ai_review:
            self.tab_ai.setMarkdown(result.ai_review)
        else:
            self.tab_ai.setPlainText("未启用或未生成 AI 最终审核。")

        # MD 源 + 日志
        self.tab_md.setPlainText(result.final_md)
        self.tab_logs.setPlainText("\n".join(log_lines))

    def render_failed(self, result: PipelineResult, log_lines: list[str]) -> None:
        self._set_export_buttons_enabled(False)
        self._set_metric_value(self.metric_errors, "1")
        self._set_metric_value(self.metric_warnings, "0")
        self._set_metric_value(self.metric_infos, "0")
        self._set_metric_value(self.metric_duration, f"{result.duration_sec:.0f}s")

        error_text = result.error_message or "未知错误"
        self.tab_summary.setHtml(
            "<h2>处理失败</h2>"
            "<p>流水线未生成可导出的检查报告。请查看实时日志定位失败步骤。</p>"
            f"<pre>{html.escape(error_text)}</pre>"
        )
        self._fill_issue_tree(self.tab_calc, [])
        self._fill_issue_tree(self.tab_stats, [])
        self._fill_issue_tree(self.tab_logic, [])
        self.tab_plan.setPlainText("任务失败，未生成分析计划。")
        self.tab_ai.setPlainText("任务失败，未生成 AI 最终审核。")
        self.tab_md.setPlainText("")
        self.tab_logs.setPlainText("\n".join(log_lines))

    def _fill_issue_tree(self, tree: QTreeWidget, issues: list) -> None:
        tree.clear()
        if not issues:
            placeholder = QTreeWidgetItem(["（无问题）", "", "", ""])
            tree.addTopLevelItem(placeholder)
            return
        grouped: dict[str, list] = defaultdict(list)
        for issue in issues:
            grouped[issue.table_name].append(issue)
        for table_name, group in grouped.items():
            err = sum(1 for i in group if i.severity == "error")
            warn = sum(1 for i in group if i.severity == "warning")
            badge = []
            if err:
                badge.append(f"E{err}")
            if warn:
                badge.append(f"W{warn}")
            top = QTreeWidgetItem([
                f"{table_name}  [{' / '.join(badge) if badge else '通过'}]",
                "",
                "",
                "",
            ])
            for issue in group:
                child = QTreeWidgetItem([
                    issue.point_id or "-",
                    issue.field_name or "-",
                    issue.severity,
                    issue.message or "",
                ])
                if issue.severity == "error":
                    for col in range(4):
                        child.setForeground(col, Qt.red)
                elif issue.severity == "warning":
                    for col in range(4):
                        from PySide6.QtGui import QBrush, QColor
                        child.setForeground(col, QBrush(QColor("#d97706")))
                top.addChild(child)
            top.setExpanded(bool(err))
            tree.addTopLevelItem(top)
        for col in range(tree.columnCount()):
            tree.resizeColumnToContents(col)

    def _format_analysis_plan(self, plan: list) -> str:
        if not plan:
            return "未生成分析计划。"
        lines = []
        for p in plan:
            notes = " ⚠️ " + "; ".join(p.get("special_notes", [])) if p.get("special_notes") else ""
            lines.append(f"### {p.get('table_name', '?')}{notes}")
            lines.append(f"- 类别: `{p.get('category', '-')}`")
            lines.append(f"- 测点数: {p.get('point_count', 0)}")
            lines.append(f"- 单位: `{p.get('unit', '-')}`")
            interval = p.get("interval_days")
            if interval:
                lines.append(f"- 监测间隔: **{interval:.0f}天** ({p.get('interval_source', '')})")
            lines.append(f"- 容差: `{p.get('tolerance', '-')}` 级别: `{p.get('severity', '-')}`")
            methods = p.get("verification_methods", [])
            if methods:
                lines.append("**验证规则：**")
                for m in methods:
                    lines.append(f"- {m.get('name')} = `{m.get('formula', '')}` 容差={m.get('tolerance', '')} 级别={m.get('severity', '')}")
            lines.append("")
        return "\n".join(lines)


# ─── 空闲面板（首次打开 / 已完成后新建任务）─────────────────
class IdlePanel(QWidget):
    file_chosen = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.addStretch(1)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        logo.setObjectName("HeroLogo")
        pixmap = QPixmap(str(APP_LOGO_PATH))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(logo)

        brand = QLabel("城安物联")
        brand.setObjectName("BrandName")
        brand.setAlignment(Qt.AlignCenter)
        layout.addWidget(brand)

        brand_en = QLabel("CITY SAFETY IOT")
        brand_en.setObjectName("BrandSubName")
        brand_en.setAlignment(Qt.AlignCenter)
        layout.addWidget(brand_en)

        title = QLabel("建筑变形监测报告核验台")
        title_font = title.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("拖入 PDF 文件，或点击按钮选择")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #5b6472; font-size: 14px; margin: 10px;")
        layout.addWidget(subtitle)

        btn = QPushButton("📂  选择 PDF 文件")
        btn.setMinimumSize(220, 50)
        btn.setStyleSheet("font-size: 14px; padding: 10px;")
        btn.clicked.connect(self._on_browse)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(btn)
        h.addStretch(1)
        layout.addLayout(h)

        self._hint = QLabel("支持拖拽 .pdf 文件到本窗口任意位置")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setStyleSheet("color: #94a3b8; font-size: 12px; margin-top: 18px;")
        layout.addWidget(self._hint)

        layout.addStretch(2)

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PDF 文件", "", "PDF 文件 (*.pdf)"
        )
        if path:
            self.file_chosen.emit(path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.file_chosen.emit(urls[0].toLocalFile())


# ─── 主窗 ───────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setObjectName("AppShell")
        self.setWindowTitle("建筑变形监测报告核验台 · 桌面版")
        self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setMinimumSize(1180, 760)
        self.resize(1280, 820)
        self.setFont(QFont("Microsoft YaHei UI", 9))
        self.setStyleSheet(PROFESSIONAL_STYLESHEET)
        self._settings = load_settings()

        self._worker: Optional[PipelineWorker] = None
        self._thread: Optional[QThread] = None
        self._result: Optional[PipelineResult] = None
        self._log_lines: list[str] = []
        self._current_pdf: Optional[str] = None

        self._build_ui()
        self._build_menu()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪")
        self.setAcceptDrops(True)

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(2)

        # 左侧：配置面板
        self.config_panel = ConfigPanel(self._settings)
        config_wrap = QWidget()
        config_wrap.setObjectName("Sidebar")
        cw = QVBoxLayout(config_wrap)
        cw.setContentsMargins(12, 12, 12, 12)

        brand_row = QHBoxLayout()
        brand_icon = QLabel()
        brand_icon.setFixedSize(QSize(44, 44))
        brand_icon.setAlignment(Qt.AlignCenter)
        mark = QPixmap(str(APP_MARK_PATH))
        if not mark.isNull():
            brand_icon.setPixmap(mark.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand_row.addWidget(brand_icon)

        brand_text = QVBoxLayout()
        brand_name = QLabel("城安物联")
        brand_name.setObjectName("BrandName")
        brand_text.addWidget(brand_name)
        brand_en = QLabel("CITY SAFETY IOT")
        brand_en.setObjectName("BrandSubName")
        brand_text.addWidget(brand_en)
        brand_row.addLayout(brand_text)
        brand_row.addStretch(1)
        cw.addLayout(brand_row)

        sidebar_title = QLabel("配置")
        sidebar_title.setObjectName("SidebarTitle")
        cw.addWidget(sidebar_title)
        cw.addWidget(self.config_panel)
        splitter.addWidget(config_wrap)

        # 右侧：状态栈
        self.stack = QStackedWidget()
        self.idle_panel = IdlePanel()
        self.idle_panel.file_chosen.connect(self.start_pipeline)
        self.stack.addWidget(self.idle_panel)

        self.running_panel = RunningPanel()
        self.running_panel.cancel_requested.connect(self.cancel_pipeline)
        self.stack.addWidget(self.running_panel)

        self.results_panel = ResultsPanel()
        self.results_panel.new_pdf_requested.connect(self.reset_to_idle)
        self.results_panel.export_md_requested.connect(self._export_md)
        self.results_panel.export_docx_requested.connect(self._export_docx)
        self.results_panel.export_html_requested.connect(self._export_html)
        self.results_panel.export_xlsx_requested.connect(self._export_xlsx)
        self.stack.addWidget(self.results_panel)

        splitter.addWidget(self.stack)
        splitter.setSizes([380, 900])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu("文件")
        a_open = QAction("打开 PDF...", self)
        a_open.triggered.connect(self._menu_open)
        m_file.addAction(a_open)
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(self.close)
        m_file.addSeparator()
        m_file.addAction(a_quit)

        m_help = self.menuBar().addMenu("帮助")
        a_about = QAction("关于", self)
        a_about.triggered.connect(self._show_about)
        m_help.addAction(a_about)

    # ─── 事件处理 ──────────────────────────────────────
    def _menu_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 PDF", "", "PDF 文件 (*.pdf)"
        )
        if path:
            self.start_pipeline(path)

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            "<b>建筑变形监测报告核验台</b><br>"
            "桌面版 (PySide6) · QThread 流水线<br>"
            "<br>"
            "8 步自动核查：PDF → LLM 解析 → 计算/统计/逻辑核校 → AI 自验证 → 报告生成",
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".pdf"):
            self.start_pipeline(urls[0].toLocalFile())

    def start_pipeline(self, pdf_path: str) -> None:
        if self._thread is not None:
            QMessageBox.warning(self, "提示", "已有任务在运行，请先取消或等待完成。")
            return
        if not Path(pdf_path).exists():
            QMessageBox.warning(self, "文件不存在", f"找不到 PDF 文件：\n{pdf_path}")
            return

        cfg = self.config_panel.to_runtime_config(pdf_path)
        if not cfg.llm_api_key:
            ret = QMessageBox.warning(
                self,
                "缺少 LLM API Key",
                "未填写 LLM API Key，流水线会在第 2 步失败。\n是否仍继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return

        self._current_pdf = pdf_path
        self._log_lines.clear()
        self.running_panel.reset()
        self.stack.setCurrentWidget(self.running_panel)
        self.statusBar().showMessage(f"正在处理：{Path(pdf_path).name}")

        # 启动后台 worker
        self._worker = PipelineWorker(cfg)
        self._worker.progress.connect(self.running_panel.on_progress)
        self._worker.log_line.connect(self._on_log_line)
        self._worker.finished.connect(self._on_pipeline_finished)
        self._thread = make_worker_thread(self._worker)
        self._thread.finished.connect(self._on_worker_thread_finished)
        self._thread.start()

    def _on_log_line(self, line: str) -> None:
        self._log_lines.append(line)
        self.running_panel.append_log(line)

    def _on_pipeline_finished(self, result: PipelineResult) -> None:
        self._result = result

        if result.cancelled:
            self.statusBar().showMessage("已取消")
            self.stack.setCurrentWidget(self.idle_panel)
            return

        if not result.success:
            QMessageBox.critical(
                self,
                "处理失败",
                f"流水线在某步骤失败：\n\n{result.error_message}\n\n查看运行日志了解详情。",
            )
            self.statusBar().showMessage("失败")
            self.results_panel.render_failed(result, self._log_lines)
            self.stack.setCurrentWidget(self.results_panel)
            return

        self.results_panel.render(result, self._log_lines)
        self.stack.setCurrentWidget(self.results_panel)
        self.statusBar().showMessage(
            f"完成 - 用时 {result.duration_sec:.1f}s, 错误 {len(result.errors)} / 警告 {len(result.warnings)}"
        )

    def _on_worker_thread_finished(self) -> None:
        """Release QThread references only after Qt confirms the thread stopped."""
        self._thread = None
        self._worker = None

    def cancel_pipeline(self) -> None:
        if self._worker:
            self._worker.cancel()
            self.statusBar().showMessage("已请求取消，等待当前步骤结束...")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._thread is not None and self._thread.isRunning():
            ret = QMessageBox.question(
                self,
                "任务仍在运行",
                "当前检查任务仍在运行。是否取消任务并退出？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            if self._worker:
                self._worker.cancel()
            self._thread.quit()
            if not self._thread.wait(3000):
                QMessageBox.information(
                    self,
                    "正在取消",
                    "已请求取消当前任务。请等待当前步骤结束后再关闭窗口。",
                )
                event.ignore()
                return
        self.config_panel.persist()
        super().closeEvent(event)

    def reset_to_idle(self) -> None:
        self._result = None
        self._log_lines.clear()
        self._current_pdf = None
        self.stack.setCurrentWidget(self.idle_panel)
        self.statusBar().showMessage("就绪")

    # ─── 导出 ──────────────────────────────────────────
    def _export_md(self) -> None:
        if not self._result:
            return
        suggested = Path(self._current_pdf or "report.pdf").stem + "_检查报告.md"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Markdown", suggested, "Markdown (*.md)"
        )
        if path:
            Path(path).write_text(self._result.final_md, encoding="utf-8")
            self.statusBar().showMessage(f"已保存：{path}", 5000)

    def _export_docx(self) -> None:
        if not self._result or not self._result.report:
            return
        suggested = Path(self._current_pdf or "report.pdf").stem + "_检查报告.docx"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Word 文档", suggested, "Word (*.docx)"
        )
        if not path:
            return
        try:
            from src.tools.export_formats import generate_docx
            data = generate_docx(
                self._result.final_md,
                self._result.report,
                self._result.errors,
                self._result.warnings,
            )
            Path(path).write_bytes(data)
            self.statusBar().showMessage(f"已保存：{path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _export_html(self) -> None:
        if not self._result or not self._result.report:
            return
        suggested = Path(self._current_pdf or "report.pdf").stem + "_检查报告.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 HTML", suggested, "HTML (*.html)"
        )
        if not path:
            return
        try:
            from src.tools.export_formats import generate_html
            html = generate_html(
                self._result.final_md,
                getattr(self._result.report, "project_name", "") or "检查报告",
            )
            Path(path).write_text(html, encoding="utf-8")
            self.statusBar().showMessage(f"已保存：{path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _export_xlsx(self) -> None:
        if not self._result or not self._result.report:
            return
        suggested = Path(self._current_pdf or "report.pdf").stem + "_Excel中间层.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 Excel 中间层", suggested, "Excel 工作簿 (*.xlsx)"
        )
        if not path:
            return
        try:
            from src.tools.export_formats import generate_intermediate_xlsx
            data = generate_intermediate_xlsx(
                self._result.report,
                calc_issues=self._result.calc_issues,
                stats_issues=self._result.stats_issues,
                logic_issues=self._result.logic_issues,
                analysis_plan=self._result.analysis_plan,
            )
            Path(path).write_bytes(data)
            self.statusBar().showMessage(f"已保存：{path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

def run_app() -> int:
    """启动桌面应用"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("BuildingDeformationChecker")
    app.setOrganizationName("OpenClaw")

    # 默认中文字体
    f = QFont("Microsoft YaHei UI", 10)
    app.setFont(f)

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_app())
