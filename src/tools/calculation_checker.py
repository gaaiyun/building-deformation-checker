"""
计算验证工具

核心公式：
  本次变化 = 本次测值 - 上次测值
  累计变化 = 本次测值 - 初始测值
  变化速率 = 本次变化 / 时间间隔（天）

逐条验证每个测点的计算结果。

注意：
- 高程类数据（竖向位移/沉降）单位为 m，差值 * 1000 = mm
- 当报告只给出本次和初始值时，由于浮点精度限制（高程通常5位小数，
  0.00001m = 0.01mm），累计值验证允许更大容差
- 水位监测的"初始"可能不是建设初期值，累计可能包含历史叠加
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Optional

from src.config import FLOAT_TOLERANCE, RATE_TOLERANCE
from src.models.data_models import (
    CheckIssue,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
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


def check_cumulative_change(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """
    验证累计变化量 = 本次测值 - 初始测值。

    高程类（m→mm 转换）容差更大，因为高程小数位有限导致精度损失。
    水位类数据的"初始"含义可能不同于建设初期，仅做宽松检查。
    """
    is_elevation = table.category in (
        MonitoringCategory.VERTICAL_DISP,
        MonitoringCategory.SETTLEMENT,
    )
    is_water = table.category == MonitoringCategory.WATER_LEVEL

    for pt in table.points:
        if pt.initial_value is None or pt.current_value is None or pt.cumulative_change is None:
            continue

        if is_elevation:
            expected = (pt.current_value - pt.initial_value) * 1000.0
            # 高程5位小数 → 0.01mm 精度，69次累积误差可达几mm
            tol = max(FLOAT_TOLERANCE * 5, abs(pt.cumulative_change) * 0.05)
        elif is_water:
            expected = pt.current_value - pt.initial_value
            # 水位数据"初始"含义可能不同，仅做大幅偏差检查
            tol = max(FLOAT_TOLERANCE * 50, abs(pt.cumulative_change) * 0.2)
        else:
            expected = pt.current_value - pt.initial_value
            tol = FLOAT_TOLERANCE

        if not _close_enough(expected, pt.cumulative_change, tol):
            severity = "error"
            if is_elevation or is_water:
                severity = "warning"

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
                    f"{' × 1000' if is_elevation else ''}"
                    f" = {_fmt(expected, 2)}, 报告值 = {_fmt(pt.cumulative_change, 2)}"
                    f"{'（高程精度限制，可能需人工确认）' if is_elevation else ''}"
                    f"{'（水位初始基准可能不同，需人工确认）' if is_water else ''}"
                ),
            ))


def check_change_rate(
    table: MonitoringTable,
    issues: list[CheckIssue],
    interval_days: Optional[float] = None,
) -> None:
    """验证 变化速率 = 本次变化量 / 间隔天数"""
    if interval_days is None:
        interval_days = _infer_interval_days(table)

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

    for pt in table.points:
        if pt.current_change is None or pt.change_rate is None:
            continue
        if abs(pt.current_change) < 1e-6:
            continue

        expected_rate = pt.current_change / interval_days

        if not _close_enough(expected_rate, pt.change_rate, RATE_TOLERANCE):
            issues.append(CheckIssue(
                severity="error",
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="变化速率",
                expected_value=_fmt(expected_rate, 3),
                actual_value=_fmt(pt.change_rate, 3),
                message=(
                    f"变化速率计算不符: "
                    f"本次变化({_fmt(pt.current_change, 2)}) / {interval_days:.0f}天 "
                    f"= {_fmt(expected_rate, 3)}, 报告值 = {_fmt(pt.change_rate, 3)}"
                ),
            ))


def check_deep_displacement_rate(
    table: MonitoringTable,
    issues: list[CheckIssue],
    interval_days: Optional[float] = None,
) -> None:
    """深层水平位移速率: |本次累计 - 上次累计| / 间隔天数"""
    if not table.deep_points:
        return

    if interval_days is None:
        rates_data: list[float] = []
        for dp in table.deep_points:
            if (
                dp.previous_cumulative is not None
                and dp.current_cumulative is not None
                and dp.change_rate is not None
                and abs(dp.change_rate) > 1e-6
            ):
                diff = abs(dp.current_cumulative - dp.previous_cumulative)
                if diff > 1e-6:
                    inferred = diff / dp.change_rate
                    if 0.5 < abs(inferred) < 365:
                        rates_data.append(round(abs(inferred)))

        if rates_data:
            interval_days = Counter(rates_data).most_common(1)[0][0]

    table_label = f"{table.monitoring_item}({table.borehole_id})"

    if interval_days is None:
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

    logger.info("深层位移表 [%s] 推断监测间隔 = %.0f 天", table_label, interval_days)

    for dp in table.deep_points:
        if dp.previous_cumulative is None or dp.current_cumulative is None or dp.change_rate is None:
            continue

        diff = abs(dp.current_cumulative - dp.previous_cumulative)
        expected_rate = diff / interval_days

        if not _close_enough(expected_rate, dp.change_rate, RATE_TOLERANCE):
            issues.append(CheckIssue(
                severity="error",
                table_name=table_label,
                point_id=f"深度{dp.depth}m",
                field_name="变化速率",
                expected_value=_fmt(expected_rate, 3),
                actual_value=_fmt(dp.change_rate, 3),
                message=(
                    f"深层位移速率不符: "
                    f"|{_fmt(dp.current_cumulative, 2)} - {_fmt(dp.previous_cumulative, 2)}| "
                    f"/ {interval_days:.0f} = {_fmt(expected_rate, 3)}, "
                    f"报告值 = {_fmt(dp.change_rate, 3)}"
                ),
            ))


def check_anchor_force(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """锚索拉力: 累计变化量 = 本次内力 - 初始内力"""
    if table.category not in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
        return

    for pt in table.points:
        if pt.initial_value is None or pt.current_value is None or pt.cumulative_change is None:
            continue

        expected = pt.current_value - pt.initial_value
        if not _close_enough(expected, pt.cumulative_change, FLOAT_TOLERANCE):
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

    for table in report.tables:
        logger.info("=== 计算验证: %s ===", table.monitoring_item)

        if table.category in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
            check_anchor_force(table, issues)
        elif table.deep_points:
            check_deep_displacement_rate(table, issues)
        else:
            check_cumulative_change(table, issues)
            check_change_rate(table, issues)

    return issues
