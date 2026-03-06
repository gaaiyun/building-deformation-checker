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
from typing import Optional

from src.models.data_models import CheckIssue, MonitoringReport

logger = logging.getLogger(__name__)

MAX_ERRORS_TO_VERIFY = 20


def _find_table_text(raw_text: str, table_name: str) -> str:
    """Extract ~2000 chars of raw text around the table name mention."""
    clean_name = table_name.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
    patterns = [table_name, clean_name]
    for kw in patterns[:1]:
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

    if len(to_verify) > MAX_ERRORS_TO_VERIFY:
        to_verify = to_verify[:MAX_ERRORS_TO_VERIFY]
        logger.info("限制自验证数量为 %d 个", MAX_ERRORS_TO_VERIFY)

    error_descriptions = []
    for i, err in enumerate(to_verify):
        context = _find_table_text(report.raw_text, err.table_name)
        error_descriptions.append(
            f"错误{i + 1}: 表={err.table_name}, 测点={err.point_id}, "
            f"字段={err.field_name}, 期望={err.expected_value}, "
            f"实际={err.actual_value}\n"
            f"描述: {err.message}\n"
            f"原文片段: {context[:500]}"
        )

    prompt = (
        "你是建筑变形监测报告审核专家。以下是自动检查发现的错误，请逐一确认：\n"
        "- 如果错误确实存在，回复 'confirm'\n"
        "- 如果是误报（例如数据提取错误、精度问题、单位换算导致），回复 'dismiss'\n"
        "- 如果不确定，回复 'downgrade'（降为警告）\n\n"
        "正负号代表方向不代表大小。高程数据单位m与mm之间存在精度损失。\n"
        "水位初始基准可能与建设初期不同。\n\n"
        + "\n---\n".join(error_descriptions)
        + "\n\n返回JSON数组: "
        '[{"error_idx":0,"verdict":"confirm|dismiss|downgrade","reason":"简要原因"}]'
    )

    from openai import OpenAI
    from src.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

    try:
        client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "你是建筑变形监测数据审核专家。返回纯JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=4000,
            timeout=120,
        )
        raw = resp.choices[0].message.content or ""
        raw = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', raw, flags=re.DOTALL).strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if not m:
            logger.warning("自验证LLM未返回有效JSON数组")
            return errors

        verdicts = json.loads(m.group())
        dismissed = 0
        downgraded = 0

        error_idx_map = {id(e): i for i, e in enumerate(to_verify)}

        for v in verdicts:
            idx = v.get("error_idx")
            verdict = v.get("verdict", "confirm")
            reason = v.get("reason", "")

            if idx is None or idx < 0 or idx >= len(to_verify):
                continue

            target = to_verify[idx]

            if verdict == "dismiss":
                for orig_err in errors:
                    if orig_err is target:
                        orig_err.severity = "info"
                        orig_err.message += f" [AI自验证: 已排除 - {reason}]"
                        dismissed += 1
                        break
            elif verdict == "downgrade":
                for orig_err in errors:
                    if orig_err is target:
                        orig_err.severity = "warning"
                        orig_err.message += f" [AI自验证: 降级 - {reason}]"
                        downgraded += 1
                        break

        logger.info(
            "自验证完成: %d个确认, %d个降级, %d个排除",
            len(to_verify) - dismissed - downgraded, downgraded, dismissed,
        )
        return errors

    except Exception as e:
        logger.warning("自验证LLM调用失败 (non-fatal): %s", e)
        return errors
