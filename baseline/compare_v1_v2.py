"""并排对比 v1 (修复前) vs v2 (修复后) 在所有已完成 PDF 上的表现。

读取 baseline/results_before_fix/*.json (v1) 和 baseline/results/*.json (v2)
两份结果，生成横向对比报告。

输出：
    baseline/results/v1_vs_v2_comparison.md
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
V1_DIR = ROOT / "baseline" / "results_before_fix"
V2_DIR = ROOT / "baseline" / "results"
DIFFS_DIR = ROOT / "baseline" / "diffs"

# 测试 case + 对应 ground truth
CASES = [
    ("质安模板-错误版", "质安_diff.json", 14),
    ("质安模板-正确版", None, 0),
    ("深工勘模板-错误版", "深工勘_diff.json", 17),
    ("深工勘模板-正确版", None, 0),
    ("展誉模板-错误版", "展誉_diff.json", 16),
    ("展誉模板-正确版", None, 0),
]


def load_result(directory: Path, stem: str) -> dict | None:
    path = directory / f"{stem}_tool_output.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def count_ground_truth(diff_file: str | None) -> int:
    if diff_file is None:
        return 0
    path = DIFFS_DIR / diff_file
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    return sum(len(diffs) for diffs in data.values())


def precision_recall_estimate(result: dict, gt_count: int) -> tuple[float, float, int]:
    """粗略估计 precision/recall。

    返回 (precision%, recall%, true_positive 估算数)
    用法：将 issue 列表与 ground truth 测点做模糊匹配。
    """
    if not result:
        return 0.0, 0.0, 0
    if gt_count == 0:
        # 正确版：所有 errors 都是 false positive
        total = result.get("errors_count", 0) + result.get("warnings_count", 0)
        return (0.0 if total == 0 else 100.0 * 0 / total), 100.0, 0
    issues = result.get("errors", []) + result.get("warnings", [])
    # 简单匹配：用 message 中是否含明显数值不一致信号判断
    # （真实更准的匹配需要 ground truth 含坐标→测点的映射）
    true_positive = min(len(issues), gt_count)  # 上界估计
    precision = 100.0 * true_positive / len(issues) if issues else 0.0
    recall = 100.0 * true_positive / gt_count
    return precision, recall, true_positive


def main():
    lines = [
        "# v1 vs v2 工具表现对比",
        "",
        "本报告对比 **修复前**（v1，仅初始版本）和 **修复后**（v2，三大 bug 已修）",
        "工具在 6 个测试 PDF 上的表现。",
        "",
        "## 三大修复回顾",
        "",
        "1. **间隔仲裁** (`calculation_checker._choose_interval_days`)：",
        "   报告日期范围与行级反推差距 > 2 天时，按行级支持率决定采用哪个。",
        "   消除"7 天 vs 2 天"类型的大量速率误报。",
        "",
        "2. **多期数据分组** (`statistics_checker._get_group_key`)：",
        "   分组键加入 monitor_date 维度，避免把不同期数据合并核对统计。",
        "",
        "3. **混合单位自动检测** (`table_analyzer._detect_mixed_units_ratio`)：",
        "   通过 cumulative/(current-initial) 中位比值判断 m→mm 转换，",
        "   覆盖之前只对沉降类启用的启发式。",
        "",
        "## 汇总对比表",
        "",
        "| PDF | GT 数 | v1 errors | v1 warns | v2 errors | v2 warns | v1 noise | v2 noise |",
        "|-----|-------|-----------|----------|-----------|----------|----------|----------|",
    ]

    detail_sections = []
    sum_v1_errs = sum_v1_warns = 0
    sum_v2_errs = sum_v2_warns = 0

    for stem, diff_file, gt_count in CASES:
        v1 = load_result(V1_DIR, stem)
        v2 = load_result(V2_DIR, stem)

        v1_errs = v1.get("errors_count", "-") if v1 else "未跑"
        v1_warns = v1.get("warnings_count", "-") if v1 else "未跑"
        v2_errs = v2.get("errors_count", "-") if v2 else "未跑"
        v2_warns = v2.get("warnings_count", "-") if v2 else "未跑"

        # noise 估计：正确版的 errors/warnings 都是 noise；错误版超过 ground truth 部分是 noise
        if isinstance(v1_errs, int) and isinstance(v1_warns, int):
            v1_total = v1_errs + v1_warns
            v1_noise = v1_total - gt_count if v1_total > gt_count else 0
            sum_v1_errs += v1_errs
            sum_v1_warns += v1_warns
        else:
            v1_noise = "-"
        if isinstance(v2_errs, int) and isinstance(v2_warns, int):
            v2_total = v2_errs + v2_warns
            v2_noise = v2_total - gt_count if v2_total > gt_count else 0
            sum_v2_errs += v2_errs
            sum_v2_warns += v2_warns
        else:
            v2_noise = "-"

        lines.append(
            f"| {stem} | {gt_count} | {v1_errs} | {v1_warns} | "
            f"{v2_errs} | {v2_warns} | {v1_noise} | {v2_noise} |"
        )

        # 详细对比
        if v1 and v2:
            section = [
                "",
                f"### {stem} (Ground truth: {gt_count} 个真实错误)",
                "",
                f"- v1: 错误 {v1_errs} + 警告 {v1_warns} = **{v1_errs + v1_warns}** issues",
                f"- v2: 错误 {v2_errs} + 警告 {v2_warns} = **{v2_errs + v2_warns}** issues",
                f"- 减少: {(v1_errs + v1_warns) - (v2_errs + v2_warns)} 个",
                "",
            ]
            detail_sections.extend(section)

    lines.append(
        f"| **合计** | {sum(gt for _, _, gt in CASES)} | {sum_v1_errs} | {sum_v1_warns} | "
        f"{sum_v2_errs} | {sum_v2_warns} | - | - |"
    )
    lines.extend(detail_sections)

    out = V2_DIR / "v1_vs_v2_comparison.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 对比报告: {out}")
    print(f"已收集 v1 结果 {sum(1 for s, _, _ in CASES if (V1_DIR / f'{s}_tool_output.json').exists())} 份")
    print(f"已收集 v2 结果 {sum(1 for s, _, _ in CASES if (V2_DIR / f'{s}_tool_output.json').exists())} 份")


if __name__ == "__main__":
    main()
