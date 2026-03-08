import unittest
from unittest.mock import patch

from src.tools.llm_parser import parse_report_with_llm


class LlmParserTests(unittest.TestCase):
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
