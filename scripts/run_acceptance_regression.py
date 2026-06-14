"""Run real PDF acceptance cases and save auditable outputs.

This script is intended for release verification, not unit testing. It runs the
same pipeline used by desktop/Streamlit/CLI, writes per-sample reports, and
produces a summary JSON/Markdown file that can be reviewed by business users.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core import PipelineResult, RuntimeConfig, run_pipeline
from src.tools.export_formats import generate_docx, generate_html, generate_intermediate_xlsx
from src.utils.dotenv_loader import load_dotenv

try:
    from gui_desktop.settings_store import load_settings
except Exception:  # pragma: no cover - only used in local acceptance runs
    load_settings = None


EXCEL_CASES = [
    ("excel_pdf", "质安模板-错误版", "质安模板-错误版.pdf", False),
    ("excel_pdf", "质安模板-正确版", "质安模板-正确版.pdf", False),
    ("excel_pdf", "深工勘模板-错误版", "深工勘模板-错误版.pdf", False),
    ("excel_pdf", "深工勘模板-正确版", "深工勘模板-正确版.pdf", False),
    ("excel_pdf", "展誉模板-错误版", "展誉模板-错误版.pdf", False),
    ("excel_pdf", "展誉模板-正确版", "展誉模板-正确版.pdf", False),
]

ORIGINAL_CASES = [
    ("original_pdf", "鱼珠乐天", "【监测2023011-017】鱼珠乐天智能科技创新中心(1).pdf", False),
    ("original_pdf", "监测报告测试", "监测报告检查（测试）.pdf", False),
    ("original_pdf", "红土创新广场", "红土创新广场项目基坑监测报告第133期-hb.pdf", False),
    ("original_pdf", "恒大中心", "恒大中心基坑支护工程地铁监测报告第209期（第3616次）.pdf", False),
    ("original_pdf", "设计说明", "设计的完整说明1.pdf", False),
]

PADDLE_CASES = [
    ("paddle_forced", "Paddle强制OCR smoke", "test_ocr_smoke.pdf", True),
]


def _default_sample_roots() -> list[Path]:
    roots: list[Path] = []
    env_roots = os.environ.get("BDC_SAMPLE_ROOTS", "")
    for item in env_roots.split(os.pathsep):
        if item.strip():
            roots.append(Path(item.strip()))
    sibling_archive = ROOT.parent / "_archive" / "建筑变形监测Agent-desktop-legacy-20260607"
    roots.extend([
        ROOT / "test_pdfs",
        ROOT,
        sibling_archive,
        sibling_archive / "test_pdfs",
    ])
    return roots


def _resolve_pdf(name: str, sample_roots: list[Path]) -> Path:
    for root in sample_roots:
        candidate = root / name
        if candidate.exists():
            return candidate
    return sample_roots[0] / name


def _load_runtime_settings() -> dict:
    load_dotenv()
    saved = load_settings() if load_settings else {}
    return {
        "llm_api_key": os.environ.get("LLM_API_KEY") or saved.get("llm_api_key", ""),
        "llm_base_url": os.environ.get("LLM_BASE_URL") or saved.get("llm_base_url", "https://api.deepseek.com"),
        "llm_model": os.environ.get("LLM_MODEL") or saved.get("llm_model", "deepseek-v4-flash"),
        "paddle_ocr_token": os.environ.get("PADDLE_OCR_TOKEN") or saved.get("paddle_ocr_token", ""),
        "paddle_ocr_model": os.environ.get("PADDLE_OCR_MODEL") or saved.get("paddle_ocr_model", "PaddleOCR-VL-1.6"),
    }


def _build_config(
    pdf_path: Path,
    settings: dict,
    output_dir: Path,
    *,
    force_ocr: bool,
    fresh: bool,
    full_ai: bool,
    llm_parallel: int,
) -> RuntimeConfig:
    return RuntimeConfig(
        pdf_path=str(pdf_path),
        llm_api_key=settings["llm_api_key"],
        llm_base_url=settings["llm_base_url"],
        llm_model=settings["llm_model"],
        llm_parse_max_parallel=llm_parallel,
        paddle_ocr_token=settings["paddle_ocr_token"],
        paddle_ocr_model=settings["paddle_ocr_model"],
        paddle_ocr_use_async=True,
        paddle_ocr_use_cache=not fresh,
        use_ocr=force_ocr,
        prefer_ocr=force_ocr,
        auto_fallback=not force_ocr,
        skip_self_verify=not full_ai,
        skip_ai_review=not full_ai,
        output_dir=str(output_dir),
    )


def _issue_sample(issues: list, limit: int = 12) -> list[dict]:
    return [
        {
            "severity": issue.severity,
            "table": issue.table_name,
            "point": issue.point_id,
            "field": issue.field_name,
            "message": issue.message,
        }
        for issue in issues[:limit]
    ]


def _write_extra_exports(result: PipelineResult, output_dir: Path, case_name: str) -> dict:
    paths: dict[str, str] = {}
    if not result.success or not result.report:
        return paths

    safe_name = case_name.replace("/", "_").replace("\\", "_")

    xlsx_path = output_dir / f"{safe_name}_Excel中间层.xlsx"
    xlsx_path.write_bytes(generate_intermediate_xlsx(
        result.report,
        calc_issues=result.calc_issues,
        stats_issues=result.stats_issues,
        logic_issues=result.logic_issues,
        analysis_plan=result.analysis_plan,
    ))
    paths["xlsx_path"] = str(xlsx_path)

    docx_path = output_dir / f"{safe_name}_检查报告.docx"
    docx_path.write_bytes(generate_docx(result.final_md, result.report, result.errors, result.warnings))
    paths["docx_path"] = str(docx_path)

    html_path = output_dir / f"{safe_name}_检查报告.html"
    html_path.write_text(
        generate_html(result.final_md, getattr(result.report, "project_name", "") or "检查报告"),
        encoding="utf-8",
    )
    paths["html_path"] = str(html_path)
    return paths


def run_case(
    case: tuple[str, str, str, bool],
    *,
    settings: dict,
    sample_roots: list[Path],
    output_dir: Path,
    fresh: bool,
    full_ai: bool,
    llm_parallel: int,
) -> dict:
    group, name, file_name, force_ocr = case
    pdf_path = _resolve_pdf(file_name, sample_roots)
    if not pdf_path.exists():
        return {
            "group": group,
            "name": name,
            "file": file_name,
            "success": False,
            "error_message": f"PDF 不存在: {pdf_path}",
        }

    print(f"\n{'=' * 80}\n{name} :: {pdf_path}\n{'=' * 80}")
    cfg = _build_config(
        pdf_path,
        settings,
        output_dir,
        force_ocr=force_ocr,
        fresh=fresh,
        full_ai=full_ai,
        llm_parallel=llm_parallel,
    )

    progress_lines: list[str] = []

    def progress(step_id: str, label: str, percent: int, detail: str) -> None:
        line = f"[{percent:3d}%] {step_id} {label} {detail}"
        progress_lines.append(line)
        print(line)

    old_cache = os.environ.get("LLM_USE_CACHE")
    os.environ["LLM_USE_CACHE"] = "0" if fresh else "1"
    start = time.time()
    try:
        result = run_pipeline(cfg, progress_callback=progress)
    finally:
        if old_cache is None:
            os.environ.pop("LLM_USE_CACHE", None)
        else:
            os.environ["LLM_USE_CACHE"] = old_cache

    duration = time.time() - start
    extra_paths = _write_extra_exports(result, output_dir, name)
    log_path = output_dir / f"{name}_progress.log"
    log_path.write_text("\n".join(progress_lines), encoding="utf-8")

    report = result.report
    summary = {
        "group": group,
        "name": name,
        "file": file_name,
        "path": str(pdf_path),
        "forced_ocr": force_ocr,
        "success": result.success,
        "cancelled": result.cancelled,
        "error_message": result.error_message,
        "duration_sec": round(duration, 1),
        "extraction_method": result.extraction_method,
        "extraction_profile": result.extraction_profile,
        "tables_count": len(report.tables) if report else 0,
        "errors_count": len(result.errors),
        "warnings_count": len(result.warnings),
        "infos_count": len(result.infos),
        "raw_chars": len(result.raw_text or ""),
        "output_path": result.output_path,
        "progress_log": str(log_path),
        **extra_paths,
        "sample_errors": _issue_sample(result.errors),
        "sample_warnings": _issue_sample(result.warnings),
    }
    print(
        f"完成: success={result.success} err={summary['errors_count']} "
        f"warn={summary['warnings_count']} tables={summary['tables_count']} "
        f"time={duration:.1f}s method={result.extraction_method}/{result.extraction_profile}"
    )
    return summary


def _write_markdown(summary: list[dict], output_dir: Path, settings: dict, fresh: bool, full_ai: bool) -> Path:
    lines = [
        "# 实际验收测试报告",
        "",
        f"- 时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 输出目录: `{output_dir}`",
        f"- LLM Base URL: `{settings['llm_base_url']}`",
        f"- LLM Model: `{settings['llm_model']}`",
        f"- PaddleOCR Model: `{settings['paddle_ocr_model']}`",
        f"- 从头测试: `{fresh}`",
        f"- Step 6/7 AI 复核: `{'启用' if full_ai else '跳过'}`",
        "",
        "| 分组 | 样本 | 成功 | 提取 | 表数 | error | warning | info | 耗时(s) |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row.get('group','')} | {row.get('name','')} | {row.get('success')} | "
            f"{row.get('extraction_method','')}/{row.get('extraction_profile','')} | "
            f"{row.get('tables_count',0)} | {row.get('errors_count',0)} | "
            f"{row.get('warnings_count',0)} | {row.get('infos_count',0)} | "
            f"{row.get('duration_sec',0)} |"
        )
    lines.extend(["", "## 失败与样例问题", ""])
    for row in summary:
        if row.get("error_message"):
            lines.append(f"### {row['name']}")
            lines.append("")
            lines.append(f"- 失败原因: `{row['error_message']}`")
            lines.append("")
        elif row.get("sample_errors"):
            lines.append(f"### {row['name']} · error 样例")
            lines.append("")
            for issue in row["sample_errors"][:5]:
                lines.append(
                    f"- `{issue['table']}` / `{issue['point']}` / `{issue['field']}`: "
                    f"{issue['message']}"
                )
            lines.append("")
    report_path = output_dir / "实际验收测试报告.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", choices=["all", "excel", "original", "paddle"], default="all")
    parser.add_argument("--only", help="只跑名称或文件名包含该关键字的样本")
    parser.add_argument("--output-dir", help="输出目录；默认 output/actual_acceptance_<timestamp>")
    parser.add_argument("--sample-root", action="append", default=[], help="额外样本根目录，可重复")
    parser.add_argument("--reuse-cache", action="store_true", help="复用 LLM/OCR 缓存；默认从头测试")
    parser.add_argument("--full-ai", action="store_true", help="启用 Step 6/7 AI 自验证和最终审核")
    parser.add_argument("--llm-parallel", type=int, default=4)
    args = parser.parse_args()

    settings = _load_runtime_settings()
    if not settings["llm_api_key"]:
        print("LLM_API_KEY 未设置，且 keyring 中未读取到 LLM API Key。")
        return 2

    sample_roots = [Path(p) for p in args.sample_root] + _default_sample_roots()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "output" / f"actual_acceptance_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    cases: list[tuple[str, str, str, bool]] = []
    if args.group in {"all", "excel"}:
        cases.extend(EXCEL_CASES)
    if args.group in {"all", "original"}:
        cases.extend(ORIGINAL_CASES)
    if args.group in {"all", "paddle"}:
        cases.extend(PADDLE_CASES)
    if args.only:
        cases = [c for c in cases if args.only in c[1] or args.only in c[2]]

    print(f"样本数: {len(cases)}")
    print(f"输出目录: {output_dir}")
    print(f"从头测试: {not args.reuse_cache}; full_ai: {args.full_ai}; parallel: {args.llm_parallel}")

    summary = []
    for case in cases:
        summary.append(run_case(
            case,
            settings=settings,
            sample_roots=sample_roots,
            output_dir=output_dir,
            fresh=not args.reuse_cache,
            full_ai=args.full_ai,
            llm_parallel=args.llm_parallel,
        ))

    json_path = output_dir / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = _write_markdown(summary, output_dir, settings, fresh=not args.reuse_cache, full_ai=args.full_ai)
    print(f"\nsummary: {json_path}")
    print(f"report:  {md_path}")
    return 0 if all(row.get("success") for row in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
