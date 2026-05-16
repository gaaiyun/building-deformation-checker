"""text_normalize 单元测试 - 关键的 Unicode 静默 bug 防线"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import unittest

from src.utils.text_normalize import (
    extract_unit,
    normalize_digits,
    normalize_minus,
    normalize_numeric_text,
    parse_float,
)


class TestNormalizeMinus(unittest.TestCase):
    def test_u2212_math_minus(self):
        """U+2212 是 OCR/PDF 最常出现的非 ASCII 负号"""
        self.assertEqual(normalize_minus("−0.5"), "-0.5")

    def test_fullwidth_minus(self):
        self.assertEqual(normalize_minus("－1"), "-1")

    def test_various_dashes(self):
        # en dash, em dash, hyphen, figure dash 都应被替换
        for ch in ["‐", "‑", "‒", "–", "—", "―"]:
            self.assertEqual(normalize_minus(f"{ch}1.5"), "-1.5")

    def test_does_not_touch_ascii_minus(self):
        self.assertEqual(normalize_minus("-3.14"), "-3.14")

    def test_empty_and_none(self):
        self.assertEqual(normalize_minus(""), "")


class TestNormalizeDigits(unittest.TestCase):
    def test_fullwidth_digits(self):
        self.assertEqual(normalize_digits("１２３"), "123")

    def test_fullwidth_period(self):
        self.assertEqual(normalize_digits("１．５"), "1.5")

    def test_keep_ascii(self):
        self.assertEqual(normalize_digits("1.234"), "1.234")

    def test_mixed_text(self):
        self.assertEqual(normalize_digits("累计：１２mm"), "累计：12mm")


class TestNormalizeNumericText(unittest.TestCase):
    def test_combined(self):
        # 复合：全角 - + 全角数字 + 全角空格
        result = normalize_numeric_text("－１２　mm")
        self.assertEqual(result, "-12 mm")

    def test_idempotent(self):
        s = "−1.5"
        self.assertEqual(normalize_numeric_text(normalize_numeric_text(s)), "-1.5")


class TestParseFloat(unittest.TestCase):
    def test_passthrough_int_float(self):
        self.assertEqual(parse_float(1), 1.0)
        self.assertEqual(parse_float(1.5), 1.5)

    def test_simple_string(self):
        self.assertEqual(parse_float("3.14"), 3.14)
        self.assertEqual(parse_float("-2.5"), -2.5)

    def test_u2212_minus(self):
        # 这是 #1 静默 bug 源：现状 calculation_checker 会拒绝
        self.assertEqual(parse_float("−0.5"), -0.5)

    def test_fullwidth_digits(self):
        self.assertEqual(parse_float("１２．３"), 12.3)

    def test_thousands_separator(self):
        self.assertEqual(parse_float("1,234.56"), 1234.56)

    def test_with_unit_suffix(self):
        self.assertEqual(parse_float("23.6mm"), 23.6)
        self.assertEqual(parse_float("-23.6 mm"), -23.6)
        self.assertEqual(parse_float("0.484mm/d"), 0.484)

    def test_with_unit_prefix_chinese(self):
        self.assertEqual(parse_float("累计 -23.6mm"), -23.6)

    def test_sentinels_return_none(self):
        for s in ["", None, "正常", "—", "--", "-", "/", "N/A", "None", "null"]:
            self.assertIsNone(parse_float(s), f"failed for {s!r}")

    def test_scientific_notation(self):
        self.assertAlmostEqual(parse_float("1.23e-2"), 0.0123)

    def test_plus_sign(self):
        self.assertEqual(parse_float("+1.5"), 1.5)

    def test_garbage_string(self):
        self.assertIsNone(parse_float("hello"))
        self.assertIsNone(parse_float("abc def"))

    def test_combined_real_world_chinese_report(self):
        # 真实场景模拟（鱼珠乐天报告页 9）
        # OCR 输出有时混入 U+2212
        cases = {
            "−23.6mm/S5": -23.6,
            "36.6mm/2S11": 36.6,
            "−0.010mm/d/D2": -0.010,
            "176.8mm/d/W4": 176.8,
            "214.9kN/M5": 214.9,
        }
        for src, expected in cases.items():
            self.assertAlmostEqual(parse_float(src), expected, msg=f"failed for {src}")


class TestExtractUnit(unittest.TestCase):
    def test_mm(self):
        self.assertEqual(extract_unit("23.6mm"), "mm")

    def test_mm_per_day(self):
        self.assertEqual(extract_unit("0.484mm/d"), "mm/d")

    def test_kn(self):
        self.assertEqual(extract_unit("214.9kN"), "kN")
        self.assertEqual(extract_unit("214.9KN"), "kN")
        self.assertEqual(extract_unit("214.9kn"), "kN")

    def test_no_unit(self):
        self.assertIsNone(extract_unit("23.6"))


if __name__ == "__main__":
    unittest.main()
