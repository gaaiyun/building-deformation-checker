"""
Self-verification pass.

For each error found by the deterministic checkers, sends the relevant
table text + extracted data + check result to LLM for confirmation.
LLM can confirm, downgrade (error -> warning), or dismiss the finding.

Implements the two-LLM verification pattern for higher accuracy.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from src.models.data_models import CheckIssue, MonitoringReport
from src.tools.extraction_quality import infer_source_from_reason

logger = logging.getLogger(__name__)

BATCH_SIZE = 5  # 每批验证的错误数量


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

    from openai import OpenAI
    import src.config as cfg

    timeout_sec = getattr(cfg, "LLM_TIMEOUT_LARGE", 180)
    max_retries = getattr(cfg, "LLM_MAX_RETRIES", 2)
    backoff_sec = getattr(cfg, "LLM_RETRY_BACKOFF_SEC", 10)

    client = OpenAI(api_key=cfg.LLM_API_KEY, base_url=cfg.LLM_BASE_URL)
    dismissed = 0
    downgraded = 0

    for batch_start in range(0, len(to_verify), BATCH_SIZE):
        batch = to_verify[batch_start : batch_start + BATCH_SIZE]
        error_descriptions = []
        for j, err in enumerate(batch):
            context = _find_table_text(report.raw_text, err.table_name)
            error_descriptions.append(
                f"错误{j + 1}(本批内索引={j}): 表={err.table_name}, 测点={err.point_id}, "
                f"字段={err.field_name}, 期望={err.expected_value}, "
                f"实际={err.actual_value}\n"
                f"描述: {err.message}\n"
                f"原文片段: {context[:300]}"
            )

        prompt = (
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

        verdicts = None
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
                    max_tokens=4000,
                    timeout=timeout_sec,
                )
                raw = resp.choices[0].message.content or ""
                raw = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', raw, flags=re.DOTALL).strip()
                m = re.search(r'\[.*\]', raw, re.DOTALL)
                if m:
                    verdicts = json.loads(m.group())
                    break
            except Exception as e:
                last_exc = e
                if attempt < max_retries:
                    backoff = backoff_sec * (2 ** attempt)
                    logger.warning("自验证本批请求失败，%ds 后重试: %s", backoff, e)
                    time.sleep(backoff)
                else:
                    logger.warning("自验证本批LLM调用失败 (non-fatal): %s", e)
                    break

        if verdicts is None:
            continue

        for v in verdicts:
            idx = v.get("error_idx")
            verdict = v.get("verdict", "confirm")
            reason = v.get("reason", "")
            suspected_origin = v.get("suspected_origin", "")
            if idx is None or idx < 0 or idx >= len(batch):
                continue
            global_idx = batch_start + idx
            target = to_verify[global_idx]
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

    logger.info(
        "自验证完成: %d个确认, %d个降级, %d个排除",
        len(to_verify) - dismissed - downgraded, downgraded, dismissed,
    )
    return errors
