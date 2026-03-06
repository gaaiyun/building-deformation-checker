"""
逻辑检查工具

检查内容：
1. 安全状态判定：根据报警值/控制值判定累计变化量和变化速率是否超标
2. 汇总表与分表一致性：简报中的统计结果 vs 各分表的统计结果
3. 数据完整性：测点数量、编号一致性等
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.config import FLOAT_TOLERANCE
from src.models.data_models import (
    CheckIssue,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    ThresholdConfig,
)

logger = logging.getLogger(__name__)


def _safe_float_from_str(s: str) -> Optional[float]:
    if not s or s in ("/", "--", "-", "——", "N/A", ""):
        return None
    cleaned = re.sub(r"[a-zA-Z/\s]", "", s)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _fmt(v: Optional[float], p: int = 2) -> str:
    return f"{v:.{p}f}" if v is not None else "N/A"


def _find_threshold(thresholds, item_name):
    keywords_map = {
        "水平位移": ["水平位移", "顶部水平", "坡顶水平", "桩顶水平"],
        "竖向位移": ["竖向位移", "顶部竖向", "沉降观测", "坡顶沉降"],
        "地面沉降": ["地面沉降", "道路沉降", "周边地面"],
        "管线": ["管线"],
        "水位": ["水位", "地下水"],
        "深层": ["深层", "测斜"],
        "锚索": ["锚索", "拉力", "轴力"],
    }
    for th in thresholds:
        if th.item_name in item_name or item_name in th.item_name:
            return th
    for _group, keywords in keywords_map.items():
        item_matches = any(k in item_name for k in keywords)
        if item_matches:
            for th in thresholds:
                if any(k in th.item_name for k in keywords):
                    return th
    return None


def _tables_match(table_item: str, summary_item: str) -> bool:
    # "深层"/"测斜" must be checked BEFORE "水平位移" since "深层水平位移" contains "水平位移"
    is_deep_t = any(k in table_item for k in ["深层", "测斜"])
    is_deep_s = any(k in summary_item for k in ["深层", "测斜"])
    if is_deep_t != is_deep_s:
        return False
    if is_deep_t and is_deep_s:
        return True

    keywords_groups = [
        ["水平位移", "顶部水平", "坡顶水平", "基坑顶位移"],
        ["竖向位移", "顶部竖向", "坡顶沉降", "基坑顶沉降"],
        ["地面沉降", "道路沉降", "周边地面", "周边道路"],
        ["管线"],
        ["水位", "地下水"],
        ["深层", "测斜"],
        ["锚索", "拉力", "轴力"],
    ]
    for group in keywords_groups:
        t_match = any(k in table_item for k in group)
        s_match = any(k in summary_item for k in group)
        if t_match and s_match:
            return True
    return table_item in summary_item or summary_item in table_item


def check_safety_status(report, issues):
    for table in report.tables:
        threshold = _find_threshold(report.thresholds, table.monitoring_item)
        if threshold is None:
            issues.append(CheckIssue(
                severity="info", table_name=table.monitoring_item,
                point_id="ALL", field_name="安全状态",
                expected_value="N/A", actual_value="N/A",
                message="未找到对应的报警/控制值阈值配置，无法验证安全状态",
            ))
            continue

        for pt in table.points:
            if not pt.safety_status:
                continue
            should_be = "正常"
            if pt.cumulative_change is not None and threshold.warning_value is not None:
                abs_cum = abs(pt.cumulative_change)
                if threshold.control_value and abs_cum >= threshold.control_value:
                    should_be = "控制"
                elif abs_cum >= threshold.warning_value:
                    should_be = "报警"
            if pt.change_rate is not None and threshold.rate_limit is not None:
                if abs(pt.change_rate) >= threshold.rate_limit:
                    if should_be == "正常":
                        should_be = "报警"

            reported = pt.safety_status.strip()
            if reported == "正常" and should_be != "正常":
                issues.append(CheckIssue(
                    severity="error", table_name=table.monitoring_item,
                    point_id=pt.point_id, field_name="安全状态",
                    expected_value=should_be, actual_value=reported,
                    message=(
                        f"安全状态判定有误: 累计={_fmt(pt.cumulative_change)}, "
                        f"速率={_fmt(pt.change_rate, 3)}, "
                        f"报警值={_fmt(threshold.warning_value)}, "
                        f"控制值={_fmt(threshold.control_value)} → 应为 {should_be}"
                    ),
                ))
            elif reported != "正常" and should_be == "正常":
                issues.append(CheckIssue(
                    severity="warning", table_name=table.monitoring_item,
                    point_id=pt.point_id, field_name="安全状态",
                    expected_value=should_be, actual_value=reported,
                    message=f"安全状态可能过严: 数据正常但标记为 {reported}",
                ))


def check_summary_consistency(report, issues):
    if not report.summary_items:
        issues.append(CheckIssue(
            severity="info", table_name="简报汇总", point_id="ALL",
            field_name="汇总表", expected_value="N/A", actual_value="N/A",
            message="报告中未提取到简报汇总表，跳过一致性检查",
        ))
        return

    for si in report.summary_items:
        matched = [t for t in report.tables if _tables_match(t.monitoring_item, si.monitoring_item)]
        if not matched:
            issues.append(CheckIssue(
                severity="warning", table_name="简报汇总", point_id="N/A",
                field_name=si.monitoring_item,
                expected_value="有对应分表", actual_value="未找到",
                message=f"汇总项 [{si.monitoring_item}] 未找到对应分表",
            ))
            continue

        is_anchor = any(
            t.category in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE)
            for t in matched
        )

        if is_anchor:
            # anchor/strut: summary uses max/min force value, not directional extremes
            all_forces = []
            for t in matched:
                for pt in t.points:
                    if pt.current_value is not None:
                        all_forces.append((pt.point_id, pt.current_value))
            if all_forces:
                act_max_id, act_max = max(all_forces, key=lambda x: x[1])
                act_min_id, act_min = min(all_forces, key=lambda x: x[1])
                s_pos = _safe_float_from_str(si.positive_max)
                s_neg = _safe_float_from_str(si.negative_max)
                tol = FLOAT_TOLERANCE * 2
                if s_pos is not None and not (abs(act_max - s_pos) <= tol or abs(act_min - s_pos) <= tol):
                    issues.append(CheckIssue(
                        severity="warning", table_name="简报汇总",
                        point_id=si.monitoring_item, field_name="锚索力值",
                        expected_value=f"max={act_max_id}/{_fmt(act_max,1)}, min={act_min_id}/{_fmt(act_min,1)}",
                        actual_value=f"{si.positive_max_id}={si.positive_max}",
                        message=f"锚索汇总值与分表不一致，请人工确认",
                    ))
            continue

        all_cum = []
        for t in matched:
            for pt in t.points:
                if pt.cumulative_change is not None:
                    all_cum.append((pt.point_id, pt.cumulative_change))
            for dp in t.deep_points:
                if dp.current_cumulative is not None:
                    all_cum.append((f"深度{dp.depth}m", dp.current_cumulative))

        if not all_cum:
            continue

        actual_pos_id, actual_pos = max(all_cum, key=lambda x: x[1])
        actual_neg_id, actual_neg = min(all_cum, key=lambda x: x[1])
        summary_pos = _safe_float_from_str(si.positive_max)
        summary_neg = _safe_float_from_str(si.negative_max)
        tol = FLOAT_TOLERANCE

        if summary_pos is not None and abs(actual_pos - summary_pos) > tol:
            issues.append(CheckIssue(
                severity="error", table_name="简报汇总",
                point_id=si.monitoring_item, field_name="正方向最大",
                expected_value=f"{actual_pos_id}={_fmt(actual_pos)}",
                actual_value=f"{si.positive_max_id}={si.positive_max}",
                message=f"汇总表正方向最大与分表不一致",
            ))

        if summary_neg is not None and abs(actual_neg - summary_neg) > tol:
            issues.append(CheckIssue(
                severity="error", table_name="简报汇总",
                point_id=si.monitoring_item, field_name="负方向最大",
                expected_value=f"{actual_neg_id}={_fmt(actual_neg)}",
                actual_value=f"{si.negative_max_id}={si.negative_max}",
                message=f"汇总表负方向最大与分表不一致",
            ))


def check_point_count(report, issues):
    for table in report.tables:
        if table.point_count <= 0:
            continue
        actual = len(table.points) if table.points else len(table.deep_points)
        if actual != table.point_count:
            name = table.monitoring_item
            if table.borehole_id:
                name += f"({table.borehole_id})"
            issues.append(CheckIssue(
                severity="warning", table_name=name, point_id="ALL",
                field_name="监测点数量",
                expected_value=str(table.point_count), actual_value=str(actual),
                message=f"表头声明 {table.point_count} 个点, 实际 {actual} 行",
            ))


def run_logic_checks(report: MonitoringReport) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    logger.info("=== 安全状态判定检查 ===")
    check_safety_status(report, issues)
    logger.info("=== 汇总表一致性检查 ===")
    check_summary_consistency(report, issues)
    logger.info("=== 监测点数量检查 ===")
    check_point_count(report, issues)
    return issues
