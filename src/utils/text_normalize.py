"""数字与文本归一化（PDF/OCR 提取后必须先过此层）

修复 PDF 解析中最常见的静默 bug：
- U+2212 (math minus, −) 被误识别为破折号，导致 float('−0.5') 抛出
- U+FF0D/U+2010..U+2015 等各种连字符 → ASCII '-'
- 全角数字 U+FF11..U+FF19 → 半角
- 全角小数点/逗号/空格
- OCR 易混字符 (零/O, 一/l 仅在数值上下文)

设计原则：保守、可逆、不误伤化学式与中文文本，只在"数值字段"显式调用。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional


# ─── 各类负号/连字符 → ASCII '-' ────────────────────────────
_MINUS_LIKE = (
    "−"  # − minus sign (math)
    "－"  # － full-width hyphen-minus
    "‐"  # ‐ hyphen
    "‑"  # ‑ non-breaking hyphen
    "‒"  # ‒ figure dash
    "–"  # – en dash
    "—"  # — em dash
    "―"  # ― horizontal bar
    "˗"  # ˗ modifier letter minus
    "⁻"  # ⁻ superscript minus
    "₋"  # ₋ subscript minus
)

_MINUS_TRANS = str.maketrans({ch: "-" for ch in _MINUS_LIKE})


# ─── 全角数字 → 半角 ─────────────────────────────────────────
_FULLWIDTH_DIGIT_TRANS = str.maketrans(
    "０１２３４５６７８９．，",
    "0123456789.,",
)


# ─── 其他常见杂质 ────────────────────────────────────────────
_OTHER_TRANS = str.maketrans({
    " ": " ",   # NBSP
    "　": " ",   # ideographic space
    " ": " ",   # figure space
    " ": " ",   # narrow no-break
})


def normalize_minus(text: str) -> str:
    """仅替换各类负号/连字符为 ASCII '-'。"""
    if not text:
        return text
    return text.translate(_MINUS_TRANS)


def normalize_digits(text: str) -> str:
    """全角数字/小数点/逗号 → 半角。"""
    if not text:
        return text
    return text.translate(_FULLWIDTH_DIGIT_TRANS)


def normalize_whitespace(text: str) -> str:
    """各种特殊空格 → 普通空格。"""
    if not text:
        return text
    return text.translate(_OTHER_TRANS)


def normalize_numeric_text(text: str) -> str:
    """对包含数值的文本做完整归一化（保留中文、字母不变）。

    用于 PDF/OCR 输出整段文本（表格 markdown、原始文字层）。
    幂等可重复调用。
    """
    if not text:
        return text
    text = normalize_minus(text)
    text = normalize_digits(text)
    text = normalize_whitespace(text)
    return text


# ─── 数值字符串 → float 强解析 ───────────────────────────────
# 支持: "1,234.56", "−0.5", "+1.23e-2", "12.3 mm", "正常", "/", "--"
_NUMBER_RE = re.compile(
    r"""
    (?P<sign>[+\-−])?
    \s*
    (?P<num>
        (?:\d{1,3}(?:,\d{3})+(?:\.\d+)?)   # 1,234.56
        |
        (?:\d+(?:\.\d+)?)                   # 1234.56 / 123
        |
        (?:\.\d+)                            # .56
    )
    (?:[eE](?P<exp>[+\-]?\d+))?
    """,
    re.VERBOSE,
)


def parse_float(value: object) -> Optional[float]:
    """宽容地把任意值解析为 float；解析失败返回 None。

    处理：
    - 已是 int/float → 直接返回
    - "正常"/"/"/None/""/"--"/"-" → None
    - "1,234.56" → 1234.56
    - "−0.5" / "－0.5" → -0.5
    - 中文文本里抽取第一个数值 → "累计 -23.6mm" → -23.6
    - "+ 1.23e-2" → 0.0123
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN
            return None
        return float(value)
    if not isinstance(value, str):
        return None
    text = normalize_numeric_text(value).strip()
    if not text:
        return None
    # 哨兵字符串
    sentinels = {"正常", "—", "--", "-", "/", "N/A", "n/a", "NA", "None", "none", "null", "—", "--", "—-"}
    if text in sentinels:
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
        if sign == "-":
            result = -result
        return result
    except ValueError:
        return None


# ─── 单位识别（仅识别，不转换）────────────────────────────────
_UNIT_RE = re.compile(r"(mm/d|mm/天|mm|cm|m|kN|kn|KN|kPa|°C|°|m³|d)\s*$")


def extract_unit(value: str) -> Optional[str]:
    """从字符串末尾识别单位标识符，返回归一化后的单位字符串。"""
    if not isinstance(value, str):
        return None
    s = normalize_numeric_text(value).strip()
    m = _UNIT_RE.search(s)
    if m:
        unit = m.group(1).lower()
        # 归一化
        if unit in {"kn"}:
            unit = "kN"
        return unit
    return None


__all__ = [
    "normalize_minus",
    "normalize_digits",
    "normalize_whitespace",
    "normalize_numeric_text",
    "parse_float",
    "extract_unit",
]
