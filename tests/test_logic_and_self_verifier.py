import json
import re
import sys
import threading
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


def _parse_batch_size(messages: list[dict]) -> int:
    prompt = messages[1]["content"]
    return int(re.search(r"本批共 (\d+) 个错误", prompt).group(1))


class LogicCheckerTests(unittest.TestCase):
    def test_no_tables_is_reported_as_extraction_warning(self):
        report = MonitoringReport(
            raw_text="这是一份说明性 PDF，不包含监测数据表。",
            extraction_diagnostics={"method": "pdfplumber", "clean_chars": 32},
        )

        issues = run_logic_checks(report)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertEqual(issues[0].field_name, "数据表识别")
        self.assertEqual(issues[0].suspected_source, "extraction")

    def test_partial_llm_parse_failure_is_reported_as_extraction_warning(self):
        report = MonitoringReport(
            tables=[
                MonitoringTable(
                    monitoring_item="支护结构顶部水平位移",
                    category=MonitoringCategory.HORIZONTAL_DISP,
                    points=[MeasurementPoint(point_id="P1", cumulative_change=1.0)],
                )
            ],
            extraction_diagnostics={
                "llm_chunk_count": 8,
                "llm_chunk_success_count": 7,
                "llm_chunk_parse_failures": 1,
            },
            threshold_map={"_": []},
            summary_map={},
        )

        issues = run_logic_checks(report)

        self.assertTrue(any(issue.field_name == "LLM 分块解析" for issue in issues))
        self.assertTrue(any(issue.suspected_source == "extraction" for issue in issues))

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

    def test_summary_mismatch_is_warning_due_mapping_and_period_ambiguity(self):
        table = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            points=[
                MeasurementPoint(point_id="W7", cumulative_change=6.50),
                MeasurementPoint(point_id="W17", cumulative_change=0.46),
            ],
        )
        report = MonitoringReport(
            summary_items=[
                ReportSummaryItem(
                    monitoring_item="支护结构顶部水平位移",
                    positive_max="0.46",
                    positive_max_id="W17",
                )
            ],
            tables=[table],
            threshold_map={"_": []},
            summary_map={"支护结构顶部水平位移": ["支护结构顶部水平位移"]},
        )

        issues = run_logic_checks(report)

        self.assertFalse(any(issue.field_name == "正方向最大" and issue.severity == "error" for issue in issues))
        self.assertTrue(any(issue.field_name == "正方向最大" and issue.severity == "warning" for issue in issues))

    def test_unmatched_summary_item_is_info_not_warning(self):
        report = MonitoringReport(
            summary_items=[
                ReportSummaryItem(monitoring_item="11号线下行线沉降累计变化最大值")
            ],
            tables=[
                MonitoringTable(
                    monitoring_item="5号点X方向位移",
                    category=MonitoringCategory.HORIZONTAL_DISP,
                    points=[MeasurementPoint(point_id="P1", cumulative_change=1.0)],
                )
            ],
            threshold_map={"_": []},
            summary_map={"11号线下行线沉降累计变化最大值": []},
        )

        issues = run_logic_checks(report)
        unmatched = [issue for issue in issues if issue.field_name == "11号线下行线沉降累计变化最大值"]

        self.assertEqual([issue.severity for issue in unmatched], ["info"])

    def test_minor_point_count_gap_is_not_logic_warning(self):
        table = MonitoringTable(
            monitoring_item="地铁沉降",
            category=MonitoringCategory.SETTLEMENT,
            point_count=31,
            points=[MeasurementPoint(point_id=f"P{i}", cumulative_change=0.1) for i in range(27)],
        )
        report = MonitoringReport(tables=[table], threshold_map={"_": []}, summary_map={})

        issues = run_logic_checks(report)

        self.assertFalse(any(issue.field_name == "监测点数量" for issue in issues))


class SelfVerifierTests(unittest.TestCase):
    def test_prompt_defaults_to_confirm_without_direct_extraction_evidence(self):
        from src.tools.self_verifier import _build_prompt

        issue = CheckIssue(
            severity="error",
            table_name="支护结构顶部水平位移",
            point_id="P1",
            field_name="最大值统计",
            expected_value="P1=2.0",
            actual_value="P2=1.0",
            message="报告最值点与计算结果不一致",
        )
        prompt = _build_prompt([issue], "支护结构顶部水平位移 原文", 120)

        self.assertIn("默认倾向 confirm", prompt)
        self.assertIn("不是", prompt)
        self.assertIn("列错位的证据", prompt)

    def test_request_verdicts_uses_unified_llm_call(self):
        from src.tools.self_verifier import _request_verdicts

        response = json.dumps([{
            "error_idx": 0,
            "verdict": "confirm",
            "reason": "确认",
            "suspected_origin": "report",
        }], ensure_ascii=False)
        with patch("src.utils.llm_client.call_chat_completion", return_value=response) as call:
            verdicts, error = _request_verdicts(
                None,
                None,
                "prompt",
                timeout_sec=77,
                max_retries=1,
                backoff_sec=2,
                max_tokens=1234,
            )

        self.assertIsNone(error)
        self.assertEqual(verdicts[0]["verdict"], "confirm")
        self.assertEqual(call.call_args.kwargs["timeout"], 77)
        self.assertEqual(call.call_args.kwargs["max_tokens"], 1234)
        self.assertEqual(call.call_args.kwargs["max_retries"], 1)

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

        calls = {"count": 0}
        lock = threading.Lock()

        def fake_call(messages, **kwargs):
            del kwargs
            with lock:
                calls["count"] += 1
            batch_size = _parse_batch_size(messages)
            verdicts = [{
                "error_idx": idx,
                "verdict": "downgrade" if idx % 2 == 0 else "confirm",
                "reason": "OCR错列导致疑似误报" if idx % 2 == 0 else "确为报告错误",
                "suspected_origin": "extraction" if idx % 2 == 0 else "report",
            } for idx in range(batch_size)]
            return json.dumps(verdicts, ensure_ascii=False)

        with patch("src.utils.llm_client.call_chat_completion", side_effect=fake_call):
            verified = verify_errors_with_llm(report, issues)

        self.assertEqual(calls["count"], 5)
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

        calls = []

        def fake_call(messages, **kwargs):
            del kwargs
            batch_size = _parse_batch_size(messages)
            calls.append(batch_size)
            if batch_size > 1:
                return None
            return json.dumps([{
                "error_idx": 0,
                "verdict": "downgrade",
                "reason": "拆单后确认更像提取误差",
                "suspected_origin": "extraction",
            }], ensure_ascii=False)

        with patch("src.utils.llm_client.call_chat_completion", side_effect=fake_call):
            verified = verify_errors_with_llm(report, issues)

        self.assertEqual(calls[0], 2)
        self.assertEqual(calls.count(1), 2)
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

        def fake_call(messages, **kwargs):
            del kwargs
            batch_size = _parse_batch_size(messages)
            verdicts = [{
                "error_idx": idx,
                "verdict": "downgrade" if idx % 2 == 0 else "confirm",
                "reason": "并发复核测试",
                "suspected_origin": "logic" if idx % 2 == 0 else "report",
            } for idx in range(batch_size)]
            return json.dumps(verdicts, ensure_ascii=False)

        with patch("src.utils.llm_client.call_chat_completion", side_effect=fake_call), \
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

        calls = {"count": 0}

        def fake_call(messages, **kwargs):
            del kwargs
            calls["count"] += 1
            batch_size = _parse_batch_size(messages)
            return json.dumps([{
                "error_idx": idx,
                "verdict": "confirm",
                "reason": "确认",
                "suspected_origin": "report",
            } for idx in range(batch_size)], ensure_ascii=False)

        with patch("src.utils.llm_client.call_chat_completion", side_effect=fake_call), \
             patch("src.config.SELF_VERIFY_MAX_ERRORS", 10), \
             patch("src.config.SELF_VERIFY_BATCH_SIZE", 5), \
             patch("src.config.SELF_VERIFY_MAX_PARALLEL", 1):
            verify_errors_with_llm(report, issues, progress_callback=events.append)

        self.assertTrue(any(evt.get("stage") == "truncated" for evt in events))
        self.assertEqual(calls["count"], 2)


if __name__ == "__main__":
    unittest.main()
