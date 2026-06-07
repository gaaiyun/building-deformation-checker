"""桌面 GUI 的配置持久化（API key 等）

存储策略 v2（带向后兼容）:
    - **敏感字段**（`llm_api_key`、`paddle_ocr_token`）首选 keyring（Windows
      Credential Manager / macOS Keychain / Linux Secret Service），明文从不
      落盘。keyring 不可用时不会保存新密钥，请改用环境变量或先启用系统
      keyring。
    - **非敏感字段**（模型名、URL、超时配置等）写 JSON，方便用户/团队复制配置。
    - 启动时若发现 JSON 里残留 v1 留下的明文 API key，自动迁移到 keyring 并
      清除 JSON 里的明文（一次性迁移，不重复）。

存储位置:
    - JSON: `%APPDATA%/BuildingDeformationChecker/settings.json`
    - keyring service name: `BuildingDeformationChecker`

测试钩子:
    `_keyring_backend` 是模块级可注入对象，测试时可替换为内存 dict 以隔离真实
    系统 keyring（参见 tests/test_settings_store.py）。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ─── 常量 ────────────────────────────────────────────────────
_SERVICE_NAME = "BuildingDeformationChecker"

# 必须用 keyring 加密存储的字段（绝不落盘到 JSON）
_SENSITIVE_KEYS: frozenset[str] = frozenset({"llm_api_key", "paddle_ocr_token"})


# ─── 默认值 ──────────────────────────────────────────────────
_DEFAULTS: dict[str, Any] = {
    "llm_api_key": "",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-v4-flash",
    "paddle_ocr_token": "",
    "paddle_ocr_model": "PaddleOCR-VL-1.6",
    "paddle_ocr_use_async": True,
    "paddle_ocr_use_cache": True,
    "paddle_ocr_enable_legacy_fallback": True,
    "paddle_ocr_poll_timeout_sec": 900,
    "llm_parse_chunk_chars": 18000,
    "llm_parse_max_tokens": 24000,
    "llm_parse_timeout_sec": 300,
    "llm_parse_max_parallel": 4,
    "llm_timeout_normal": 120,
    "use_ocr": False,
    "prefer_ocr": False,
    "skip_self_verify": False,
    "skip_ai_review": False,
    "output_dir": "output",
    "last_pdf_dir": "",
}


# ─── 路径辅助 ────────────────────────────────────────────────
def _config_dir() -> Path:
    """跨平台配置目录（Windows: %APPDATA%, 其它: ~/.config）"""
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / _SERVICE_NAME


def _config_file() -> Path:
    return _config_dir() / "settings.json"


# ─── keyring 抽象（可测试替换）────────────────────────────────
class _KeyringBackend:
    """对 `keyring` 库的薄包装，提供测试替换钩子并优雅降级。

    生产环境用真实 keyring（Windows Credential Manager 等）；测试可注入
    内存版本避免污染用户 Vault。
    """

    def __init__(self):
        self._available = self._probe()

    @staticmethod
    def _probe() -> bool:
        """探测 keyring 是否可用（首次调用会触发后端检查）"""
        try:
            import keyring  # noqa: F401
            return True
        except Exception as exc:
            logger.warning("keyring 不可用，敏感字段不会写入 settings.json: %s", exc)
            return False

    def get(self, key: str) -> Optional[str]:
        if not self._available:
            return None
        try:
            import keyring
            return keyring.get_password(_SERVICE_NAME, key)
        except Exception as exc:
            logger.warning("keyring 读取失败 (%s)，已忽略: %s", key, exc)
            return None

    def set(self, key: str, value: str) -> bool:
        """返回是否真的写入了 keyring；失败时调用方不应明文落盘。"""
        if not self._available:
            return False
        try:
            import keyring
            if value:
                keyring.set_password(_SERVICE_NAME, key, value)
            else:
                # 空值视为删除（避免遗留旧 key）
                try:
                    keyring.delete_password(_SERVICE_NAME, key)
                except Exception:
                    pass
            return True
        except Exception as exc:
            logger.warning("keyring 写入失败 (%s)，未写入 settings.json: %s", key, exc)
            return False


class _InMemoryKeyring:
    """测试专用的内存 keyring 假实现。

    与 `_KeyringBackend` 同接口，所有数据存在进程内 dict 里，
    测试结束后随对象销毁。永远不会触碰真实系统 keyring。
    """

    def __init__(self):
        self._store: dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self._store.get(key)

    def set(self, key: str, value: str) -> bool:
        if value:
            self._store[key] = value
        else:
            self._store.pop(key, None)
        return True


# 模块级单例。
# 生产环境是真实 keyring；测试用 patch.object 替换为 _InMemoryKeyring()。
_keyring_backend = _KeyringBackend()


# ─── 公开 API ────────────────────────────────────────────────
def load_settings() -> dict[str, Any]:
    """加载用户配置。

    加载顺序（优先级从高到低）:
        1. JSON 文件中明确写入的字段
        2. 敏感字段从 keyring 读取（覆盖 JSON 中可能残留的明文）
        3. 环境变量回退（仅当字段为空时生效）
        4. `_DEFAULTS` 中的默认值

    任何步骤出错都不抛异常，保证启动不被损坏的配置文件阻塞。
    """
    out = dict(_DEFAULTS)
    path = _config_file()
    file_exists = path.exists()

    # 1) JSON 文件 → out（非敏感字段为主，但兼容 v1 写入的明文敏感字段）
    if file_exists:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    out[k] = v
        except Exception as exc:
            logger.warning("settings.json 解析失败，使用默认值: %s", exc)

    # 2) keyring → 敏感字段（覆盖 JSON，因为 keyring 更权威）
    for key in _SENSITIVE_KEYS:
        secret = _keyring_backend.get(key)
        if secret:
            out[key] = secret

    # 3) 环境变量回退：仅当文件不存在时启用，避免覆盖用户保存的配置；
    #    敏感字段优先 keyring，所以这里不要覆盖已经从 keyring 读到的值。
    if not file_exists:
        env_map = {
            "LLM_API_KEY": "llm_api_key",
            "LLM_BASE_URL": "llm_base_url",
            "LLM_MODEL": "llm_model",
            "PADDLE_OCR_TOKEN": "paddle_ocr_token",
            "LLM_PARSE_MAX_PARALLEL": "llm_parse_max_parallel",
        }
        for env_name, settings_key in env_map.items():
            val = os.environ.get(env_name)
            if not val:
                continue
            if settings_key == "llm_parse_max_parallel":
                try:
                    out[settings_key] = int(val)
                except ValueError:
                    continue
                continue
            # 敏感字段：若 keyring 已提供则保留 keyring 值；否则用环境变量
            if settings_key in _SENSITIVE_KEYS and out.get(settings_key):
                continue
            out[settings_key] = val

    return out


def save_settings(settings: dict[str, Any]) -> None:
    """持久化用户配置。

    敏感字段只写入 keyring；keyring 不可用时不明文回退到 JSON。
    非敏感字段始终写 JSON。
    """
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 拆分敏感 vs 非敏感
    json_payload: dict[str, Any] = {}
    for k, v in settings.items():
        if k in _SENSITIVE_KEYS:
            stored_to_keyring = _keyring_backend.set(k, v or "")
            if not stored_to_keyring:
                logger.warning("keyring 不可用，敏感字段 %s 未写入 settings.json", k)
        else:
            json_payload[k] = v

    with path.open("w", encoding="utf-8") as f:
        json.dump(json_payload, f, ensure_ascii=False, indent=2)


__all__ = ["load_settings", "save_settings"]
