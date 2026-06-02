from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import config
from src.core.pipeline import RuntimeConfig


def test_global_defaults_use_deepseek_v4_flash_and_paddle_vl16():
    assert config.LLM_BASE_URL == "https://api.deepseek.com"
    assert config.LLM_MODEL == "deepseek-v4-flash"
    assert "deepseek-v4-flash" in config.AVAILABLE_MODELS
    assert "deepseek-v4-pro" in config.AVAILABLE_MODELS
    assert config.PADDLE_OCR_MODEL == "PaddleOCR-VL-1.6"


def test_runtime_config_defaults_match_desktop_delivery_defaults():
    cfg = RuntimeConfig(pdf_path="sample.pdf")

    assert cfg.llm_base_url == "https://api.deepseek.com"
    assert cfg.llm_model == "deepseek-v4-flash"
    assert cfg.paddle_ocr_model == "PaddleOCR-VL-1.6"
