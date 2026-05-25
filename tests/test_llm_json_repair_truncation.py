"""LLM JSON 截断修复的鲁棒性测试。

生产暴露：MiniMax LLM 偶发 MAX_TOKENS 截断，JSON 半截。
旧代码 _repair_llm_json 需要 text 中存在 } 或 ] 才能 trigger 截断闭合。
但极端情况下截断可能发生在更深嵌套，没有任何已闭合的 brace。

新行为：即使没有 close bracket，也尝试用 bracket stack 闭合。
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


class JsonTruncationRepairTests(unittest.TestCase):

    def test_truncation_with_no_existing_close_bracket(self):
        """嵌套深处截断（没有任何 } 或 ] 出现过）→ 应能闭合"""
        text = '{"a": {"b": [1, 2'  # stack = [{, {, [], expect ]}} closer
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": {"b": [1, 2]}})

    def test_truncation_with_some_existing_close_keeps_complete_elements(self):
        """已有部分闭合：保留完整 T1，丢弃半截 T2（其字符串都没闭）"""
        text = '{"tables": [{"name": "T1"}, {"name": "T2'
        result = _extract_json_from_response(text)
        # 应保留 T1，T2 是不可恢复的（string "T2 没闭）所以丢弃
        self.assertEqual(result, {"tables": [{"name": "T1"}]})

    def test_truncation_in_array_value(self):
        """数组中部截断"""
        text = '{"vals": [1.0, 2.5, 3.7'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"vals": [1.0, 2.5, 3.7]})

    def test_complete_json_unchanged(self):
        """完整 JSON 不应被改坏"""
        text = '{"a": 1, "b": [2, 3]}'
        result = _extract_json_from_response(text)
        self.assertEqual(result, {"a": 1, "b": [2, 3]})

    def test_truncation_inside_unfinished_string(self):
        """字符串未闭合 + 整体截断（更难场景）"""
        text = '{"name": "T1", "desc": "unfinished'
        try:
            result = _extract_json_from_response(text)
            # 允许失败或合理修复（截到 desc 之前，给个 fallback）
            # 我们至少不要抛非 JSONDecodeError
        except json.JSONDecodeError:
            pass  # 接受
        except Exception as e:
            self.fail(f"应捕获并优雅处理（return None 或合理修复），不该抛 {type(e).__name__}: {e}")


if __name__ == "__main__":
    unittest.main()
