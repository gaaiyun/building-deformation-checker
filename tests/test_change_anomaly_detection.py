"""Gap 2 修复测试：单期变化幅度异常检测

基线发现：监测报告测试 T6 M5 本次变化 -23.9kN 与累计变化 -1.7kN 严重不协调。
其它测点本次变化都 ≤ 0.4kN，M5 突然 60 倍。若为真：锚索预应力 11% 损失，应触发报警。

需识别两类异常：
1. **行间离群**：某测点本次变化绝对值远大于同表中位数（如 3 倍以上）
2. **本次 vs 累计 不协调**：|本次变化| 显著大于 |累计变化|（≥3 倍），暗示数据可疑

公式 (本次=current_change, 累计=cumulative_change):
- 离群检测：|cc_i| > 3 × median(|cc|)，标 warning
- 协调检测：|cc| > 3 × max(|cum|, 0.5)，标 warning
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
)
from src.tools.calculation_checker import check_current_change_anomaly


def _make_anchor_table(point_specs: list[tuple[str, float, float]]) -> MonitoringTable:
    """构造锚索拉力表; point_specs: [(point_id, current_change, cumulative_change), ...]"""
    points = [
        MeasurementPoint(
            point_id=pid,
            initial_value=200.0,
            current_value=200.0 + cum,
            current_change=cc,
            cumulative_change=cum,
        )
        for pid, cc, cum in point_specs
    ]
    return MonitoringTable(
        monitoring_item="锚索拉力",
        category=MonitoringCategory.ANCHOR_FORCE,
        monitor_date="2024-03-26",
        verification_config=TableVerificationConfig(interval_days=10),
        points=points,
    )


class CurrentChangeAnomalyTests(unittest.TestCase):

    def test_m5_anchor_outlier_detected(self):
        """M5 本次变化 -23.9 远超其它 ≤0.4 → 应标 warning"""
        table = _make_anchor_table([
            ("M3", -0.3, 5.9),
            ("M4", -0.4, -0.9),
            ("M5", -23.9, -1.7),  # ❌ 离群
            ("M8", -0.2, 2.1),
            ("M9", 0.2, 0.6),
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        m5_anomalies = [i for i in issues if i.point_id == "M5" and i.severity == "warning"]
        self.assertGreaterEqual(len(m5_anomalies), 1,
                                f"M5 应被识别为离群：{[i.message for i in issues]}")

    def test_m5_inconsistent_change_vs_cumulative(self):
        """|本次变化 -23.9| ≫ |累计 -1.7|（≥3 倍）→ 应标 warning"""
        table = _make_anchor_table([
            ("M3", 0.1, 5.0),
            ("M5", -23.9, -1.7),  # |cc|=23.9 > 3*|cum|=5.1
            ("M9", 0.2, 0.6),
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        # 至少 1 个 warning 标记 M5
        m5_warnings = [i for i in issues if i.point_id == "M5" and i.severity == "warning"]
        self.assertGreaterEqual(len(m5_warnings), 1)
        # 消息应包含 "23.9" 或 "本次" / "累计" 字样
        msg = m5_warnings[0].message
        self.assertTrue(
            "23.9" in msg or "本次" in msg or "异常" in msg,
            f"消息应说明异常类型：{msg}",
        )

    def test_normal_data_no_anomaly(self):
        """所有测点本次变化都合理 → 0 异常"""
        table = _make_anchor_table([
            ("M1", -0.3, 5.9),
            ("M2", -0.4, -0.9),
            ("M3", -0.5, -1.7),
            ("M4", -0.2, 2.1),
            ("M5", 0.2, 0.6),
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        self.assertEqual(len(issues), 0)

    def test_outlier_skipped_when_too_few_points_but_inconsistency_still_works(self):
        """少于 4 个测点时跳过 outlier 检查（统计无意义），但 inconsistency 仍生效（每行独立）"""
        table = _make_anchor_table([
            ("M1", 0.1, 5.0),
            ("M2", -10.0, 1.0),  # |cc|=10 > 3×max(|cum|=1, 0.5)=3 → inconsistency
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        # M2 应触发 inconsistency 提示（"本次 ≫ 累计"）
        m2_issues = [i for i in issues if i.point_id == "M2"]
        self.assertGreaterEqual(len(m2_issues), 1, "inconsistency 每行独立，不依赖样本量")
        # 消息应是 inconsistency 类型，不是 outlier
        self.assertIn("累计", m2_issues[0].message)

    def test_no_inconsistency_when_change_and_cumulative_comparable(self):
        """少点且 cc/cum 量级合理 → 不应报"""
        table = _make_anchor_table([
            ("M1", 0.1, 0.5),
            ("M2", -0.3, -1.0),  # |cc|=0.3 < 3×max(|cum|=1.0, 0.5)=3 → OK
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        self.assertEqual(len(issues), 0)

    def test_small_outliers_not_flagged(self):
        """微小变化的离群（如 0.001 vs 0.01）不应标—因为绝对值都很小"""
        # 都在 0.01-0.05 范围，0.05 是 0.01 的 5 倍但绝对值很小
        table = _make_anchor_table([
            ("M1", 0.01, 0.1),
            ("M2", 0.02, 0.2),
            ("M3", 0.03, 0.3),
            ("M4", 0.05, 0.5),  # 5x 1st，但 0.05 太小，不算"幅度异常"
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        self.assertEqual(len(issues), 0, f"小变化不应标：{[i.message for i in issues]}")

    def test_horizontal_displacement_also_works(self):
        """水平位移表也应检测异常（不限锚索）"""
        points = [
            MeasurementPoint(point_id=f"S{i}", initial_value=0, current_value=v,
                            current_change=cc, cumulative_change=v)
            for i, (cc, v) in enumerate([
                (-0.3, 1.0),
                (-0.5, 2.0),
                (10.0, 1.5),   # ❌ 离群
                (-0.2, 0.5),
                (0.1, 0.8),
            ], 1)
        ]
        table = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            monitor_date="2024-03-26",
            verification_config=TableVerificationConfig(interval_days=10),
            points=points,
        )
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        s3 = [i for i in issues if i.point_id == "S3"]
        self.assertGreaterEqual(len(s3), 1, "水平位移离群也应识别")

    def test_severity_is_warning_not_error(self):
        """异常是"建议复核"性质，应为 warning（不阻断流程）"""
        table = _make_anchor_table([
            ("M1", 0.1, 1.0),
            ("M2", 0.2, 2.0),
            ("M3", 30.0, 3.0),  # 离群
            ("M4", 0.1, 1.0),
            ("M5", 0.2, 2.0),
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        for i in issues:
            self.assertEqual(i.severity, "warning",
                            f"异常应为 warning，不应为 {i.severity}: {i.message}")


if __name__ == "__main__":
    unittest.main()
