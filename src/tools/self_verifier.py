"""
Self-verification pass.

For each error found by the deterministic checkers, sends the relevant
table text + extracted data + check result to LLM for confirmation.
LLM can confirm, downgrade (error -> warning), or dismiss the finding.

Implements the two-LLM verification pattern for higher accuracy.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import json
import logging
import re
import time
from typing import Callable, Optional

from src.models.data_models import CheckIssue, MonitoringReport
from src.tools.extraction_quality import infer_source_from_reason

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5  # 默认每批验证的错误数量
DEFAULT_CONTEXT_CHARS = 120


def _build_prompt(batch: list[CheckIssue], raw_text: str, context_chars: int) -> str:
    """Build self-verification prompt for a batch of issues."""
    error_descriptions = []
    for j, err in enumerate(batch):
        context = _find_table_text(raw_text, err.table_name)
        error_descriptions.append(
            f"错误{j + 1}(本批内索引={j}): 表={err.table_name}, 测点={err.point_id}, "
            f"字段={err.field_name}, 期望={err.expected_value}, "
            f"实际={err.actual_value}\n"
            f"描述: {err.message}\n"
            f"原文片段: {context[:context_chars]}"
        )

    return (
        "你是建筑变形监测报告审核专家。本批共 "
        + str(len(batch))
        + " 个错误，error_idx 请使用 0 到 "
        + str(len(batch) - 1)
        + "。请逐一确认：\n"
        "- 如果错误确实存在，回复 'confirm'\n"
        "- 如果是误报（例如数据提取错误、列错位、分页拆表、精度问题、单位换算导致），回复 'dismiss'\n"
        "- 如果不确定，回复 'downgrade'（降为警告）\n\n"
        "同时请给出 suspected_origin，取值只能是 report / extraction / logic。\n"
        "- report: 报告原文或表内数据确有错误\n"
        "- extraction: OCR、列映射、分页拆表、单位理解错误导致的误报\n"
        "- logic: 规则边界、统计口径、匹配逻辑导致的误报或不确定\n\n"
        "正负号代表方向不代表大小。高程数据单位m与mm之间存在精度损失。\n"
        "水位初始基准可能与建设初期不同。\n"
        "若属于同一监测项多页表的统计引用、OCR/提取错列、列映射错误，应倾向 dismiss 或 downgrade，并在 reason 中说明。\n\n"
        + "\n---\n".join(error_descriptions)
        + "\n\n返回JSON数组: "
        '[{"error_idx":0,"verdict":"confirm|dismiss|downgrade","reason":"简要原因","suspected_origin":"report|extraction|logic"}]'
    )


def _request_verdicts(
    client,
    cfg,
    prompt: str,
    *,
    timeout_sec: int,
    max_retries: int,
    backoff_sec: int,
    max_tokens: int,
    progress_callback: Optional[Callable[[dict], None]] = None,
    batch_index: int = 0,
    total_batches: int = 0,
) -> tuple[list[dict] | None, Exception | None]:
    """Request verdicts from LLM with retry."""
    last_exc = None
    for attempt in range(1 + max_retries):
        try:
            resp = client.chat.completions.create(
                model=cfg.LLM_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是建筑变形监测数据审核专家。"
                            "请返回纯JSON，并准确区分 report / extraction / logic 三类来源。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=max_tokens,
                timeout=timeout_sec,
            )
            raw = resp.choices[0].message.content or ""
            raw = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', raw, flags=re.DOTALL).strip()
            m = re.search(r'\[.*\]', raw, re.DOTALL)
            if m:
                return json.loads(m.group()), None
            last_exc = ValueError("LLM 未返回可解析 JSON 数组")
        except Exception as e:
            last_exc = e

        if attempt < max_retries:
            backoff = backoff_sec * (2 ** attempt)
            logger.warning("自验证本批请求失败，%ds 后重试: %s", backoff, last_exc)
            if progress_callback:
                progress_callback({
                    "stage": "batch_retry",
                    "batch_index": batch_index,
                    "total_batches": total_batches,
                    "attempt": attempt + 2,
                    "max_attempts": max_retries + 1,
                    "error": str(last_exc),
                })
            time.sleep(backoff)

    logger.warning("自验证本批LLM调用失败 (non-fatal): %s", last_exc)
    return None, last_exc


def _apply_verdicts(
    *,
    errors: list[CheckIssue],
    batch: list[CheckIssue],
    verdicts: list[dict],
    batch_start: int,
) -> tuple[int, int]:
    """Apply LLM verdicts to issues and return (dismissed_delta, downgraded_delta)."""
    dismissed = 0
    downgraded = 0
    for v in verdicts:
        idx = v.get("error_idx")
        verdict = v.get("verdict", "confirm")
        reason = v.get("reason", "")
        suspected_origin = v.get("suspected_origin", "")
        if idx is None or idx < 0 or idx >= len(batch):
            continue
        target = batch[idx]
        normalized_origin = suspected_origin if suspected_origin in {"report", "extraction", "logic"} else ""
        if not normalized_origin:
            normalized_origin = infer_source_from_reason(reason)
        if verdict == "dismiss":
            for orig_err in errors:
                if orig_err is target:
                    orig_err.severity = "info"
                    if normalized_origin:
                        orig_err.suspected_source = normalized_origin
                    orig_err.message += f" [AI自验证: 已排除 - {reason}]"
                    dismissed += 1
                    break
        elif verdict == "downgrade":
            for orig_err in errors:
                if orig_err is target:
                    orig_err.severity = "warning"
                    if normalized_origin:
                        orig_err.suspected_source = normalized_origin
                    orig_err.message += f" [AI自验证: 降级 - {reason}]"
                    downgraded += 1
                    break
        else:
            if normalized_origin:
                target.suspected_source = normalized_origin
    return dismissed, downgraded


def _verify_batch_task(
    raw_text: str,
    batch: list[CheckIssue],
    *,
    timeout_sec: int,
    max_retries: int,
    backoff_sec: int,
    context_chars: int,
) -> dict:
    """Verify one batch and fall back to single-item verification if needed."""
    from openai import OpenAI
    import src.config as cfg

    client = OpenAI(api_key=cfg.LLM_API_KEY, base_url=cfg.LLM_BASE_URL, max_retries=0)
    prompt = _build_prompt(batch, raw_text, context_chars)
    verdicts, last_exc = _request_verdicts(
        client,
        cfg,
        prompt,
        timeout_sec=timeout_sec,
        max_retries=max_retries,
        backoff_sec=backoff_sec,
        max_tokens=2600 if len(batch) > 1 else 1400,
        progress_callback=None,
    )
    if verdicts is not None:
        return {
            "split": False,
            "segments": [(batch, verdicts)],
            "error": None,
            "all_failed": False,
        }

    if len(batch) <= 1:
        return {
            "split": False,
            "segments": [],
            "error": last_exc,
            "all_failed": True,
        }

    logger.warning("自验证批次失败，拆分为单条重试")
    segments: list[tuple[list[CheckIssue], list[dict]]] = []
    single_client = OpenAI(api_key=cfg.LLM_API_KEY, base_url=cfg.LLM_BASE_URL, max_retries=0)
    final_exc = last_exc
    for single_issue in batch:
        single_verdicts, single_exc = _request_verdicts(
            single_client,
            cfg,
            _build_prompt([single_issue], raw_text, context_chars),
            timeout_sec=max(20, min(timeout_sec, 30)),
            max_retries=0,
            backoff_sec=1,
            max_tokens=1200,
            progress_callback=None,
        )
        if single_verdicts is None:
            final_exc = single_exc or final_exc
            continue
        segments.append(([single_issue], single_verdicts))

    return {
        "split": True,
        "segments": segments,
        "error": final_exc,
        "all_failed": not segments,
    }


def _find_table_text(raw_text: str, table_name: str) -> str:
    """Extract ~2000 chars of raw text around the table name mention."""
    clean_name = table_name.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
    patterns = [table_name, clean_name]
    for kw in patterns:
        idx = raw_text.find(kw)
        if idx >= 0:
            start = max(0, idx - 200)
            end = min(len(raw_text), idx + 2000)
            return raw_text[start:end]
    return ""


def verify_errors_with_llm(
    report: MonitoringReport,
    errors: list[CheckIssue],
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> list[CheckIssue]:
    """
    Self-verify error-level findings using LLM.
    Returns the updated list with some errors potentially downgraded to warnings.
    """
    if not errors:
        return errors

    to_verify = [e for e in errors if e.severity == "error"]
    if not to_verify:
        return errors

    import src.config as cfg

    timeout_sec = getattr(cfg, "SELF_VERIFY_TIMEOUT_SEC", getattr(cfg, "LLM_TIMEOUT_NORMAL", 90))
    max_retries = getattr(cfg, "SELF_VERIFY_MAX_RETRIES", 0)
    backoff_sec = getattr(cfg, "SELF_VERIFY_RETRY_BACKOFF_SEC", 2)
    batch_size = max(1, int(getattr(cfg, "SELF_VERIFY_BATCH_SIZE", DEFAULT_BATCH_SIZE)))
    single_shot_threshold = max(1, int(getattr(cfg, "SELF_VERIFY_SINGLE_SHOT_THRESHOLD", 6)))
    context_chars = max(60, int(getattr(cfg, "SELF_VERIFY_CONTEXT_CHARS", DEFAULT_CONTEXT_CHARS)))
    max_parallel = max(1, int(getattr(cfg, "SELF_VERIFY_MAX_PARALLEL", 2)))
    max_total_sec = max(10, int(getattr(cfg, "SELF_VERIFY_MAX_TOTAL_SEC", 90)))
    max_errors = max(1, int(getattr(cfg, "SELF_VERIFY_MAX_ERRORS", 24)))
    deadline = time.time() + max_total_sec

    if len(to_verify) > max_errors:
        skipped_count = len(to_verify) - max_errors
        to_verify = to_verify[:max_errors]
        logger.warning("自验证错误数超过上限，仅处理前 %d 条，跳过 %d 条", max_errors, skipped_count)
        if progress_callback:
            progress_callback({
                "stage": "truncated",
                "processed_errors": len(to_verify),
                "skipped_errors": skipped_count,
            })

    if len(to_verify) <= single_shot_threshold:
        batch_size = len(to_verify)

    dismissed = 0
    downgraded = 0
    total_batches = (len(to_verify) + batch_size - 1) // batch_size
    batches: list[tuple[int, int, list[CheckIssue]]] = []
    for batch_start in range(0, len(to_verify), batch_size):
        batch = to_verify[batch_start : batch_start + batch_size]
        batch_index = batch_start // batch_size + 1
        batches.append((batch_start, batch_index, batch))

    if progress_callback:
        progress_callback({
            "stage": "start",
            "total_errors": len(to_verify),
            "total_batches": total_batches,
            "batch_size": batch_size,
        })
    parallelism = min(max_parallel, total_batches)

    if parallelism <= 1:
        for batch_start, batch_index, batch in batches:
            if time.time() >= deadline:
                logger.warning("自验证达到总耗时上限(%ds)，提前结束", max_total_sec)
                if progress_callback:
                    progress_callback({
                        "stage": "deadline_reached",
                        "max_total_sec": max_total_sec,
                    })
                break
            if progress_callback:
                progress_callback({
                    "stage": "batch_start",
                    "batch_index": batch_index,
                    "total_batches": total_batches,
                    "batch_size": len(batch),
                    "total_errors": len(to_verify),
                })
            result = _verify_batch_task(
                report.raw_text,
                batch,
                timeout_sec=timeout_sec,
                max_retries=max_retries,
                backoff_sec=backoff_sec,
                context_chars=context_chars,
            )
            if result["split"] and progress_callback:
                progress_callback({
                    "stage": "batch_split",
                    "batch_index": batch_index,
                    "total_batches": total_batches,
                    "batch_size": len(batch),
                    "error": str(result["error"]) if result["error"] else "unknown",
                })
            if result["all_failed"]:
                if progress_callback:
                    progress_callback({
                        "stage": "batch_failed",
                        "batch_index": batch_index,
                        "total_batches": total_batches,
                        "error": str(result["error"]) if result["error"] else "unknown",
                    })
                continue
            for segment_batch, segment_verdicts in result["segments"]:
                dismissed_delta, downgraded_delta = _apply_verdicts(
                    errors=errors,
                    batch=segment_batch,
                    verdicts=segment_verdicts,
                    batch_start=batch_start,
                )
                dismissed += dismissed_delta
                downgraded += downgraded_delta
            if progress_callback:
                progress_callback({
                    "stage": "batch_finish",
                    "batch_index": batch_index,
                    "total_batches": total_batches,
                    "dismissed": dismissed,
                    "downgraded": downgraded,
                })
    else:
        if progress_callback:
            progress_callback({
                "stage": "parallel_start",
                "parallelism": parallelism,
                "total_batches": total_batches,
            })
        future_map = {}
        with ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix="self_verify") as executor:
            for batch_start, batch_index, batch in batches:
                if time.time() >= deadline:
                    logger.warning("自验证达到总耗时上限(%ds)，停止提交新批次", max_total_sec)
                    break
                if progress_callback:
                    progress_callback({
                        "stage": "batch_start",
                        "batch_index": batch_index,
                        "total_batches": total_batches,
                        "batch_size": len(batch),
                        "total_errors": len(to_verify),
                    })
                future = executor.submit(
                    _verify_batch_task,
                    report.raw_text,
                    batch,
                    timeout_sec=timeout_sec,
                    max_retries=max_retries,
                    backoff_sec=backoff_sec,
                    context_chars=context_chars,
                )
                future_map[future] = (batch_start, batch_index, batch)

            while future_map:
                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning("自验证达到总耗时上限(%ds)，提前结束并取消未完成批次", max_total_sec)
                    for pending in future_map:
                        pending.cancel()
                    if progress_callback:
                        progress_callback({
                            "stage": "deadline_reached",
                            "max_total_sec": max_total_sec,
                        })
                    break
                done_futures, _ = wait(set(future_map.keys()), timeout=max(1.0, remaining), return_when=FIRST_COMPLETED)
                if not done_futures:
                    logger.warning("自验证等待批次结果超时，提前结束并取消未完成批次")
                    for pending in future_map:
                        pending.cancel()
                    if progress_callback:
                        progress_callback({
                            "stage": "deadline_reached",
                            "max_total_sec": max_total_sec,
                        })
                    break

                for future in done_futures:
                    batch_start, batch_index, batch = future_map.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        logger.warning("自验证第 %d/%d 批并发执行异常: %s", batch_index, total_batches, exc)
                        if progress_callback:
                            progress_callback({
                                "stage": "batch_failed",
                                "batch_index": batch_index,
                                "total_batches": total_batches,
                                "error": str(exc),
                            })
                        continue
                    if result["split"] and progress_callback:
                        progress_callback({
                            "stage": "batch_split",
                            "batch_index": batch_index,
                            "total_batches": total_batches,
                            "batch_size": len(batch),
                            "error": str(result["error"]) if result["error"] else "unknown",
                        })
                    if result["all_failed"]:
                        if progress_callback:
                            progress_callback({
                                "stage": "batch_failed",
                                "batch_index": batch_index,
                                "total_batches": total_batches,
                                "error": str(result["error"]) if result["error"] else "unknown",
                            })
                        continue
                    for segment_batch, segment_verdicts in result["segments"]:
                        dismissed_delta, downgraded_delta = _apply_verdicts(
                            errors=errors,
                            batch=segment_batch,
                            verdicts=segment_verdicts,
                            batch_start=batch_start,
                        )
                        dismissed += dismissed_delta
                        downgraded += downgraded_delta
                    if progress_callback:
                        progress_callback({
                            "stage": "batch_finish",
                            "batch_index": batch_index,
                            "total_batches": total_batches,
                            "dismissed": dismissed,
                            "downgraded": downgraded,
                        })

    logger.info(
        "自验证完成: %d个确认, %d个降级, %d个排除",
        len(to_verify) - dismissed - downgraded, downgraded, dismissed,
    )
    if progress_callback:
        progress_callback({
            "stage": "done",
            "total_errors": len(to_verify),
            "dismissed": dismissed,
            "downgraded": downgraded,
        })
    return errors
