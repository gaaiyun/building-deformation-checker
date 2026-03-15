"""
统计分析验证工具

验证每张表底部的统计值：
  - 当次累计正方向最大统计
  - 当次累计负方向最大统计
  - 最大变化速率统计
  - 最大/最小内力（锚索拉力）

核心原则：
  1. 同一监测项多页时，统计值与 **组内合并数据** 比对（而不是只看当前页）
  2. 方向性检查：若所有累计值均非正/非负，对应方向统计应为 "-"
  3. 跨表引用检查：若统计引用的测点不在本表中，视为错误
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from src.config import FLOAT_TOLERANCE, RATE_TOLERANCE
from src.tools.extraction_quality import annotate_issues_for_table
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
) -> tuple[list[tuple[str, float]], list[tuple[str, float]], list[tuple[str, float]]]:
    """只取当前表自身的数据，不跨表聚合"""
    cum_vals: list[tuple[str, float]] = []
    rate_vals: list[tuple[str, float]] = []
    change_vals: list[tuple[str, float]] = []

    for pt in table.points:
        if pt.cumulative_change is not None:
            cum_vals.append((pt.point_id, pt.cumulative_change))
        if pt.change_rate is not None:
            rate_vals.append((pt.point_id, pt.change_rate))
    for dp in table.deep_points:
        label = f"深度{dp.depth}m"
        if dp.current_cumulative is not None:
            cum_vals.append((label, dp.current_cumulative))
        if dp.current_change is not None:
            change_vals.append((label, dp.current_change))
        if dp.change_rate is not None:
            rate_vals.append((label, dp.change_rate))

    return cum_vals, rate_vals, change_vals


def _get_table_point_ids(table: MonitoringTable) -> set[str]:
    """获取本表所有测点 ID（用于检测跨表引用）"""
    ids: set[str] = set()
    for pt in table.points:
        ids.add(pt.point_id)
    for dp in table.deep_points:
        ids.add(f"深度{dp.depth}m")
        ids.add(str(dp.depth))
    return ids


def _get_group_key(table: MonitoringTable) -> tuple[str, str]:
    """同一监测项多页表按 monitoring_item + borehole_id 归组。"""
    return table.monitoring_item.strip(), table.borehole_id.strip()


def _build_allowed_point_ids_map(report: MonitoringReport) -> dict[tuple[str, str], set[str]]:
    """为同一逻辑表的多页数据合并允许引用的测点集合。"""
    allowed: dict[tuple[str, str], set[str]] = defaultdict(set)
    for table in report.tables:
        allowed[_get_group_key(table)].update(_get_table_point_ids(table))
    return allowed


def _build_group_data_map(
    report: MonitoringReport,
) -> dict[tuple[str, str], tuple[list[tuple[str, float]], list[tuple[str, float]], list[tuple[str, float]]]]:
    """为同一逻辑表的多页数据合并统计计算所需的值。"""
    grouped_tables: dict[tuple[str, str], list[MonitoringTable]] = defaultdict(list)
    for table in report.tables:
        grouped_tables[_get_group_key(table)].append(table)

    group_data: dict[tuple[str, str], tuple[list[tuple[str, float]], list[tuple[str, float]], list[tuple[str, float]]]] = {}
    for group_key, tables in grouped_tables.items():
        cum_vals: list[tuple[str, float]] = []
        rate_vals: list[tuple[str, float]] = []
        change_vals: list[tuple[str, float]] = []
        for table in tables:
            table_cum_vals, table_rate_vals, table_change_vals = _get_table_own_data(table)
            cum_vals.extend(table_cum_vals)
            rate_vals.extend(table_rate_vals)
            change_vals.extend(table_change_vals)
        group_data[group_key] = (cum_vals, rate_vals, change_vals)
    return group_data


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
    allowed_point_ids: Optional[set[str]] = None,
    grouped_data: Optional[tuple[list[tuple[str, float]], list[tuple[str, float]], list[tuple[str, float]]]] = None,
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
        or stats.max_change_value is not None
        or stats.max_force_value is not None
        or stats.min_force_value is not None
    )
    if not has_any_stat:
        return

    cum_vals, rate_vals, change_vals = grouped_data or _get_table_own_data(table)
    table_point_ids = allowed_point_ids or _get_table_point_ids(table)
    is_deep = bool(table.deep_points)

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
            cross_ref = False
            if not is_deep:
                cross_ref = _check_cross_table_ref(
                    stats.positive_max_id, "正方向最大统计",
                    table_point_ids, table_label, issues,
                )
            if not cross_ref:
                if not pos_vals:
                    # 行业惯例：所有值同为负时，"正方向最大"可能填绝对值最小的负值（最接近0）
                    if neg_vals:
                        closest_id, closest_val = max(neg_vals, key=lambda x: x[1])
                        if _close(closest_val, stats.positive_max_value, tol):
                            issues.append(CheckIssue(
                                severity="info", table_name=table_label,
                                point_id=stats.positive_max_id or "N/A",
                                field_name="正方向最大统计",
                                expected_value="无正值",
                                actual_value=f"{stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}",
                                message=(
                                    f"所有累计变化量均为负值，报告正方向最大填写了绝对值最小的负值 "
                                    f"{stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}（行业惯例）"
                                ),
                            ))
                        else:
                            issues.append(CheckIssue(
                                severity="warning", table_name=table_label,
                                point_id=stats.positive_max_id or "N/A",
                                field_name="正方向最大统计",
                                expected_value=f"无正值，最接近0: {closest_id}={_fmt(closest_val, 2)}",
                                actual_value=f"{stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}",
                                message=(
                                    f"所有累计变化量均为负值，正方向最大统计应为'-'或绝对值最小的负值，"
                                    f"但报告显示 {stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}"
                                ),
                            ))
                    else:
                        issues.append(CheckIssue(
                            severity="warning", table_name=table_label,
                            point_id=stats.positive_max_id or "N/A",
                            field_name="正方向最大统计",
                            expected_value="无数据",
                            actual_value=f"{stats.positive_max_id}={_fmt(stats.positive_max_value, 2)}",
                            message=(
                                f"无累计变化量数据，正方向最大统计应为'-'，"
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
            cross_ref = False
            if not is_deep:
                cross_ref = _check_cross_table_ref(
                    stats.negative_max_id, "负方向最大统计",
                    table_point_ids, table_label, issues,
                )
            if not cross_ref:
                if not neg_vals:
                    # 行业惯例：所有值同为正时，"负方向最大"可能填最小正值（最接近0）
                    if pos_vals:
                        closest_id, closest_val = min(pos_vals, key=lambda x: x[1])
                        if _close(closest_val, stats.negative_max_value, tol):
                            issues.append(CheckIssue(
                                severity="info", table_name=table_label,
                                point_id=stats.negative_max_id or "N/A",
                                field_name="负方向最大统计",
                                expected_value="无负值",
                                actual_value=f"{stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}",
                                message=(
                                    f"所有累计变化量均为正值，报告负方向最大填写了最小正值 "
                                    f"{stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}（行业惯例）"
                                ),
                            ))
                        else:
                            issues.append(CheckIssue(
                                severity="warning", table_name=table_label,
                                point_id=stats.negative_max_id or "N/A",
                                field_name="负方向最大统计",
                                expected_value=f"无负值，最小正值: {closest_id}={_fmt(closest_val, 2)}",
                                actual_value=f"{stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}",
                                message=(
                                    f"所有累计变化量均为正值，负方向最大统计应为'-'或最小正值，"
                                    f"但报告显示 {stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}"
                                ),
                            ))
                    else:
                        issues.append(CheckIssue(
                            severity="warning", table_name=table_label,
                            point_id=stats.negative_max_id or "N/A",
                            field_name="负方向最大统计",
                            expected_value="无数据",
                            actual_value=f"{stats.negative_max_id}={_fmt(stats.negative_max_value, 2)}",
                            message=(
                                f"无累计变化量数据，负方向最大统计应为'-'，"
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
        cross_ref = False
        if not is_deep:
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

    if change_vals and stats.max_change_value is not None:
        actual_change_id, actual_change_val = max(change_vals, key=lambda x: abs(x[1]))
        if not _close(abs(actual_change_val), abs(stats.max_change_value), FLOAT_TOLERANCE):
            issues.append(CheckIssue(
                severity="error",
                table_name=table_label,
                point_id=actual_change_id,
                field_name="最大变化位移统计",
                expected_value=_fmt(actual_change_val),
                actual_value=_fmt(stats.max_change_value),
                message=(
                    f"最大变化位移不符: 实际 {actual_change_id}={_fmt(actual_change_val)}, "
                    f"报告 {stats.max_change_id}={_fmt(stats.max_change_value)}"
                ),
            ))


def run_statistics_checks(report: MonitoringReport) -> list[CheckIssue]:
    """对报告中所有表格的统计数据进行验证。"""
    issues: list[CheckIssue] = []
    allowed_point_ids_map = _build_allowed_point_ids_map(report)
    group_data_map = _build_group_data_map(report)
    for table_index, table in enumerate(report.tables):
        logger.info("=== 统计验证: %s ===", table.monitoring_item)
        group_key = _get_group_key(table)
        allowed_point_ids = allowed_point_ids_map[group_key]
        grouped_data = group_data_map[group_key]
        table_issues: list[CheckIssue] = []
        check_table_statistics(
            table,
            table_issues,
            allowed_point_ids=allowed_point_ids,
            grouped_data=grouped_data,
        )
        annotate_issues_for_table(report, table_issues, table_index, default_source="report")
        issues.extend(table_issues)
    return issues
