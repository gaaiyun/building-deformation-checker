"""单元测试：table_analyzer 的混合单位检测（_detect_mixed_units_ratio）

场景：监测公司模板的水平位移表常见格式为
    | 测点 | 初始断面距离(m) | 本次断面距离(m) | 本次变化(mm) | 累计变化(mm) | 速率(mm/d) |
即初始/本次列是 m，累计列是 mm。LLM 偶尔会把 table_unit 标成 "mm"
导致 unit_conversion=1.0，于是 `(current - initial)` 算出的是 m 级别小数，
而 `cumulative_change` 是 mm，二者相差 1000 倍。

本测试验证 _detect_mixed_units_ratio 能基于数学关系自动识别 1000 倍因子，
让 build_verification_config 输出正确的 cfg.unit_conversion=1000。
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
    MonitoringTable,
    TableVerificationConfig,
)
from src.tools.table_analyzer import (
    _detect_mixed_units_ratio,
    build_verification_config,
)


def _make_table(category: MonitoringCategory, points: list) -> MonitoringTable:
    return MonitoringTable(
        monitoring_item="支护结构水平位移",
        category=category,
        verification_config=TableVerificationConfig(),
        points=points,
    )


class DetectMixedUnitsRatioTests(unittest.TestCase):
    """测试核心检测函数"""

    def test_detects_1000x_when_initial_in_meters_cumulative_in_mm(self):
        # 真实数据：质安模板第64次的支护水平位移
        points = [
            MeasurementPoint(point_id="WY236", initial_value=2.0709, current_value=2.0817,
                            cumulative_change=10.8),
            MeasurementPoint(point_id="WY237", initial_value=2.4306, current_value=2.4331,
                            cumulative_change=2.5),
            MeasurementPoint(point_id="WY238", initial_value=2.2869, current_value=2.2887,
                            cumulative_change=1.8),
            MeasurementPoint(point_id="WY239", initial_value=1.6890, current_value=1.6924,
                            cumulative_change=3.4),
            MeasurementPoint(point_id="WY240", initial_value=2.0232, current_value=2.0289,
                            cumulative_change=5.7),
            MeasurementPoint(point_id="WY241", initial_value=2.4477, current_value=2.4595,
                            cumulative_change=11.8),
            MeasurementPoint(point_id="WY242", initial_value=1.5079, current_value=1.5136,
                            cumulative_change=5.7),
        ]
        table = _make_table(MonitoringCategory.HORIZONTAL_DISP, points)
        ratio = _detect_mixed_units_ratio(table)
        self.assertEqual(ratio, 1000.0)

    def test_returns_1_when_both_in_same_unit_mm(self):
        # 真实 mm 数据：变化量直接对应（current-initial）
        points = [
            MeasurementPoint(point_id=f"P{i}", initial_value=10.0, current_value=10.0 + change,
                            cumulative_change=change)
            for i, change in enumerate([0.5, 1.2, 0.8, 1.5, 2.0, 3.0])
        ]
        table = _make_table(MonitoringCategory.HORIZONTAL_DISP, points)
        ratio = _detect_mixed_units_ratio(table)
        self.assertEqual(ratio, 1.0)

    def test_returns_none_with_insufficient_points(self):
        points = [
            MeasurementPoint(point_id="P1", initial_value=1.0, current_value=1.01,
                            cumulative_change=10),
        ]
        table = _make_table(MonitoringCategory.HORIZONTAL_DISP, points)
        self.assertIsNone(_detect_mixed_units_ratio(table))

    def test_skips_zero_change_points(self):
        # 一半点是 0 变化，剩下 6 个是真实 m→mm 数据
        points = [
            MeasurementPoint(point_id=f"P{i}", initial_value=2.0, current_value=2.0,
                            cumulative_change=0)
            for i in range(4)
        ]
        points.extend([
            MeasurementPoint(point_id=f"R{i}", initial_value=2.0 + i*0.01,
                            current_value=2.0 + i*0.01 + 0.005, cumulative_change=5.0)
            for i in range(5)
        ])
        table = _make_table(MonitoringCategory.HORIZONTAL_DISP, points)
        ratio = _detect_mixed_units_ratio(table)
        self.assertEqual(ratio, 1000.0)

    def test_returns_none_for_chaotic_ratios(self):
        # 比值在 1, 100, 10000 之间随机，无明确单位
        from random import seed
        seed(42)
        ratios = [1, 100, 5000, 50, 200, 800]
        points = [
            MeasurementPoint(point_id=f"P{i}", initial_value=1.0,
                            current_value=1.0 + 0.001,
                            cumulative_change=0.001 * r)
            for i, r in enumerate(ratios)
        ]
        table = _make_table(MonitoringCategory.HORIZONTAL_DISP, points)
        self.assertIsNone(_detect_mixed_units_ratio(table))


class BuildVerificationConfigMixedUnitsTests(unittest.TestCase):
    """端到端验证：build_verification_config 自动应用检测结果"""

    def test_horizontal_displacement_with_m_initial_gets_1000x_conversion(self):
        points = [
            MeasurementPoint(point_id="WY236", initial_value=2.0709, current_value=2.0817,
                            cumulative_change=10.8),
            MeasurementPoint(point_id="WY237", initial_value=2.4306, current_value=2.4331,
                            cumulative_change=2.5),
            MeasurementPoint(point_id="WY238", initial_value=2.2869, current_value=2.2887,
                            cumulative_change=1.8),
            MeasurementPoint(point_id="WY239", initial_value=1.6890, current_value=1.6924,
                            cumulative_change=3.4),
            MeasurementPoint(point_id="WY240", initial_value=2.0232, current_value=2.0289,
                            cumulative_change=5.7),
            MeasurementPoint(point_id="WY241", initial_value=2.4477, current_value=2.4595,
                            cumulative_change=11.8),
        ]
        table = _make_table(MonitoringCategory.HORIZONTAL_DISP, points)
        # LLM 错误地标了 mm
        cfg = build_verification_config(table, table_unit="mm", initial_reliable=True)
        self.assertEqual(cfg.unit_conversion, 1000.0,
                        "应自动检测到 m→mm 转换")

    def test_normal_mm_table_keeps_unit_conversion_1(self):
        points = [
            MeasurementPoint(point_id=f"P{i}", initial_value=10.0,
                            current_value=10.0 + change,
                            cumulative_change=change)
            for i, change in enumerate([0.5, 1.2, 0.8, 1.5, 2.0, 3.0])
        ]
        table = _make_table(MonitoringCategory.HORIZONTAL_DISP, points)
        cfg = build_verification_config(table, table_unit="mm", initial_reliable=True)
        self.assertEqual(cfg.unit_conversion, 1.0,
                        "纯 mm 表不应被误判为 m")

    def test_water_level_not_affected(self):
        """水位表有独立的容差放宽逻辑，不应被混合单位检测覆盖"""
        points = [
            MeasurementPoint(point_id=f"W{i}", initial_value=5.0,
                            current_value=5.0 + i*0.01,
                            cumulative_change=i*10.0)
            for i in range(6)
        ]
        table = MonitoringTable(
            monitoring_item="地下水位",
            category=MonitoringCategory.WATER_LEVEL,
            verification_config=TableVerificationConfig(),
            points=points,
        )
        cfg = build_verification_config(table, table_unit="mm", initial_reliable=False)
        # 水位类应进入水位专属分支，不调用 _apply_mixed_units_if_detected
        # 容差应被放宽到 ≥10 mm
        self.assertGreaterEqual(cfg.cumulative_tolerance, 10.0)


if __name__ == "__main__":
    unittest.main()
