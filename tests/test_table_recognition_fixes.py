"""
针对鱼珠乐天报告中发现的表格识别和统计误报问题的回归测试。
用真实数据结构模拟 LLM 解析结果，验证下游检查器的修复效果。
"""
import unittest
from src.models.data_models import (
    CheckIssue, DeepDisplacementPoint, MeasurementPoint,
    MonitoringCategory, MonitoringReport, MonitoringTable,
    StatisticsSummary, TableVerificationConfig,
)
from src.tools.statistics_checker import run_statistics_checks
from src.tools.logic_checker import check_summary_consistency
from src.tools.extraction_quality import analyze_extraction_quality


def _make_config(**overrides):
    defaults = dict(
        unit="mm", unit_conversion=1.0, cumulative_tolerance=0.15,
        rate_tolerance=0.05, interval_days=9, direction_convention="",
        initial_value_reliable=True, severity_for_cumulative="error",
    )
    defaults.update(overrides)
    return TableVerificationConfig(**defaults)


class TestSameSignConvention(unittest.TestCase):
    """测试所有值同号时的行业惯例处理"""

    def _build_all_positive_table(self):
        """竖向位移表：所有累计值为正（如鱼珠乐天 S1~S9 全正）"""
        t = MonitoringTable(
            monitoring_item="支护结构顶部竖向位移",
            category=MonitoringCategory.VERTICAL_DISP,
            verification_config=_make_config(unit="m", unit_conversion=1000.0,
                                             severity_for_cumulative="warning"),
        )
        # 模拟真实数据：S1=31.21, S2=33.92, S3=42.13, S7=7.40 (最小正值)
        data = [
            ("S1", 31.21), ("S2", 33.92), ("S3", 42.13), ("S4", 28.06),
            ("S5", 26.50), ("S6", 27.49), ("S7", 7.40), ("S8", 18.09), ("S9", 20.73),
        ]
        for pid, cum in data:
            t.points.append(MeasurementPoint(
                point_id=pid, cumulative_change=cum, change_rate=0.1,
            ))
        # 报告统计：正方向最大=S3/42.13, 负方向最大=S7/7.40（行业惯例：最小正值）
        t.statistics = StatisticsSummary(
            positive_max_id="S3", positive_max_value=42.13,
            negative_max_id="S7", negative_max_value=7.40,
            max_rate_id="S3", max_rate_value=0.484,
        )
        return t

    def _build_all_negative_table(self):
        """周边地面沉降表：所有累计值为负"""
        t = MonitoringTable(
            monitoring_item="周边地面沉降",
            category=MonitoringCategory.SETTLEMENT,
            verification_config=_make_config(unit="m", unit_conversion=1000.0,
                                             severity_for_cumulative="warning"),
        )
        data = [("D1", -29.75), ("D2", -27.41), ("D4", -31.23), ("D5", -30.59)]
        for pid, cum in data:
            t.points.append(MeasurementPoint(
                point_id=pid, cumulative_change=cum, change_rate=-0.01,
            ))
        # 报告统计：正方向最大=D2/-27.41（行业惯例：绝对值最小的负值）
        t.statistics = StatisticsSummary(
            positive_max_id="D2", positive_max_value=-27.41,
            negative_max_id="D4", negative_max_value=-31.23,
            max_rate_id="D2", max_rate_value=-0.010,
        )
        return t

    def test_all_positive_negative_stat_is_info_not_error(self):
        """所有值为正时，负方向最大填最小正值应为 info 而非 error"""
        report = MonitoringReport(project_name="test")
        report.tables.append(self._build_all_positive_table())
        issues = run_statistics_checks(report)
        neg_issues = [i for i in issues if "负方向最大" in i.field_name]
        for issue in neg_issues:
            self.assertNotEqual(issue.severity, "error",
                                f"行业惯例不应标为error: {issue.message}")
            self.assertIn(issue.severity, ("info", "warning"))

    def test_all_negative_positive_stat_is_info_not_error(self):
        """所有值为负时，正方向最大填绝对值最小的负值应为 info 而非 error"""
        report = MonitoringReport(project_name="test")
        report.tables.append(self._build_all_negative_table())
        issues = run_statistics_checks(report)
        pos_issues = [i for i in issues if "正方向最大" in i.field_name]
        for issue in pos_issues:
            self.assertNotEqual(issue.severity, "error",
                                f"行业惯例不应标为error: {issue.message}")

    def test_real_positive_max_error_still_detected(self):
        """真正的正方向最大不一致仍应标为 error"""
        t = MonitoringTable(
            monitoring_item="测试表",
            category=MonitoringCategory.HORIZONTAL_DISP,
            verification_config=_make_config(),
        )
        t.points = [
            MeasurementPoint(point_id="A", cumulative_change=10.0),
            MeasurementPoint(point_id="B", cumulative_change=5.0),
        ]
        t.statistics = StatisticsSummary(
            positive_max_id="B", positive_max_value=5.0,  # 错误：应为 A=10.0
        )
        report = MonitoringReport(project_name="test")
        report.tables.append(t)
        issues = run_statistics_checks(report)
        errors = [i for i in issues if i.severity == "error"]
        self.assertTrue(len(errors) > 0, "真正的统计错误应被检出")


class TestDeepDisplacementTable(unittest.TestCase):
    """测试深层位移表的识别和检查"""

    def _build_deep_table_c1(self):
        """模拟 C1 深层位移表：只有 previous_cumulative, current_cumulative, change_rate"""
        t = MonitoringTable(
            monitoring_item="深层水平位移观测",
            category=MonitoringCategory.DEEP_HORIZONTAL,
            borehole_id="C1",
            borehole_depth=12.0,
            verification_config=_make_config(initial_value_reliable=False),
        )
        # 真实数据：所有 current_cumulative 为正，没有 current_change
        data = [
            (0.5, 0.40, 0.59, 0.019), (1, 0.39, 0.48, 0.009),
            (3.5, 0.26, 0.56, 0.030), (6, 0.49, 0.70, 0.021),
            (12, 0.02, 0.03, 0.001),
        ]
        for depth, prev, curr, rate in data:
            t.deep_points.append(DeepDisplacementPoint(
                depth=depth, previous_cumulative=prev,
                current_cumulative=curr, current_change=None, change_rate=rate,
            ))
        t.statistics = StatisticsSummary()  # 无统计区域
        return t

    def test_deep_table_no_current_change_not_flagged_as_abnormal(self):
        """深层位移表没有 current_change 列不应标记为异常"""
        report = MonitoringReport(project_name="test")
        report.tables.append(self._build_deep_table_c1())
        analyze_extraction_quality(report)
        flags = report.table_extraction_flags.get(0, [])
        change_flags = [f for f in flags if "current_change" in f.lower()]
        self.assertEqual(len(change_flags), 0,
                         f"深层表缺少 current_change 不应标记异常: {change_flags}")

    def test_deep_table_empty_statistics_no_errors(self):
        """深层位移表没有统计区域时不应产生统计错误"""
        report = MonitoringReport(project_name="test")
        report.tables.append(self._build_deep_table_c1())
        issues = run_statistics_checks(report)
        errors = [i for i in issues if i.severity == "error"]
        self.assertEqual(len(errors), 0,
                         f"空统计不应产生错误: {[e.message for e in errors]}")


class TestAnchorForceTable(unittest.TestCase):
    """测试锚索拉力表的识别"""

    def test_anchor_no_change_rate_not_flagged(self):
        """锚索拉力表没有 change_rate 列不应标记为异常"""
        t = MonitoringTable(
            monitoring_item="锚索拉力",
            category=MonitoringCategory.ANCHOR_FORCE,
            verification_config=_make_config(unit="kN"),
        )
        data = [
            ("M3", 172.8, 178.7, 5.9), ("M4", 193.6, 192.7, -0.9),
            ("M5", 216.6, 214.9, -1.7), ("M8", 165.3, 167.4, 2.1),
            ("M9", 202.3, 202.9, 0.6),
        ]
        for pid, init, curr, cum in data:
            t.points.append(MeasurementPoint(
                point_id=pid, initial_value=init, current_value=curr,
                cumulative_change=cum, change_rate=None,  # 锚索无速率
            ))
        t.statistics = StatisticsSummary(
            max_force_id="M5", max_force_value=214.9,
            min_force_id="M8", min_force_value=167.4,
        )
        report = MonitoringReport(project_name="test")
        report.tables.append(t)
        analyze_extraction_quality(report)
        flags = report.table_extraction_flags.get(0, [])
        rate_flags = [f for f in flags if "change_rate" in f]
        self.assertEqual(len(rate_flags), 0,
                         f"锚索表缺少 change_rate 不应标记异常: {rate_flags}")


class TestLogicCheckerSameSign(unittest.TestCase):
    """测试逻辑检查中汇总表同号行业惯例"""

    def test_summary_all_negative_positive_max_is_info(self):
        """汇总表：分表全负时，正方向最大填绝对值最小的负值应为 info"""
        from src.models.data_models import ReportSummaryItem
        report = MonitoringReport(project_name="test")
        t = MonitoringTable(
            monitoring_item="周边地面沉降",
            category=MonitoringCategory.SETTLEMENT,
            verification_config=_make_config(),
        )
        for pid, cum in [("D1", -29.75), ("D2", -27.41), ("D4", -31.23)]:
            t.points.append(MeasurementPoint(point_id=pid, cumulative_change=cum))
        report.tables.append(t)
        report.summary_items.append(ReportSummaryItem(
            monitoring_item="周边地面沉降",
            positive_max="-27.41", positive_max_id="D2",
            negative_max="-31.23", negative_max_id="D4",
        ))
        # 需要构建语义映射
        report.summary_map = {"周边地面沉降": ["周边地面沉降"]}
        report.threshold_map = {}
        issues: list[CheckIssue] = []
        check_summary_consistency(report, issues)
        pos_issues = [i for i in issues if "正方向最大" in i.field_name]
        for issue in pos_issues:
            self.assertNotEqual(issue.severity, "error",
                                f"行业惯例不应标为error: {issue.message}")


if __name__ == "__main__":
    unittest.main()
