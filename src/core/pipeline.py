"""UI 无关的 8 步核查流水线编排器

这是核心引擎，可被任意 UI 调用：CLI / Streamlit / PySide6 桌面 / FastAPI / 单元测试。

设计原则：
- 无全局状态依赖（所有配置通过 RuntimeConfig 传入）
- 进度通过回调函数上报（不直接操作任何 UI）
- 可中途取消（通过 cancel_event）
- 单步失败时尽可能降级而非整体中断
- 返回结构化 PipelineResult 包含所有中间产物

用法:
    cfg = RuntimeConfig(pdf_path="x.pdf", llm_api_key="sk-...", llm_model="deepseek-v4-flash")
    result = run_pipeline(cfg, progress_callback=lambda step, pct, msg: print(msg))
    print(result.final_md)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 进度回调签名: (step_id, step_label, percent, detail)
ProgressCallback = Callable[[str, str, int, str], None]

# 全局配置同步锁
#
# Why: RuntimeConfig.to_app_globals() 会写 src.config 模块属性、os.environ
#      以及 src.tools.pdf_extractor 的导入常量。这些都是进程级共享状态，
#      若两个流水线并发运行（如同一进程的两个桌面窗口、Streamlit 多 session）
#      会出现配置互相覆盖的竞态条件。本锁把配置写入串行化以避免半改半读。
#
# How:  pipeline 内部所有"配置写入 + 立即使用"的临界区都用本锁保护。
#       底层工具读取 src.config.XXX 时不需要持锁（最终一致即可）。
_CONFIG_LOCK = threading.RLock()


# ─── 配置 ───────────────────────────────────────────────────
@dataclass
class RuntimeConfig:
    """流水线运行时配置 - 取代环境变量与 src.config 全局状态"""

    pdf_path: str
    """待核查 PDF 文件路径"""

    # ── LLM 配置 ────────────────────────────────
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_normal: int = 120
    llm_parse_chunk_chars: int = 18000
    llm_parse_max_tokens: int = 24000
    llm_parse_timeout_sec: int = 300

    # ── PaddleOCR 配置 ──────────────────────────
    paddle_ocr_token: str = ""
    paddle_ocr_model: str = "PaddleOCR-VL-1.6"
    paddle_ocr_use_async: bool = True
    paddle_ocr_use_cache: bool = True
    paddle_ocr_enable_legacy_fallback: bool = True
    paddle_ocr_poll_timeout_sec: float = 900.0

    # ── 提取行为 ────────────────────────────────
    use_ocr: bool = False
    """显式优先 OCR"""
    prefer_ocr: bool = False
    """同 use_ocr，保留为兼容字段"""
    auto_fallback: bool = True
    """文本层抽取失败时自动回退 OCR"""
    skip_text_layer_check: bool = False
    """跳过 PyMuPDF 文本层质量判断（强制按 use_ocr 走）"""

    # ── 流水线开关 ──────────────────────────────
    skip_self_verify: bool = False
    skip_ai_review: bool = False

    # ── 输出 ────────────────────────────────────
    output_dir: str = "output"
    output_path: Optional[str] = None
    """显式输出 .md 路径；None 则自动生成"""

    def to_app_globals(self) -> None:
        """同步本配置到 `src.config` 模块属性、`os.environ` 与 `pdf_extractor` 常量。

        Why（为什么需要这个看上去很危险的全局变量写入）:
            v1 设计是所有工具模块直接读 `src.config.XXX` 的进程级常量。彻底
            重构成依赖注入会需要改动 8 个 tools 模块的全部 LLM/OCR 调用点，
            v2 重构暂未做这件事。本方法作为**临时桥接**，把 RuntimeConfig
            的字段反推到所有"读全局"的位置。

        线程安全:
            内部用 `_CONFIG_LOCK` 串行化所有写入，避免并发 run_pipeline()
            互相覆盖配置。底层工具读这些值不需要持锁（最终一致即可）。

        清理计划:
            等所有 tools 都接受 RuntimeConfig 作为参数后即可删除此方法。
            预计 2026 Q3 完成。
        """
        import os

        import src.config as cfg
        from src.tools import pdf_extractor as pe

        with _CONFIG_LOCK:
            cfg.LLM_API_KEY = self.llm_api_key
            cfg.LLM_BASE_URL = self.llm_base_url.rstrip("/")
            cfg.LLM_MODEL = self.llm_model
            cfg.LLM_PARSE_CHUNK_CHARS = self.llm_parse_chunk_chars
            cfg.LLM_PARSE_MAX_TOKENS = self.llm_parse_max_tokens
            cfg.LLM_PARSE_TIMEOUT_SEC = self.llm_parse_timeout_sec
            cfg.LLM_TIMEOUT_NORMAL = self.llm_timeout_normal
            cfg.PADDLE_OCR_TOKEN = self.paddle_ocr_token
            cfg.PADDLE_OCR_MODEL = self.paddle_ocr_model
            cfg.PADDLE_OCR_USE_ASYNC = self.paddle_ocr_use_async
            cfg.PADDLE_OCR_USE_CACHE = self.paddle_ocr_use_cache
            cfg.PADDLE_OCR_ENABLE_LEGACY_FALLBACK = self.paddle_ocr_enable_legacy_fallback
            cfg.PADDLE_OCR_POLL_TIMEOUT_SEC = self.paddle_ocr_poll_timeout_sec

            # 环境变量同步（避免 OpenAI SDK 等 fork 出来读不到）
            os.environ["LLM_API_KEY"] = self.llm_api_key
            os.environ["LLM_BASE_URL"] = self.llm_base_url
            os.environ["LLM_MODEL"] = self.llm_model
            os.environ["PADDLE_OCR_TOKEN"] = self.paddle_ocr_token

            # pdf_extractor 模块使用 from-config 导入常量，须显式同步
            pe.PADDLE_OCR_TOKEN = self.paddle_ocr_token
            pe.PADDLE_OCR_MODEL = self.paddle_ocr_model
            pe.PADDLE_OCR_USE_ASYNC = self.paddle_ocr_use_async
            pe.PADDLE_OCR_USE_CACHE = self.paddle_ocr_use_cache
            pe.PADDLE_OCR_ENABLE_LEGACY_FALLBACK = self.paddle_ocr_enable_legacy_fallback
            pe.PADDLE_OCR_POLL_TIMEOUT_SEC = self.paddle_ocr_poll_timeout_sec


# ─── 结果 ───────────────────────────────────────────────────
@dataclass
class PipelineResult:
    """流水线运行结果 - 所有 UI 渲染所需数据的容器"""

    success: bool = False
    cancelled: bool = False
    error_message: Optional[str] = None

    # 中间产物
    raw_text: str = ""
    extraction_method: str = ""
    extraction_profile: str = ""
    extraction_diagnostics: dict = field(default_factory=dict)
    report: object = None  # MonitoringReport
    analysis_plan: list = field(default_factory=list)
    calc_issues: list = field(default_factory=list)
    stats_issues: list = field(default_factory=list)
    logic_issues: list = field(default_factory=list)
    ai_review: str = ""
    process_notes: list[str] = field(default_factory=list)

    # 最终输出
    final_md: str = ""
    output_path: Optional[str] = None

    # 性能
    duration_sec: float = 0.0
    step_timings: dict[str, float] = field(default_factory=dict)

    @property
    def all_issues(self) -> list:
        return self.calc_issues + self.stats_issues + self.logic_issues

    @property
    def errors(self) -> list:
        return [i for i in self.all_issues if i.severity == "error"]

    @property
    def warnings(self) -> list:
        return [i for i in self.all_issues if i.severity == "warning"]

    @property
    def infos(self) -> list:
        return [i for i in self.all_issues if i.severity == "info"]


# ─── 取消支持 ───────────────────────────────────────────────
class CancelledError(Exception):
    """流水线被外部取消时抛出的异常。

    触发条件：``_check_cancel(cancel_event)`` 检测到 ``cancel_event.is_set()``。

    在 ``run_pipeline`` 内被捕获并转换为 ``PipelineResult(cancelled=True)``，
    调用方应通过 ``result.cancelled`` 判断而非自己捕获本异常。
    """


def _noop_progress(step: str, label: str, pct: int, detail: str) -> None:
    pass


def _check_cancel(cancel_event: Optional[threading.Event]) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CancelledError("流水线被外部取消")


# ─── 主入口 ─────────────────────────────────────────────────
def run_pipeline(
    config: RuntimeConfig,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> PipelineResult:
    """运行完整 8 步流水线。

    参数:
        config: RuntimeConfig 配置对象
        progress_callback: 进度回调 (step_id, label, percent, detail)
        cancel_event: 可选的取消事件；置位时流水线在下一个检查点抛出 CancelledError

    返回:
        PipelineResult；即使失败也返回（success=False, error_message 不为空）
    """
    callback = progress_callback or _noop_progress
    result = PipelineResult()
    pipeline_start = time.time()

    try:
        # 先校验输入；失败时不污染全局 config（避免半改半读的状态）
        pdf_path = config.pdf_path
        if not Path(pdf_path).exists():
            result.error_message = f"PDF 文件不存在: {pdf_path}"
            callback("error", "失败", 0, result.error_message)
            return result

        # 输入校验通过后才同步全局配置（临时桥接，2026 Q3 后可移除）
        config.to_app_globals()

        pdf_name = Path(pdf_path).stem
        output_path = config.output_path or str(
            Path(config.output_dir) / f"{pdf_name}_检查报告.md"
        )

        # ── Step 1 ──────────────────────────────
        _check_cancel(cancel_event)
        step_start = time.time()
        callback("step1", "Step 1/8 · PDF 提取", 5, "读取文本层，必要时回退 OCR")

        from src.tools import pdf_extractor

        extraction_result = pdf_extractor.extract_pdf(
            pdf_path,
            use_ocr=config.use_ocr,
            prefer_ocr=config.prefer_ocr,
            auto_fallback=config.auto_fallback,
            ocr_output_dir=str(Path(config.output_dir) / f"{pdf_name}_ocr_debug"),
            return_details=True,
        )
        raw_text = extraction_result.text
        extraction_result.diagnostics.setdefault("method", extraction_result.method)
        extraction_result.diagnostics.setdefault("selected_profile", extraction_result.selected_profile)
        extraction_result.diagnostics.setdefault("debug_dir", extraction_result.debug_output_dir)

        result.raw_text = raw_text
        result.extraction_method = extraction_result.method
        result.extraction_profile = extraction_result.selected_profile
        result.extraction_diagnostics = extraction_result.diagnostics
        result.step_timings["step1"] = time.time() - step_start
        callback(
            "step1",
            "Step 1/8 · PDF 提取",
            12,
            f"完成 {len(raw_text):,} 字符 ({extraction_result.method}/{extraction_result.selected_profile})",
        )

        # ── Step 2 ──────────────────────────────
        _check_cancel(cancel_event)
        step_start = time.time()
        callback("step2", "Step 2/8 · LLM 结构化解析", 15, "发送文本到 LLM 提取结构化数据")

        from src.tools.extraction_quality import analyze_extraction_quality
        from src.tools.llm_parser import parse_report_with_llm

        report = parse_report_with_llm(raw_text)
        report.raw_text = raw_text
        report.extraction_diagnostics = extraction_result.diagnostics
        analyze_extraction_quality(report)
        result.report = report
        result.step_timings["step2"] = time.time() - step_start
        callback(
            "step2",
            "Step 2/8 · LLM 结构化解析",
            30,
            f"解析完成 - {len(report.tables)} 张数据表，{len(report.thresholds)} 项阈值",
        )

        # ── Step 2b: 配置增强 + 分析计划 (ReAct) ──
        _check_cancel(cancel_event)
        step_start = time.time()
        callback("step2.5", "Step 2.5/8 · 分析计划生成 (ReAct)", 35, "为每张表生成验证计划")

        from src.tools.table_analyzer import enrich_configs_with_llm, generate_analysis_plan

        try:
            enrich_configs_with_llm(report)
        except Exception as exc:
            logger.warning("配置增强失败，继续: %s", exc)
            result.process_notes.append(f"配置增强失败但已忽略: {exc}")

        analysis_plan = generate_analysis_plan(report)
        result.analysis_plan = analysis_plan
        result.step_timings["step2.5"] = time.time() - step_start
        callback("step2.5", "Step 2.5/8 · 分析计划生成", 40, f"已为 {len(analysis_plan)} 张表生成计划")

        # ── Step 3 ──────────────────────────────
        _check_cancel(cancel_event)
        step_start = time.time()
        callback("step3", "Step 3/8 · 计算验证", 45, "校验累计变化量、速率、深层位移")

        from src.tools.calculation_checker import run_calculation_checks

        calc_issues = run_calculation_checks(report)
        result.calc_issues = calc_issues
        result.step_timings["step3"] = time.time() - step_start
        callback("step3", "Step 3/8 · 计算验证", 55, f"完成 - 发现 {len(calc_issues)} 个问题")

        # ── Step 4 ──────────────────────────────
        _check_cancel(cancel_event)
        step_start = time.time()
        callback("step4", "Step 4/8 · 统计验证", 60, "校验最大/最小值统计")

        from src.tools.statistics_checker import run_statistics_checks

        stats_issues = run_statistics_checks(report)
        result.stats_issues = stats_issues
        result.step_timings["step4"] = time.time() - step_start
        callback("step4", "Step 4/8 · 统计验证", 65, f"完成 - 发现 {len(stats_issues)} 个问题")

        # ── Step 5 ──────────────────────────────
        _check_cancel(cancel_event)
        step_start = time.time()
        callback("step5", "Step 5/8 · 逻辑检查", 70, "安全状态匹配 + 汇总一致性")

        from src.tools.logic_checker import run_logic_checks

        logic_issues = run_logic_checks(report)
        result.logic_issues = logic_issues
        result.step_timings["step5"] = time.time() - step_start
        callback("step5", "Step 5/8 · 逻辑检查", 75, f"完成 - 发现 {len(logic_issues)} 个问题")

        # ── Step 6: 自验证 ──────────────────────
        _check_cancel(cancel_event)
        all_issues = calc_issues + stats_issues + logic_issues
        if not config.skip_self_verify:
            errors = [i for i in all_issues if i.severity == "error"]
            if errors:
                step_start = time.time()
                callback("step6", "Step 6/8 · AI 自验证", 78, f"对 {len(errors)} 个错误进行二次确认")

                from src.tools.self_verifier import verify_errors_with_llm

                try:
                    all_issues = verify_errors_with_llm(report, all_issues)
                    calc_issues = [i for i in all_issues if i in calc_issues]
                    stats_issues = [i for i in all_issues if i in stats_issues]
                    logic_issues = [i for i in all_issues if i in logic_issues]
                    result.calc_issues = calc_issues
                    result.stats_issues = stats_issues
                    result.logic_issues = logic_issues
                except Exception as exc:
                    logger.exception("Step 6 自验证失败")
                    result.process_notes.append(f"错误复核未完成，已跳过。原因: {exc}")
                result.step_timings["step6"] = time.time() - step_start
                callback("step6", "Step 6/8 · AI 自验证", 85, "完成")
            else:
                result.process_notes.append("错误复核未执行：没有 error 级问题。")
                callback("step6", "Step 6/8 · AI 自验证", 85, "跳过 - 无错误需复核")
        else:
            result.process_notes.append("错误复核未执行：用户关闭了该步骤。")
            callback("step6", "Step 6/8 · AI 自验证", 85, "跳过 - 用户关闭")

        # ── Step 7: AI 最终审核 ─────────────────
        _check_cancel(cancel_event)
        ai_review = ""
        if not config.skip_ai_review:
            step_start = time.time()
            callback("step7", "Step 7/8 · AI 最终审核", 88, "整体审查")

            from src.tools.llm_parser import verify_report_with_llm
            from src.tools.report_generator import generate_report_md

            preliminary_md = generate_report_md(
                report,
                calc_issues,
                stats_issues,
                logic_issues,
                analysis_plan=analysis_plan,
                process_notes=result.process_notes,
            )
            try:
                ai_review = verify_report_with_llm(preliminary_md, raw_text)
            except Exception as exc:
                logger.exception("Step 7 最终审核失败")
                result.process_notes.append(f"最终审核未完成，已跳过。原因: {exc}")
            result.ai_review = ai_review
            result.step_timings["step7"] = time.time() - step_start
            callback("step7", "Step 7/8 · AI 最终审核", 92, "完成" if ai_review else "已跳过")
        else:
            result.process_notes.append("最终审核未执行：用户关闭了该步骤。")
            callback("step7", "Step 7/8 · AI 最终审核", 92, "跳过 - 用户关闭")

        # ── Step 8: 生成报告 ────────────────────
        _check_cancel(cancel_event)
        step_start = time.time()
        callback("step8", "Step 8/8 · 报告生成", 95, "汇总并生成 Markdown 报告")

        from src.tools.report_generator import generate_report_md, save_report

        final_md = generate_report_md(
            report,
            calc_issues,
            stats_issues,
            logic_issues,
            ai_review,
            analysis_plan,
            process_notes=result.process_notes,
        )
        save_report(final_md, output_path)
        result.final_md = final_md
        result.output_path = output_path
        result.step_timings["step8"] = time.time() - step_start

        result.success = True
        result.duration_sec = time.time() - pipeline_start
        callback(
            "done",
            "完成",
            100,
            f"用时 {result.duration_sec:.1f}s - 错误 {len(result.errors)} / 警告 {len(result.warnings)} / 提示 {len(result.infos)}",
        )
        return result

    except CancelledError as exc:
        result.cancelled = True
        result.error_message = str(exc)
        result.duration_sec = time.time() - pipeline_start
        callback("cancelled", "已取消", 0, str(exc))
        return result
    except Exception as exc:
        logger.exception("流水线异常退出")
        result.error_message = f"{type(exc).__name__}: {exc}"
        result.duration_sec = time.time() - pipeline_start
        callback("error", "失败", 0, result.error_message)
        return result
