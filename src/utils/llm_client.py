"""
统一的 LLM 调用封装

提供带超时、重试、指数退避的 LLM API 调用接口，
避免在各模块中重复实现相同的错误处理逻辑。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def call_chat_completion(
    messages: list[dict],
    timeout: Optional[int] = None,
    max_tokens: int = 4000,
    max_retries: Optional[int] = None,
    temperature: float = 0.1,
) -> Optional[str]:
    """
    统一的 LLM 调用接口，带超时与重试。

    参数:
        messages: OpenAI 格式的消息列表
        timeout: 超时秒数（None 则使用 config 默认值）
        max_tokens: 最大生成 token 数
        max_retries: 重试次数（None 则使用 config 默认值）
        temperature: 采样温度

    返回:
        LLM 返回的文本内容（已去除 <thinking> 标签），失败返回 None
    """
    import src.config as cfg

    timeout_sec = timeout if timeout is not None else getattr(cfg, "LLM_TIMEOUT_NORMAL", 90)
    retries = max_retries if max_retries is not None else getattr(cfg, "LLM_MAX_RETRIES", 2)
    backoff_sec = getattr(cfg, "LLM_RETRY_BACKOFF_SEC", 10)

    client = OpenAI(api_key=cfg.LLM_API_KEY, base_url=cfg.LLM_BASE_URL)

    last_exc = None
    for attempt in range(1 + retries):
        try:
            resp = client.chat.completions.create(
                model=cfg.LLM_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout_sec,
            )
            raw = resp.choices[0].message.content or ""
            # 去除 <thinking> 标签
            raw = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', raw, flags=re.DOTALL).strip()
            return raw
        except Exception as e:
            last_exc = e
            if attempt < retries:
                backoff = backoff_sec * (2 ** attempt)
                logger.warning("LLM 调用失败，%ds 后重试 (attempt %d/%d): %s", backoff, attempt + 1, retries + 1, e)
                time.sleep(backoff)
            else:
                logger.error("LLM 调用失败，已达最大重试次数: %s", e)

    return None


def extract_json_from_response(raw: str, expected_type: str = "object") -> Optional[dict | list]:
    """
    从 LLM 响应中提取 JSON 对象或数组。

    参数:
        raw: LLM 返回的原始文本
        expected_type: "object" 或 "array"

    返回:
        解析后的 dict 或 list，失败返回 None
    """
    if expected_type == "array":
        m = re.search(r'\[.*\]', raw, re.DOTALL)
    else:
        m = re.search(r'\{.*\}', raw, re.DOTALL)

    if not m:
        logger.warning("未在响应中找到有效的 JSON %s", expected_type)
        return None

    try:
        return json.loads(m.group())
    except json.JSONDecodeError as e:
        logger.warning("JSON 解析失败: %s", e)
        return None
