"""测试 LLM JSON 修复启发式（覆盖 MiniMax/qwen 等模型实际遇到的故障模式）"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.llm_parser import _extract_json_from_response


class JsonRepairTests(unittest.TestCase):
    def test_trailing_comma_in_object(self):
        text = '{"a": 1, "b": 2,}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_trailing_comma_in_array(self):
        text = '{"items": [1, 2, 3,]}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"items": [1, 2, 3]})

    def test_number_with_extra_dot(self):
        # LLM 偶尔输出 "0.0." 应为 "0.0"
        text = '{"rate": 0.5., "value": 12.3.}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["rate"], 0.5)
        self.assertEqual(result["value"], 12.3)

    def test_number_with_unit_suffix(self):
        # LLM 没遵守 "数值原样" 规则，把单位粘上去了
        text = '{"cum": 12.5mm, "rate": 0.4mm/d, "force": 200kN}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["cum"], 12.5)
        self.assertEqual(result["rate"], 0.4)
        self.assertEqual(result["force"], 200)

    def test_code_block_wrapped(self):
        text = '```json\n{"a": 1}\n```'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": 1})

    def test_thinking_block_stripped(self):
        text = '<thinking>let me analyze</thinking>\n{"a": 1}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": 1})

    def test_prose_before_and_after(self):
        text = '解析结果如下：\n{"a": 1}\n以上即结果。'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": 1})

    def test_valid_json_unchanged(self):
        text = '{"a": 1, "b": 2.5, "c": [1, 2, 3], "d": {"nested": true}}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["b"], 2.5)
        self.assertIsInstance(result["c"], list)
        self.assertTrue(result["d"]["nested"])

    def test_raises_on_truly_broken_json(self):
        text = '{"a": this is not json'
        with self.assertRaises(Exception):
            _extract_json_from_response(text)

    def test_repair_handles_missing_comma_same_line_multi_space(self):
        """MiniMax 偶发输出键值对之间缺逗号；repair 正则应能补"""
        # 多空格分隔 → 触发 same-line 修复
        text = '{"a": 1    "b": 2}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_repair_handles_missing_comma_newline(self):
        """跨行缺逗号场景"""
        text = '{"a": 1\n"b": 2}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_json5_handles_unquoted_keys(self):
        """json5 支持无引号 key（LLM 偶发省略）"""
        text = '{a: 1, b: "value"}'
        result = _extract_json_from_response(text)
        self.assertEqual(result["a"], 1)
        self.assertEqual(result["b"], "value")

    def test_json5_handles_single_quotes(self):
        """json5 支持单引号字符串"""
        text = "{'name': 'WY236', 'value': 12.5}"
        result = _extract_json_from_response(text)
        self.assertEqual(result["name"], "WY236")


if __name__ == "__main__":
    unittest.main()
