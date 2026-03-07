import unittest

from src.models.data_models import (
    DeepDisplacementPoint,
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


if __name__ == "__main__":
    unittest.main()
