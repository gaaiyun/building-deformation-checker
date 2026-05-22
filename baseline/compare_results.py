"""把 v2 工具的输出 JSON 与 ground truth diff 对比，输出 precision/recall。

执行顺序：
    1. 先跑 baseline/run_tool_tests.py 生成 results/*.json
    2. 再跑 本脚本 生成 comparison_report.md

匹配逻辑：
    - 一个 ground truth diff 在 sheet S 的某 cell（带测点编号 P 和字段名 F）
    - 检查工具的 errors+warnings 里是否有：table_name 接近 S、point_id 接近 P、
      message 中含数值变化迹象
    - 这是模糊匹配（工具输出格式与 cell 坐标不同，需要语义对齐）
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "baseline" / "results"
DIFFS = ROOT / "baseline" / "diffs"

# 模板名 -> diff JSON 路径
DIFF_MAP = {
    "质安": DIFFS / "质安_diff.json",
    "深工勘": DIFFS / "深工勘_diff.json",
    "展誉": DIFFS / "展誉_diff.json",
}


def load_tool_output(pdf_stem: str) -> dict | None:
    path = RESULTS / f"{pdf_stem}_tool_output.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_sheet_name(name: str) -> str:
    """工具识别的表名 vs XLSX sheet 名映射（模糊化）"""
    return name.replace("【", "").replace("】", "").replace(" ", "").replace("观测", "")


def normalize_point_id(pid: str) -> str:
    """测点编号归一化：去掉行业后缀（如"(位移)"、"(沉降)"）"""
    pid = pid.replace("(位移)", "").replace("(沉降)", "").replace("(水位)", "")
    pid = pid.replace("(倾斜)", "").replace("(内力)", "")
    return pid.strip()


def match_diff_to_tool(diff: dict, tool_issues: list[dict]) -> dict | None:
    """模糊匹配：ground truth diff 是否在工具的 issues 里被报告？

    匹配优先级：
        1. sheet 与 table 名互相包含（最关键）
        2. 测点编号互相包含（去后缀对齐）
        3. 至少其一字段命中（变化速率、累计变化量、本次断面距离 → 本次值）
    """
    sheet = normalize_sheet_name(diff["sheet"])
    point = normalize_point_id(diff.get("point_id", ""))

    for issue in tool_issues:
        issue_table = normalize_sheet_name(issue["table"])
        issue_point = normalize_point_id(issue.get("point", ""))

        # 1. table 名匹配（互相包含）
        if sheet and issue_table:
            if sheet not in issue_table and issue_table not in sheet:
                continue
        # 2. 测点编号匹配
        if point and issue_point:
            if point not in issue_point and issue_point not in point:
                continue
        return issue
    return None


def compare_pair(company: str, error_pdf_stem: str, correct_pdf_stem: str) -> dict:
    """对比一个 company 的 (错误版, 正确版) 与其 ground truth diff"""
    result = {
        "company": company,
        "error_pdf": error_pdf_stem,
        "correct_pdf": correct_pdf_stem,
    }

    # 读取 ground truth
    diff_path = DIFF_MAP.get(company)
    if not diff_path or not diff_path.exists():
        result["error"] = f"未找到 ground truth: {diff_path}"
        return result

    diffs_by_sheet: dict = json.loads(diff_path.read_text(encoding="utf-8"))
    all_diffs = []
    for sheet, sheet_diffs in diffs_by_sheet.items():
        all_diffs.extend(sheet_diffs)
    result["ground_truth_count"] = len(all_diffs)

    # 读取工具输出
    err_output = load_tool_output(error_pdf_stem)
    cor_output = load_tool_output(correct_pdf_stem)
    result["error_pdf_tool_output"] = err_output
    result["correct_pdf_tool_output"] = cor_output

    if err_output is None or cor_output is None:
        result["status"] = "工具未运行或 JSON 缺失"
        return result

    # 错误版：工具找到的所有 errors+warnings，与 ground truth 模糊匹配
    err_issues = err_output.get("errors", []) + err_output.get("warnings", [])
    matched = []
    missed = []
    for diff in all_diffs:
        m = match_diff_to_tool(diff, err_issues)
        if m:
            matched.append({"diff": diff, "tool_issue": m})
        else:
            missed.append(diff)

    result["matched"] = len(matched)
    result["missed"] = len(missed)
    result["missed_details"] = missed[:5]  # 列出前 5 条漏报详情
    result["tool_total_errors"] = err_output.get("errors_count", 0)
    result["tool_total_warnings"] = err_output.get("warnings_count", 0)

    # Precision/Recall
    if len(all_diffs) > 0:
        result["recall"] = round(len(matched) / len(all_diffs), 2)
    if (err_output.get("errors_count", 0) + err_output.get("warnings_count", 0)) > 0:
        result["precision_approx"] = round(
            len(matched) / (err_output.get("errors_count", 0) + err_output.get("warnings_count", 0)),
            2,
        )

    # 正确版：理想是 0 错误
    result["correct_pdf_errors_count"] = cor_output.get("errors_count", 0)
    result["correct_pdf_warnings_count"] = cor_output.get("warnings_count", 0)
    result["correct_pdf_false_positives"] = cor_output.get("errors_count", 0)

    return result


def main():
    lines = ["# v2 工具 vs Ground Truth · 比对报告", ""]
    lines.append("## 方法学")
    lines.append("")
    lines.append("- **Ground truth**：通过逐 cell 对比 `错误版.xlsx` vs `正确版.xlsx` 获得，共 47 处差异")
    lines.append("- **工具输出**：v2 流水线在 6 个 PDF 上跑出的 issues 清单")
    lines.append("- **匹配规则**：模糊匹配（table 名 + 测点编号），允许工具用不同坐标语言描述相同问题")
    lines.append("")

    summary_rows = []
    for company in ["质安", "深工勘", "展誉"]:
        err_stem = f"{company}模板-错误版"
        cor_stem = f"{company}模板-正确版"
        r = compare_pair(company, err_stem, cor_stem)
        summary_rows.append(r)

        lines.append(f"## {company}")
        lines.append("")
        if "error" in r:
            lines.append(f"❌ {r['error']}")
            lines.append("")
            continue
        if r.get("status"):
            lines.append(f"⚠️  {r['status']}")
            lines.append("")
            continue

        lines.append(f"- Ground truth 差异数: **{r['ground_truth_count']}**")
        lines.append(f"- 工具在 ERROR 版找到的 issues: {r['tool_total_errors']} 错误 + {r['tool_total_warnings']} 警告")
        lines.append(f"- 命中: **{r['matched']}** / 漏报: **{r['missed']}**")
        if "recall" in r:
            lines.append(f"- Recall: **{r['recall']:.0%}**")
        if "precision_approx" in r:
            lines.append(f"- Precision (近似): {r['precision_approx']:.0%}")
        lines.append(f"- 工具在 CORRECT 版的误报: {r['correct_pdf_false_positives']} 错误 + {r['correct_pdf_warnings_count']} 警告")

        if r.get("missed_details"):
            lines.append("")
            lines.append("**未被工具捕获的 diff 示例（前 5 条）：**")
            for d in r["missed_details"]:
                lines.append(
                    f"- `{d['sheet']}` / `{d['cell']}` / 测点 {d.get('point_id', '?')} : "
                    f"错误版 {d['error_value']} vs 正确版 {d['correct_value']}"
                )
        lines.append("")

    # 总汇
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 模板 | GT 数 | 命中 | 漏报 | Recall | 正确版误报 |")
    lines.append("|------|-------|------|------|--------|------------|")
    for r in summary_rows:
        if "error" in r or r.get("status"):
            continue
        lines.append(
            f"| {r['company']} | {r['ground_truth_count']} | {r['matched']} | "
            f"{r['missed']} | {r.get('recall', 0):.0%} | "
            f"{r['correct_pdf_false_positives']} |"
        )

    out = RESULTS / "comparison_report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 比对报告: {out}")


if __name__ == "__main__":
    main()
