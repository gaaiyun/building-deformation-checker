"""LLM 响应磁盘缓存。

开发迭代场景下重复跑同一 PDF（如恒大 99 页 ≈ 15 min/run）时，
LLM 调用是瓶颈。对于温度低 (≤0.3) 的确定性 prompt，
响应可以按 (model, messages, params) 哈希缓存到磁盘。

设计：
- 缓存键 = SHA256(json(canonicalized_inputs))，长度 64
- 缓存文件: <cache_dir>/<key>.json，存 {"text": str, "ts": str, "params": {...}}
- 命中 → 直接返回 text，跳过 API
- 未命中 → 调 API 后写入缓存
- temperature > 0.3 或 LLM_USE_CACHE=0 → 跳过缓存（响应非确定）

环境变量：
- LLM_CACHE_DIR：缓存目录（默认 output/llm_cache）
- LLM_USE_CACHE：0/1 开关（默认 1）
- LLM_CACHE_TEMP_MAX：启用缓存的最大温度（默认 0.3）
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_CACHE_TEMP_MAX = 0.3

_MIN_CACHEABLE_LENGTH = 20
"""短于此阈值的响应不缓存（可能是错误信息或 thinking 残留）"""


def is_cache_enabled() -> bool:
    """根据环境变量决定是否启用缓存"""
    return os.environ.get("LLM_USE_CACHE", "1").lower() not in {"0", "false", "no", "off"}


def get_cache_dir() -> Path:
    """缓存目录（环境变量优先，否则项目 output/llm_cache）"""
    env_dir = os.environ.get("LLM_CACHE_DIR")
    if env_dir:
        return Path(env_dir)
    # 项目根的 output/llm_cache（与现有惯例一致）
    return Path("output") / "llm_cache"


def cache_temp_threshold() -> float:
    try:
        return float(os.environ.get("LLM_CACHE_TEMP_MAX", str(DEFAULT_CACHE_TEMP_MAX)))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TEMP_MAX


def should_use_cache_for(temperature: float) -> bool:
    """该温度是否应走缓存（高温响应非确定，跳过）"""
    if not is_cache_enabled():
        return False
    return temperature <= cache_temp_threshold()


def build_cache_key(
    messages: list[dict],
    *,
    model: str,
    temperature: float,
    max_tokens: int,
    base_url: str = "",
) -> str:
    """生成 SHA256 hex 缓存键。

    inputs 规范化为有序 JSON 以确保跨平台一致：
    - messages 用 sort_keys，避免顺序差异
    - 数字精度统一
    - base_url 纳入 key：不同 endpoint 同模型名（DashScope vs MiniMax 都叫 qwen3.5-plus）
      不可命中（防误命中错误响应）。base_url 默认空字符串保持后向兼容。
    """
    payload = {
        "model": model,
        "temperature": round(float(temperature), 6),
        "max_tokens": int(max_tokens),
        "base_url": base_url or "",
        "messages": messages,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_cached_response(cache_dir: Path, key: str) -> Optional[str]:
    """从磁盘读取缓存的 LLM 文本响应；未命中或损坏返回 None"""
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str):
        return None
    return text


def save_cached_response(
    cache_dir: Path,
    key: str,
    text: str,
    params: Optional[dict] = None,
) -> None:
    """保存 LLM 响应到磁盘。

    缓存写入失败静默忽略（缓存仅为优化，非主流程）。

    拒绝缓存的场景：
    - 空 / 纯空白响应
    - 短于 _MIN_CACHEABLE_LENGTH 字符的响应（可能是错误信息或 thinking 残留）

    原子写入：先写 .tmp.{pid} 再 os.replace，避免并发读到半写文件。
    """
    # 过滤：拒绝缓存空响应；合法短 JSON 仍值得缓存，避免重复调用 LLM。
    if not text or not text.strip():
        return
    if len(text) < _MIN_CACHEABLE_LENGTH:
        try:
            json.loads(text.strip())
        except (ValueError, TypeError):
            return

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "text": text,
            "ts": datetime.now(timezone.utc).isoformat(),
            "params": params or {},
        }
        final_path = cache_dir / f"{key}.json"
        # 原子写入：先写临时文件，再 os.replace 到目标位置
        tmp_path = cache_dir / f"{key}.json.tmp.{os.getpid()}.{id(text):x}"
        tmp_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, final_path)
    except (OSError, TypeError, ValueError):
        # 缓存写入失败不影响主流程；清理可能残留的 tmp
        try:
            if 'tmp_path' in locals() and tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
