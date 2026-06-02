"""跑原 5 个 PDF 验证 Gap 1/2/3 是否实际生效。

使用缓存 OCR 数据（output/*_ocr_debug）+ LLM，输出结果到
baseline/results_original/<pdf>_tool_output.json
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.dotenv_loader import load_dotenv
load_dotenv()

import os

from src.core import PipelineResult, RuntimeConfig, run_pipeline

RESULTS_DIR = ROOT / "baseline" / "results_original"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_SAMPLE_ROOT = Path("C:/Users/gaaiy/Desktop/建筑变形监测Agent")

# 原 5 个 PDF（不是 XLSX 转换的）
TEST_CASES = [
    ("鱼珠乐天", "【监测2023011-017】鱼珠乐天智能科技创新中心(1).pdf"),
    ("监测报告测试", "监测报告检查（测试）.pdf"),
    ("红土创新广场", "红土创新广场项目基坑监测报告第133期-hb.pdf"),
    ("恒大中心", "恒大中心基坑支护工程地铁监测报告第209期（第3616次）.pdf"),
    ("设计说明", "设计的完整说明1.pdf"),
]


def _resolve_pdf_path(pdf_name: str) -> Path:
    """Find an original PDF in the current repo or the legacy local sample folder."""
    candidates = [
        ROOT / pdf_name,
        ROOT / "test_pdfs" / pdf_name,
        LEGACY_SAMPLE_ROOT / pdf_name,
        LEGACY_SAMPLE_ROOT / "test_pdfs" / pdf_name,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def build_runtime_config(pdf_path: str, quick: bool) -> RuntimeConfig:
    return RuntimeConfig(
        pdf_path=pdf_path,
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1"),
        llm_model=os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed"),
        paddle_ocr_token=os.environ.get("PADDLE_OCR_TOKEN", ""),
        paddle_ocr_use_cache=True,  # 关键：复用缓存 OCR
        use_ocr=False,
        prefer_ocr=False,
        auto_fallback=True,
        skip_self_verify=quick,
        skip_ai_review=quick,
        output_dir=str(RESULTS_DIR),
    )


def run_single(name: str, pdf_name: str, quick: bool) -> dict:
    pdf_path = _resolve_pdf_path(pdf_name)
    if not pdf_path.exists():
        return {"name": name, "pdf": pdf_name, "success": False, "error": f"PDF 不存在: {pdf_path}"}

    print(f"\n{'─' * 70}\n🔍 {name} — {pdf_name}\n{'─' * 70}")
    cfg = build_runtime_config(str(pdf_path), quick)
    if not cfg.llm_api_key:
        return {"error": "LLM_API_KEY 未设置"}

    start = time.time()

    def progress(step_id, label, percent, detail):
        print(f"  [{percent:3d}%] {label}  {detail}")

    result: PipelineResult = run_pipeline(cfg, progress_callback=progress)
    duration = time.time() - start

    out_json = RESULTS_DIR / f"{name}_tool_output.json"
    out_md = RESULTS_DIR / f"{name}_tool_output.md"
    if result.final_md:
        out_md.write_text(result.final_md, encoding="utf-8")

    summary = {
        "name": name,
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
                "table": i.table_name, "point": i.point_id,
                "field": i.field_name, "severity": i.severity,
                "message": i.message[:200],
            } for i in result.errors[:30]
        ],
        "warnings": [
            {
                "table": i.table_name, "point": i.point_id,
                "field": i.field_name, "severity": i.severity,
                "message": i.message[:200],
            } for i in result.warnings[:30]
        ],
        # 新增 Gap 相关字段
        "ocr_damage_count": (result.report.extraction_diagnostics.get("ocr_damage_count", 0)
                              if result.report and result.report.extraction_diagnostics else 0),
        "proximity_warning_count": sum(
            1 for w in result.warnings if "接近" in (w.message or "")
        ),
        "anomaly_warning_count": sum(
            1 for w in result.warnings if "单期变化" in (w.field_name or "")
        ),
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n  完成 {duration:.0f}s · err {len(result.errors)} / warn {len(result.warnings)} / "
          f"ocr_damage {summary['ocr_damage_count']} / proximity {summary['proximity_warning_count']} / "
          f"anomaly {summary['anomaly_warning_count']}")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="跳过 Step 6+7 (推荐)")
    parser.add_argument("--only", help="只跑包含此关键字")
    args = parser.parse_args()

    if not os.environ.get("LLM_API_KEY"):
        print("⚠️  LLM_API_KEY 未设置")
        return 1

    cases = TEST_CASES
    if args.only:
        cases = [c for c in cases if args.only in c[0] or args.only in c[1]]

    print(f"原 PDF 测试 · 跑 {len(cases)} 个 case")

    results = []
    for name, pdf in cases:
        r = run_single(name, pdf, args.quick)
        results.append(r)

    # 汇总
    print(f"\n{'=' * 70}\n汇总\n{'=' * 70}")
    print(f"  {'name':12} {'err':>5} {'warn':>5} {'ocr_dmg':>8} {'prox':>5} {'anom':>5} {'time':>6}")
    for r in results:
        if "name" not in r:
            continue
        print(f"  {r['name']:12} {r.get('errors_count', '-'):>5} {r.get('warnings_count', '-'):>5} "
              f"{r.get('ocr_damage_count', 0):>8} {r.get('proximity_warning_count', 0):>5} "
              f"{r.get('anomaly_warning_count', 0):>5} {r.get('duration_sec', 0):>5.0f}s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
