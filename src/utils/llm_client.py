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
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Test hook: unit tests patch ``src.utils.llm_client.OpenAI`` directly.
# Production keeps it as None and imports the real class lazily in
# ``create_openai_client``.
OpenAI = None


def normalize_openai_base_url(base_url: str) -> str:
    """Normalize and validate an OpenAI-compatible base URL.

    Users often paste bare hosts or values with trailing slashes. We normalize
    those harmless cases here, but keep truly malformed values visible with a
    clear error message.
    """
    value = (base_url or "").strip()
    if not value:
        value = "https://api.deepseek.com"
    if not re.match(r"^https?://", value, flags=re.IGNORECASE):
        value = f"https://{value}"
    value = value.rstrip("/")

    try:
        httpx.URL(value)
    except Exception as exc:
        raise ValueError(f"LLM Base URL 无效：{value!r}，请检查是否多写冒号、端口或空格。") from exc
    return value


def create_openai_client(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    max_retries: int = 0,
) -> Any:
    """Create an OpenAI client that is insulated from broken proxy env vars.

    httpx parses HTTP(S)_PROXY/NO_PROXY during client initialization. Some
    Windows environments contain bare IPv6 entries like ``::1`` in no_proxy,
    which newer httpx versions can parse as an invalid ``:1`` port. Passing a
    client with ``trust_env=False`` prevents local proxy settings from breaking
    otherwise valid DeepSeek/MiniMax/OpenAI-compatible endpoints.
    """
    import src.config as cfg
    global OpenAI
    client_cls = OpenAI
    if client_cls is None:
        from openai import OpenAI as real_openai
        client_cls = real_openai

    return client_cls(
        api_key=api_key if api_key is not None else cfg.LLM_API_KEY,
        base_url=normalize_openai_base_url(base_url if base_url is not None else cfg.LLM_BASE_URL),
        max_retries=max_retries,
        http_client=httpx.Client(trust_env=False),
    )


def call_chat_completion(
    messages: list[dict],
    timeout: Optional[int] = None,
    max_tokens: int = 4000,
    max_retries: Optional[int] = None,
    temperature: float = 0.1,
    stream: bool = False,
) -> Optional[str]:
    """
    统一的 LLM 调用接口，带超时与重试。

    参数:
        messages: OpenAI 格式的消息列表
        timeout: 超时秒数（None 则使用 config 默认值）
        max_tokens: 最大生成 token 数
        max_retries: 重试次数（None 则使用 config 默认值）
        temperature: 采样温度
        stream: 是否流式接收响应；长结构化请求可避免连接长时间空闲被中间网络设备关闭

    返回:
        LLM 返回的文本内容（已去除 <thinking> 标签），失败返回 None

    缓存：
        当 temperature ≤ 0.3 且 LLM_USE_CACHE 启用时，按
        (model, messages, params) 哈希查磁盘缓存，命中直接返回。
        见 src.utils.llm_cache。
    """
    import src.config as cfg
    from src.utils import llm_cache

    timeout_sec = timeout if timeout is not None else getattr(cfg, "LLM_TIMEOUT_NORMAL", 90)
    retries = max_retries if max_retries is not None else getattr(cfg, "LLM_MAX_RETRIES", 2)
    backoff_sec = getattr(cfg, "LLM_RETRY_BACKOFF_SEC", 10)

    # 缓存命中检查（先于 API 调用）
    cache_enabled = llm_cache.should_use_cache_for(temperature)
    cache_dir = llm_cache.get_cache_dir() if cache_enabled else None
    cache_key = None
    if cache_enabled:
        cache_key = llm_cache.build_cache_key(
            messages, model=cfg.LLM_MODEL,
            temperature=temperature, max_tokens=max_tokens,
            base_url=cfg.LLM_BASE_URL,  # 包含 endpoint，防同名不同端点误命中
        )
        cached = llm_cache.load_cached_response(cache_dir, cache_key)
        if cached is not None:
            logger.info("LLM 缓存命中 [%s]", cache_key[:8])
            return cached

    # 统一关闭 SDK 隐式重试，避免与本模块显式重试叠加导致长时间阻塞。
    client = create_openai_client(max_retries=0)

    last_exc = None
    for attempt in range(1 + retries):
        try:
            resp = client.chat.completions.create(
                model=cfg.LLM_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout_sec,
                stream=stream,
            )
            if stream:
                content_parts: list[str] = []
                for event in resp:
                    if not getattr(event, "choices", None):
                        continue
                    delta = event.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        content_parts.append(content)
                raw = "".join(content_parts)
            else:
                raw = resp.choices[0].message.content or ""
            # 去除 <thinking> 标签
            raw = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', raw, flags=re.DOTALL).strip()
            # 写缓存（异常时不破坏主流程）
            if cache_enabled and cache_key:
                llm_cache.save_cached_response(
                    cache_dir, cache_key, raw,
                    params={"model": cfg.LLM_MODEL, "temperature": temperature, "max_tokens": max_tokens},
                )
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
