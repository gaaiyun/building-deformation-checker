import unittest

from src.models.data_models import (
    DeepDisplacementPoint,
    MeasurementPoint,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    TableVerificationConfig,
)
from src.tools.calculation_checker import run_calculation_checks


class CalculationCheckerTests(unittest.TestCase):
    def test_deep_table_without_rate_column_skips_rate_validation(self):
        table = MonitoringTable(
            monitoring_item="深层水平位移",
            category=MonitoringCategory.DEEP_HORIZONTAL,
            borehole_id="CX10",
            verification_config=TableVerificationConfig(interval_days=14),
            deep_points=[
                DeepDisplacementPoint(
                    depth=1.0,
                    previous_cumulative=0.10,
                    current_cumulative=0.24,
                    current_change=0.14,
                    change_rate=None,
                )
            ],
        )
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)

        self.assertFalse(any(issue.field_name == "变化速率" for issue in issues))
        self.assertFalse(any(issue.field_name == "本期变化" for issue in issues))

    def test_deep_table_change_validation_detects_mismatch(self):
        table = MonitoringTable(
            monitoring_item="深层水平位移",
            category=MonitoringCategory.DEEP_HORIZONTAL,
            borehole_id="CX10",
            verification_config=TableVerificationConfig(interval_days=14),
            deep_points=[
                DeepDisplacementPoint(
                    depth=1.0,
                    previous_cumulative=0.10,
                    current_cumulative=0.24,
                    current_change=0.40,
                )
            ],
        )
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)

        self.assertTrue(any(issue.field_name == "本期变化" for issue in issues))

    def test_unreliable_initial_baseline_skips_cumulative_validation(self):
        table = MonitoringTable(
            monitoring_item="支护结构顶部竖向位移",
            category=MonitoringCategory.VERTICAL_DISP,
            verification_config=TableVerificationConfig(
                unit="m",
                unit_conversion=1000.0,
                initial_value_reliable=False,
                severity_for_cumulative="warning",
            ),
            points=[
                MeasurementPoint(
                    point_id="S7",
                    initial_value=-1.69993,
                    current_value=-1.69741,
                    cumulative_change=7.40,
                    current_change=1.83,
                    change_rate=0.183,
                )
            ],
        )
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)

        self.assertFalse(any(issue.field_name == "累计变化量" for issue in issues))

    def test_rate_validation_prefers_consistent_row_inferred_interval(self):
        table = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            verification_config=TableVerificationConfig(interval_days=9),
            points=[
                MeasurementPoint(
                    point_id="S7",
                    current_change=8.2,
                    change_rate=0.82,
                ),
                MeasurementPoint(
                    point_id="S5",
                    current_change=2.7,
                    change_rate=0.27,
                ),
            ],
        )
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)

        self.assertFalse(any(issue.field_name == "变化速率" for issue in issues))


if __name__ == "__main__":
    unittest.main()
