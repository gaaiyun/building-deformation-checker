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

    def test_anchor_force_skips_initial_subtraction_when_columns_are_not_comparable(self):
        table = MonitoringTable(
            monitoring_item="锚索拉力观测结果",
            category=MonitoringCategory.ANCHOR_FORCE,
            verification_config=TableVerificationConfig(
                unit="kN",
                initial_value_reliable=False,
            ),
            points=[
                MeasurementPoint(
                    point_id="MS2",
                    initial_value=153.0,
                    current_value=10.9,
                    current_change=1.3,
                    cumulative_change=10.9,
                    change_rate=1.3,
                )
            ],
        )

        issues = run_calculation_checks(MonitoringReport(tables=[table]))

        self.assertFalse(
            any(issue.field_name == "累计变化量" for issue in issues),
            [issue.message for issue in issues],
        )

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

    def test_rounded_point_specific_interval_is_warning_not_error(self):
        """报告速率保留两位时，0.20/0.030≈6.67 天应识别为约 7 天的点级间隔差异。"""
        table = MonitoringTable(
            monitoring_item="地面沉降",
            category=MonitoringCategory.SETTLEMENT,
            verification_config=TableVerificationConfig(interval_days=1),
            points=[
                MeasurementPoint(point_id="SM1", current_change=0.10, change_rate=0.10),
                MeasurementPoint(point_id="SM2", current_change=-0.20, change_rate=-0.20),
                MeasurementPoint(point_id="SM7", current_change=0.20, change_rate=0.030),
            ],
        )
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)
        sm7_rate = [
            issue for issue in issues
            if issue.point_id == "SM7" and issue.field_name == "变化速率"
        ]

        self.assertEqual([issue.severity for issue in sm7_rate], ["warning"])

    def test_first_period_without_initial_uses_current_change_as_initial_baseline(self):
        """无初始值列时，监测时间段第一天的累计值应等于本次变化。"""
        table = MonitoringTable(
            monitoring_item="基坑顶水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            monitor_date="2022-05-16",
            verification_config=TableVerificationConfig(interval_days=1),
            points=[
                MeasurementPoint(point_id="WY1", current_change=0.30, cumulative_change=0.30),
                MeasurementPoint(point_id="WY2", current_change=-0.20, cumulative_change=-0.20),
            ],
        )
        report = MonitoringReport(monitoring_period="2022-05-16至2022-05-22", tables=[table])

        issues = run_calculation_checks(report)

        self.assertFalse(any(issue.field_name == "首期累计基准" for issue in issues))

    def test_first_period_without_initial_skips_when_period_start_is_not_initial_baseline(self):
        """报告时间段首日不等于项目首测日时，不能硬判累计应等于本次变化。"""
        table = MonitoringTable(
            monitoring_item="基坑顶水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            monitor_date="2022-05-16",
            verification_config=TableVerificationConfig(interval_days=1),
            points=[
                MeasurementPoint(point_id="WY1", current_change=0.30, cumulative_change=1.20),
                MeasurementPoint(point_id="WY2", current_change=-0.20, cumulative_change=4.80),
                MeasurementPoint(point_id="WY3", current_change=0.10, cumulative_change=2.90),
            ],
        )
        report = MonitoringReport(
            monitoring_period="2022-05-16至2022-05-22",
            tables=[table],
            raw_text="本报告为例行周报，监测时间段为2022-05-16至2022-05-22。",
        )

        issues = run_calculation_checks(report)
        first_period_errors = [
            issue for issue in issues
            if issue.field_name == "首期累计基准" and issue.severity == "error"
        ]

        self.assertEqual(first_period_errors, [])

    def test_first_period_without_initial_detects_bad_cumulative_baseline_when_evidence_exists(self):
        """有首测证据时，首日累计不等于本次变化应作为计算错误。"""
        table = MonitoringTable(
            monitoring_item="基坑顶水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            monitor_date="2022-05-16",
            monitor_count="第1次",
            verification_config=TableVerificationConfig(interval_days=1),
            points=[
                MeasurementPoint(point_id="WY1", current_change=0.30, cumulative_change=1.20),
            ],
        )
        report = MonitoringReport(
            monitoring_period="2022-05-16至2022-05-22",
            tables=[table],
            raw_text="本期为首次监测，未单列初始值。",
        )

        issues = run_calculation_checks(report)
        first_period = [issue for issue in issues if issue.field_name == "首期累计基准"]

        self.assertEqual(len(first_period), 1)
        self.assertEqual(first_period[0].severity, "error")

    def test_first_period_global_raw_text_cue_does_not_trigger_table_without_evidence(self):
        """全局正文出现“首次”等词，不能让无表级证据的历史累计表误报。"""
        table = MonitoringTable(
            monitoring_item="基坑顶水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            monitor_date="2022-05-16",
            verification_config=TableVerificationConfig(interval_days=1),
            points=[
                MeasurementPoint(point_id="WY1", current_change=0.30, cumulative_change=8.20),
                MeasurementPoint(point_id="WY2", current_change=-0.20, cumulative_change=4.80),
            ],
        )
        report = MonitoringReport(
            monitoring_period="2022-05-16至2022-05-22",
            tables=[table],
            raw_text="首次监测技术交底会议已完成。本报告为第209期历史累计监测报告。",
        )

        issues = run_calculation_checks(report)

        self.assertFalse(any(issue.field_name == "首期累计基准" for issue in issues))


class IntervalInferenceArbitrationTests(unittest.TestCase):
    """configured 与 inferred 显著差距时，按行级支持率仲裁。

    场景：XLSX 模板把多期数据并到一张 sheet，每期间隔 2 天但 LLM 从报告
    日期范围抽到 7 天作为 configured。v1 在差距 >2 天时盲信 configured，
    导致所有行都触发"反推 2 天"误报。
    """

    def _make_multi_period_table(self, configured_interval: float, real_interval: int) -> MonitoringTable:
        """构造一张速率与 real_interval 一致的表（10 行齐刷刷支持推断值）"""
        points = []
        for i in range(10):
            change = 1.0 + 0.1 * i
            points.append(MeasurementPoint(
                point_id=f"WY{240 + i}",
                initial_value=2.0,
                current_value=2.0 + change,
                current_change=change,
                cumulative_change=change,
                change_rate=round(change / real_interval, 3),
            ))
        return MonitoringTable(
            monitoring_item="支护结构水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            verification_config=TableVerificationConfig(
                interval_days=configured_interval,
                initial_value_reliable=True,
            ),
            points=points,
        )

    def test_multi_period_inferred_overrides_misconfigured(self):
        """配置=7天，实际=2天，所有行支持 → 应采用推断的 2 天"""
        table = self._make_multi_period_table(configured_interval=7, real_interval=2)
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)

        # 修复前：会有 10 个"反推 2 天"警告
        # 修复后：用推断 2 天后所有速率都验证通过，应无 rate 相关 issue
        rate_issues = [i for i in issues if i.field_name == "变化速率"]
        self.assertEqual(
            len(rate_issues), 0,
            f"修复后不应有速率误报，实际有 {len(rate_issues)}：{[i.message for i in rate_issues]}",
        )

    def test_misconfigured_with_no_consistent_inference_keeps_configured(self):
        """配置=7天，数据各行间隔不一致 → 推断值置信度低，保留 configured"""
        # 各行间隔分别为 2, 3, 5, 7, 14 天（无共识）
        irregular_intervals = [2, 3, 5, 7, 14] * 2
        points = []
        for i, interval in enumerate(irregular_intervals):
            change = 1.0
            points.append(MeasurementPoint(
                point_id=f"P{i+1}",
                initial_value=2.0,
                current_value=3.0,
                current_change=change,
                cumulative_change=change,
                change_rate=round(change / interval, 3),
            ))
        table = MonitoringTable(
            monitoring_item="不规则间隔表",
            category=MonitoringCategory.HORIZONTAL_DISP,
            verification_config=TableVerificationConfig(
                interval_days=7,
                initial_value_reliable=True,
            ),
            points=points,
        )
        report = MonitoringReport(tables=[table])
        # 这里不断言具体 issue 数，只断言行为不崩溃
        # （混乱数据触发部分速率不一致是预期的）
        run_calculation_checks(report)

    def test_inferred_only_used_when_configured_missing(self):
        """配置缺失时直接用推断值（原有行为保持）"""
        table = self._make_multi_period_table(configured_interval=None, real_interval=10)
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)

        rate_issues = [i for i in issues if i.field_name == "变化速率"]
        self.assertEqual(len(rate_issues), 0)

    def test_minor_offset_within_2_days_uses_inferred(self):
        """配置=10天，实际 9 天 (典型鱼珠乐天场景) → 差 1 天 ≤2，用推断 9"""
        table = self._make_multi_period_table(configured_interval=10, real_interval=9)
        report = MonitoringReport(tables=[table])

        issues = run_calculation_checks(report)
        rate_issues = [i for i in issues if i.field_name == "变化速率"]
        self.assertEqual(len(rate_issues), 0)


if __name__ == "__main__":
    unittest.main()
