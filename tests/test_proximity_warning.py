"""Gap 1 修复测试：接近预警值的 proximity warning

基线发现：恒大中心累计 -5.6mm 达预警 6.0mm 的 93%，但报告标"正常"无任何
"接近预警"提示。现有 check_safety_status 只判"已超 vs 未超"，不能识别
"还差一点就到"的情况。

预期行为：当 |cumulative| / warning_value ∈ [0.8, 1.0) 时，应触发 warning
级别的"proximity"提示（不是 error，因为未超）。
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
    TableVerificationConfig,
    ThresholdConfig,
)
from src.tools.logic_checker import check_safety_status


def _make_report(point_id: str, cumulative: float, status: str,
                 warning_value: float = 6.0, control_value: float = 10.0):
    table = MonitoringTable(
        monitoring_item="支护结构顶部水平位移",
        category=MonitoringCategory.HORIZONTAL_DISP,
        monitor_date="2022-05-22",
        verification_config=TableVerificationConfig(interval_days=7),
        points=[
            MeasurementPoint(
                point_id=point_id,
                initial_value=0.0,
                current_value=cumulative,
                current_change=-0.5,
                cumulative_change=cumulative,
                change_rate=-0.05,
                safety_status=status,
            )
        ],
    )
    thresholds = [
        ThresholdConfig(
            item_name="支护结构顶部水平位移",
            warning_value=warning_value,
            control_value=control_value,
            rate_limit=2.0,
        ),
    ]
    return MonitoringReport(tables=[table], thresholds=thresholds)


class ProximityWarningTests(unittest.TestCase):
    """新功能：接近预警值时触发 warning"""

    def test_hengda_93_percent_should_trigger_proximity(self):
        """恒大场景：|cumulative|=5.6, warning=6.0 → 93%，应触发 proximity warning"""
        report = _make_report("11S031-1", -5.6, "正常", warning_value=6.0)
        issues = []
        check_safety_status(report, issues)
        # 应至少有 1 个 warning 提示接近预警
        proximity = [i for i in issues if "接近" in (i.message or "") and i.severity == "warning"]
        self.assertGreaterEqual(len(proximity), 1,
                                f"93% 接近预警应触发 warning：{[i.message for i in issues]}")

    def test_safe_at_30_percent_no_proximity_alert(self):
        """30% 距预警还远，不应触发 proximity warning"""
        report = _make_report("S1", -1.8, "正常", warning_value=6.0)  # 30%
        issues = []
        check_safety_status(report, issues)
        proximity = [i for i in issues if "接近" in (i.message or "")]
        self.assertEqual(len(proximity), 0, f"30% 不应触发：{[i.message for i in issues]}")

    def test_at_50_percent_no_proximity_alert(self):
        """50% 仍不算接近，不应触发"""
        report = _make_report("S1", -3.0, "正常", warning_value=6.0)  # 50%
        issues = []
        check_safety_status(report, issues)
        proximity = [i for i in issues if "接近" in (i.message or "")]
        self.assertEqual(len(proximity), 0)

    def test_at_80_percent_triggers_proximity(self):
        """80% 应当触发 proximity（阈值边界）"""
        report = _make_report("S1", -4.8, "正常", warning_value=6.0)  # 80%
        issues = []
        check_safety_status(report, issues)
        proximity = [i for i in issues if "接近" in (i.message or "") and i.severity == "warning"]
        self.assertGreaterEqual(len(proximity), 1)

    def test_at_99_percent_triggers_proximity_not_error(self):
        """99% 接近但未超，应为 warning（不是 error）"""
        report = _make_report("S1", -5.94, "正常", warning_value=6.0)
        issues = []
        check_safety_status(report, issues)
        # 不应是 error（未超）
        errors = [i for i in issues if i.severity == "error" and i.field_name == "安全状态"]
        self.assertEqual(len(errors), 0, "99% 未超不应 error")
        # 但应是 warning
        proximity = [i for i in issues if "接近" in (i.message or "")]
        self.assertGreaterEqual(len(proximity), 1)

    def test_at_100_percent_keeps_existing_error_behavior(self):
        """100% 触发原有 error（已超 → 应为报警），不应被 proximity 覆盖"""
        report = _make_report("S1", -6.0, "正常", warning_value=6.0)
        issues = []
        check_safety_status(report, issues)
        errors = [i for i in issues if i.severity == "error" and "应为" in (i.message or "")]
        self.assertGreaterEqual(len(errors), 1, "已超应仍触发原有 error")

    def test_proximity_includes_percentage_in_message(self):
        """proximity warning message 应包含具体百分比，便于用户判断紧急程度"""
        report = _make_report("11S031-1", -5.6, "正常", warning_value=6.0)
        issues = []
        check_safety_status(report, issues)
        proximity = [i for i in issues if "接近" in (i.message or "")]
        self.assertGreaterEqual(len(proximity), 1)
        # 消息应含 93% 或类似数字
        msg = proximity[0].message
        self.assertTrue(
            "93" in msg or "93%" in msg or "0.93" in msg,
            f"消息应含 93% 或类似百分比：{msg}",
        )

    def test_rate_proximity_to_rate_limit(self):
        """速率接近 rate_limit 时也应 proximity warning"""
        # cumulative 远低于警戒，但 rate 达 90% rate_limit
        report = _make_report("S1", -1.0, "正常", warning_value=6.0)
        # 调整 rate：让速率达到 rate_limit (2.0 mm/d) 的 90%
        report.tables[0].points[0].change_rate = -1.8
        issues = []
        check_safety_status(report, issues)
        proximity = [i for i in issues if "接近" in (i.message or "") and "速率" in (i.message or "")]
        self.assertGreaterEqual(len(proximity), 1,
                                f"速率接近限值应触发：{[i.message for i in issues]}")


if __name__ == "__main__":
    unittest.main()
