"""对工具输出做 GT 召回/精度评分（无需 API，秒级）。

用法：
    python baseline/score_recall.py            # 评所有模板
    python baseline/score_recall.py 质安        # 只评指定模板

口径：
    - GT = baseline/diffs/<模板>_diff.json 里逐 cell 的注入错误（错误版 vs 正确版）。
    - 召回：某 GT cell 被"命中" = 工具在错误版的某条 error 里，测点号一致且字段关键词相关。
      高程类改动允许用"累计"命中（工具靠累计=本次−初始不一致间接检出）。
    - 精度：正确版工具报出的 error 数（应为 0，每个都是假阳性）。
    - 未匹配 GT 的工具 error 记为"额外报出"（可能是衍生判定或假阳性，需人工看）。

注意：测点+字段的匹配是近似的——同一测点同一字段在多期会重复，脚本按"该测点该字段
至少被命中一次"计，可能略微高估；但对趋势对照足够。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DIFFS = ROOT / "baseline" / "diffs"
RESULTS = ROOT / "baseline" / "results"

TEMPLATES = ["质安", "深工勘", "展誉"]


def _gt_keywords(field_name: str) -> list[str]:
    """从 GT 的 field_name（如 '累计变化量 / (mm) / 10.8'）解出匹配关键词。"""
    core = field_name.split("/")[0].strip()
    kws: list[str] = []
    if "累计" in core:
        kws.append("累计")
    if "速率" in core:
        kws.append("速率")
    if "断面" in core or "距离" in core:
        kws += ["断面", "距离"]
    if "高程" in core:
        kws += ["高程", "累计"]  # 本次高程改动 → 累计变化量不一致
    if "位移" in core and not kws:
        kws.append("位移")
    if "沉降" in core:
        kws.append("沉降")
    if "水位" in core:
        kws.append("水位")
    if "倾斜" in core:
        kws.append("倾斜")
    # 空 field_name（如展誉 GT 的位置型改动）→ 不返回空串关键词（"" in blob 恒真会虚高），
    # 改为只依赖数值匹配
    return kws or ([core] if core else [])


def _load_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _value_strings(gt: dict) -> list[str]:
    """GT 的错误值/正确值里足够独特（≥3 位有效数字）的数字串，用于值匹配。

    工具常以'安全状态/负方向最大'等框架报出同一处错误（字段关键词对不上），
    但消息里会出现该处的具体数值（如 -130.36），按值匹配可避免低估召回。
    """
    out = []
    for v in (gt.get("error_value"), gt.get("correct_value")):
        s = str(v).strip().lstrip("-")
        digits = s.replace(".", "")
        if len(digits.rstrip("0")) >= 3:  # 至少3位有效数字，排除 "10"/"2.0" 等泛匹配
            out.append(s.rstrip("0").rstrip(".") if "." in s else s)
    return out


def _caught(gt: dict, tool_errors: list[dict]):
    """该 GT cell 是否被工具某条 error 命中；返回命中的 error 或 None。

    命中 = 测点号一致，且（字段关键词相关 或 GT 具体数值出现在消息里）。
    """
    # GT 测点号可能带类别后缀（如展誉的 "WY4(位移)"），工具报的是 "WY4"；去后缀再匹配
    pt = gt["point_id"].split("(")[0].split("（")[0].strip()
    kws = _gt_keywords(gt["field_name"])
    vals = _value_strings(gt)
    for e in tool_errors:
        ept = e.get("point") or ""
        if pt and (pt in ept or ept.split("(")[0].split("（")[0].strip() == pt):
            blob = (e.get("field") or "") + (e.get("message") or "")
            if any(kw in blob for kw in kws) or any(vs in blob for vs in vals):
                return e
    return None


def score_template(name: str) -> dict | None:
    gt_data = _load_json(DIFFS / f"{name}_diff.json")
    err = _load_json(RESULTS / f"{name}模板-错误版_tool_output.json")
    ok = _load_json(RESULTS / f"{name}模板-正确版_tool_output.json")
    if gt_data is None:
        print(f"[{name}] 缺 GT diff，跳过")
        return None

    gt_cells = [c for cells in gt_data.values() for c in cells]
    total = len(gt_cells)

    if err is None:
        print(f"[{name}] 错误版结果尚未产出（e2e 未跑到），跳过")
        return None

    tool_errors = err.get("errors", [])
    matched, missed = [], []
    used_err_ids = set()
    for gt in gt_cells:
        hit = _caught(gt, tool_errors)
        if hit is not None:
            matched.append((gt, hit))
            used_err_ids.add(id(hit))
        else:
            missed.append(gt)

    extra = [e for e in tool_errors if id(e) not in used_err_ids]
    recall = len(matched) / total if total else 0.0
    fp_on_correct = ok.get("errors_count") if ok else "?"

    print(f"\n{'='*64}")
    print(f"【{name}模板】GT={total} | 工具错误版 errors={err.get('errors_count')} "
          f"| 命中 GT={len(matched)} | 召回≈{recall*100:.0f}% | 正确版假阳性 errors={fp_on_correct}")
    print(f"{'-'*64}")
    if missed:
        print("  漏报的 GT：")
        for gt in missed:
            print(f"    ✗ {gt['point_id']:8} {gt['field_name'].split('/')[0].strip():10} "
                  f"({gt['cell']}: {gt['correct_value']}→{gt['error_value']})")
    if extra:
        print(f"  额外报出（未对上 GT，需人工判断假阳性/衍生）: {len(extra)} 条")
        for e in extra[:6]:
            print(f"    ? [{e.get('point')}] {e.get('field')}: {(e.get('message') or '')[:50]}")
    return {"template": name, "gt": total, "tool_errors": err.get("errors_count"),
            "matched": len(matched), "recall": recall, "fp_correct": fp_on_correct}


def main():
    targets = [a for a in sys.argv[1:]] or TEMPLATES
    rows = []
    for name in targets:
        r = score_template(name)
        if r:
            rows.append(r)
    if rows:
        print(f"\n{'='*64}\n汇总矩阵\n{'='*64}")
        print(f"  {'模板':8} {'GT':>4} {'工具errors':>10} {'命中GT':>7} {'召回':>6} {'正确版FP':>8}")
        for r in rows:
            print(f"  {r['template']:8} {r['gt']:>4} {r['tool_errors']:>10} "
                  f"{r['matched']:>7} {r['recall']*100:>5.0f}% {str(r['fp_correct']):>8}")


if __name__ == "__main__":
    main()
