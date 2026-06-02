import unittest

from src.models.data_models import (
    DeepDisplacementPoint,
    MeasurementPoint,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    StatisticsSummary,
)
from src.tools.statistics_checker import run_statistics_checks


class StatisticsCheckerTests(unittest.TestCase):
    def test_multi_page_group_allows_cross_page_point_reference(self):
        first_page = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            points=[
                MeasurementPoint(point_id="S5", cumulative_change=2.0),
                MeasurementPoint(point_id="S7", cumulative_change=1.5),
            ],
        )
        second_page = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            points=[MeasurementPoint(point_id="Z1", cumulative_change=2.0)],
            statistics=StatisticsSummary(
                positive_max_id="S5",
                positive_max_value=2.0,
            ),
        )
        report = MonitoringReport(tables=[first_page, second_page])

        issues = run_statistics_checks(report)

        self.assertFalse(any("不在本表中" in issue.message for issue in issues))
        self.assertFalse(any(issue.field_name == "正方向最大统计" for issue in issues))

    def test_deep_table_supports_max_change_without_rate(self):
        table = MonitoringTable(
            monitoring_item="深层水平位移",
            category=MonitoringCategory.DEEP_HORIZONTAL,
            borehole_id="CX10",
            deep_points=[
                DeepDisplacementPoint(depth=1.0, previous_cumulative=0.10, current_cumulative=0.24, current_change=0.14),
                DeepDisplacementPoint(depth=2.0, previous_cumulative=-0.05, current_cumulative=0.03, current_change=0.08),
            ],
            statistics=StatisticsSummary(
                max_change_id="深度1.0m",
                max_change_value=0.14,
            ),
        )
        report = MonitoringReport(tables=[table])

        issues = run_statistics_checks(report)

        self.assertFalse(any(issue.field_name == "最大速率统计" for issue in issues))
        self.assertFalse(any(issue.severity == "error" for issue in issues))

    def test_deep_max_change_matching_current_cumulative_is_not_change_error(self):
        """深层位移宽表中，LLM 可能把当前累计列误放进 max_change 摘要。"""
        table = MonitoringTable(
            monitoring_item="支护结构深层水平位移",
            category=MonitoringCategory.DEEP_HORIZONTAL,
            borehole_id="CX6",
            deep_points=[
                DeepDisplacementPoint(
                    depth=0.5,
                    previous_cumulative=3.73,
                    current_cumulative=3.65,
                    current_change=-0.08,
                ),
                DeepDisplacementPoint(
                    depth=1.5,
                    previous_cumulative=3.41,
                    current_cumulative=3.60,
                    current_change=0.19,
                ),
            ],
            statistics=StatisticsSummary(
                max_change_id="0.5",
                max_change_value=3.65,
            ),
        )
        report = MonitoringReport(tables=[table])

        issues = run_statistics_checks(report)

        self.assertFalse(
            any(issue.severity == "error" and issue.field_name == "最大变化位移统计" for issue in issues),
            [issue.message for issue in issues],
        )

    def test_missing_borehole_id_does_not_break_grouped_statistics(self):
        first_page = MonitoringTable(
            monitoring_item="horizontal displacement",
            category=MonitoringCategory.HORIZONTAL_DISP,
            borehole_id=None,
            points=[MeasurementPoint(point_id="S1", cumulative_change=1.2)],
        )
        second_page = MonitoringTable(
            monitoring_item="horizontal displacement",
            category=MonitoringCategory.HORIZONTAL_DISP,
            borehole_id=None,
            points=[MeasurementPoint(point_id="S2", cumulative_change=0.5)],
            statistics=StatisticsSummary(
                positive_max_id="S1",
                positive_max_value=1.2,
            ),
        )

        issues = run_statistics_checks(MonitoringReport(tables=[first_page, second_page]))

        self.assertFalse(any(issue.severity == "error" for issue in issues))

    def test_all_negative_max_rate_numeric_convention_is_info(self):
        table = MonitoringTable(
            monitoring_item="settlement",
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

        self.assertFalse(any(issue.severity == "error" for issue in issues))
        self.assertTrue(any(issue.severity == "info" and issue.point_id == "D2" for issue in issues))

    def test_positive_max_that_matches_current_change_is_not_cumulative_error(self):
        """宽表摘要可能是本次变化最大值，不应硬拿来和累计最大值比较。"""
        table = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            points=[
                MeasurementPoint(point_id="W7", cumulative_change=6.50, current_change=0.12),
                MeasurementPoint(point_id="W17", cumulative_change=2.10, current_change=0.46),
                MeasurementPoint(point_id="W3", cumulative_change=-0.20, current_change=-0.03),
            ],
            statistics=StatisticsSummary(
                positive_max_id="W17",
                positive_max_value=0.46,
            ),
        )

        issues = run_statistics_checks(MonitoringReport(tables=[table]))

        self.assertFalse(
            any(issue.severity == "error" and issue.field_name == "正方向最大统计" for issue in issues),
            [issue.message for issue in issues],
        )


if __name__ == "__main__":
    unittest.main()
