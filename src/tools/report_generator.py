"""
检查报告生成工具

生成 Markdown 格式的检查报告。
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.models.data_models import CheckIssue, MonitoringReport

logger = logging.getLogger(__name__)


def generate_report_md(
    report: MonitoringReport,
    calc_issues: list[CheckIssue],
    stats_issues: list[CheckIssue],
    logic_issues: list[CheckIssue],
    ai_review: str = "",
    analysis_plan: list[dict] | None = None,
) -> str:
    """生成 Markdown 格式的检查报告"""
    all_issues = calc_issues + stats_issues + logic_issues
    error_count = sum(1 for i in all_issues if i.severity == "error")
    warning_count = sum(1 for i in all_issues if i.severity == "warning")
    info_count = sum(1 for i in all_issues if i.severity == "info")

    lines: list[str] = []

    # ── 标题与概览 ──────────────────────────────────────
    lines.append("# 建筑变形监测报告检查报告\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"**项目名称**: {report.project_name}\n")
    lines.append(f"**监测单位**: {report.monitoring_company}\n")
    lines.append(f"**报告编号**: {report.report_number}\n")
    lines.append(f"**监测日期**: {report.monitoring_date}\n")
    lines.append("")

    # ── 检查统计 ──────────────────────────────────────
    lines.append("## 检查结果统计\n")
    lines.append(f"| 类别 | 数量 |")
    lines.append(f"|------|------|")
    lines.append(f"| 错误 | {error_count} |")
    lines.append(f"| 警告 | {warning_count} |")
    lines.append(f"| 提示 | {info_count} |")
    lines.append(f"| **合计** | **{len(all_issues)}** |")
    lines.append("")

    if error_count == 0 and warning_count == 0:
        lines.append("> 检查通过，未发现错误或警告。\n")
    elif error_count > 0:
        lines.append(f"> 发现 **{error_count}** 个计算错误，请重点关注。\n")
    lines.append("")

    # ── 数据提取摘要 ──────────────────────────────────
    lines.append("## 数据提取摘要\n")
    lines.append(f"- 报警/控制值配置: {len(report.thresholds)} 项")
    lines.append(f"- 简报汇总项: {len(report.summary_items)} 项")
    lines.append(f"- 监测数据表: {len(report.tables)} 张")
    lines.append("")

    if report.tables:
        lines.append("| 序号 | 监测项 | 类别 | 测点数 | 日期 |")
        lines.append("|------|--------|------|--------|------|")
        for i, t in enumerate(report.tables, 1):
            name = t.monitoring_item
            if t.borehole_id:
                name += f" ({t.borehole_id})"
            pts = len(t.points) if t.points else len(t.deep_points)
            lines.append(f"| {i} | {name} | {t.category.value} | {pts} | {t.monitor_date} |")
        lines.append("")

    # ── AI 理解与验证策略 ──────────────────────────────
    if analysis_plan:
        lines.append("## AI 理解与验证策略\n")
        for plan in analysis_plan:
            header = f"### {plan['table_name']} ({plan['category']} | {plan['point_count']}个测点)\n"
            lines.append(header)

            unit_desc = f"**{plan['unit']}**"
            if plan["unit_conversion"] != 1.0:
                unit_desc += f" → mm (×{plan['unit_conversion']:.0f})"
            else:
                unit_desc += f" ({plan['conversion_note']})"
            lines.append(f"- **单位**: {unit_desc}")

            reliable_icon = "✅" if plan["initial_reliable"] else "⚠"
            lines.append(f"- **初始值**: {reliable_icon} {plan['reliability_reason']}")

            if plan["interval_days"]:
                lines.append(f"- **监测间隔**: {plan['interval_days']:.0f}天 ({plan['interval_source']})")
            else:
                lines.append(f"- **监测间隔**: {plan['interval_source']}")

            lines.append("- **验证规则**:")
            for method in plan["verification_methods"]:
                icon = "⚠" if method["severity"] == "warning" else "✅"
                lines.append(
                    f"  - {icon} {method['name']} = `{method['formula']}`, "
                    f"容差={method['tolerance']}, {method['severity']}"
                )

            if plan["special_notes"]:
                lines.append(f"- **特殊说明**: {'; '.join(plan['special_notes'])}")

            lines.append("")
        lines.append("")

    # ── 详细检查结果 ──────────────────────────────────
    _section(lines, "计算验证结果", calc_issues)
    _section(lines, "统计验证结果", stats_issues)
    _section(lines, "逻辑检查结果", logic_issues)

    # ── AI 补充审核 ──────────────────────────────────
    if ai_review:
        lines.append("## AI 专家补充审核\n")
        lines.append(ai_review)
        lines.append("")

    # ── 结论 ──────────────────────────────────────────
    lines.append("## 结论\n")
    if report.conclusion:
        lines.append(f"**报告原文结论**: {report.conclusion}\n")

    if error_count == 0:
        lines.append("**自动检查结论**: 监测报告数据计算与统计结果验证通过。\n")
    else:
        lines.append(
            f"**自动检查结论**: 发现 {error_count} 处计算错误和 "
            f"{warning_count} 处警告，建议复核上述问题。\n"
        )

    lines.append("\n---\n*本报告由建筑变形监测报告检查智能体自动生成*\n")
    return "\n".join(lines)


def _section(lines: list[str], title: str, issues: list[CheckIssue]) -> None:
    """输出一个检查类别的详细结果"""
    lines.append(f"## {title}\n")

    if not issues:
        lines.append("> 全部通过，未发现问题。\n")
        lines.append("")
        return

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos = [i for i in issues if i.severity == "info"]

    if errors:
        lines.append("### 错误\n")
        lines.append("| # | 表名 | 测点 | 字段 | 描述 |")
        lines.append("|---|------|------|------|------|")
        for idx, issue in enumerate(errors, 1):
            lines.append(
                f"| {idx} | {issue.table_name} | {issue.point_id} | "
                f"{issue.field_name} | {issue.message} |"
            )
        lines.append("")

    if warnings:
        lines.append("### 警告\n")
        lines.append("| # | 表名 | 测点 | 字段 | 描述 |")
        lines.append("|---|------|------|------|------|")
        for idx, issue in enumerate(warnings, 1):
            lines.append(
                f"| {idx} | {issue.table_name} | {issue.point_id} | "
                f"{issue.field_name} | {issue.message} |"
            )
        lines.append("")

    if infos:
        lines.append("### 提示\n")
        for issue in infos:
            lines.append(f"- {issue.message}")
        lines.append("")


def save_report(md_content: str, output_path: str) -> str:
    """保存报告到文件"""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(md_content, encoding="utf-8")
    logger.info("检查报告已保存至: %s", output_path)
    return output_path
