"""跑 v2 工具对 6 个测试 PDF，输出每份的检查报告并与 diff baseline 对比。

用法：
    python baseline/run_tool_tests.py            # 需 .env 含 LLM_API_KEY
    python baseline/run_tool_tests.py --quick    # 跳过 Step 6+7 提速
    python baseline/run_tool_tests.py --only 质安  # 只跑指定模板

输出：
    baseline/results/<pdf_name>_tool_output.md   - v2 工具的完整检查报告
    baseline/results/<pdf_name>_tool_output.json - 结构化结果
    baseline/results/comparison_report.md        - 工具 vs ground truth 对比
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 加载 .env
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.utils.dotenv_loader import load_dotenv
load_dotenv()

import os

from src.core import PipelineResult, RuntimeConfig, run_pipeline

RESULTS_DIR = ROOT / "baseline" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_CASES = [
    # (PDF 文件名, ground truth diff 文件名, 预期错误数)
    ("质安模板-错误版.pdf", "质安_diff.md", 14),
    ("质安模板-正确版.pdf", None, 0),
    ("深工勘模板-错误版.pdf", "深工勘_diff.md", 17),
    ("深工勘模板-正确版.pdf", None, 0),
    ("展誉模板-错误版.pdf", "展誉_diff.md", 16),
    ("展誉模板-正确版.pdf", None, 0),
]


def build_runtime_config(pdf_path: str, quick: bool) -> RuntimeConfig:
    """从环境变量构造 RuntimeConfig"""
    return RuntimeConfig(
        pdf_path=pdf_path,
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1"),
        llm_model=os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed"),
        paddle_ocr_token=os.environ.get("PADDLE_OCR_TOKEN", ""),
        paddle_ocr_use_cache=True,
        use_ocr=False,           # XLSX 转的 PDF 是文字版，不需要 OCR
        prefer_ocr=False,
        auto_fallback=False,     # 强制走 pdfplumber 文本层
        skip_self_verify=quick,
        skip_ai_review=quick,
        output_dir=str(RESULTS_DIR),
    )


def run_single(pdf_name: str, quick: bool) -> dict:
    pdf_path = ROOT / "test_pdfs" / pdf_name
    if not pdf_path.exists():
        return {"error": f"PDF 不存在: {pdf_path}"}

    print(f"\n{'─' * 70}")
    print(f"🔍 跑工具: {pdf_name}")
    print("─" * 70)

    cfg = build_runtime_config(str(pdf_path), quick)
    if not cfg.llm_api_key:
        return {"error": "LLM_API_KEY 未设置（在 .env 中填）"}

    start = time.time()

    def progress(step_id, label, percent, detail):
        print(f"  [{percent:3d}%] {label}  {detail}")

    result: PipelineResult = run_pipeline(cfg, progress_callback=progress)
    duration = time.time() - start

    # 保存详细 MD
    out_md = RESULTS_DIR / f"{Path(pdf_name).stem}_tool_output.md"
    if result.final_md:
        out_md.write_text(result.final_md, encoding="utf-8")

    # 保存 JSON 摘要（便于程序对比）
    out_json = RESULTS_DIR / f"{Path(pdf_name).stem}_tool_output.json"
    summary = {
        "pdf": pdf_name,
        "success": result.success,
        "cancelled": result.cancelled,
        "error_message": result.error_message,
        "duration_sec": round(duration, 1),
        "extraction_method": result.extraction_method,
        "errors_count": len(result.errors),
        "warnings_count": len(result.warnings),
        "infos_count": len(result.infos),
        "tables_count": len(result.report.tables) if result.report else 0,
        "errors": [
            {
                "table": i.table_name,
                "point": i.point_id,
                "field": i.field_name,
                "severity": i.severity,
                "message": i.message,
            }
            for i in result.errors
        ],
        "warnings": [
            {
                "table": i.table_name,
                "point": i.point_id,
                "field": i.field_name,
                "severity": i.severity,
                "message": i.message,
            }
            for i in result.warnings
        ],
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n  完成 - 耗时 {duration:.0f}s, 错误 {len(result.errors)} / 警告 {len(result.warnings)}")
    print(f"  输出: {out_md.name}")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="跳过 Step 6+7")
    parser.add_argument("--only", help="只跑包含此关键字的 case")
    args = parser.parse_args()

    print(f"v2 工具批量测试 · 6 个测试 PDF")
    print(f"API: {os.environ.get('LLM_BASE_URL', '?')} · 模型: {os.environ.get('LLM_MODEL', '?')}")
    print(f"快速模式: {'是' if args.quick else '否（含 Step 6+7）'}")
    if not os.environ.get("LLM_API_KEY"):
        print("\n⚠️  LLM_API_KEY 未设置。请创建 .env 文件，参照 .env.example")
        return 1

    cases = TEST_CASES
    if args.only:
        cases = [c for c in cases if args.only in c[0]]
        print(f"过滤: 只跑 {len(cases)} 个 case")

    all_results = []
    for pdf_name, diff_name, expected_errors in cases:
        result = run_single(pdf_name, args.quick)
        all_results.append({
            **result,
            "expected_errors": expected_errors,
            "diff_file": diff_name,
        })

    # 汇总
    print(f"\n{'=' * 70}")
    print("汇总")
    print("=" * 70)
    print(f"  {'PDF':30} {'工具错误':>8} {'工具警告':>8} {'期望错误':>8} {'耗时':>6}")
    for r in all_results:
        if "pdf" not in r:
            continue
        ec = r.get("errors_count", "-")
        wc = r.get("warnings_count", "-")
        exp = r.get("expected_errors", "-")
        dur = f"{r.get('duration_sec', 0):.0f}s"
        print(f"  {r['pdf']:30} {ec:>8} {wc:>8} {exp:>8} {dur:>6}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
