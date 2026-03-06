"""
建筑变形监测报告检查智能体 — 主入口

用法:
  python main.py <PDF文件路径> [--ocr] [--no-ai-review] [--output <输出路径>]

示例:
  python main.py "监测报告检查（测试）.pdf"
  python main.py report.pdf --ocr --output output/check_report.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main():
    parser = argparse.ArgumentParser(
        description="建筑变形监测报告检查智能体",
    )
    parser.add_argument("pdf_path", help="待检查的 PDF 文件路径")
    parser.add_argument("--ocr", action="store_true", help="使用 PaddleOCR（适用于扫描件）")
    parser.add_argument("--no-ai-review", action="store_true", help="跳过 AI 最终审核")
    parser.add_argument("--output", "-o", default=None, help="输出报告路径（默认 output/<文件名>_检查报告.md）")

    args = parser.parse_args()

    pdf_path = args.pdf_path
    if not Path(pdf_path).exists():
        logger.error("文件不存在: %s", pdf_path)
        sys.exit(1)

    pdf_name = Path(pdf_path).stem

    if args.output:
        output_path = args.output
    else:
        output_path = f"output/{pdf_name}_检查报告.md"

    # ── Step 1: PDF 提取 ─────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 1: 提取 PDF 内容")
    logger.info("=" * 60)

    from src.tools.pdf_extractor import extract_pdf

    raw_text = extract_pdf(
        pdf_path,
        use_ocr=args.ocr,
        ocr_output_dir=f"output/{pdf_name}_ocr" if args.ocr else None,
    )
    logger.info("提取完成，文本长度: %d 字符", len(raw_text))

    # ── Step 2: LLM 结构化解析 ────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 2: LLM 结构化解析")
    logger.info("=" * 60)

    from src.tools.llm_parser import parse_report_with_llm

    report = parse_report_with_llm(raw_text)
    report.raw_text = raw_text

    logger.info("解析结果: %s", report.project_name)
    logger.info("  - 阈值配置: %d 项", len(report.thresholds))
    logger.info("  - 汇总项: %d 项", len(report.summary_items))
    logger.info("  - 数据表: %d 张", len(report.tables))
    for t in report.tables:
        pts = len(t.points) if t.points else len(t.deep_points)
        label = t.monitoring_item
        if t.borehole_id:
            label += f" ({t.borehole_id})"
        logger.info("    * %s — %d 个测点", label, pts)

    # ── Step 3: 计算验证 ──────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 3: 计算验证")
    logger.info("=" * 60)

    from src.tools.calculation_checker import run_calculation_checks

    calc_issues = run_calculation_checks(report)
    logger.info("计算验证完成: %d 个问题", len(calc_issues))

    # ── Step 4: 统计验证 ──────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 4: 统计验证")
    logger.info("=" * 60)

    from src.tools.statistics_checker import run_statistics_checks

    stats_issues = run_statistics_checks(report)
    logger.info("统计验证完成: %d 个问题", len(stats_issues))

    # ── Step 5: 逻辑检查 ──────────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 5: 逻辑检查")
    logger.info("=" * 60)

    from src.tools.logic_checker import run_logic_checks

    logic_issues = run_logic_checks(report)
    logger.info("逻辑检查完成: %d 个问题", len(logic_issues))

    # ── Step 6: AI 最终审核（可选）──────────────────────────
    ai_review = ""
    if not args.no_ai_review:
        logger.info("=" * 60)
        logger.info("Step 6: AI 最终审核")
        logger.info("=" * 60)

        from src.tools.report_generator import generate_report_md
        from src.tools.llm_parser import verify_report_with_llm

        preliminary_md = generate_report_md(report, calc_issues, stats_issues, logic_issues)
        ai_review = verify_report_with_llm(preliminary_md, raw_text)
        logger.info("AI 审核完成")

    # ── Step 7: 生成检查报告 ──────────────────────────────
    logger.info("=" * 60)
    logger.info("Step 7: 生成检查报告")
    logger.info("=" * 60)

    from src.tools.report_generator import generate_report_md, save_report

    final_md = generate_report_md(report, calc_issues, stats_issues, logic_issues, ai_review)
    save_report(final_md, output_path)

    # ── 汇总输出 ──────────────────────────────────────────
    all_issues = calc_issues + stats_issues + logic_issues
    errors = [i for i in all_issues if i.severity == "error"]
    warnings = [i for i in all_issues if i.severity == "warning"]

    logger.info("=" * 60)
    logger.info("检查完成!")
    logger.info("  错误: %d  |  警告: %d  |  提示: %d", len(errors), len(warnings), len(all_issues) - len(errors) - len(warnings))
    logger.info("  报告已保存至: %s", output_path)
    logger.info("=" * 60)

    if errors:
        logger.info("发现的错误:")
        for i, e in enumerate(errors, 1):
            logger.info("  %d. %s", i, e)


if __name__ == "__main__":
    main()
