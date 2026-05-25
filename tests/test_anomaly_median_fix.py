"""Gap 2 离群检测：用真正的 median（偶数样本量取均值）。

旧实现：abs_ccs[len(abs_ccs) // 2] 是"上中位数"，偶数时偏高。

示例：[0.1, 0.2, 3.0, 30.0]
- 旧（[2]）= 3.0
- 真 median = (0.2 + 3.0) / 2 = 1.6
- 3 × 1.6 = 4.8，30 / 4.8 ≈ 6.25 → 30 应被识别为离群
- 3 × 3.0 = 9.0，30 / 9.0 ≈ 3.33 → 仍触发，但放大了 5 倍上限
  → 不是漏报，但提高了误报阈值

更糟糕的偶数样本：[0.1, 1.0, 10.0, 11.0]
- 旧 = 10.0，3 × 10 = 30。11 < 30 → 不报
- 真 median = 5.5，3 × 5.5 = 16.5。11 < 16.5 → 不报（同结果）

真正坑：[1, 1, 1, 100]
- 旧 = 1，3 × max(1, 0.5) = 3。100 > 3 → 报
- 真 median = 1，结果一样

实际上 median bug 多数情况下**没有功能影响**（因为有 max(median, MIN_ABS) 保底），
但语义不对。我们仍要修，让代码可读 + 与 statistics.median 一致。
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
from src.tools.calculation_checker import check_current_change_anomaly


class AnomalyMedianTests(unittest.TestCase):

    def _make_anchor(self, ccs: list[float]):
        return MonitoringTable(
            monitoring_item="锚索拉力",
            category=MonitoringCategory.ANCHOR_FORCE,
            verification_config=TableVerificationConfig(interval_days=10),
            points=[
                MeasurementPoint(point_id=f"M{i}", initial_value=200.0,
                                 current_value=200.0 + cc,
                                 current_change=cc, cumulative_change=cc)
                for i, cc in enumerate(ccs)
            ],
        )

    def test_even_count_median_is_average_of_two_middles(self):
        """偶数样本 median 应为中间两数均值"""
        # [0.1, 0.2, 3.0, 30.0] → median = (0.2+3.0)/2 = 1.6, 3×1.6=4.8
        # 30 > 4.8 → 离群
        table = self._make_anchor([0.1, 0.2, 3.0, 30.0])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        m_idx_3 = [i for i in issues if i.point_id == "M3" and i.severity == "warning"]
        self.assertGreaterEqual(len(m_idx_3), 1, "30 应被识别为离群")

    def test_message_shows_real_median_not_upper_middle(self):
        """误差消息中显示的中位数应为真 median（不是 [len//2]）"""
        # [0.1, 0.2, 3.0, 30.0]，让 30 触发
        table = self._make_anchor([0.1, 0.2, 3.0, 30.0])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        msgs = [i.message for i in issues if i.point_id == "M3"]
        # 真 median=1.6（不是 3.0）
        # 消息格式：'本次变化 30.0 远超其它测点中位数 1.600' 或类似
        self.assertTrue(any("1.6" in m or "1.60" in m for m in msgs),
                        f"消息应显示真 median 1.6，实际：{msgs}")

    def test_odd_count_unchanged(self):
        """奇数样本 median 行为不变"""
        # [0.1, 0.2, 3.0, 30.0, 50.0] → median = 3.0
        table = self._make_anchor([0.1, 0.2, 3.0, 30.0, 50.0])
        report = MonitoringReport(tables=[table])
        issues = []
        check_current_change_anomaly(report, issues)
        # 30/3=10 > 3，50/3=16.7 > 3 → 都应被识别
        outliers = [i for i in issues if i.point_id in ("M3", "M4") and i.severity == "warning"]
        self.assertGreaterEqual(len(outliers), 2)


if __name__ == "__main__":
    unittest.main()
