"""Gap 5: 最大速率统计实际值远小于表内 max(|rate|) → 应触发 warning。

监测报告测试 ERROR-05 (D2):
- 表内速率 D1=-0.019, D2=-0.010, D5=-0.156
- 报告 max_rate=D2/-0.010（"closest to 0" 行业口径）
- 实际 max(|rate|) = D5/0.156 (是报告值的 15.6x)
- 报告的 max_rate 远低于真实最大 → 应警告"实际最大速率被掩盖"

设计：
- 当 |reported| < 0.3 × max(|rate|) 时（即报告值 < 真实最大的 30%）
  emit warning 提醒"行业口径让真实最大速率被掩盖"
- 这不与现有 "行业口径 info" 矛盾：info 解释为何匹配，
  warning 提醒数据风险，二者可并存
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.data_models import (
    MeasurementPoint,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    StatisticsSummary,
)
from src.tools.statistics_checker import run_statistics_checks


class MaxRateDisagreementTests(unittest.TestCase):

    def test_d2_case_emits_warning_about_real_max_being_hidden(self):
        """ERROR-05 真实案例：D5/-0.156 真实最大被 D2/-0.010 掩盖"""
        table = MonitoringTable(
            monitoring_item="周边地面沉降",
            category=MonitoringCategory.SETTLEMENT,
            points=[
                MeasurementPoint(point_id="D1", change_rate=-0.019),
                MeasurementPoint(point_id="D2", change_rate=-0.010),
                MeasurementPoint(point_id="D5", change_rate=-0.156),
            ],
            statistics=StatisticsSummary(
                max_rate_id="D2",
                max_rate_value=-0.010,
            ),
        )
        issues = run_statistics_checks(MonitoringReport(tables=[table]))

        # 现有"行业口径" info 仍保留（兼容）
        infos = [i for i in issues if i.severity == "info"]
        self.assertGreaterEqual(len(infos), 1, "保留行业口径 info")

        # 新增：max_rate 实际值与表内 max(|rate|) 严重不一致 → warning
        warnings = [
            i for i in issues
            if i.severity == "warning"
            and "速率" in (i.field_name or "")
        ]
        self.assertGreaterEqual(
            len(warnings), 1,
            f"|reported|=0.010 远小于真实 max=0.156 → 应 warning. issues={[i.message for i in issues]}",
        )
        # 消息应提及真实最大点 D5
        self.assertTrue(
            any("D5" in (w.message or "") or "0.156" in (w.message or "") for w in warnings),
            f"应提及真实最大测点：{[w.message for w in warnings]}",
        )

    def test_g1_case_emits_warning(self):
        """ERROR-07 类似案例：G1/-0.008 vs G5/-0.181"""
        table = MonitoringTable(
            monitoring_item="管线沉降",
            category=MonitoringCategory.SETTLEMENT,
            points=[
                MeasurementPoint(point_id="G1", change_rate=-0.008),
                MeasurementPoint(point_id="G2", change_rate=-0.020),
                MeasurementPoint(point_id="G5", change_rate=-0.181),
            ],
            statistics=StatisticsSummary(
                max_rate_id="G1",
                max_rate_value=-0.008,
            ),
        )
        issues = run_statistics_checks(MonitoringReport(tables=[table]))
        warnings = [
            i for i in issues
            if i.severity == "warning" and "速率" in (i.field_name or "")
        ]
        self.assertGreaterEqual(len(warnings), 1, f"应 warning：{[i.message for i in issues]}")

    def test_close_reported_no_warning(self):
        """报告值 ≈ max(|rate|) → 不警告"""
        table = MonitoringTable(
            monitoring_item="settlement",
            category=MonitoringCategory.SETTLEMENT,
            points=[
                MeasurementPoint(point_id="D1", change_rate=-0.019),
                MeasurementPoint(point_id="D2", change_rate=-0.150),  # 接近 max
                MeasurementPoint(point_id="D5", change_rate=-0.156),  # 实际 max
            ],
            statistics=StatisticsSummary(
                max_rate_id="D5",
                max_rate_value=-0.156,
            ),
        )
        issues = run_statistics_checks(MonitoringReport(tables=[table]))
        warnings = [
            i for i in issues
            if i.severity == "warning" and "速率" in (i.field_name or "")
        ]
        self.assertEqual(len(warnings), 0, f"报告值即真实最大，不应警告：{[w.message for w in warnings]}")

    def test_mild_disagreement_no_warning(self):
        """轻微差异（≥30% of max）→ 不警告（防误报）"""
        table = MonitoringTable(
            monitoring_item="settlement",
            category=MonitoringCategory.SETTLEMENT,
            points=[
                MeasurementPoint(point_id="D1", change_rate=-0.080),  # 报告（轻微低估）
                MeasurementPoint(point_id="D5", change_rate=-0.100),  # 实际 max
            ],
            statistics=StatisticsSummary(
                max_rate_id="D1",
                max_rate_value=-0.080,
            ),
        )
        issues = run_statistics_checks(MonitoringReport(tables=[table]))
        warnings = [
            i for i in issues
            if i.severity == "warning" and "速率" in (i.field_name or "")
        ]
        # 0.080/0.100 = 80% → 不警告
        self.assertEqual(len(warnings), 0, f"轻微低估不警告：{[w.message for w in warnings]}")

    def test_sign_mismatch_when_all_negative_caught(self):
        """全 rate 为负但报告值符号反 (+0.156 vs -0.156) → 应识别符号矛盾

        旧实现 abs() 抹平符号，漏报。新行为应触发 warning，但不作为确定错误。
        """
        table = MonitoringTable(
            monitoring_item="周边地面沉降",
            category=MonitoringCategory.SETTLEMENT,
            points=[
                MeasurementPoint(point_id="D1", change_rate=-0.019),
                MeasurementPoint(point_id="D2", change_rate=-0.010),
                MeasurementPoint(point_id="D5", change_rate=-0.156),
            ],
            statistics=StatisticsSummary(max_rate_id="D5", max_rate_value=+0.156),  # 符号错
        )
        issues = run_statistics_checks(MonitoringReport(tables=[table]))
        rate_issues = [i for i in issues if "速率" in (i.field_name or "")]
        self.assertGreaterEqual(len(rate_issues), 1, f"应识别符号矛盾：{issues}")
        severities = {i.severity for i in rate_issues}
        self.assertNotIn("error", severities, f"符号口径矛盾应保留为 warning 而非 error：{severities}")
        self.assertIn("warning", severities)

    def test_mixed_sign_message_does_not_falsely_claim_same_direction(self):
        table = MonitoringTable(
            monitoring_item="周边地面沉降",
            category=MonitoringCategory.SETTLEMENT,
            points=[
                MeasurementPoint(point_id="D1", change_rate=0.20),
                MeasurementPoint(point_id="D2", change_rate=-0.05),
                MeasurementPoint(point_id="D3", change_rate=0.10),
            ],
            statistics=StatisticsSummary(max_rate_id="D1", max_rate_value=-0.20),
        )
        issues = run_statistics_checks(MonitoringReport(tables=[table]))
        sign_issues = [issue for issue in issues if "符号矛盾" in (issue.message or "")]
        self.assertGreaterEqual(len(sign_issues), 1)
        self.assertNotIn("全部速率同向", sign_issues[0].message)
        self.assertIn("符号相反", sign_issues[0].message)

    def test_small_absolute_values_no_warning(self):
        """所有速率都很小（< 0.05 mm/d）→ 不警告（噪声范围）"""
        table = MonitoringTable(
            monitoring_item="settlement",
            category=MonitoringCategory.SETTLEMENT,
            points=[
                MeasurementPoint(point_id="A", change_rate=-0.001),
                MeasurementPoint(point_id="B", change_rate=-0.010),
                MeasurementPoint(point_id="C", change_rate=-0.030),
            ],
            statistics=StatisticsSummary(
                max_rate_id="A",
                max_rate_value=-0.001,
            ),
        )
        issues = run_statistics_checks(MonitoringReport(tables=[table]))
        warnings = [
            i for i in issues
            if i.severity == "warning" and "速率" in (i.field_name or "")
        ]
        # 即使 0.001 vs 0.030 是 30x，但绝对值都小（< 0.05），不警告
        self.assertEqual(len(warnings), 0, f"小绝对值不警告：{[w.message for w in warnings]}")


if __name__ == "__main__":
    unittest.main()
