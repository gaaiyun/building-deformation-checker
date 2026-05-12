import unittest

from src.models.data_models import CheckIssue, MonitoringReport
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


if __name__ == "__main__":
    unittest.main()
