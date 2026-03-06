"""
统计分析验证工具

验证每张表底部的统计值：
  - 当次累计正方向最大统计
  - 当次累计负方向最大统计
  - 最大变化速率统计
  - 最大/最小内力（锚索拉力）

注意：同一监测项可能分布在多张表中（如水平位移分两页），
统计值可能是跨表的全局统计。
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config import FLOAT_TOLERANCE, RATE_TOLERANCE
from src.models.data_models import (
    CheckIssue,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
)

logger = logging.getLogger(__name__)


def _close(a: Optional[float], b: Optional[float], tol: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= tol


def _fmt(v: Optional[float], p: int = 3) -> str:
    return f"{v:.{p}f}" if v is not None else "N/A"


def _gather_sibling_data(
    table: MonitoringTable,
    all_tables: list[MonitoringTable],
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """
    收集与 table 同类的所有表格数据（用于跨表统计验证）。
    返回 (cumulative_values, rate_values)。
    """
    sibling_tables = [
        t for t in all_tables
        if t.monitoring_item == table.monitoring_item
        and t.category == table.category
        and (not table.borehole_id or t.borehole_id == table.borehole_id)
    ]

    cum_vals: list[tuple[str, float]] = []
    rate_vals: list[tuple[str, float]] = []

    for t in sibling_tables:
        for pt in t.points:
            if pt.cumulative_change is not None:
                cum_vals.append((pt.point_id, pt.cumulative_change))
            if pt.change_rate is not None:
                rate_vals.append((pt.point_id, pt.change_rate))
        for dp in t.deep_points:
            label = f"深度{dp.depth}m"
            if dp.current_cumulative is not None:
                cum_vals.append((label, dp.current_cumulative))
            if dp.change_rate is not None:
                rate_vals.append((label, dp.change_rate))

    return cum_vals, rate_vals


def check_table_statistics(
    table: MonitoringTable,
    all_tables: list[MonitoringTable],
    issues: list[CheckIssue],
    already_checked: set[str],
) -> None:
    """验证单张表的统计数据"""
    stats = table.statistics
    table_label = table.monitoring_item
    if table.borehole_id:
        table_label += f"({table.borehole_id})"

    # 同名同类表只验证一次统计（因为统计值是全局的）
    stat_key = f"{table.monitoring_item}|{table.borehole_id or ''}"
    if stat_key in already_checked:
        return
    already_checked.add(stat_key)

    has_any_stat = (
        stats.positive_max_value is not None
        or stats.negative_max_value is not None
        or stats.max_rate_value is not None
        or stats.max_force_value is not None
        or stats.min_force_value is not None
    )
    if not has_any_stat:
        return

    cum_vals, rate_vals = _gather_sibling_data(table, all_tables)

    # ── 深层位移表 ────────────────────────────────────────
    if table.deep_points:
        if cum_vals:
            actual_pos_id, actual_pos_val = max(cum_vals, key=lambda x: x[1])
            actual_neg_id, actual_neg_val = min(cum_vals, key=lambda x: x[1])

            if stats.positive_max_value is not None:
                if not _close(actual_pos_val, stats.positive_max_value, FLOAT_TOLERANCE):
                    issues.append(CheckIssue(
                        severity="error", table_name=table_label,
                        point_id=actual_pos_id, field_name="正方向最大统计",
                        expected_value=_fmt(actual_pos_val),
                        actual_value=_fmt(stats.positive_max_value),
                        message=f"正方向最大不符: 实际 {actual_pos_id}={_fmt(actual_pos_val)}, 报告 {stats.positive_max_id}={_fmt(stats.positive_max_value)}",
                    ))

            if stats.negative_max_value is not None:
                if not _close(actual_neg_val, stats.negative_max_value, FLOAT_TOLERANCE):
                    issues.append(CheckIssue(
                        severity="error", table_name=table_label,
                        point_id=actual_neg_id, field_name="负方向最大统计",
                        expected_value=_fmt(actual_neg_val),
                        actual_value=_fmt(stats.negative_max_value),
                        message=f"负方向最大不符: 实际 {actual_neg_id}={_fmt(actual_neg_val)}, 报告 {stats.negative_max_id}={_fmt(stats.negative_max_value)}",
                    ))

        if rate_vals and stats.max_rate_value is not None:
            actual_rate_id, actual_rate_val = max(rate_vals, key=lambda x: abs(x[1]))
            if not _close(abs(actual_rate_val), abs(stats.max_rate_value), RATE_TOLERANCE):
                issues.append(CheckIssue(
                    severity="error", table_name=table_label,
                    point_id=actual_rate_id, field_name="最大速率统计",
                    expected_value=_fmt(actual_rate_val),
                    actual_value=_fmt(stats.max_rate_value),
                    message=f"最大速率不符: 实际 {actual_rate_id}={_fmt(actual_rate_val)}, 报告 {stats.max_rate_id}={_fmt(stats.max_rate_value)}",
                ))
        return

    # ── 锚索拉力 ─────────────────────────────────────────
    if table.category in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
        force_vals = [
            (pt.point_id, pt.current_value)
            for t in [tt for tt in all_tables if tt.monitoring_item == table.monitoring_item]
            for pt in t.points
            if pt.current_value is not None
        ]
        if force_vals:
            actual_max_id, actual_max_val = max(force_vals, key=lambda x: x[1])
            actual_min_id, actual_min_val = min(force_vals, key=lambda x: x[1])

            if stats.max_force_value is not None:
                if not _close(actual_max_val, stats.max_force_value, FLOAT_TOLERANCE):
                    issues.append(CheckIssue(
                        severity="error", table_name=table_label,
                        point_id=actual_max_id, field_name="最大内力",
                        expected_value=_fmt(actual_max_val, 1),
                        actual_value=_fmt(stats.max_force_value, 1),
                        message=f"最大内力不符: 实际 {actual_max_id}={_fmt(actual_max_val, 1)}, 报告 {stats.max_force_id}={_fmt(stats.max_force_value, 1)}",
                    ))
            if stats.min_force_value is not None:
                if not _close(actual_min_val, stats.min_force_value, FLOAT_TOLERANCE):
                    issues.append(CheckIssue(
                        severity="error", table_name=table_label,
                        point_id=actual_min_id, field_name="最小内力",
                        expected_value=_fmt(actual_min_val, 1),
                        actual_value=_fmt(stats.min_force_value, 1),
                        message=f"最小内力不符: 实际 {actual_min_id}={_fmt(actual_min_val, 1)}, 报告 {stats.min_force_id}={_fmt(stats.min_force_value, 1)}",
                    ))
        return

    # ── 通用表格 ──────────────────────────────────────────
    tol = FLOAT_TOLERANCE
    if table.category == MonitoringCategory.WATER_LEVEL:
        tol = FLOAT_TOLERANCE * 10

    if cum_vals:
        actual_pos_id, actual_pos_val = max(cum_vals, key=lambda x: x[1])
        actual_neg_id, actual_neg_val = min(cum_vals, key=lambda x: x[1])

        if stats.positive_max_value is not None:
            if not _close(actual_pos_val, stats.positive_max_value, tol):
                issues.append(CheckIssue(
                    severity="error", table_name=table_label,
                    point_id=actual_pos_id, field_name="正方向最大统计",
                    expected_value=_fmt(actual_pos_val, 2),
                    actual_value=_fmt(stats.positive_max_value, 2),
                    message=f"正方向最大不符: 实际 {actual_pos_id}={_fmt(actual_pos_val, 2)}, 报告 {stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}",
                ))

        if stats.negative_max_value is not None:
            if not _close(actual_neg_val, stats.negative_max_value, tol):
                issues.append(CheckIssue(
                    severity="error", table_name=table_label,
                    point_id=actual_neg_id, field_name="负方向最大统计",
                    expected_value=_fmt(actual_neg_val, 2),
                    actual_value=_fmt(stats.negative_max_value, 2),
                    message=f"负方向最大不符: 实际 {actual_neg_id}={_fmt(actual_neg_val, 2)}, 报告 {stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}",
                ))

    if rate_vals and stats.max_rate_value is not None:
        actual_rate_id, actual_rate_val = max(rate_vals, key=lambda x: abs(x[1]))
        if not _close(abs(actual_rate_val), abs(stats.max_rate_value), RATE_TOLERANCE):
            issues.append(CheckIssue(
                severity="error", table_name=table_label,
                point_id=actual_rate_id, field_name="最大速率统计",
                expected_value=_fmt(actual_rate_val),
                actual_value=_fmt(stats.max_rate_value),
                message=f"最大速率不符: 实际 {actual_rate_id}={_fmt(actual_rate_val)}, 报告 {stats.max_rate_id}={_fmt(stats.max_rate_value)}",
            ))


def run_statistics_checks(report: MonitoringReport) -> list[CheckIssue]:
    """对报告中所有表格的统计数据进行验证"""
    issues: list[CheckIssue] = []
    already_checked: set[str] = set()
    for table in report.tables:
        logger.info("=== 统计验证: %s ===", table.monitoring_item)
        check_table_statistics(table, report.tables, issues, already_checked)
    return issues
