"""src.core.pipeline 单元测试

不发起真实 LLM/OCR 调用，所有外部依赖都通过 unittest.mock 打桩。
覆盖 RuntimeConfig 配置同步、PipelineResult 派生属性、CancelledError 取消机制、
以及 run_pipeline 在 PDF 不存在、提前取消等边界场景下的行为。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.pipeline import (
    CancelledError,
    PipelineResult,
    RuntimeConfig,
    _check_cancel,
    _noop_progress,
    run_pipeline,
)
from src.models.data_models import CheckIssue


class TestRuntimeConfigDefaults(unittest.TestCase):
    """RuntimeConfig 默认值应安全可用，无需任何环境变量即可构造"""

    def test_minimal_construction(self):
        cfg = RuntimeConfig(pdf_path="x.pdf")
        self.assertEqual(cfg.pdf_path, "x.pdf")
        self.assertEqual(cfg.llm_api_key, "")
        self.assertTrue(cfg.llm_base_url.startswith("http"))
        self.assertEqual(cfg.llm_model, "qwen3.5-plus")

    def test_default_timeouts_sensible(self):
        cfg = RuntimeConfig(pdf_path="x.pdf")
        self.assertGreater(cfg.llm_timeout_normal, 0)
        self.assertGreater(cfg.llm_parse_timeout_sec, 0)
        self.assertGreater(cfg.llm_parse_chunk_chars, 0)
        self.assertGreater(cfg.llm_parse_max_tokens, 0)

    def test_default_ocr_flags(self):
        cfg = RuntimeConfig(pdf_path="x.pdf")
        # 默认行为：不强制 OCR，但允许自动回退
        self.assertFalse(cfg.use_ocr)
        self.assertFalse(cfg.prefer_ocr)
        self.assertTrue(cfg.auto_fallback)
        self.assertFalse(cfg.skip_text_layer_check)

    def test_default_pipeline_switches(self):
        cfg = RuntimeConfig(pdf_path="x.pdf")
        self.assertFalse(cfg.skip_self_verify)
        self.assertFalse(cfg.skip_ai_review)

    def test_default_output_dir(self):
        cfg = RuntimeConfig(pdf_path="x.pdf")
        self.assertEqual(cfg.output_dir, "output")
        self.assertIsNone(cfg.output_path)


class TestRuntimeConfigToAppGlobals(unittest.TestCase):
    """to_app_globals() 必须把 cfg 同步到 src.config 与环境变量"""

    def test_syncs_to_src_config(self):
        cfg = RuntimeConfig(
            pdf_path="x.pdf",
            llm_api_key="sk-test-123",
            llm_base_url="https://example.com/v1/",
            llm_model="custom-model",
            llm_timeout_normal=99,
            paddle_ocr_token="paddle-tok",
        )
        cfg.to_app_globals()

        import src.config as srccfg

        self.assertEqual(srccfg.LLM_API_KEY, "sk-test-123")
        # 末尾斜杠应被剥离
        self.assertEqual(srccfg.LLM_BASE_URL, "https://example.com/v1")
        self.assertEqual(srccfg.LLM_MODEL, "custom-model")
        self.assertEqual(srccfg.LLM_TIMEOUT_NORMAL, 99)
        self.assertEqual(srccfg.PADDLE_OCR_TOKEN, "paddle-tok")

    def test_syncs_to_env_vars(self):
        cfg = RuntimeConfig(
            pdf_path="x.pdf",
            llm_api_key="sk-env-test",
            llm_model="env-model",
            paddle_ocr_token="env-paddle",
        )
        cfg.to_app_globals()

        self.assertEqual(os.environ.get("LLM_API_KEY"), "sk-env-test")
        self.assertEqual(os.environ.get("LLM_MODEL"), "env-model")
        self.assertEqual(os.environ.get("PADDLE_OCR_TOKEN"), "env-paddle")

    def test_empty_key_does_not_crash(self):
        cfg = RuntimeConfig(pdf_path="x.pdf", llm_api_key="", paddle_ocr_token="")
        # 不应抛异常
        cfg.to_app_globals()
        import src.config as srccfg

        self.assertEqual(srccfg.LLM_API_KEY, "")

    def test_syncs_pdf_extractor_constants(self):
        cfg = RuntimeConfig(
            pdf_path="x.pdf",
            paddle_ocr_token="tok-pe",
            paddle_ocr_model="custom-paddle",
            paddle_ocr_use_async=False,
            paddle_ocr_use_cache=False,
            paddle_ocr_poll_timeout_sec=12.5,
        )
        cfg.to_app_globals()

        from src.tools import pdf_extractor as pe

        self.assertEqual(pe.PADDLE_OCR_TOKEN, "tok-pe")
        self.assertEqual(pe.PADDLE_OCR_MODEL, "custom-paddle")
        self.assertFalse(pe.PADDLE_OCR_USE_ASYNC)
        self.assertFalse(pe.PADDLE_OCR_USE_CACHE)
        self.assertEqual(pe.PADDLE_OCR_POLL_TIMEOUT_SEC, 12.5)


class TestPipelineResultProperties(unittest.TestCase):
    """PipelineResult 的派生属性应正确聚合错误/警告/提示"""

    def _make_issue(self, severity: str) -> CheckIssue:
        return CheckIssue(
            severity=severity,
            table_name="测试表",
            point_id="P1",
            field_name="cumulative_change",
            expected_value="1.0",
            actual_value="2.0",
            message="测试消息",
        )

    def test_empty_defaults(self):
        r = PipelineResult()
        self.assertFalse(r.success)
        self.assertFalse(r.cancelled)
        self.assertIsNone(r.error_message)
        self.assertEqual(r.raw_text, "")
        self.assertEqual(r.all_issues, [])
        self.assertEqual(r.errors, [])
        self.assertEqual(r.warnings, [])
        self.assertEqual(r.infos, [])
        self.assertEqual(r.process_notes, [])
        self.assertEqual(r.step_timings, {})
        self.assertEqual(r.duration_sec, 0.0)

    def test_all_issues_concatenates_three_lists(self):
        r = PipelineResult()
        r.calc_issues = [self._make_issue("error")]
        r.stats_issues = [self._make_issue("warning"), self._make_issue("warning")]
        r.logic_issues = [self._make_issue("info")]
        self.assertEqual(len(r.all_issues), 4)

    def test_errors_filters_severity(self):
        r = PipelineResult()
        r.calc_issues = [self._make_issue("error"), self._make_issue("warning")]
        r.stats_issues = [self._make_issue("error")]
        self.assertEqual(len(r.errors), 2)

    def test_warnings_filters_severity(self):
        r = PipelineResult()
        r.calc_issues = [self._make_issue("warning"), self._make_issue("error")]
        r.logic_issues = [self._make_issue("warning")]
        self.assertEqual(len(r.warnings), 2)

    def test_infos_filters_severity(self):
        r = PipelineResult()
        r.stats_issues = [self._make_issue("info"), self._make_issue("info")]
        r.logic_issues = [self._make_issue("error")]
        self.assertEqual(len(r.infos), 2)

    def test_three_categories_are_mutually_exclusive(self):
        r = PipelineResult()
        r.calc_issues = [
            self._make_issue("error"),
            self._make_issue("warning"),
            self._make_issue("info"),
        ]
        # 错误 + 警告 + 提示 应该等于 all_issues 数量
        self.assertEqual(
            len(r.errors) + len(r.warnings) + len(r.infos),
            len(r.all_issues),
        )


class TestCancelMechanism(unittest.TestCase):
    """取消机制：_check_cancel 与 CancelledError"""

    def test_check_cancel_noop_when_none(self):
        # 不应抛异常
        _check_cancel(None)

    def test_check_cancel_noop_when_event_not_set(self):
        event = threading.Event()
        # 不应抛异常
        _check_cancel(event)

    def test_check_cancel_raises_when_event_set(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(CancelledError):
            _check_cancel(event)

    def test_cancelled_error_is_exception(self):
        self.assertTrue(issubclass(CancelledError, Exception))

    def test_noop_progress_callable(self):
        # 不应抛异常，返回 None
        result = _noop_progress("step1", "label", 50, "detail")
        self.assertIsNone(result)


class TestRunPipelineErrorHandling(unittest.TestCase):
    """run_pipeline 在 PDF 不存在 / 取消 / 异常时应优雅返回 PipelineResult"""

    def test_pdf_does_not_exist_returns_error(self):
        cfg = RuntimeConfig(pdf_path="/no/such/file__definitely_missing.pdf")
        progress_calls: list[tuple] = []

        def cb(step, label, pct, detail):
            progress_calls.append((step, label, pct, detail))

        result = run_pipeline(cfg, progress_callback=cb)

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error_message)
        self.assertIn("不存在", result.error_message)
        # 应至少有一次 error 进度回调
        self.assertTrue(any(c[0] == "error" for c in progress_calls))

    def test_cancelled_before_start_returns_cancelled_quickly(self):
        # 创建一个真实的临时 PDF，避免被 "不存在" 提前短路
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4\n%fake\n")
            tmp_path = tmp.name

        try:
            cfg = RuntimeConfig(pdf_path=tmp_path)
            cancel_event = threading.Event()
            cancel_event.set()  # 启动前已取消

            import time

            start = time.time()
            result = run_pipeline(cfg, cancel_event=cancel_event)
            elapsed = time.time() - start

            self.assertTrue(result.cancelled)
            self.assertFalse(result.success)
            # 应该很快返回（不应触发任何 LLM/OCR 实际调用）
            self.assertLess(elapsed, 5.0)
        finally:
            os.unlink(tmp_path)

    def test_run_pipeline_without_callback_does_not_crash(self):
        # 不传 callback；run_pipeline 应使用 _noop_progress
        cfg = RuntimeConfig(pdf_path="/no/such/file_missing.pdf")
        result = run_pipeline(cfg)  # 不传 progress_callback
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error_message)

    def test_extract_pdf_exception_returns_error_message(self):
        """Step 1 抛异常时，应回退到 except 分支并返回 error_message"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4\n%fake\n")
            tmp_path = tmp.name

        try:
            cfg = RuntimeConfig(pdf_path=tmp_path)

            with patch("src.tools.pdf_extractor.extract_pdf") as mock_extract:
                mock_extract.side_effect = RuntimeError("OCR boom")
                result = run_pipeline(cfg)

            self.assertFalse(result.success)
            self.assertFalse(result.cancelled)
            self.assertIsNotNone(result.error_message)
            self.assertIn("OCR boom", result.error_message)
        finally:
            os.unlink(tmp_path)

    def test_cancellation_after_extract_step(self):
        """Step 1 后置位 cancel_event，应在下一个 _check_cancel 抛 CancelledError"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4\n%fake\n")
            tmp_path = tmp.name

        try:
            cfg = RuntimeConfig(pdf_path=tmp_path)
            cancel_event = threading.Event()

            # 让 extract_pdf 返回一个 mock 提取结果，同时设置取消标志
            fake_extraction = MagicMock()
            fake_extraction.text = "fake text"
            fake_extraction.method = "text_layer"
            fake_extraction.selected_profile = "default"
            fake_extraction.debug_output_dir = ""
            fake_extraction.diagnostics = {}

            def extract_side_effect(*args, **kwargs):
                cancel_event.set()
                return fake_extraction

            with patch("src.tools.pdf_extractor.extract_pdf", side_effect=extract_side_effect):
                result = run_pipeline(cfg, cancel_event=cancel_event)

            self.assertTrue(result.cancelled)
            self.assertFalse(result.success)
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
