import json
import unittest
from unittest.mock import patch

from src.models.data_models import MonitoringCategory
from src.tools.llm_parser import _split_chunks, parse_report_with_llm


class LlmParserTests(unittest.TestCase):
    def test_split_chunks_never_returns_oversized_single_page(self):
        raw_text = "--- 第 1 页 ---\n" + ("A" * 23000) + "\n--- 第 2 页 ---\nB"

        chunks = _split_chunks(raw_text, max_chars=8000)

        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 8000 for chunk in chunks))

    def test_parse_report_merges_thresholds_and_summary_across_chunks(self):
        first_chunk = {
            "project_name": "项目A",
            "monitoring_company": "",
            "report_number": "",
            "monitoring_period": "",
            "monitoring_date": "",
            "interval_days": None,
            "thresholds": [{"item_name": "水平位移", "warning_value": 10, "control_value": 20, "rate_limit": 2}],
            "summary_items": [{"monitoring_item": "水平位移", "negative_max": "", "negative_max_id": "", "positive_max": "", "positive_max_id": "", "max_rate": "", "max_rate_id": "", "safety_status": ""}],
            "tables": [],
            "conclusion": "",
        }
        second_chunk = {
            **first_chunk,
            "thresholds": [{"item_name": "深层水平位移", "warning_value": 15, "control_value": 25, "rate_limit": 3}],
            "summary_items": [{"monitoring_item": "深层水平位移", "negative_max": "", "negative_max_id": "", "positive_max": "", "positive_max_id": "", "max_rate": "", "max_rate_id": "", "safety_status": ""}],
            "tables": [
                {
                    "monitoring_item": "测斜孔",
                    "category": "深层水平位移",
                    "monitor_date": "",
                    "monitor_count": "",
                    "point_count": 1,
                    "equipment_type": "",
                    "equipment_model": "",
                    "borehole_id": "CX1",
                    "borehole_depth": None,
                    "table_unit": "mm",
                    "initial_value_reliable": True,
                    "points": [],
                    "deep_points": [{"depth": 1, "previous_cumulative": 0, "current_cumulative": 1, "current_change": 1, "change_rate": None}],
                    "statistics": {},
                }
            ],
        }

        with (
            patch("src.tools.llm_parser._split_chunks", return_value=["chunk-1", "chunk-2"]),
            patch(
                "src.tools.llm_parser.call_chat_completion",
                side_effect=[
                    json.dumps(first_chunk, ensure_ascii=False),
                    json.dumps(second_chunk, ensure_ascii=False),
                ],
            ),
        ):
            report = parse_report_with_llm("ignored")

        self.assertEqual(len(report.thresholds), 2)
        self.assertEqual(len(report.summary_items), 2)
        self.assertEqual(report.tables[0].category, MonitoringCategory.DEEP_HORIZONTAL)

    def test_parse_report_with_llm_builds_report_from_successful_chunk(self):
        raw_text = "【支护结构顶部水平位移】监测数据成果表\nS1 0.0 1.0 1.0 0.1"
        llm_json = """
        {
          "project_name":"鱼珠乐天智能科技创新中心",
          "monitoring_company":"广东质安建设工程技术有限公司",
          "report_number":"监测2023011-017",
          "monitoring_period":"2024-03-17至2024-03-26",
          "monitoring_date":"2024-03-26",
          "interval_days":10,
          "thresholds":[],
          "summary_items":[],
          "tables":[
            {
              "monitoring_item":"支护结构顶部水平位移",
              "category":"水平位移",
              "monitor_date":"2024-03-26",
              "monitor_count":"第69次",
              "point_count":1,
              "equipment_type":"全站仪",
              "equipment_model":"TS15",
              "borehole_id":"",
              "borehole_depth":null,
              "table_unit":"mm",
              "initial_value_reliable":true,
              "points":[
                {
                  "point_id":"S1",
                  "initial_value":0.0,
                  "previous_value":null,
                  "current_value":1.0,
                  "current_change":1.0,
                  "cumulative_change":1.0,
                  "change_rate":0.1,
                  "safety_status":"正常"
                }
              ],
              "deep_points":[],
              "statistics":{
                "positive_max_id":"S1",
                "positive_max_value":1.0,
                "negative_max_id":"",
                "negative_max_value":null,
                "max_rate_id":"S1",
                "max_rate_value":0.1,
                "max_change_id":"",
                "max_change_value":null,
                "max_force_id":"",
                "max_force_value":null,
                "min_force_id":"",
                "min_force_value":null
              }
            }
          ],
          "conclusion":"正常"
        }
        """

        with patch("src.tools.llm_parser.call_chat_completion", return_value=llm_json):
            report = parse_report_with_llm(raw_text)

        self.assertEqual(report.project_name, "鱼珠乐天智能科技创新中心")
        self.assertEqual(len(report.tables), 1)
        self.assertEqual(report.tables[0].monitoring_item, "支护结构顶部水平位移")

    def test_parse_report_with_llm_raises_when_all_chunks_fail(self):
        with patch("src.tools.llm_parser.call_chat_completion", return_value=None):
            with self.assertRaises(RuntimeError):
                parse_report_with_llm("任意文本")


if __name__ == "__main__":
    unittest.main()
