"""跨期累计连续性核查单元测试（calculation_checker.check_cross_period_continuity）

场景：监测公司模板把多次监测横向并排到同一 sheet，无独立初始值列。
工具的 (current - initial) 公式失效，必须用 累计_{N+1} = 累计_N + 本次_{N+1}
跨期校验。
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
from src.tools.calculation_checker import (
    check_cross_period_continuity,
    run_calculation_checks,
)


def _make_period_table(item: str, date: str, points: list) -> MonitoringTable:
    return MonitoringTable(
        monitoring_item=item,
        category=MonitoringCategory.SETTLEMENT,
        monitor_date=date,
        verification_config=TableVerificationConfig(),
        points=points,
    )


class CrossPeriodContinuityTests(unittest.TestCase):
    """直接调用 check_cross_period_continuity 验证逻辑"""

    def test_detects_cumulative_jump(self):
        """期 N 累计 1.0，期 N+1 本次 0.5 → 应该 1.5；若报 3.5 应报错"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0)],
        )
        t2 = _make_period_table(
            "立柱沉降", "2026-05-12",
            [MeasurementPoint(point_id="LZ1", cumulative_change=3.5, current_change=0.5)],
        )
        report = MonitoringReport(tables=[t1, t2])
        issues = []
        check_cross_period_continuity(report, issues)
        cross = [i for i in issues if i.field_name == "跨期累计连续性"]
        self.assertEqual(len(cross), 1)
        self.assertEqual(cross[0].point_id, "LZ1")
        self.assertIn("3.50", cross[0].message)

    def test_continuity_passes_when_consistent(self):
        """期 N 累计 + 期 N+1 本次 = 期 N+1 累计 时不应报错"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0)],
        )
        t2 = _make_period_table(
            "立柱沉降", "2026-05-12",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.5, current_change=0.5)],
        )
        report = MonitoringReport(tables=[t1, t2])
        issues = []
        check_cross_period_continuity(report, issues)
        cross = [i for i in issues if i.field_name == "跨期累计连续性"]
        self.assertEqual(len(cross), 0)

    def test_single_period_skipped(self):
        """只有 1 期数据时不做检查（向后兼容）"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.5)],
        )
        report = MonitoringReport(tables=[t1])
        issues = []
        check_cross_period_continuity(report, issues)
        self.assertEqual(len(issues), 0)

    def test_different_monitoring_items_not_paired(self):
        """不同 monitoring_item 的表不该相互检查"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0)],
        )
        t2 = _make_period_table(
            "地下水位", "2026-05-12",
            [MeasurementPoint(point_id="LZ1", cumulative_change=100.0, current_change=99.0)],
        )
        report = MonitoringReport(tables=[t1, t2])
        issues = []
        check_cross_period_continuity(report, issues)
        self.assertEqual(len(issues), 0)

    def test_skips_when_point_missing_in_one_period(self):
        """某测点在某期缺失 → 跳过该测点，但其他测点照常验证"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [
                MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0),
                MeasurementPoint(point_id="LZ2", cumulative_change=2.0, current_change=0.0),
            ],
        )
        t2 = _make_period_table(
            "立柱沉降", "2026-05-12",
            # 只有 LZ1（缺 LZ2）
            [MeasurementPoint(point_id="LZ1", cumulative_change=10.0, current_change=0.5)],
        )
        report = MonitoringReport(tables=[t1, t2])
        issues = []
        check_cross_period_continuity(report, issues)
        cross = [i for i in issues if i.field_name == "跨期累计连续性"]
        self.assertEqual(len(cross), 1)
        self.assertEqual(cross[0].point_id, "LZ1")

    def test_multi_period_chain_validates_each_step(self):
        """3 期数据：连续 N→N+1 与 N+1→N+2 都应独立验证"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0)],
        )
        t2 = _make_period_table(
            "立柱沉降", "2026-05-12",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.5, current_change=0.5)],
        )
        t3 = _make_period_table(
            "立柱沉降", "2026-05-13",
            # 错：1.5 + 0.3 = 1.8 但报 5.0
            [MeasurementPoint(point_id="LZ1", cumulative_change=5.0, current_change=0.3)],
        )
        report = MonitoringReport(tables=[t1, t2, t3])
        issues = []
        check_cross_period_continuity(report, issues)
        cross = [i for i in issues if i.field_name == "跨期累计连续性"]
        # 只 t2→t3 不一致；t1→t2 是 OK 的
        self.assertEqual(len(cross), 1)
        self.assertIn("5.00", cross[0].message)

    def test_relative_tolerance_for_large_cumulative(self):
        """累计 > 10mm 时 5% 相对容差启动；小偏差不应误报"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [MeasurementPoint(point_id="LZ1", cumulative_change=100.0, current_change=0.0)],
        )
        t2 = _make_period_table(
            "立柱沉降", "2026-05-12",
            # 应为 100 + 2 = 102，实际报 103（差 1，相对 1%，在 5% 容差内）
            [MeasurementPoint(point_id="LZ1", cumulative_change=103.0, current_change=2.0)],
        )
        report = MonitoringReport(tables=[t1, t2])
        issues = []
        check_cross_period_continuity(report, issues)
        cross = [i for i in issues if i.field_name == "跨期累计连续性"]
        self.assertEqual(len(cross), 0, "小偏差应在 5% 容差内")

    def test_integration_with_run_calculation_checks(self):
        """run_calculation_checks 是否调用了 cross_period_continuity"""
        t1 = _make_period_table(
            "立柱沉降", "2026-05-11",
            [MeasurementPoint(point_id="LZ1", cumulative_change=1.0, current_change=0.0)],
        )
        t2 = _make_period_table(
            "立柱沉降", "2026-05-12",
            [MeasurementPoint(point_id="LZ1", cumulative_change=99.0, current_change=0.5)],
        )
        report = MonitoringReport(tables=[t1, t2])
        all_issues = run_calculation_checks(report)
        cross = [i for i in all_issues if i.field_name == "跨期累计连续性"]
        self.assertEqual(len(cross), 1)

    def test_same_date_duplicate_tables_not_paired(self):
        """LLM 可能在不同 chunk 重复抽取同期数据 → 两表 monitor_date 完全相同。
        这种情况不应视为前后两期，否则会算出"自己减自己"类型的假阳性。"""
        t1 = _make_period_table(
            "周边建筑竖向位移", "2026-05-15",
            [MeasurementPoint(point_id="SC1", cumulative_change=-1.06, current_change=0.77)],
        )
        # 同样的 monitoring_item + 同样的 date → LLM 在另一 chunk 重复抽
        t2 = _make_period_table(
            "周边建筑竖向位移", "2026-05-15",
            [MeasurementPoint(point_id="SC1", cumulative_change=-1.06, current_change=0.77)],
        )
        report = MonitoringReport(tables=[t1, t2])
        issues = []
        check_cross_period_continuity(report, issues)
        cross = [i for i in issues if i.field_name == "跨期累计连续性"]
        self.assertEqual(len(cross), 0, "同日期重复表不应触发跨期检查")

    def test_deep_displacement_table_not_affected(self):
        """深层位移走自己的 prev/current 路径，不应被本函数检查"""
        from src.models.data_models import DeepDisplacementPoint

        t1 = MonitoringTable(
            monitoring_item="深层水平位移",
            category=MonitoringCategory.DEEP_HORIZONTAL,
            monitor_date="2026-05-11",
            borehole_id="CX1",
            verification_config=TableVerificationConfig(),
            deep_points=[
                DeepDisplacementPoint(depth=1.0, current_cumulative=1.0, current_change=0.0),
            ],
        )
        t2 = MonitoringTable(
            monitoring_item="深层水平位移",
            category=MonitoringCategory.DEEP_HORIZONTAL,
            monitor_date="2026-05-12",
            borehole_id="CX1",
            verification_config=TableVerificationConfig(),
            deep_points=[
                DeepDisplacementPoint(depth=1.0, current_cumulative=99.0, current_change=0.5),
            ],
        )
        report = MonitoringReport(tables=[t1, t2])
        issues = []
        check_cross_period_continuity(report, issues)
        # 深层位移表没有 points，应跳过
        cross = [i for i in issues if i.field_name == "跨期累计连续性"]
        self.assertEqual(len(cross), 0)


if __name__ == "__main__":
    unittest.main()
