"""数字与文本归一化（PDF/OCR 提取后必须先过此层）

修复 PDF 解析中最常见的静默 bug：
- U+2212 (math minus, "−") 被误识别为破折号，导致 ``float('−0.5')`` 抛 ValueError
- U+FF0D / U+2010..U+2015 等各种连字符 → ASCII ``-``
- 全角数字 U+FF11..U+FF19 → 半角 ``0-9``
- 全角小数点 U+FF0E / 全角逗号 U+FF0C → ASCII ``.`` ``,``
- 多种非 ASCII 空格（U+00A0、U+3000、U+2007、U+202F）→ 普通空格 ``0x20``

设计原则：
- **保守**：只动确认为"数值/空白语义"的字符，不动化学下标、中文文本主体
- **可逆**：每个翻译表都列出原字符与目标字符，便于审计
- **幂等**：可重复调用 ``normalize_numeric_text`` 不会破坏已归一化的文本
- **零依赖**：纯 stdlib，不引入 unicodedata 之外的库

引用文献：
- pdfminer.six issue #289（负号识别问题）
- OmniDocBench 2026 OCR 评测中 PaddleOCR-VL 仍偶尔输出 U+2212
"""

from __future__ import annotations

import re
from typing import Optional


# ─── 各类负号/连字符 → ASCII '-' ────────────────────────────
# 这里列举的字符在视觉上几乎与 ASCII 减号无差别，但 Unicode 类目不同，导致
# float()、int() 等内置函数会拒绝解析。全部归一化到 0x2D（HYPHEN-MINUS）。
_MINUS_LIKE = (
    "−"  # U+2212 MINUS SIGN（math minus，OCR 最常输出此变体）
    "－"  # U+FF0D FULLWIDTH HYPHEN-MINUS（中文键盘常见）
    "‐"  # U+2010 HYPHEN（排版连字符）
    "‑"  # U+2011 NON-BREAKING HYPHEN
    "‒"  # U+2012 FIGURE DASH（与数字等宽）
    "–"  # U+2013 EN DASH（区间符号，常被误识别为负号）
    "—"  # U+2014 EM DASH
    "―"  # U+2015 HORIZONTAL BAR
    "˗"  # U+02D7 MODIFIER LETTER MINUS SIGN
    "⁻"  # U+207B SUPERSCRIPT MINUS
    "₋"  # U+208B SUBSCRIPT MINUS
)

_MINUS_TRANS = str.maketrans({ch: "-" for ch in _MINUS_LIKE})


# ─── 全角数字/标点 → 半角 ────────────────────────────────────
# 中文输入法或扫描件 OCR 偶尔会输出 U+FF11..U+FF19 的全角数字，
# 标准 float() 不接受。一并把全角小数点和逗号也转过来。
_FULLWIDTH_DIGIT_TRANS = str.maketrans(
    "０１２３４５６７８９．，",
    "0123456789.,",
)


# ─── 各种特殊空白字符 → ASCII 空格 ───────────────────────────
# OCR/PDF 常输出多种非 ASCII 空白，下游正则/分词通常按普通空格 (0x20) 处理。
# 仅替换确认为"空格语义"的字符，不动 \t / \n / \r。
_OTHER_TRANS = str.maketrans({
    " ": " ",  # NO-BREAK SPACE（常见于 HTML 转 markdown）
    "　": " ",  # IDEOGRAPHIC SPACE（中文排版全角空格）
    " ": " ",  # FIGURE SPACE（与数字等宽的空格）
    " ": " ",  # NARROW NO-BREAK SPACE（法语千分位常用）
})


def normalize_minus(text: str) -> str:
    """把各类负号/连字符（U+2212、全角负号、en/em dash 等）替换为 ASCII ``-``。

    空字符串与 None 安全返回原值。不修改其它字符。
    """
    if not text:
        return text
    return text.translate(_MINUS_TRANS)


def normalize_digits(text: str) -> str:
    """全角数字（U+FF10..U+FF19）+ 全角小数点/逗号 → 半角。

    幂等。仅替换数字相关字符，中英文文本不动。
    """
    if not text:
        return text
    return text.translate(_FULLWIDTH_DIGIT_TRANS)


def normalize_whitespace(text: str) -> str:
    """常见非 ASCII 空白（NBSP、表意空格、figure space 等）→ ASCII 空格。

    不动 ``\\t`` / ``\\n`` / ``\\r``。
    """
    if not text:
        return text
    return text.translate(_OTHER_TRANS)


def normalize_numeric_text(text: str) -> str:
    """对包含数值的文本做完整归一化（保留中文、字母不变）。

    串联调用 ``normalize_minus`` → ``normalize_digits`` → ``normalize_whitespace``。
    可对 PDF/OCR 输出的整段文本调用（表格 markdown、原始文字层皆可）。

    幂等：可重复调用，结果不变。
    """
    if not text:
        return text
    text = normalize_minus(text)
    text = normalize_digits(text)
    text = normalize_whitespace(text)
    return text


# ─── 数值字符串 → float 强解析 ───────────────────────────────
# 优先级匹配：千分位格式 > 普通整数/小数 > .开头小数
# 不在正则里处理负号，统一交给 sign 捕获组（兼容 U+2212 已被 normalize_minus 处理后的 '-'）
_NUMBER_RE = re.compile(
    r"""
    (?P<sign>[+\-−])?            # 可选符号（含 U+2212，正则层兜底）
    \s*
    (?P<num>
        (?:\d{1,3}(?:,\d{3})+(?:\.\d+)?)   # 千分位 1,234.56
        |
        (?:\d+(?:\.\d+)?)                   # 普通 1234.56 / 123
        |
        (?:\.\d+)                            # 小数 .56
    )
    (?:[eE](?P<exp>[+\-]?\d+))?       # 科学计数法
    """,
    re.VERBOSE,
)

# 解析时视为"无数据"的哨兵字符串集合
_SENTINELS: frozenset[str] = frozenset({
    "正常", "—", "--", "-", "/", "N/A", "n/a", "NA", "None", "none", "null", "—-",
})


def parse_float(value: object) -> Optional[float]:
    """宽容地把任意值解析为 float；解析失败返回 ``None``。

    支持的输入：
        - ``int`` / ``float`` → 直接返回（NaN 视为 None）
        - ``"3.14"`` / ``"-2.5"`` → 标准数值字符串
        - ``"−0.5"`` / ``"－0.5"`` → U+2212 / U+FF0D 等非 ASCII 负号
        - ``"１２．３"`` → 全角数字
        - ``"1,234.56"`` → 千分位分隔符
        - ``"23.6mm"`` / ``"0.484mm/d"`` → 带单位后缀
        - ``"累计 -23.6mm"`` → 中文上下文中的第一个数值
        - ``"1.23e-2"`` → 科学计数法

    返回 ``None`` 的情况：
        - ``None`` / 空字符串
        - 非字符串/数值类型（list、dict 等）
        - 哨兵字符串：``"正常"``, ``"—"``, ``"--"``, ``"-"``, ``"/"``, ``"N/A"``, ``"None"``
        - 完全无法匹配数字模式的字符串
        - NaN 浮点数

    使用建议：在 LLM/OCR 返回值进入计算前**必过此层**，避免静默错误。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN 自比较为 False
            return None
        return float(value)
    if not isinstance(value, str):
        return None
    text = normalize_numeric_text(value).strip()
    if not text or text in _SENTINELS:
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    sign = m.group("sign") or "+"
    num_str = m.group("num").replace(",", "")
    exp = m.group("exp")
    try:
        result = float(num_str)
        if exp:
            result *= 10 ** int(exp)
        if sign in ("-", "−"):
            result = -result
        return result
    except ValueError:
        return None


# ─── 单位识别（仅识别，不转换）────────────────────────────────
# 末尾匹配；建筑监测领域常见单位都在此清单。新单位添加规则：
# - 大小写敏感的统一标识符（kN 不要写成 KN）由调用方处理
# - 复合单位（mm/d）放最前面，避免被简单 'mm' 抢先匹配
_UNIT_RE = re.compile(r"(mm/d|mm/天|mm|cm|m|kN|kn|KN|kPa|°C|°|m³|d)\s*$")


def extract_unit(value: str) -> Optional[str]:
    """从字符串末尾识别单位标识符。

    返回归一化后的单位字符串（如 'KN' / 'kn' → 'kN'）；未匹配返回 None。
    """
    if not isinstance(value, str):
        return None
    s = normalize_numeric_text(value).strip()
    m = _UNIT_RE.search(s)
    if not m:
        return None
    unit = m.group(1)
    # 大小写归一
    if unit.lower() == "kn":
        return "kN"
    return unit


__all__ = [
    "normalize_minus",
    "normalize_digits",
    "normalize_whitespace",
    "normalize_numeric_text",
    "parse_float",
    "extract_unit",
]
