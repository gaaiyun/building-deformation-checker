"""桌面 GUI 的配置持久化（API key 等）

存放在 %APPDATA%/BuildingDeformationChecker/settings.json
让用户输入一次 key 后下次自动加载，避免每次启动都重新填写。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "BuildingDeformationChecker"


def _config_file() -> Path:
    return _config_dir() / "settings.json"


_DEFAULTS: dict[str, Any] = {
    "llm_api_key": "",
    "llm_base_url": "https://api.minimaxi.com/v1",
    "llm_model": "MiniMax-M2.7-highspeed",
    "paddle_ocr_token": "",
    "paddle_ocr_model": "PaddleOCR-VL-1.5",
    "paddle_ocr_use_async": True,
    "paddle_ocr_use_cache": True,
    "paddle_ocr_enable_legacy_fallback": True,
    "paddle_ocr_poll_timeout_sec": 900,
    "llm_parse_chunk_chars": 18000,
    "llm_parse_max_tokens": 24000,
    "llm_parse_timeout_sec": 300,
    "llm_timeout_normal": 120,
    "use_ocr": False,
    "prefer_ocr": False,
    "skip_self_verify": False,
    "skip_ai_review": False,
    "output_dir": "output",
    "last_pdf_dir": "",
}


def load_settings() -> dict[str, Any]:
    """加载用户配置；首次启动用默认值并兼容旧版本（缺失字段补默认）"""
    path = _config_file()
    out = dict(_DEFAULTS)
    if not path.exists():
        # 兼容环境变量回退
        if env := os.environ.get("LLM_API_KEY"):
            out["llm_api_key"] = env
        if env := os.environ.get("LLM_BASE_URL"):
            out["llm_base_url"] = env
        if env := os.environ.get("LLM_MODEL"):
            out["llm_model"] = env
        if env := os.environ.get("PADDLE_OCR_TOKEN"):
            out["paddle_ocr_token"] = env
        return out
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            out[k] = v
    except Exception:
        # 损坏文件不应阻塞启动
        pass
    return out


def save_settings(settings: dict[str, Any]) -> None:
    """保存配置到磁盘（API key 用明文，依赖 OS 的用户权限隔离）"""
    path = _config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


__all__ = ["load_settings", "save_settings"]
