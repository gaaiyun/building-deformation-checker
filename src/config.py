"""全局配置"""

import os

# ── LLM 配置 ──────────────────────────────────────────────
LLM_API_KEY = os.getenv(
    "LLM_API_KEY",
    "sk-sp-0b28da8e3f404df182c05d3fd45787a5",
)
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "https://coding.dashscope.aliyuncs.com/v1",
)
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5-plus")

# ── PaddleOCR 版式分析（扫描件备选方案）────────────────────
PADDLE_OCR_URL = "https://ucyduai2gcx8e403.aistudio-app.com/layout-parsing"
PADDLE_OCR_TOKEN = "1002254afa7100a68da7ebfae37bf3504bf2cd7f"

# ── 数值精度 ──────────────────────────────────────────────
FLOAT_TOLERANCE = 0.15  # mm，允许的浮点误差
RATE_TOLERANCE = 0.05   # mm/d，速率允许误差
