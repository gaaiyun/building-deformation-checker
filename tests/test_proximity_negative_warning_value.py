"""Gap 1 proximity: 当 LLM 误抽出负 warning_value 时仍能正确判断。

LLM 偶尔会因列错位/单位混淆把 warning_value=30 抽成 -30。
旧代码 `threshold.warning_value` falsy 检查通过（-30 是 truthy），
但 ratio = abs(cum) / -30 = 负值 → 与 0.80 比较永远 False → 漏报。

修复：要求 warning_value > 0；同理 rate_limit > 0。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.models.data_models import (
    MeasurementPoint,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    TableVerificationConfig,
    ThresholdConfig,
)
import src.tools.logic_checker as logic_checker
from src.tools.logic_checker import run_logic_checks


def _offline_semantic_maps(report):
    """离线替身：用关键词回退建图，避免构造 OpenAI 客户端发起真实网络调用。

    run_logic_checks 内部会调 _build_semantic_maps，后者无条件构造 OpenAI()，
    在无 API key 的 CI 环境会抛 OpenAIError。本测试只验证 proximity 规则逻辑，
    与 LLM 语义匹配无关，故直接走 _build_fallback_maps（纯关键词，无网络）。
    """
    threshold_names = [th.item_name for th in report.thresholds]
    table_names = list({t.monitoring_item for t in report.tables})
    summary_names = [si.monitoring_item for si in report.summary_items]
    logic_checker._build_fallback_maps(report, threshold_names, table_names, summary_names)


def _make(threshold_warn, threshold_rate, cum, rate):
    return MonitoringReport(
        thresholds=[
            ThresholdConfig(
                item_name="管线沉降",
                warning_value=threshold_warn,
                rate_limit=threshold_rate,
            ),
        ],
        tables=[
            MonitoringTable(
                monitoring_item="管线沉降",
                category=MonitoringCategory.SETTLEMENT,
                verification_config=TableVerificationConfig(unit="mm"),
                points=[
                    MeasurementPoint(
                        point_id="G1",
                        initial_value=0,
                        current_value=cum,
                        current_change=cum * 0.1,
                        cumulative_change=cum,
                        change_rate=rate,
                        safety_status="正常",
                    ),
                ],
            ),
        ],
    )


class ProximityNegativeThresholdTests(unittest.TestCase):

    def setUp(self):
        # 拦截 LLM 语义匹配，改用离线关键词回退（无网络、无 API key 依赖）
        self._patch = patch.object(
            logic_checker, "_build_semantic_maps", _offline_semantic_maps
        )
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_negative_warning_value_does_not_block_proximity(self):
        """LLM 抽出负 warning_value (-30, 应为 30) 时，仍能识别 96.8% 接近

        旧代码：ratio = abs(-29.03) / -30 = -0.968 → 永不触发 proximity
        新代码：对 warning_value 取绝对值，恢复 LLM 误抽出的符号错
        """
        report = _make(threshold_warn=-30, threshold_rate=None, cum=-29.03, rate=None)
        issues = run_logic_checks(report)
        prox = [i for i in issues if i.field_name == "安全状态"
                and "接近预警值" in (i.message or "")]
        self.assertGreaterEqual(len(prox), 1,
            "即使 warning_value 被 LLM 误标负号，|cum|=29.03 接近 |limit|=30 应该报")

    def test_zero_warning_value_no_division_by_zero(self):
        """warning_value=0 时不应 div by zero"""
        report = _make(threshold_warn=0, threshold_rate=None, cum=5.0, rate=None)
        try:
            issues = run_logic_checks(report)
            # 应优雅跳过，不抛
        except ZeroDivisionError:
            self.fail("warning_value=0 不应抛 ZeroDivisionError")

    def test_positive_warning_value_still_works(self):
        """正常 warning_value=30 仍正确识别 96.8% 接近"""
        report = _make(threshold_warn=30, threshold_rate=None, cum=-29.03, rate=None)
        issues = run_logic_checks(report)
        prox = [i for i in issues if i.field_name == "安全状态"
                and "接近预警值" in (i.message or "")]
        self.assertGreaterEqual(len(prox), 1, "正向 warning_value=30, |cum|=29.03 → 97% 应识别")


if __name__ == "__main__":
    unittest.main()
