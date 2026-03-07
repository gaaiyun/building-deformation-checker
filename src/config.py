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

# 可切换的模型列表（Coding Plan 支持的模型）
AVAILABLE_MODELS = [
    "qwen3.5-plus",
    "kimi-k2.5",
    "glm-5",
    "MiniMax-M2.5",
    "qwen3-coder-plus",
    "glm-4.7",
]


def set_model(model_name: str):
    """运行时切换 LLM 模型"""
    global LLM_MODEL
    LLM_MODEL = model_name

# ── PaddleOCR 版式分析（扫描件备选方案）────────────────────
PADDLE_OCR_URL = "https://ucyduai2gcx8e403.aistudio-app.com/layout-parsing"
PADDLE_OCR_TOKEN = "1002254afa7100a68da7ebfae37bf3504bf2cd7f"

# ── LLM 超时与重试配置 ────────────────────────────────────
LLM_TIMEOUT_NORMAL = int(os.getenv("LLM_TIMEOUT_NORMAL", "90"))  # 常规请求超时（秒）
LLM_TIMEOUT_LARGE = int(os.getenv("LLM_TIMEOUT_LARGE", "180"))   # 大 prompt 超时（秒）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))         # 应用层重试次数
LLM_RETRY_BACKOFF_SEC = int(os.getenv("LLM_RETRY_BACKOFF_SEC", "10"))  # 首次重试等待秒数
LLM_STEP_DELAY_SEC = int(os.getenv("LLM_STEP_DELAY_SEC", "0"))   # 步骤间延迟（0=禁用）

# ── 数值精度 ──────────────────────────────────────────────
FLOAT_TOLERANCE = 0.15  # mm，允许的浮点误差
RATE_TOLERANCE = 0.05   # mm/d，速率允许误差
