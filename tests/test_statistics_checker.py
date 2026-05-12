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


if __name__ == "__main__":
    unittest.main()
