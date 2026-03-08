import json
import re
import sys
import types
import unittest
from unittest.mock import patch

from src.models.data_models import (
    CheckIssue,
    MeasurementPoint,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    ReportSummaryItem,
)
from src.tools.logic_checker import run_logic_checks
from src.tools.self_verifier import verify_errors_with_llm


class LogicCheckerTests(unittest.TestCase):
    def test_summary_consistency_respects_positive_negative_direction(self):
        table = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            points=[
                MeasurementPoint(point_id="P1", cumulative_change=-1.20),
                MeasurementPoint(point_id="P2", cumulative_change=-2.40),
            ],
        )
        report = MonitoringReport(
            summary_items=[
                ReportSummaryItem(
                    monitoring_item="支护结构顶部水平位移",
                    positive_max="1.20",
                    positive_max_id="P1",
                    negative_max="-2.40",
                    negative_max_id="P2",
                )
            ],
            tables=[table],
            threshold_map={"_": []},
            summary_map={"支护结构顶部水平位移": ["支护结构顶部水平位移"]},
        )

        issues = run_logic_checks(report)

        self.assertTrue(any(issue.field_name == "正方向最大" for issue in issues))
        self.assertFalse(any(issue.field_name == "负方向最大" for issue in issues if issue.severity == "error"))


class SelfVerifierTests(unittest.TestCase):
    def test_self_verifier_processes_more_than_twenty_errors_and_writes_origin(self):
        issues = [
            CheckIssue(
                severity="error",
                table_name="支护结构顶部水平位移",
                point_id=f"P{i}",
                field_name="累计变化量",
                expected_value="1.00",
                actual_value="2.00",
                message="累计变化量不符",
            )
            for i in range(21)
        ]
        report = MonitoringReport(raw_text="支护结构顶部水平位移 原文片段")

        class FakeOpenAI:
            calls = 0

            def __init__(self, *args, **kwargs):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create)
                )

            def _create(self, model, messages, temperature, max_tokens, timeout):
                FakeOpenAI.calls += 1
                prompt = messages[1]["content"]
                batch_size = int(re.search(r"本批共 (\d+) 个错误", prompt).group(1))
                verdicts = []
                for idx in range(batch_size):
                    if idx % 2 == 0:
                        verdicts.append({
                            "error_idx": idx,
                            "verdict": "downgrade",
                            "reason": "OCR错列导致疑似误报",
                            "suspected_origin": "extraction",
                        })
                    else:
                        verdicts.append({
                            "error_idx": idx,
                            "verdict": "confirm",
                            "reason": "确为报告错误",
                            "suspected_origin": "report",
                        })
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(verdicts, ensure_ascii=False)))]
                )

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
            verified = verify_errors_with_llm(report, issues)

        self.assertEqual(FakeOpenAI.calls, 5)
        self.assertEqual(verified[-1].severity, "warning")
        self.assertEqual(verified[0].suspected_source, "extraction")

    def test_self_verifier_falls_back_to_single_item_when_batch_times_out(self):
        issues = [
            CheckIssue(
                severity="error",
                table_name="支护结构顶部水平位移",
                point_id=f"P{i}",
                field_name="累计变化量",
                expected_value="1.00",
                actual_value="2.00",
                message="累计变化量不符",
            )
            for i in range(2)
        ]
        report = MonitoringReport(raw_text="支护结构顶部水平位移 原文片段")

        class FakeOpenAI:
            calls = []

            def __init__(self, *args, **kwargs):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create)
                )

            def _create(self, model, messages, temperature, max_tokens, timeout):
                prompt = messages[1]["content"]
                batch_size = int(re.search(r"本批共 (\d+) 个错误", prompt).group(1))
                FakeOpenAI.calls.append(batch_size)
                if batch_size > 1:
                    raise TimeoutError("batch timeout")
                verdicts = [{
                    "error_idx": 0,
                    "verdict": "downgrade",
                    "reason": "拆单后确认更像提取误差",
                    "suspected_origin": "extraction",
                }]
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(verdicts, ensure_ascii=False)))]
                )

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}), \
             patch("src.tools.self_verifier.time.sleep", return_value=None):
            verified = verify_errors_with_llm(report, issues)

        self.assertEqual(FakeOpenAI.calls[0], 2)
        self.assertEqual(FakeOpenAI.calls.count(1), 2)
        self.assertTrue(all(issue.severity == "warning" for issue in verified))

    def test_self_verifier_parallel_mode_keeps_results_correct(self):
        issues = [
            CheckIssue(
                severity="error",
                table_name="支护结构顶部水平位移",
                point_id=f"P{i}",
                field_name="累计变化量",
                expected_value="1.00",
                actual_value="2.00",
                message="累计变化量不符",
            )
            for i in range(8)
        ]
        report = MonitoringReport(raw_text="支护结构顶部水平位移 原文片段")

        class FakeOpenAI:
            def __init__(self, *args, **kwargs):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create)
                )

            def _create(self, model, messages, temperature, max_tokens, timeout):
                prompt = messages[1]["content"]
                batch_size = int(re.search(r"本批共 (\d+) 个错误", prompt).group(1))
                verdicts = [{
                    "error_idx": idx,
                    "verdict": "downgrade" if idx % 2 == 0 else "confirm",
                    "reason": "并发复核测试",
                    "suspected_origin": "logic" if idx % 2 == 0 else "report",
                } for idx in range(batch_size)]
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(verdicts, ensure_ascii=False)))]
                )

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}), \
             patch("src.config.SELF_VERIFY_MAX_PARALLEL", 2):
            verified = verify_errors_with_llm(report, issues)

        warning_count = sum(1 for issue in verified if issue.severity == "warning")
        error_count = sum(1 for issue in verified if issue.severity == "error")
        self.assertGreaterEqual(warning_count, 3)
        self.assertGreaterEqual(error_count, 3)

    def test_self_verifier_truncates_when_error_count_exceeds_limit(self):
        issues = [
            CheckIssue(
                severity="error",
                table_name="支护结构顶部水平位移",
                point_id=f"P{i}",
                field_name="累计变化量",
                expected_value="1.00",
                actual_value="2.00",
                message="累计变化量不符",
            )
            for i in range(30)
        ]
        report = MonitoringReport(raw_text="支护结构顶部水平位移 原文片段")
        events: list[dict] = []

        class FakeOpenAI:
            calls = 0

            def __init__(self, *args, **kwargs):
                self.chat = types.SimpleNamespace(
                    completions=types.SimpleNamespace(create=self._create)
                )

            def _create(self, model, messages, temperature, max_tokens, timeout):
                FakeOpenAI.calls += 1
                prompt = messages[1]["content"]
                batch_size = int(re.search(r"本批共 (\d+) 个错误", prompt).group(1))
                verdicts = [{
                    "error_idx": idx,
                    "verdict": "confirm",
                    "reason": "确认",
                    "suspected_origin": "report",
                } for idx in range(batch_size)]
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(verdicts, ensure_ascii=False)))]
                )

        with patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}), \
             patch("src.config.SELF_VERIFY_MAX_ERRORS", 10), \
             patch("src.config.SELF_VERIFY_BATCH_SIZE", 5), \
             patch("src.config.SELF_VERIFY_MAX_PARALLEL", 1):
            verify_errors_with_llm(report, issues, progress_callback=events.append)

        self.assertTrue(any(evt.get("stage") == "truncated" for evt in events))
        self.assertEqual(FakeOpenAI.calls, 2)


if __name__ == "__main__":
    unittest.main()
