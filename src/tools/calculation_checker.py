"""
计算验证工具

核心公式：
  本次变化 = 本次测值 - 上次测值
  累计变化 = 本次测值 - 初始测值
  变化速率 = 本次变化 / 时间间隔（天）

Uses TableVerificationConfig for adaptive tolerance/severity per table,
instead of hardcoded category branches.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from src.config import RATE_TOLERANCE
from src.tools.extraction_quality import annotate_issues_for_table
from src.models.data_models import (
    CheckIssue,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    TableVerificationConfig,
)

logger = logging.getLogger(__name__)


def _close_enough(a: Optional[float], b: Optional[float], tol: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= tol


def _fmt(v: Optional[float], precision: int = 4) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{precision}f}"


def _infer_interval_days(table: MonitoringTable) -> Optional[float]:
    """从表中反推监测间隔天数（取众数）"""
    intervals: list[float] = []
    for pt in table.points:
        if (
            pt.current_change is not None
            and pt.change_rate is not None
            and abs(pt.change_rate) > 1e-6
        ):
            interval = pt.current_change / pt.change_rate
            if 0.5 < abs(interval) < 365:
                intervals.append(round(abs(interval)))
    if not intervals:
        return None
    return Counter(intervals).most_common(1)[0][0]


def _choose_interval_days(
    table: MonitoringTable,
    configured_interval: Optional[float],
) -> Optional[float]:
    """
    Choose the best interval for rate validation.

    Prefer row-inferred interval when it forms a consistent near-match to the
    global report interval (typical 9d vs 10d discrepancy in fishzhu-like reports).
    """
    inferred_interval = _infer_interval_days(table)
    if configured_interval is None:
        return inferred_interval
    if inferred_interval is None:
        return configured_interval
    if abs(inferred_interval - configured_interval) <= 2:
        return inferred_interval
    return configured_interval


def check_cumulative_change(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """
    验证累计变化量 = 本次测值 - 初始测值。
    Uses table.verification_config for tolerance and severity.
    """
    cfg = table.verification_config

    if not cfg.initial_value_reliable:
        logger.info("表 [%s] 初始基准不可靠，跳过累计变化量验证", table.monitoring_item)
        return

    for pt in table.points:
        if pt.initial_value is None or pt.current_value is None or pt.cumulative_change is None:
            continue

        if cfg.unit_conversion != 1.0:
            expected = (pt.current_value - pt.initial_value) * cfg.unit_conversion
        else:
            expected = pt.current_value - pt.initial_value

        tol = cfg.cumulative_tolerance
        if abs(pt.cumulative_change) > 10:
            tol = max(tol, abs(pt.cumulative_change) * 0.05)

        if not _close_enough(expected, pt.cumulative_change, tol):
            severity = cfg.severity_for_cumulative

            hint = ""
            if cfg.unit == "m":
                hint = "（高程精度限制，可能需人工确认）"
            elif not cfg.initial_value_reliable:
                hint = "（初始基准可能不同，需人工确认）"

            issues.append(CheckIssue(
                severity=severity,
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="累计变化量",
                expected_value=_fmt(expected, 2),
                actual_value=_fmt(pt.cumulative_change, 2),
                message=(
                    f"累计变化量与初始/本次测值推算不符: "
                    f"({_fmt(pt.current_value, 5)} - {_fmt(pt.initial_value, 5)})"
                    f"{' × 1000' if cfg.unit_conversion != 1.0 else ''}"
                    f" = {_fmt(expected, 2)}, 报告值 = {_fmt(pt.cumulative_change, 2)}"
                    f"{hint}"
                ),
            ))


def check_change_rate(
    table: MonitoringTable,
    issues: list[CheckIssue],
    interval_days: Optional[float] = None,
) -> None:
    """验证 变化速率 = 本次变化量 / 间隔天数"""
    cfg = table.verification_config

    if interval_days is None:
        interval_days = _choose_interval_days(table, cfg.interval_days)
    else:
        interval_days = _choose_interval_days(table, interval_days)
    if interval_days is None:
        logger.warning("表 [%s] 无法推断监测间隔天数，跳过速率验证", table.monitoring_item)
        issues.append(CheckIssue(
            severity="info",
            table_name=table.monitoring_item,
            point_id="ALL",
            field_name="变化速率",
            expected_value="N/A",
            actual_value="N/A",
            message="无法推断监测间隔天数，跳过速率验证",
        ))
        return

    logger.info("表 [%s] 推断监测间隔 = %.0f 天", table.monitoring_item, interval_days)
    rate_tol = cfg.rate_tolerance

    for pt in table.points:
        if pt.current_change is None or pt.change_rate is None:
            continue
        if abs(pt.current_change) < 1e-6:
            continue

        expected_rate = pt.current_change / interval_days

        if not _close_enough(expected_rate, pt.change_rate, rate_tol):
            pt_interval = abs(pt.current_change / pt.change_rate) if abs(pt.change_rate) > 1e-6 else 0
            pt_interval_r = round(pt_interval)
            interval_is_clean = abs(pt_interval - pt_interval_r) < 0.3
            if pt_interval_r != interval_days and 1 <= pt_interval_r <= 365 and interval_is_clean:
                severity = "warning"
                msg = (
                    f"变化速率与多数测点间隔({interval_days:.0f}天)不一致: "
                    f"本次变化({_fmt(pt.current_change, 2)}) / {interval_days:.0f}天 = {_fmt(expected_rate, 3)}, "
                    f"报告值 = {_fmt(pt.change_rate, 3)} (反推间隔≈{pt_interval_r}天，可能该点上次监测时间不同)"
                )
            else:
                severity = "error"
                msg = (
                    f"变化速率计算不符: "
                    f"本次变化({_fmt(pt.current_change, 2)}) / {interval_days:.0f}天 "
                    f"= {_fmt(expected_rate, 3)}, 报告值 = {_fmt(pt.change_rate, 3)}"
                )
            issues.append(CheckIssue(
                severity=severity,
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="变化速率",
                expected_value=_fmt(expected_rate, 3),
                actual_value=_fmt(pt.change_rate, 3),
                message=msg,
            ))


def check_deep_displacement_rate(
    table: MonitoringTable,
    issues: list[CheckIssue],
    interval_days: Optional[float] = None,
) -> None:
    """深层水平位移速率验证（仅在存在速率列时执行）。"""
    if not table.deep_points:
        return
    if not any(dp.change_rate is not None for dp in table.deep_points):
        return

    cfg = table.verification_config

    if interval_days is None:
        interval_days = cfg.interval_days
    chosen_interval = None
    if interval_days is not None:
        chosen_interval = interval_days
    if chosen_interval is None:
        rates_data: list[float] = []
        for dp in table.deep_points:
            diff = None
            if dp.current_change is not None:
                diff = abs(dp.current_change)
            elif dp.previous_cumulative is not None and dp.current_cumulative is not None:
                diff = abs(dp.current_cumulative - dp.previous_cumulative)
            if (
                diff is not None
                and dp.change_rate is not None
                and abs(dp.change_rate) > 1e-6
            ):
                if diff > 1e-6:
                    inferred = diff / dp.change_rate
                    if 0.5 < abs(inferred) < 365:
                        rates_data.append(round(abs(inferred)))
        if rates_data:
            chosen_interval = Counter(rates_data).most_common(1)[0][0]
    else:
        rates_data: list[float] = []
        for dp in table.deep_points:
            diff = None
            if dp.current_change is not None:
                diff = abs(dp.current_change)
            elif dp.previous_cumulative is not None and dp.current_cumulative is not None:
                diff = abs(dp.current_cumulative - dp.previous_cumulative)
            if (
                diff is not None
                and dp.change_rate is not None
                and abs(dp.change_rate) > 1e-6
                and diff > 1e-6
            ):
                inferred = diff / dp.change_rate
                if 0.5 < abs(inferred) < 365:
                    rates_data.append(round(abs(inferred)))
        if rates_data:
            inferred_interval = Counter(rates_data).most_common(1)[0][0]
            if abs(inferred_interval - chosen_interval) <= 2:
                chosen_interval = inferred_interval

    table_label = table.monitoring_item
    if table.borehole_id:
        table_label += f"({table.borehole_id})"

    if chosen_interval is None:
        issues.append(CheckIssue(
            severity="info",
            table_name=table_label,
            point_id="ALL",
            field_name="变化速率",
            expected_value="N/A",
            actual_value="N/A",
            message="深层位移表无法推断监测间隔天数，跳过速率验证",
        ))
        return

    interval_days = chosen_interval
    logger.info("深层位移表 [%s] 推断监测间隔 = %.0f 天", table_label, interval_days)

    for dp in table.deep_points:
        if dp.change_rate is None:
            continue

        if dp.current_change is not None:
            diff = dp.current_change
        elif dp.previous_cumulative is not None and dp.current_cumulative is not None:
            diff = dp.current_cumulative - dp.previous_cumulative
        else:
            continue

        expected_rate = diff / interval_days

        if not _close_enough(abs(expected_rate), abs(dp.change_rate), cfg.rate_tolerance):
            issues.append(CheckIssue(
                severity="error",
                table_name=table_label,
                point_id=f"深度{dp.depth}m",
                field_name="变化速率",
                expected_value=_fmt(expected_rate, 3),
                actual_value=_fmt(dp.change_rate, 3),
                message=(
                    f"深层位移速率不符: "
                    f"({_fmt(dp.current_cumulative, 2)} - {_fmt(dp.previous_cumulative, 2)}) "
                    f"/ {interval_days:.0f} = {_fmt(expected_rate, 3)}, "
                    f"报告值 = {_fmt(dp.change_rate, 3)}"
                ),
            ))


def check_deep_displacement_change(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """深层位移本期变化验证：本期变化 = 本次累计 - 上次累计。"""
    if not table.deep_points:
        return

    cfg = table.verification_config
    table_label = table.monitoring_item
    if table.borehole_id:
        table_label += f"({table.borehole_id})"

    for dp in table.deep_points:
        if (
            dp.previous_cumulative is None
            or dp.current_cumulative is None
            or dp.current_change is None
        ):
            continue

        expected_change = dp.current_cumulative - dp.previous_cumulative
        if not _close_enough(expected_change, dp.current_change, cfg.cumulative_tolerance):
            issues.append(CheckIssue(
                severity="error",
                table_name=table_label,
                point_id=f"深度{dp.depth}m",
                field_name="本期变化",
                expected_value=_fmt(expected_change, 3),
                actual_value=_fmt(dp.current_change, 3),
                message=(
                    f"深层位移本期变化不符: "
                    f"({_fmt(dp.current_cumulative, 2)} - {_fmt(dp.previous_cumulative, 2)}) "
                    f"= {_fmt(expected_change, 3)}, 报告值 = {_fmt(dp.current_change, 3)}"
                ),
            ))


def check_anchor_force(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """锚索拉力: 累计变化量 = 本次内力 - 初始内力"""
    cfg = table.verification_config

    for pt in table.points:
        if pt.initial_value is None or pt.current_value is None or pt.cumulative_change is None:
            continue

        expected = pt.current_value - pt.initial_value
        if not _close_enough(expected, pt.cumulative_change, cfg.cumulative_tolerance):
            issues.append(CheckIssue(
                severity="error",
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="累计变化量",
                expected_value=_fmt(expected, 1),
                actual_value=_fmt(pt.cumulative_change, 1),
                message=(
                    f"锚索拉力累计变化不符: "
                    f"本次({_fmt(pt.current_value, 1)}) - 初始({_fmt(pt.initial_value, 1)}) "
                    f"= {_fmt(expected, 1)}, 报告值 = {_fmt(pt.cumulative_change, 1)}"
                ),
            ))


def run_calculation_checks(report: MonitoringReport) -> list[CheckIssue]:
    """对报告中的所有表格运行计算验证"""
    issues: list[CheckIssue] = []

    for table_index, table in enumerate(report.tables):
        logger.info("=== 计算验证: %s ===", table.monitoring_item)
        table_issues: list[CheckIssue] = []

        if table.category in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
            check_anchor_force(table, table_issues)
        elif table.deep_points:
            check_deep_displacement_change(table, table_issues)
            check_deep_displacement_rate(table, table_issues)
        else:
            check_cumulative_change(table, table_issues)
            check_change_rate(table, table_issues)

        annotate_issues_for_table(report, table_issues, table_index, default_source="report")
        issues.extend(table_issues)

    return issues
