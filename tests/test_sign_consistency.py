"""Gap 6: 初始-本次-累计三量符号一致性

监测报告测试 ERROR-06 (G2 管线沉降):
- initial_value: 9.51112 (本次高程)
- current_value: 9.52275
- diff = current - initial = +0.01163 m = +11.63 mm (上升)
- reported_cumulative: -17.45 mm (报告标"下沉")
- 符号矛盾 → ERROR

规则：
sign(current_value - initial_value) ≠ sign(cumulative_change) → error
当差值 > 显著阈值时（避免噪声 ±0.01 mm 误报）。

该检查独立于现有的"数量级异常"检查（后者需 60%+ 行错才触发），
弥补单个测点孤立错号的盲区。
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
from src.tools.calculation_checker import check_sign_consistency


class SignConsistencyTests(unittest.TestCase):

    def _make_pipe_table(self, point_specs: list[tuple[str, float, float, float]]):
        """点规格: [(id, initial_m, current_m, cumulative_mm), ...]"""
        points = [
            MeasurementPoint(
                point_id=pid,
                initial_value=init,
                current_value=cur,
                cumulative_change=cum,
            )
            for pid, init, cur, cum in point_specs
        ]
        return MonitoringTable(
            monitoring_item="管线沉降",
            category=MonitoringCategory.SETTLEMENT,
            verification_config=TableVerificationConfig(
                unit="m",
                unit_conversion=1000.0,  # m → mm
                initial_value_reliable=True,
            ),
            points=points,
        )

    def test_g2_sign_mismatch_detected(self):
        """G2: 本次 > 初始 (+11.63 mm) 但累计 -17.45 mm → ERROR"""
        table = self._make_pipe_table([
            ("G1", 9.50, 9.52, 20.0),     # +20 mm ↑ OK
            ("G2", 9.51112, 9.52275, -17.45),  # ❌ 符号矛盾
            ("G3", 9.40, 9.42, 19.5),     # OK
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_sign_consistency(report, issues)
        g2_issues = [i for i in issues if i.point_id == "G2"]
        self.assertGreaterEqual(len(g2_issues), 1, f"G2 应被识别为符号矛盾：{[i.message for i in issues]}")
        msg = g2_issues[0].message
        self.assertTrue(
            "符号" in msg or "矛盾" in msg or "不一致" in msg,
            f"消息应说明矛盾性质：{msg}",
        )
        self.assertEqual(g2_issues[0].severity, "error")

    def test_all_consistent_no_issue(self):
        """所有点符号都一致 → 0 issues"""
        table = self._make_pipe_table([
            ("G1", 9.50, 9.52, 20.0),     # +/+ OK
            ("G2", 9.55, 9.53, -20.0),    # -/- OK
            ("G3", 9.40, 9.40, 0.5),      # ≈0/+ OK
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_sign_consistency(report, issues)
        self.assertEqual(issues, [])

    def test_tiny_diff_skipped(self):
        """初始-本次差极小（<0.5 mm）→ 不应触发（噪声范围）"""
        table = self._make_pipe_table([
            ("G1", 9.500, 9.5001, -10.0),  # diff=0.1 mm 微小，不应判定
        ])
        report = MonitoringReport(tables=[table])
        issues = []
        check_sign_consistency(report, issues)
        self.assertEqual(issues, [])

    def test_unreliable_initial_still_checks_sign(self):
        """initial_value_reliable=False 时也应检查符号（GT ERROR-06 真实案例）。

        管线沉降表标记"不可靠"是因为高程精度限制 (0.01mm)，与符号无关。
        Sign(current - initial) 仍应匹配 sign(cumulative_change)。
        监测报告测试 G2: initial=9.51112, current=9.52275 (上升 +11.63mm) 但累计 -17.45mm → ERROR
        """
        points = [
            MeasurementPoint(point_id="G2", initial_value=9.51112, current_value=9.52275, cumulative_change=-17.45),
        ]
        table = MonitoringTable(
            monitoring_item="管线沉降",
            category=MonitoringCategory.SETTLEMENT,
            verification_config=TableVerificationConfig(
                unit="m", unit_conversion=1000.0,
                initial_value_reliable=False,  # 高程不可靠但符号仍可验
            ),
            points=points,
        )
        report = MonitoringReport(tables=[table])
        issues = []
        check_sign_consistency(report, issues)
        g2 = [i for i in issues if i.point_id == "G2"]
        self.assertGreaterEqual(len(g2), 1, "不可靠基准也应检测符号矛盾")
        # 不可靠时降级为 warning，可靠时保持 error
        self.assertEqual(g2[0].severity, "warning")
        # 消息应提示不可靠
        self.assertIn("不可靠", g2[0].message)

    def test_horizontal_displacement_sign(self):
        """水平位移也适用：本次-初始>0 但累计<0 → ERROR"""
        points = [
            MeasurementPoint(point_id="S1", initial_value=100.0, current_value=105.0, cumulative_change=5.0),  # OK
            MeasurementPoint(point_id="S2", initial_value=100.0, current_value=110.0, cumulative_change=-10.0),  # ❌
        ]
        table = MonitoringTable(
            monitoring_item="支护结构顶部水平位移",
            category=MonitoringCategory.HORIZONTAL_DISP,
            verification_config=TableVerificationConfig(
                unit="mm", unit_conversion=1.0,
                initial_value_reliable=True,
            ),
            points=points,
        )
        report = MonitoringReport(tables=[table])
        issues = []
        check_sign_consistency(report, issues)
        s2 = [i for i in issues if i.point_id == "S2"]
        self.assertEqual(len(s2), 1)
        self.assertEqual(s2[0].severity, "error")

    def test_none_values_skipped(self):
        """缺失字段直接跳过，不报错"""
        points = [
            MeasurementPoint(point_id="S1", initial_value=None, current_value=5.0, cumulative_change=-10.0),
            MeasurementPoint(point_id="S2", initial_value=5.0, current_value=None, cumulative_change=-10.0),
            MeasurementPoint(point_id="S3", initial_value=5.0, current_value=10.0, cumulative_change=None),
        ]
        table = MonitoringTable(
            monitoring_item="管线沉降",
            category=MonitoringCategory.SETTLEMENT,
            verification_config=TableVerificationConfig(unit="m", unit_conversion=1000.0, initial_value_reliable=True),
            points=points,
        )
        report = MonitoringReport(tables=[table])
        issues = []
        check_sign_consistency(report, issues)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
