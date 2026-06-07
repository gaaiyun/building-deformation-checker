import unittest

from src.models.data_models import CheckIssue, MonitoringCategory, MonitoringReport, MonitoringTable
from src.tools.report_generator import generate_report_md


class ReportGeneratorTests(unittest.TestCase):
    def test_warning_only_report_is_not_marked_as_passed_and_escapes_cells(self):
        issue = CheckIssue(
            severity="warning",
            table_name="表A|表B",
            point_id="P1\nP2",
            field_name="数据表|识别",
            expected_value="至少 1 张表",
            actual_value="0 张",
            message="未识别到表格|需要复核\n请检查 OCR",
            suspected_source="extraction",
        )

        md = generate_report_md(MonitoringReport(), [], [], [issue])

        self.assertIn("需复核", md)
        self.assertIn("表A\\|表B", md)
        self.assertIn("P1<br>P2", md)
        self.assertIn("未识别到表格\\|需要复核<br>请检查 OCR", md)
        self.assertNotIn("自动检查结论**: 监测报告数据计算与统计结果验证通过", md)

    def test_repeated_extraction_flags_are_grouped_by_table_and_period(self):
        report = MonitoringReport(
            tables=[
                MonitoringTable(
                    monitoring_item="地铁隧道沉降累计变化",
                    category=MonitoringCategory.SETTLEMENT,
                    monitor_date="2023-01-01",
                ),
                MonitoringTable(
                    monitoring_item="地铁隧道沉降累计变化",
                    category=MonitoringCategory.SETTLEMENT,
                    monitor_date="2023-01-02",
                ),
            ],
            table_extraction_flags={
                0: ["关键列空值较多: current_change"],
                1: ["关键列空值较多: current_change"],
            },
        )

        md = generate_report_md(report, [], [], [])

        self.assertIn("2 期", md)
        self.assertEqual(md.count("关键列空值较多: current_change"), 1)

    def test_repeated_analysis_plan_sections_are_grouped(self):
        plan = {
            "table_name": "地铁隧道沉降累计变化",
            "category": "竖向位移/沉降",
            "point_count": 27,
            "unit": "mm",
            "unit_conversion": 1.0,
            "conversion_note": "无需换算",
            "initial_reliable": False,
            "reliability_reason": "未见初始值列",
            "interval_days": None,
            "interval_source": "未识别",
            "verification_methods": [
                {
                    "name": "累计值完整性",
                    "formula": "cumulative_change exists",
                    "tolerance": "N/A",
                    "severity": "info",
                }
            ],
            "special_notes": ["只有累计值，无法核验本次变化"],
        }

        md = generate_report_md(
            MonitoringReport(),
            [],
            [],
            [],
            analysis_plan=[{**plan, "table_index": 1}, {**plan, "table_index": 2}],
        )

        self.assertIn("2 张表", md)
        self.assertEqual(md.count("### 地铁隧道沉降累计变化"), 1)


if __name__ == "__main__":
    unittest.main()
