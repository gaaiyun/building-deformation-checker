"""验证 LLM 解析器对非数字 depth 字段的鲁棒性。

实际生产暴露：恒大中心 PDF 重跑时 LLM 返回 depth="01-1"（深度标识而非数字），
旧实现直接 float("01-1") 抛 ValueError，导致整个 pipeline 失败：
    ValueError: could not convert string to float: '01-1'

新行为：解析失败的深层位移点应被跳过（或 depth 设为 None），不阻断 pipeline。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.llm_parser import _build_report


class LlmParserDepthRobustTests(unittest.TestCase):

    def test_bad_depth_string_does_not_crash(self):
        """LLM 返回 depth='01-1' 不应抛 ValueError"""
        data = {
            "project_name": "test",
            "tables": [
                {
                    "monitoring_item": "深层水平位移",
                    "category": "深层水平位移",
                    "borehole_id": "CX1",
                    "deep_points": [
                        {"depth": "01-1", "current_cumulative": 5.0, "current_change": 0.1, "change_rate": 0.01},
                        {"depth": 2.0, "current_cumulative": 6.0, "current_change": 0.2, "change_rate": 0.02},
                    ],
                },
            ],
        }
        # 不应抛
        try:
            report = _build_report(data)
        except ValueError as e:
            self.fail(f"应优雅处理非法 depth，但抛了：{e}")

        # 至少保留有效点（depth=2.0 的那个）
        deep_points = report.tables[0].deep_points
        valid = [dp for dp in deep_points if dp.depth is not None]
        self.assertGreaterEqual(len(valid), 1, "至少保留 depth=2.0 的有效点")

    def test_missing_depth_field_does_not_crash(self):
        """LLM 返回 deep_point 缺 depth 字段，不应抛"""
        data = {
            "project_name": "test",
            "tables": [
                {
                    "monitoring_item": "深层水平位移",
                    "category": "深层水平位移",
                    "deep_points": [
                        {"current_cumulative": 1.0},
                    ],
                },
            ],
        }
        try:
            _build_report(data)
        except (ValueError, TypeError) as e:
            self.fail(f"缺 depth 不应抛：{e}")

    def test_valid_depth_still_parsed(self):
        """合法 depth 数字应正常解析"""
        data = {
            "project_name": "test",
            "tables": [
                {
                    "monitoring_item": "深层水平位移",
                    "deep_points": [
                        {"depth": 3.5, "current_cumulative": 4.0},
                        {"depth": "5.0", "current_cumulative": 5.0},  # 字符串 5.0 也应可解析
                    ],
                },
            ],
        }
        report = _build_report(data)
        depths = [dp.depth for dp in report.tables[0].deep_points if dp.depth is not None]
        self.assertIn(3.5, depths)
        self.assertIn(5.0, depths)

    def test_depth_unit_suffix_stripped(self):
        """LLM 可能返回 depth='3.5m'，应去掉单位"""
        data = {
            "project_name": "test",
            "tables": [
                {
                    "monitoring_item": "深层水平位移",
                    "deep_points": [
                        {"depth": "3.5m", "current_cumulative": 4.0},
                    ],
                },
            ],
        }
        report = _build_report(data)
        depths = [dp.depth for dp in report.tables[0].deep_points]
        self.assertEqual(depths, [3.5])


if __name__ == "__main__":
    unittest.main()
