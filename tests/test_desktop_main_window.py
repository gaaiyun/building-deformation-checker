"""PySide6 桌面主窗基础可用性测试。

这些测试不启动真实 LLM/OCR 流水线，但会在 offscreen Qt 环境下实际实例化
配置面板、主窗口和结果面板，覆盖桌面端最容易失效的 UI→RuntimeConfig 链路。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QGroupBox, QPushButton

    from gui_desktop.main_window import ConfigPanel, MainWindow, ResultsPanel
    from src.core.pipeline import PipelineResult
    from src.models.data_models import CheckIssue, MonitoringReport

    _PYSIDE_AVAILABLE = True
    _PYSIDE_ERROR = None
except Exception as exc:  # pragma: no cover - environment-dependent
    _PYSIDE_AVAILABLE = False
    _PYSIDE_ERROR = exc


_qapp = None
if _PYSIDE_AVAILABLE:
    try:
        _qapp = QApplication.instance() or QApplication(["pytest", "-platform", "offscreen"])
    except Exception as exc:  # pragma: no cover
        _PYSIDE_AVAILABLE = False
        _PYSIDE_ERROR = exc


@unittest.skipUnless(_PYSIDE_AVAILABLE, f"PySide6 不可用，跳过桌面主窗测试: {_PYSIDE_ERROR}")
class DesktopMainWindowTests(unittest.TestCase):
    def test_config_panel_builds_runtime_config_from_controls(self):
        panel = ConfigPanel(
            {
                "llm_api_key": "sk-test",
                "llm_base_url": "https://example.test/v1",
                "llm_model": "deepseek-v4-flash",
                "paddle_ocr_token": "ocr-token",
                "paddle_ocr_model": "PaddleOCR-VL-1.6",
                "paddle_ocr_use_async": True,
                "paddle_ocr_use_cache": False,
                "use_ocr": True,
                "skip_self_verify": True,
                "skip_ai_review": True,
            }
        )

        cfg = panel.to_runtime_config("sample.pdf")

        self.assertEqual(cfg.pdf_path, "sample.pdf")
        self.assertEqual(cfg.llm_api_key, "sk-test")
        self.assertEqual(cfg.llm_base_url, "https://example.test/v1")
        self.assertEqual(cfg.llm_model, "deepseek-v4-flash")
        self.assertEqual(cfg.paddle_ocr_token, "ocr-token")
        self.assertEqual(cfg.paddle_ocr_model, "PaddleOCR-VL-1.6")
        self.assertTrue(cfg.use_ocr)
        self.assertTrue(cfg.prefer_ocr)
        self.assertTrue(cfg.skip_self_verify)
        self.assertTrue(cfg.skip_ai_review)
        self.assertFalse(cfg.paddle_ocr_use_cache)

    def test_config_panel_default_ocr_model_is_vl_16(self):
        panel = ConfigPanel({})
        self.assertEqual(panel.paddle_ocr_model.text(), "PaddleOCR-VL-1.6")

    def test_main_window_constructs_three_state_panels(self):
        win = MainWindow()
        try:
            self.assertEqual(win.windowTitle(), "建筑变形监测报告核验台 v2 · 桌面版")
            self.assertIsNotNone(win.config_panel)
            self.assertIsNotNone(win.idle_panel)
            self.assertIsNotNone(win.running_panel)
            self.assertIsNotNone(win.results_panel)
            self.assertEqual(win.stack.count(), 3)
        finally:
            win.close()

    def test_main_window_applies_professional_theme_and_layout_metrics(self):
        win = MainWindow()
        try:
            style = win.styleSheet()
            self.assertEqual(win.objectName(), "AppShell")
            self.assertGreaterEqual(win.minimumWidth(), 1180)
            self.assertIn("QMainWindow#AppShell", style)
            self.assertIn("Microsoft YaHei UI", style)
            self.assertIn("QGroupBox#ConfigCard", style)
            self.assertIn("QPushButton#PrimaryButton", style)
            self.assertIn("QProgressBar::chunk", style)
        finally:
            win.close()

    def test_config_panel_marks_cards_and_primary_action_for_styling(self):
        panel = ConfigPanel({})

        cards = panel.findChildren(QGroupBox, "ConfigCard")
        primary_buttons = panel.findChildren(QPushButton, "PrimaryButton")

        self.assertGreaterEqual(len(cards), 3)
        self.assertEqual(len(primary_buttons), 1)
        self.assertEqual(primary_buttons[0].text(), "保存配置")

    def test_results_panel_renders_summary_and_issue_trees(self):
        panel = ResultsPanel()
        result = PipelineResult(
            success=True,
            report=MonitoringReport(project_name="测试项目", monitoring_company="测试单位"),
            final_md="# 检查报告\n\n正文",
            output_path="output/report.md",
            duration_sec=2.4,
            extraction_method="pdfplumber",
            extraction_profile="pdfplumber",
        )
        result.calc_issues = [
            CheckIssue(
                severity="error",
                table_name="支护结构水平位移",
                point_id="WY1",
                field_name="累计变化量",
                expected_value="1.00",
                actual_value="2.00",
                message="累计变化量不符",
            )
        ]

        panel.render(result, ["log line"])

        self.assertIn("检查报告", panel.tab_md.toPlainText())
        self.assertIn("log line", panel.tab_logs.toPlainText())
        self.assertGreater(panel.tab_calc.topLevelItemCount(), 0)


if __name__ == "__main__":
    unittest.main()
