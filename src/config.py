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
PADDLE_OCR_URL = os.getenv("PADDLE_OCR_URL", "https://ucyduai2gcx8e403.aistudio-app.com/layout-parsing")
PADDLE_OCR_TOKEN = os.getenv("PADDLE_OCR_TOKEN", "1002254afa7100a68da7ebfae37bf3504bf2cd7f")

# ── LLM 超时与重试配置 ────────────────────────────────────
LLM_TIMEOUT_NORMAL = int(os.getenv("LLM_TIMEOUT_NORMAL", "120"))  # 常规请求超时（秒）
LLM_TIMEOUT_LARGE = int(os.getenv("LLM_TIMEOUT_LARGE", "240"))   # 大 prompt 超时（秒）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))         # 应用层重试次数
LLM_RETRY_BACKOFF_SEC = int(os.getenv("LLM_RETRY_BACKOFF_SEC", "10"))  # 首次重试等待秒数
LLM_STEP_DELAY_SEC = int(os.getenv("LLM_STEP_DELAY_SEC", "0"))   # 步骤间延迟（0=禁用）

# ── 自验证配置 ────────────────────────────────────────────
SELF_VERIFY_TIMEOUT_SEC = int(os.getenv("SELF_VERIFY_TIMEOUT_SEC", "120"))
SELF_VERIFY_MAX_RETRIES = int(os.getenv("SELF_VERIFY_MAX_RETRIES", "1"))
SELF_VERIFY_RETRY_BACKOFF_SEC = int(os.getenv("SELF_VERIFY_RETRY_BACKOFF_SEC", "5"))
SELF_VERIFY_BATCH_SIZE = int(os.getenv("SELF_VERIFY_BATCH_SIZE", "5"))
SELF_VERIFY_SINGLE_SHOT_THRESHOLD = int(os.getenv("SELF_VERIFY_SINGLE_SHOT_THRESHOLD", "6"))
SELF_VERIFY_CONTEXT_CHARS = int(os.getenv("SELF_VERIFY_CONTEXT_CHARS", "200"))
SELF_VERIFY_MAX_PARALLEL = int(os.getenv("SELF_VERIFY_MAX_PARALLEL", "1"))
SELF_VERIFY_MAX_TOTAL_SEC = int(os.getenv("SELF_VERIFY_MAX_TOTAL_SEC", "360"))
SELF_VERIFY_MAX_ERRORS = int(os.getenv("SELF_VERIFY_MAX_ERRORS", "24"))

# ── 最终审核配置 ──────────────────────────────────────────
FINAL_REVIEW_TIMEOUT_SEC = int(os.getenv("FINAL_REVIEW_TIMEOUT_SEC", "180"))
FINAL_REVIEW_MAX_RETRIES = int(os.getenv("FINAL_REVIEW_MAX_RETRIES", "1"))
FINAL_REVIEW_RETRY_BACKOFF_SEC = int(os.getenv("FINAL_REVIEW_RETRY_BACKOFF_SEC", "5"))
FINAL_REVIEW_PREVIEW_CHARS = int(os.getenv("FINAL_REVIEW_PREVIEW_CHARS", "3000"))
SELF_VERIFY_BATCH_SIZE = int(os.getenv("SELF_VERIFY_BATCH_SIZE", "5"))  # 自验证默认批次大小
SELF_VERIFY_TIMEOUT_SEC = int(os.getenv("SELF_VERIFY_TIMEOUT_SEC", "45"))  # 自验证请求超时（秒）
SELF_VERIFY_MAX_RETRIES = int(os.getenv("SELF_VERIFY_MAX_RETRIES", "0"))   # 自验证默认不长时间重试
SELF_VERIFY_RETRY_BACKOFF_SEC = int(os.getenv("SELF_VERIFY_RETRY_BACKOFF_SEC", "2"))  # 自验证重试等待秒数
SELF_VERIFY_SINGLE_SHOT_THRESHOLD = int(os.getenv("SELF_VERIFY_SINGLE_SHOT_THRESHOLD", "6"))  # 少量错误直接单次复核
SELF_VERIFY_CONTEXT_CHARS = int(os.getenv("SELF_VERIFY_CONTEXT_CHARS", "120"))  # 自验证上下文截取长度
SELF_VERIFY_MAX_PARALLEL = int(os.getenv("SELF_VERIFY_MAX_PARALLEL", "2"))  # 自验证并发上限
SELF_VERIFY_MAX_TOTAL_SEC = int(os.getenv("SELF_VERIFY_MAX_TOTAL_SEC", "90"))  # 自验证总耗时上限（秒）
SELF_VERIFY_MAX_ERRORS = int(os.getenv("SELF_VERIFY_MAX_ERRORS", "24"))  # 自验证最多处理的错误数
CONFIG_ENRICH_TIMEOUT_SEC = int(os.getenv("CONFIG_ENRICH_TIMEOUT_SEC", "45"))  # 配置增强超时
CONFIG_ENRICH_MAX_RETRIES = int(os.getenv("CONFIG_ENRICH_MAX_RETRIES", "0"))   # 配置增强重试
CONFIG_ENRICH_RETRY_BACKOFF_SEC = int(os.getenv("CONFIG_ENRICH_RETRY_BACKOFF_SEC", "2"))
FINAL_REVIEW_TIMEOUT_SEC = int(os.getenv("FINAL_REVIEW_TIMEOUT_SEC", "35"))  # 最终审核超时
FINAL_REVIEW_MAX_RETRIES = int(os.getenv("FINAL_REVIEW_MAX_RETRIES", "0"))   # 最终审核重试
FINAL_REVIEW_RETRY_BACKOFF_SEC = int(os.getenv("FINAL_REVIEW_RETRY_BACKOFF_SEC", "2"))
FINAL_REVIEW_PREVIEW_CHARS = int(os.getenv("FINAL_REVIEW_PREVIEW_CHARS", "2200"))  # 最终审核原文预览长度

# ── 数值精度 ──────────────────────────────────────────────
FLOAT_TOLERANCE = 0.15  # mm，允许的浮点误差
RATE_TOLERANCE = 0.05   # mm/d，速率允许误差
