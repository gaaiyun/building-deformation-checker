"""LLM JSON repair regex 安全性测试。

multi-agent review 发现的问题：
1. 数字+单位 regex 误吞合法字符串 ("length": "5m") → "5"
2. 缺逗号 regex 误判合法 2 空格缩进的 JSON ("a":"x",  "b":1) → 双逗号
3. 字符串内换行替换不识别转义引号 ("a\"b") 被切两段
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.llm_parser import _extract_json_from_response


class RegexSafetyTests(unittest.TestCase):

    def test_legitimate_string_with_unit_not_stripped(self):
        """字符串值含单位（如 length: '5m'）不应被剥单位"""
        text = '{"length": "5m", "width": "3.5cm"}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["length"], "5m")
        self.assertEqual(result["width"], "3.5cm")

    def test_two_space_indented_json_no_double_comma(self):
        """常见 2 空格缩进 JSON 不应被误插逗号"""
        text = '{"a": "x",  "b": 1,  "c": 2}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": "x", "b": 1, "c": 2})

    def test_escaped_quote_in_string_preserved(self):
        """字符串内的转义引号不应破坏字符串边界识别"""
        # 含 \" 的合法 JSON
        text = '{"msg": "他说\\"OK\\"", "val": 1}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["msg"], '他说"OK"')
        self.assertEqual(result["val"], 1)

    def test_number_with_unit_in_value_position_still_stripped(self):
        """数字后单位（裸数据，不是字符串）应被剥（保持已有修复）"""
        # 不带引号的 12.5mm → 应剥成 12.5
        text = '{"val": 12.5mm}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["val"], 12.5)

    def test_legitimate_string_ending_with_letters_not_misjoined(self):
        """合法字符串以字母结尾，后接 key 不应被误加逗号（已有逗号场景）"""
        # 已有逗号，2 空格缩进
        text = '{"name": "Tom",  "age": 30}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"name": "Tom", "age": 30})

    def test_string_with_special_chars(self):
        """字符串含特殊字符（中文、emoji、转义）"""
        text = '{"note": "测试报告 ✓ 完成", "val": -1.5}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["note"], "测试报告 ✓ 完成")
        self.assertEqual(result["val"], -1.5)

    def test_number_with_trailing_dot_after_unit_strip(self):
        """LLM 写 '12.mm' 时（数字 + 多余点 + 单位），剥单位后应再清掉尾点

        旧实现：'12.mm' → '12.' （仍非法）
        新实现：'12.mm' → '12'
        """
        text = '{"val": 12.mm}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["val"], 12)

    def test_double_dot_with_unit(self):
        """'12.5.mm' → '12.5'（保留首个有效小数）"""
        text = '{"val": 12.5.mm}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["val"], 12.5)

    def test_trailing_dot_no_unit(self):
        """'12.' 单独出现（无单位）也应清掉尾点"""
        text = '{"val": 12.}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["val"], 12)


if __name__ == "__main__":
    unittest.main()
