"""提取质量分析与问题归因工具。"""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Optional

from src.models.data_models import CheckIssue, MonitoringReport, MonitoringTable

SOURCE_HINTS = {
    "extraction": "（可能为 PDF 提取或列匹配问题，建议核对原文）",
    "logic": "（可能为规则边界或逻辑匹配问题，建议人工复核）",
}


def _non_null_ratio(values: list[object]) -> float:
    if not values:
        return 0.0
    non_null = sum(1 for value in values if value not in (None, "", "N/A"))
    return non_null / len(values)


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def infer_source_from_reason(reason: str) -> str:
    text = _normalize_text(reason)
    if not text:
        return ""
    extraction_markers = ("提取", "ocr", "错列", "列错位", "分页", "版面", "单位", "映射")
    logic_markers = ("逻辑", "规则", "边界", "约定", "统计口径", "判定")
    lower_text = text.lower()
    if any(marker in lower_text for marker in extraction_markers):
        return "extraction"
    if any(marker in text for marker in logic_markers):
        return "logic"
    return "report"


def determine_issue_source(
    report: MonitoringReport,
    table_index: Optional[int],
    default_source: str = "report",
) -> str:
    if table_index is not None and report.table_extraction_flags.get(table_index):
        return "extraction"
    return default_source


def annotate_issues_for_table(
    report: MonitoringReport,
    issues: list[CheckIssue],
    table_index: Optional[int],
    default_source: str = "report",
) -> list[CheckIssue]:
    source = determine_issue_source(report, table_index, default_source=default_source)
    for issue in issues:
        if not issue.suspected_source:
            issue.suspected_source = source
    return issues


def append_issue_source_hint(message: str, suspected_source: str) -> str:
    hint = SOURCE_HINTS.get(suspected_source, "")
    if not hint or hint in message:
        return message
    return f"{message} {hint}"


def analyze_extraction_quality(report: MonitoringReport) -> MonitoringReport:
    flags_by_table: dict[int, list[str]] = {}
    diagnostics = dict(report.extraction_diagnostics or {})
    pages = diagnostics.get("pages", [])
    high_markup_pages = [page["page"] for page in pages if page.get("markup_ratio", 0) >= 0.9]
    duplicate_pages = diagnostics.get("identical_page_pairs", [])

    for idx, table in enumerate(report.tables):
        flags: list[str] = []
        actual_count = len(table.points) if table.points else len(table.deep_points)
        if table.point_count and actual_count and table.point_count != actual_count:
            flags.append(f"表头测点数 {table.point_count} 与实际解析行数 {actual_count} 不一致")

        if table.points:
            field_values = {
                "initial_value": [pt.initial_value for pt in table.points],
                "current_value": [pt.current_value for pt in table.points],
                "cumulative_change": [pt.cumulative_change for pt in table.points],
                "current_change": [pt.current_change for pt in table.points],
                "change_rate": [pt.change_rate for pt in table.points],
            }
            sparse_fields = [
                field for field, values in field_values.items()
                if values and _non_null_ratio(values) < 0.5
            ]
            if sparse_fields:
                flags.append(f"关键列空值较多: {', '.join(sparse_fields)}")

            ratios: list[float] = []
            for pt in table.points[: min(len(table.points), 10)]:
                if pt.initial_value is None or pt.current_value is None or pt.cumulative_change in (None, 0):
                    continue
                computed = pt.current_value - pt.initial_value
                if abs(computed) < 1e-9:
                    continue
                ratio = abs(pt.cumulative_change / computed)
                ratios.append(ratio)
            if ratios:
                mismatch_count = sum(1 for ratio in ratios if ratio > 100 or ratio < 0.01)
                if mismatch_count / len(ratios) >= 0.6:
                    flags.append("累计变化与测值差数量级异常，疑似列映射或单位错误")

        if table.deep_points:
            field_values = {
                "previous_cumulative": [dp.previous_cumulative for dp in table.deep_points],
                "current_cumulative": [dp.current_cumulative for dp in table.deep_points],
                "current_change": [dp.current_change for dp in table.deep_points],
                "change_rate": [dp.change_rate for dp in table.deep_points],
            }
            sparse_fields = [
                field for field, values in field_values.items()
                if values and _non_null_ratio(values) < 0.5
            ]
            if sparse_fields:
                flags.append(f"深层表关键列空值较多: {', '.join(sparse_fields)}")

            ratios: list[float] = []
            for dp in table.deep_points:
                if dp.current_change is None or dp.change_rate in (None, 0):
                    continue
                ratios.append(abs(dp.change_rate / dp.current_change))
            if ratios:
                ratio_median = median(ratios)
                if 0.8 <= ratio_median <= 1.2:
                    flags.append("深层表变化速率疑似误映射为本期变化")

        if flags:
            flags_by_table[idx] = flags

    diagnostics["high_markup_pages"] = high_markup_pages
    diagnostics["duplicate_pages"] = duplicate_pages
    diagnostics["abnormal_table_count"] = len(flags_by_table)
    diagnostics["flagged_table_indexes"] = sorted(flags_by_table)
    report.table_extraction_flags = flags_by_table
    report.extraction_diagnostics = diagnostics
    return report

