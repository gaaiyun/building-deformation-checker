"""建筑变形监测报告检查智能体 CLI 入口。

用法:
  python main.py <PDF文件路径> [--ocr | --no-ocr] [--no-ai-review] [--output <输出路径>]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from src.core.pipeline import RuntimeConfig, run_pipeline
from src.utils.dotenv_loader import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() not in {"0", "false", "no", "off"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="建筑变形监测报告检查智能体")
    parser.add_argument("pdf_path", help="待检查的 PDF 文件路径")
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument("--ocr", action="store_true", help="优先使用 PaddleOCR，失败时回退 pdfplumber")
    ocr_group.add_argument("--no-ocr", action="store_true", help="仅使用 pdfplumber，不调用 PaddleOCR")
    parser.add_argument("--no-ai-review", action="store_true", help="跳过 AI 最终审核")
    parser.add_argument("--no-self-verify", action="store_true", help="跳过自验证")
    parser.add_argument("--output", "-o", default=None, help="输出报告路径")
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="指定 LLM 模型 (如 deepseek-v4-flash, deepseek-v4-pro, MiniMax-M2.7)",
    )
    return parser


def _make_runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    pdf_name = Path(args.pdf_path).stem
    output_path = args.output or str(Path("output") / f"{pdf_name}_检查报告.md")

    return RuntimeConfig(
        pdf_path=args.pdf_path,
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        llm_model=args.model or os.getenv("LLM_MODEL", "deepseek-v4-flash"),
        llm_timeout_normal=int(os.getenv("LLM_TIMEOUT_NORMAL", "120")),
        llm_parse_chunk_chars=int(os.getenv("LLM_PARSE_CHUNK_CHARS", "18000")),
        llm_parse_max_tokens=int(os.getenv("LLM_PARSE_MAX_TOKENS", "24000")),
        llm_parse_timeout_sec=int(os.getenv("LLM_PARSE_TIMEOUT_SEC", "300")),
        llm_parse_max_parallel=int(os.getenv("LLM_PARSE_MAX_PARALLEL", "4")),
        paddle_ocr_token=os.getenv("PADDLE_OCR_TOKEN", ""),
        paddle_ocr_model=os.getenv("PADDLE_OCR_MODEL", "PaddleOCR-VL-1.6"),
        paddle_ocr_use_async=_env_bool("PADDLE_OCR_USE_ASYNC", True),
        paddle_ocr_use_cache=_env_bool("PADDLE_OCR_USE_CACHE", True),
        paddle_ocr_enable_legacy_fallback=_env_bool("PADDLE_OCR_ENABLE_LEGACY_FALLBACK", True),
        paddle_ocr_poll_timeout_sec=float(os.getenv("PADDLE_OCR_POLL_TIMEOUT_SEC", "900")),
        use_ocr=args.ocr,
        prefer_ocr=args.ocr,
        auto_fallback=not args.no_ocr,
        skip_self_verify=args.no_self_verify,
        skip_ai_review=args.no_ai_review,
        output_path=output_path,
    )


def _log_progress(step_id: str, label: str, percent: int, detail: str) -> None:
    logger.info("[%3d%%] %s: %s", percent, label or step_id, detail)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not Path(args.pdf_path).exists():
        logger.error("文件不存在: %s", args.pdf_path)
        return 1

    config = _make_runtime_config(args)
    if args.model:
        logger.info("使用模型: %s", config.llm_model)

    result = run_pipeline(config, progress_callback=_log_progress)

    if result.cancelled:
        logger.warning("检查已取消: %s", result.error_message)
        return 130

    if not result.success:
        logger.error("检查失败: %s", result.error_message)
        return 1

    logger.info("=" * 60)
    logger.info("检查完成!")
    logger.info(
        "  错误: %d  |  警告: %d  |  提示: %d",
        len(result.errors),
        len(result.warnings),
        len(result.infos),
    )
    logger.info("  报告已保存至: %s", result.output_path)
    logger.info("=" * 60)

    if result.errors:
        logger.info("发现的错误:")
        for index, issue in enumerate(result.errors, 1):
            logger.info("  %d. %s", index, issue)

    return 0


if __name__ == "__main__":
    sys.exit(main())
