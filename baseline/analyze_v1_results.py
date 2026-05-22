"""分析 v1 (修复前) 质安 测试结果，识别哪些 errors 是真错误 vs 噪音"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
RESULTS_BEFORE = ROOT / "baseline" / "results_before_fix"

# 加载 ground truth（错误版 vs 正确版的 diff）
diff_path = ROOT / "baseline" / "diffs" / "质安_diff.json"
ground_truth = json.loads(diff_path.read_text(encoding="utf-8"))

# 提取 ground truth 的所有测点 + 字段
gt_keys = set()
for sheet, diffs in ground_truth.items():
    for d in diffs:
        # 例：("支护结构水平位移", "WY241", "变化速率")
        # 字段从 field_name 第一段（如 "变化速率 / (mm/d) / 0.45" → "变化速率"）
        field = d.get("field_name", "").split("/")[0].strip()
        gt_keys.add((sheet, d.get("point_id", ""), field))

print(f"Ground truth: {len(gt_keys)} 个 (sheet, 测点, 字段) 三元组")
for k in sorted(gt_keys):
    print(f"  {k}")
print()

# 加载错误版工具结果
err_result = json.loads((RESULTS_BEFORE / "质安模板-错误版_tool_output.json").read_text(encoding="utf-8"))
cor_result = json.loads((RESULTS_BEFORE / "质安模板-正确版_tool_output.json").read_text(encoding="utf-8"))


def classify_issues(result: dict, label: str) -> dict:
    """对每条 issue 判断：匹配 ground truth？跨期统计噪音？速率推断噪音？"""
    cats = {
        "matched_gt": [],      # 真正命中 ground truth
        "stats_noise": [],     # 跨期统计噪音（v1 bug 1）
        "rate_noise": [],      # 7天 vs 2天 速率噪音（v1 bug 2）
        "other": [],
    }
    issues = result.get("errors", []) + result.get("warnings", [])
    for issue in issues:
        sheet = issue["table"]
        point = issue["point"]
        field = issue["field"]
        msg = issue["message"]

        # 速率噪音：含"反推间隔" 或 速率不一致从 7天 推
        if "反推间隔" in msg or "/ 7天 =" in msg:
            cats["rate_noise"].append(issue)
            continue

        # 统计噪音：跨期数据混淆（错误版/正确版都有相同的统计 errors）
        if field in ("正方向最大统计", "负方向最大统计", "最大速率统计"):
            # 如果正确版也报这条，那是噪音
            cats["stats_noise"].append(issue)
            continue

        # 命中 ground truth？
        key = (sheet, point, field)
        if key in gt_keys:
            cats["matched_gt"].append(issue)
            continue

        # 也可能 message 里含有 ground truth 测点
        for k in gt_keys:
            if k[1] and k[1] == point and sheet == k[0]:
                cats["matched_gt"].append(issue)
                break
        else:
            cats["other"].append(issue)

    print(f"\n=== {label} ===")
    for cat, lst in cats.items():
        print(f"  {cat}: {len(lst)} 条")

    return cats


print("=" * 70)
err_cats = classify_issues(err_result, "v1 错误版工具结果")
cor_cats = classify_issues(cor_result, "v1 正确版工具结果（应当 0 错误！）")

print()
print("=" * 70)
print("修复影响预估（v2 修复 bug 1 速率噪音 + bug 2 统计噪音）：")
print("=" * 70)
print(f"  错误版 issues: 总 {len(err_result.get('errors', [])) + len(err_result.get('warnings', []))}")
print(f"    - 修复后保留: matched_gt={len(err_cats['matched_gt'])} + other={len(err_cats['other'])} = "
      f"{len(err_cats['matched_gt']) + len(err_cats['other'])}")
print(f"    - 修复后移除: stats_noise={len(err_cats['stats_noise'])} + rate_noise={len(err_cats['rate_noise'])} = "
      f"{len(err_cats['stats_noise']) + len(err_cats['rate_noise'])}")
print()
print(f"  正确版 issues: 总 {len(cor_result.get('errors', [])) + len(cor_result.get('warnings', []))}")
print(f"    - 修复后保留: matched_gt={len(cor_cats['matched_gt'])} + other={len(cor_cats['other'])} = "
      f"{len(cor_cats['matched_gt']) + len(cor_cats['other'])} （理想 = 0）")
print(f"    - 修复后移除: stats_noise={len(cor_cats['stats_noise'])} + rate_noise={len(cor_cats['rate_noise'])} = "
      f"{len(cor_cats['stats_noise']) + len(cor_cats['rate_noise'])}")
