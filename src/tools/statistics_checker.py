"""
统计分析验证工具

验证每张表底部的统计值：
  - 当次累计正方向最大统计
  - 当次累计负方向最大统计
  - 最大变化速率统计
  - 最大/最小内力（锚索拉力）

核心原则：
  1. 每张表的统计值只与 **该表自身数据** 比对（不跨表聚合）
  2. 方向性检查：若所有累计值均非正/非负，对应方向统计应为 "-"
  3. 跨表引用检查：若统计引用的测点不在本表中，视为错误
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


def _get_table_own_data(
    table: MonitoringTable,
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """只取当前表自身的数据，不跨表聚合"""
    cum_vals: list[tuple[str, float]] = []
    rate_vals: list[tuple[str, float]] = []

    for pt in table.points:
        if pt.cumulative_change is not None:
            cum_vals.append((pt.point_id, pt.cumulative_change))
        if pt.change_rate is not None:
            rate_vals.append((pt.point_id, pt.change_rate))
    for dp in table.deep_points:
        label = f"深度{dp.depth}m"
        if dp.current_cumulative is not None:
            cum_vals.append((label, dp.current_cumulative))
        if dp.change_rate is not None:
            rate_vals.append((label, dp.change_rate))

    return cum_vals, rate_vals


def _get_table_point_ids(table: MonitoringTable) -> set[str]:
    """获取本表所有测点 ID（用于检测跨表引用）"""
    ids: set[str] = set()
    for pt in table.points:
        ids.add(pt.point_id)
    for dp in table.deep_points:
        ids.add(f"深度{dp.depth}m")
        ids.add(str(dp.depth))
    return ids


def _check_cross_table_ref(
    stat_id: str,
    stat_field: str,
    table_point_ids: set[str],
    table_label: str,
    issues: list[CheckIssue],
) -> bool:
    """检查统计引用的测点是否在本表中。返回 True 表示存在跨表引用问题。"""
    if not stat_id or stat_id in ("None", "null", "N/A", "-", "/"):
        return False
    if stat_id in table_point_ids:
        return False
    for pid in table_point_ids:
        if stat_id in pid or pid in stat_id:
            return False
    issues.append(CheckIssue(
        severity="error",
        table_name=table_label,
        point_id=stat_id,
        field_name=stat_field,
        expected_value="本表测点",
        actual_value=stat_id,
        message=(
            f"{stat_field}引用了测点 {stat_id}，但该测点不在本表中，"
            f"疑似错误引用了其他表的统计值"
        ),
    ))
    return True


def check_table_statistics(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """验证单张表的统计数据"""
    stats = table.statistics
    table_label = table.monitoring_item
    if table.borehole_id:
        table_label += f"({table.borehole_id})"

    has_any_stat = (
        stats.positive_max_value is not None
        or stats.negative_max_value is not None
        or stats.max_rate_value is not None
        or stats.max_force_value is not None
        or stats.min_force_value is not None
    )
    if not has_any_stat:
        return

    cum_vals, rate_vals = _get_table_own_data(table)
    table_point_ids = _get_table_point_ids(table)

    # ── 锚索拉力 ─────────────────────────────────────────
    if table.category in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
        force_vals = [
            (pt.point_id, pt.current_value)
            for pt in table.points
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

    # ── 通用表格（含深层位移）────────────────────────────────
    tol = FLOAT_TOLERANCE
    if table.category == MonitoringCategory.WATER_LEVEL:
        tol = FLOAT_TOLERANCE * 10

    if cum_vals:
        pos_vals = [(pid, v) for pid, v in cum_vals if v > 0]
        neg_vals = [(pid, v) for pid, v in cum_vals if v < 0]

        # ── 正方向最大统计 ────────────────────────────────
        if stats.positive_max_value is not None:
            cross_ref = _check_cross_table_ref(
                stats.positive_max_id, "正方向最大统计",
                table_point_ids, table_label, issues,
            )
            if not cross_ref:
                if not pos_vals:
                    issues.append(CheckIssue(
                        severity="error", table_name=table_label,
                        point_id=stats.positive_max_id or "N/A",
                        field_name="正方向最大统计",
                        expected_value="无正值，应为'-'",
                        actual_value=f"{stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}",
                        message=(
                            f"所有累计变化量均为非正值，正方向最大统计应为'-'，"
                            f"但报告显示 {stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}"
                        ),
                    ))
                else:
                    actual_pos_id, actual_pos_val = max(pos_vals, key=lambda x: x[1])
                    if not _close(actual_pos_val, stats.positive_max_value, tol):
                        issues.append(CheckIssue(
                            severity="error", table_name=table_label,
                            point_id=actual_pos_id, field_name="正方向最大统计",
                            expected_value=_fmt(actual_pos_val, 2),
                            actual_value=_fmt(stats.positive_max_value, 2),
                            message=f"正方向最大不符: 实际 {actual_pos_id}={_fmt(actual_pos_val, 2)}, 报告 {stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}",
                        ))

        # ── 负方向最大统计 ────────────────────────────────
        if stats.negative_max_value is not None:
            cross_ref = _check_cross_table_ref(
                stats.negative_max_id, "负方向最大统计",
                table_point_ids, table_label, issues,
            )
            if not cross_ref:
                if not neg_vals:
                    issues.append(CheckIssue(
                        severity="error", table_name=table_label,
                        point_id=stats.negative_max_id or "N/A",
                        field_name="负方向最大统计",
                        expected_value="无负值，应为'-'",
                        actual_value=f"{stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}",
                        message=(
                            f"所有累计变化量均为非负值，负方向最大统计应为'-'，"
                            f"但报告显示 {stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}"
                        ),
                    ))
                else:
                    actual_neg_id, actual_neg_val = min(neg_vals, key=lambda x: x[1])
                    if not _close(actual_neg_val, stats.negative_max_value, tol):
                        issues.append(CheckIssue(
                            severity="error", table_name=table_label,
                            point_id=actual_neg_id, field_name="负方向最大统计",
                            expected_value=_fmt(actual_neg_val, 2),
                            actual_value=_fmt(stats.negative_max_value, 2),
                            message=f"负方向最大不符: 实际 {actual_neg_id}={_fmt(actual_neg_val, 2)}, 报告 {stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}",
                        ))

    # ── 最大速率统计 ──────────────────────────────────────
    if rate_vals and stats.max_rate_value is not None:
        cross_ref = _check_cross_table_ref(
            stats.max_rate_id, "最大速率统计",
            table_point_ids, table_label, issues,
        )
        if not cross_ref:
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
    """对报告中所有表格的统计数据进行验证（每张表独立检查）"""
    issues: list[CheckIssue] = []
    for table in report.tables:
        logger.info("=== 统计验证: %s ===", table.monitoring_item)
        check_table_statistics(table, issues)
    return issues
