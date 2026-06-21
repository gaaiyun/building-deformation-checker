import json
import threading
import time
import unittest
from unittest.mock import patch

from src.models.data_models import MonitoringCategory
from src.tools.llm_parser import SYSTEM_PROMPT, _split_chunks, parse_report_with_llm


class LlmParserTests(unittest.TestCase):
    def test_system_prompt_guards_against_representative_table_omissions(self):
        self.assertIn("逐页逐表清点", SYSTEM_PROMPT)
        self.assertIn("不能抽样、省略", SYSTEM_PROMPT)
        self.assertIn("3号点X方向、4号点X方向、5号点X方向", SYSTEM_PROMPT)

    def test_system_prompt_distinguishes_period_rate_from_trailing_summary_change(self):
        self.assertIn("最右侧独立的“本期变化量”", SYSTEM_PROMPT)
        self.assertIn("不能填入 change_rate", SYSTEM_PROMPT)

    def test_system_prompt_excludes_report_summary_rows_from_data_tables(self):
        self.assertIn("监测结果汇总表", SYSTEM_PROMPT)
        self.assertIn("不能放入 tables", SYSTEM_PROMPT)

    def test_system_prompt_maps_force_reading_columns_without_initial_subtraction(self):
        self.assertIn("本次(kN) / 测值(kN) / 变化速率", SYSTEM_PROMPT)
        self.assertIn("initial_value_reliable=false", SYSTEM_PROMPT)
        self.assertIn("cumulative_change=测值", SYSTEM_PROMPT)

    def test_split_chunks_never_returns_oversized_single_page(self):
        raw_text = "--- 第 1 页 ---\n" + ("A" * 23000) + "\n--- 第 2 页 ---\nB"

        chunks = _split_chunks(raw_text, max_chars=8000)

        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 8000 for chunk in chunks))

    def test_split_chunks_keeps_cross_page_appendix_table_separate(self):
        raw_text = (
            "--- 第 1 页 ---\n报告摘要\n"
            "--- 第 2 页 ---\n附表 1-1-1 支护结构顶部水平位移观测结果表\nW1 1 2 3\n"
            "--- 第 3 页 ---\nW2 4 5 6\n"
            "--- 第 4 页 ---\n附表 2-1-1 周边地面沉降观测结果表\nD1 7 8 9\n"
        )

        chunks = _split_chunks(raw_text, max_chars=8000)

        first_table = next(chunk for chunk in chunks if "附表 1-1-1" in chunk)
        second_table = next(chunk for chunk in chunks if "附表 2-1-1" in chunk)
        self.assertIn("W2 4 5 6", first_table)
        self.assertNotIn("附表 2-1-1", first_table)
        self.assertNotEqual(first_table, second_table)

    def test_split_chunks_preserves_page_marker_for_structured_table(self):
        raw_text = (
            "--- 第 6 页 / 共 10 页 ---\n报告摘要\n"
            "--- 第 7 页 / 共 10 页 ---\n附表 1-1-1 支护结构水平位移观测结果表\nW1 1 2 3\n"
            "--- 第 8 页 / 共 10 页 ---\n附表 2-1-1 周边地面沉降观测结果表\nD1 4 5 6\n"
        )

        chunks = _split_chunks(raw_text, max_chars=8000)

        first_table = next(chunk for chunk in chunks if "附表 1-1-1" in chunk)
        second_table = next(chunk for chunk in chunks if "附表 2-1-1" in chunk)
        self.assertIn("--- 第 7 页 / 共 10 页 ---", first_table)
        self.assertIn("--- 第 8 页 / 共 10 页 ---", second_table)

    def test_split_chunks_does_not_pack_bracketed_tables_together(self):
        raw_text = (
            "【支护结构水平位移】监测数据成果\nS1 1 2 3\n"
            "【地表沉降】监测数据成果\nD1 4 5 6\n"
        )

        chunks = _split_chunks(raw_text, max_chars=8000)

        self.assertEqual(len(chunks), 2)
        self.assertIn("【支护结构水平位移】", chunks[0])
        self.assertNotIn("【地表沉降】", chunks[0])
        self.assertIn("【地表沉降】", chunks[1])

    def test_split_chunks_groups_contiguous_continuation_tables_of_same_family(self):
        raw_text = (
            "附表 9-1-1 CX2号孔支护结构深层水平位移结果表\n第1次\n"
            "附表 9-1-2 CX2号孔支护结构深层水平位移结果表\n第2次\n"
            "附表 9-2-1 CX3号孔支护结构深层水平位移结果表\n第1次\n"
        )

        chunks = _split_chunks(raw_text, max_chars=8000)

        self.assertEqual(len(chunks), 2)
        self.assertIn("附表 9-1-1", chunks[0])
        self.assertIn("附表 9-1-2", chunks[0])
        self.assertNotIn("附表 9-2-1", chunks[0])

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
            patch("src.tools.llm_parser.cfg.LLM_PARSE_MAX_PARALLEL", 1),
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

    def test_parse_chunk_retries_incomplete_structured_result(self):
        from src.tools import llm_parser

        incomplete = {
            "project_name": "项目A",
            "tables": [{
                "monitoring_item": "周边地面沉降",
                "point_count": 2,
                "points": [{"point_id": "D1"}],
            }],
        }
        complete = {
            "project_name": "项目A",
            "tables": [
                {
                    "monitoring_item": "周边地面沉降",
                    "monitor_date": "2026-05-11",
                    "point_count": 2,
                    "points": [{"point_id": "D1"}, {"point_id": "D2"}],
                },
                {
                    "monitoring_item": "周边地面沉降",
                    "monitor_date": "2026-05-12",
                    "point_count": 2,
                    "points": [{"point_id": "D1"}, {"point_id": "D2"}],
                },
            ],
        }
        chunk = (
            "附表 2-1-1 周边地面沉降观测结果表\n"
            "第222次 2026-05-11\n第223次 2026-05-12\n"
        )

        with (
            patch("src.tools.llm_parser.cfg.LLM_PARSE_RESULT_RETRIES", 1, create=True),
            patch(
                "src.tools.llm_parser.call_chat_completion",
                side_effect=[
                    json.dumps(incomplete, ensure_ascii=False),
                    json.dumps(complete, ensure_ascii=False),
                ],
            ) as completion,
        ):
            parsed = llm_parser._parse_chunk_with_llm(0, 1, chunk)

        self.assertEqual(len(parsed["tables"]), 2)
        self.assertEqual(completion.call_count, 2)

    def test_parse_chunk_normalizes_force_reading_layout_from_source_header(self):
        from src.tools import llm_parser

        model_result = {
            "project_name": "项目A",
            "tables": [{
                "monitoring_item": "锚索拉力观测结果",
                "category": "锚索拉力",
                "monitor_date": "2026-05-17",
                "point_count": 2,
                "initial_value_reliable": True,
                "points": [
                    {
                        "point_id": "MS2",
                        "initial_value": 153.0,
                        "current_value": 10.9,
                        "current_change": 1.3,
                        "cumulative_change": 1.8,
                        "change_rate": 1.3,
                    },
                    {
                        "point_id": "MS9-3",
                        "initial_value": 160.0,
                        "current_value": -20.3,
                        "current_change": -25.1,
                        "cumulative_change": -25.0,
                        "change_rate": -25.1,
                    },
                ],
            }],
        }
        chunk = (
            "附表 8-1-2 锚索拉力观测结果表\n2026-05-17\n"
            "本次 (kN) 测值 (kN) 变化速率 (kN/d)\n"
        )

        with patch(
            "src.tools.llm_parser.call_chat_completion",
            return_value=json.dumps(model_result, ensure_ascii=False),
        ):
            parsed = llm_parser._parse_chunk_with_llm(0, 1, chunk)

        table = parsed["tables"][0]
        self.assertFalse(table["initial_value_reliable"])
        self.assertEqual(table["points"][0]["cumulative_change"], 10.9)
        self.assertEqual(table["points"][1]["cumulative_change"], -20.3)

    def test_parse_report_preserves_source_page_and_raw_row_for_each_point(self):
        model_result = {
            "project_name": "项目A",
            "tables": [{
                "monitoring_item": "支护结构水平位移",
                "category": "水平位移",
                "monitor_date": "2026-05-17",
                "point_count": 1,
                "points": [{
                    "point_id": "WY240",
                    "current_change": 0.2,
                    "cumulative_change": 5.7,
                    "change_rate": 0.1,
                }],
            }],
        }
        chunk = (
            "--- 第 7 页 ---\n"
            "附表 1-1-1 支护结构水平位移观测结果表\n"
            "| 测点编号 | 本次变化 | 累计变化 | 变化速率 |\n"
            "| WY240 | 0.2 | 5.7 | 0.1 |\n"
        )

        with (
            patch("src.tools.llm_parser._split_chunks", return_value=[chunk]),
            patch("src.tools.llm_parser.cfg.LLM_PARSE_MAX_PARALLEL", 1),
            patch(
                "src.tools.llm_parser.call_chat_completion",
                return_value=json.dumps(model_result, ensure_ascii=False),
            ),
        ):
            report = parse_report_with_llm(chunk)

        table = report.tables[0]
        point = table.points[0]
        self.assertEqual(table.source_chunk, 1)
        self.assertEqual(table.source_pages, "7")
        self.assertEqual(point.source_page, 7)
        self.assertEqual(point.source_chunk, 1)
        self.assertEqual(point.source_row_text, "| WY240 | 0.2 | 5.7 | 0.1 |")
        self.assertEqual(
            json.loads(point.source_field_map),
            {"current_change": 2, "cumulative_change": 3, "change_rate": 4},
        )

    def test_source_field_map_supports_whitespace_separated_pdf_rows(self):
        from src.tools.llm_parser import _source_field_map

        record = {
            "initial_value": 2.0232,
            "current_value": 2.0289,
            "current_change": -0.1,
            "cumulative_change": 5.7,
            "change_rate": -0.05,
        }

        mapping = json.loads(
            _source_field_map(record, "WY240 2.0232 2.0289 -0.1 5.7 -0.05 正常")
        )

        self.assertEqual(
            mapping,
            {
                "initial_value": 2,
                "current_value": 3,
                "current_change": 4,
                "cumulative_change": 5,
                "change_rate": 6,
            },
        )

    def test_source_provenance_uses_nearby_header_to_disambiguate_equal_values(self):
        from src.tools.llm_parser import _annotate_source_provenance

        parsed = {
            "tables": [{
                "points": [{
                    "point_id": "WY237",
                    "initial_value": 2.4306,
                    "current_value": 2.4331,
                    "current_change": 0.0,
                    "cumulative_change": 2.5,
                    "change_rate": 0.0,
                }],
            }],
        }
        chunk = "\n".join([
            "--- 第 6 页 / 共 22 页 ---",
            "测点编号 初始断面距离 本次断面距离 本次变化量 累计变化量 变化速率 安全状态",
            "(m) (m) (mm) (mm) (mm/d)",
            "WY237 2.4306 2.4331 0.0 2.5 0.00 正常",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        mapping = json.loads(parsed["tables"][0]["points"][0]["_source_field_map"])
        self.assertEqual(mapping["current_change"], 4)
        self.assertEqual(mapping["change_rate"], 6)

    def test_source_provenance_reconstructs_split_header_lines(self):
        from src.tools.llm_parser import _annotate_source_provenance

        point = {
            "point_id": "WY237",
            "initial_value": 2.4306,
            "current_value": 2.4331,
            "current_change": 0.0,
            "cumulative_change": 2.5,
            "change_rate": 0.0,
        }
        parsed = {"tables": [{"points": [point]}]}
        chunk = "\n".join([
            "--- 第 6 页 / 共 22 页 ---",
            "初始断面距离 本次断面距离 本次变化量 累计变化量 变化速率",
            "测点编号 安全状态",
            "(m) (m) (mm) (mm) (mm/d)",
            "WY237 2.4306 2.4331 0.0 2.5 0.00 正常",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        mapping = json.loads(point["_source_field_map"])
        self.assertEqual(mapping["current_change"], 4)
        self.assertEqual(mapping["change_rate"], 6)

    def test_source_provenance_uses_header_from_previous_page_for_continuation(self):
        from src.tools.llm_parser import _annotate_source_provenance

        point = {
            "point_id": "WY244",
            "current_change": 0.0,
            "cumulative_change": 0.7,
            "change_rate": 0.0,
        }
        parsed = {"tables": [{"points": [point]}]}
        chunk = "\n".join([
            "--- 第 7 页 / 共 22 页 ---",
            "初始断面距离 本次断面距离 本次变化量 累计变化量 变化速率",
            "测点编号 安全状态",
            "(m) (m) (mm) (mm) (mm/d)",
            "--- 第 8 页 / 共 22 页 ---",
            "WY244 1.5451 1.5458 0.0 0.7 0.00 正常",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        mapping = json.loads(point["_source_field_map"])
        self.assertEqual(mapping["current_change"], 4)
        self.assertEqual(mapping["change_rate"], 6)

    def test_source_provenance_maps_selected_date_group_in_wide_table(self):
        from src.tools.llm_parser import _annotate_source_provenance

        point = {
            "point_id": "W3",
            "current_change": -0.34,
            "cumulative_change": 4.13,
            "change_rate": -0.34,
        }
        parsed = {"tables": [{
            "monitor_date": "2026-05-12",
            "points": [point],
        }]}
        chunk = "\n".join([
            "--- 第 9 页 / 共 59 页 ---",
            "时间 初始值 第172次 第173次 第174次",
            "(m) 2026-05-11 2026-05-12 2026-05-13 本期 变化速率 报警值 控制值",
            "本次 累计 变化速率 本次 累计 变化速率 本次 累计 变化速率 (mm) (mm/d)",
            "X Y",
            "测点",
            "W3 1938.7844 858.0474 0.15 4.47 0.15 -0.34 4.13 -0.34 0.11 4.24 0.11 -0.07 4 48 60",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        mapping = json.loads(point["_source_field_map"])
        self.assertEqual(mapping["current_change"], 7)
        self.assertEqual(mapping["cumulative_change"], 8)
        self.assertEqual(mapping["change_rate"], 9)

    def test_source_provenance_prefers_row_near_matching_monitor_date(self):
        from src.tools.llm_parser import _annotate_source_provenance

        point = {
            "point_id": "WY1",
            "current_change": 0.2,
            "cumulative_change": 1.8,
            "change_rate": 0.2,
        }
        parsed = {"tables": [{"monitor_date": "2026-05-14", "points": [point]}]}
        chunk = "\n".join([
            "--- 第 7 页 / 共 20 页 ---",
            "报告汇总日期 2026-05-14 2026-05-15 2026-05-16",
            "2026-05-11 2026-05-12 2026-05-13",
            "本次变化量 累计变化量 变化速率 本次变化量 累计变化量 变化速率 本次变化量 累计变化量 变化速率",
            "WY1 0.2 1.8 0.2 0.3 2.1 0.3 0.4 2.5 0.4",
            "--- 第 8 页 / 共 20 页 ---",
            "2026-05-14 2026-05-15 2026-05-16",
            "本次变化量 累计变化量 变化速率 本次变化量 累计变化量 变化速率 本次变化量 累计变化量 变化速率",
            "WY1 0.2 1.8 0.2 0.6 2.4 0.6 0.7 3.1 0.7",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        self.assertEqual(point["_source_page"], 8)

    def test_source_provenance_maps_single_date_wide_continuation(self):
        from src.tools.llm_parser import _annotate_source_provenance

        point = {
            "point_id": "W3",
            "current_change": 0.06,
            "cumulative_change": 4.25,
            "change_rate": 0.06,
        }
        parsed = {"tables": [{"monitor_date": "2026-05-17", "points": [point]}]}
        chunk = "\n".join([
            "--- 第 10 页 / 共 59 页 ---",
            "时间 初始值 第178次",
            "(m) 2026-05-17 本期 变化速率 报警值 控制值",
            "本次 累计 变化速率 (mm) (mm/d)",
            "X Y",
            "测点",
            "W3 1938.7844 858.0474 0.06 4.25 0.06 -0.07 4 48 60",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        mapping = json.loads(point["_source_field_map"])
        self.assertEqual(mapping["current_change"], 4)
        self.assertEqual(mapping["cumulative_change"], 5)
        self.assertEqual(mapping["change_rate"], 6)

    def test_source_provenance_maps_wide_table_with_one_initial_value_column(self):
        from src.tools.llm_parser import _annotate_source_provenance

        point = {
            "point_id": "D1",
            "current_change": -0.17,
            "cumulative_change": -5.76,
            "change_rate": -0.17,
        }
        parsed = {"tables": [{"monitor_date": "2026-05-11", "points": [point]}]}
        chunk = "\n".join([
            "--- 第 12 页 / 共 59 页 ---",
            "时间 第222次 第223次 第224次",
            "初始 本期 变化速率",
            "2026-05-11 2026-05-12 2026-05-13 报警值 控制值",
            "高程 变化量 报警值",
            "(m) 本次 累计 变化速率 本次 累计 变化速率 本次 累计 变化速率 (mm) (mm/d)",
            "测点 (mm) (mm) (mm/d) (mm) (mm) (mm/d) (mm) (mm) (mm/d)",
            "D1 9.2366 -0.17 -5.76 -0.17 -0.02 -5.78 -0.02 0.14 -5.64 0.14 -0.06 3 40 50",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        mapping = json.loads(point["_source_field_map"])
        self.assertEqual(mapping["current_change"], 3)
        self.assertEqual(mapping["cumulative_change"], 4)
        self.assertEqual(mapping["change_rate"], 5)

    def test_source_provenance_maps_force_table_measurement_value_group(self):
        from src.tools.llm_parser import _annotate_source_provenance

        point = {
            "point_id": "Z1",
            "current_change": -40.84,
            "current_value": 1082.05,
            "change_rate": -40.84,
        }
        parsed = {"tables": [{"monitor_date": "2026-05-11", "points": [point]}]}
        chunk = "\n".join([
            "--- 第 27 页 / 共 59 页 ---",
            "时间 第172次 第173次 第174次",
            "初始 本期 变化速率",
            "2026-05-11 2026-05-12 2026-05-13 报警值 控制值",
            "测值 变化量 报警值",
            "(kN) 本次 测值 变化速率 本次 测值 变化速率 本次 测值 变化速率 (kN) (kN/d)",
            "测点 (kN) (kN) (kN/d) (kN) (kN) (kN/d) (kN) (kN) (kN/d)",
            "Z1 0.00 -40.84 1082.05 -40.84 30.84 1112.89 30.84 -45.40 1067.49 -45.40 -31.26 -- 7000 8500",
        ])

        _annotate_source_provenance(chunk, parsed, 0)

        mapping = json.loads(point["_source_field_map"])
        self.assertEqual(mapping["current_change"], 3)
        self.assertEqual(mapping["current_value"], 4)
        self.assertEqual(mapping["change_rate"], 5)

    def test_parse_report_does_not_treat_structured_document_preamble_as_data_table(self):
        summary_chunk = {
            "project_name": "项目A",
            "tables": [{
                "monitoring_item": "支护结构顶部水平位移",
                "category": "水平位移",
                "monitor_date": "2026-05-17",
                "monitor_count": "第35期",
                "point_count": 1,
                "points": [{"point_id": "W22", "current_change": 3.9}],
            }],
        }
        data_chunk = {
            "project_name": "项目A",
            "tables": [{
                "monitoring_item": "支护结构顶部水平位移",
                "category": "水平位移",
                "monitor_date": "2026-05-17",
                "monitor_count": "第178次",
                "point_count": 2,
                "points": [
                    {"point_id": "W1", "current_change": 0.1},
                    {"point_id": "W2", "current_change": 0.2},
                ],
            }],
        }
        chunks = [
            "表7-1 监测结果汇总表\nW22 3.9",
            "附表 1-1-2 支护结构顶部水平位移观测结果表\n2026-05-17",
        ]

        with (
            patch("src.tools.llm_parser._split_chunks", return_value=chunks),
            patch("src.tools.llm_parser.cfg.LLM_PARSE_MAX_PARALLEL", 1),
            patch(
                "src.tools.llm_parser.call_chat_completion",
                side_effect=[
                    json.dumps(summary_chunk, ensure_ascii=False),
                    json.dumps(data_chunk, ensure_ascii=False),
                ],
            ),
        ):
            report = parse_report_with_llm("ignored")

        self.assertEqual(len(report.tables), 1)
        self.assertEqual(report.tables[0].monitor_count, "第178次")

    def test_parse_report_calls_chunks_concurrently_when_configured(self):
        base_chunk = {
            "project_name": "并发项目",
            "monitoring_company": "",
            "report_number": "",
            "monitoring_period": "",
            "monitoring_date": "",
            "interval_days": None,
            "thresholds": [],
            "summary_items": [],
            "tables": [],
            "conclusion": "",
        }
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_completion(*args, **kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return json.dumps(base_chunk, ensure_ascii=False)

        with (
            patch("src.tools.llm_parser._split_chunks", return_value=["chunk-1", "chunk-2", "chunk-3", "chunk-4"]),
            patch("src.tools.llm_parser.cfg.LLM_PARSE_MAX_PARALLEL", 4),
            patch("src.tools.llm_parser.call_chat_completion", side_effect=fake_completion),
        ):
            report = parse_report_with_llm("ignored")

        self.assertGreater(max_active, 1)
        self.assertEqual(report.extraction_diagnostics["llm_chunk_success_count"], 4)

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

    def test_parse_report_skips_chunk_that_parses_to_non_dict(self):
        good_chunk = {
            "project_name": "项目X",
            "monitoring_company": "",
            "report_number": "",
            "monitoring_period": "",
            "monitoring_date": "",
            "interval_days": None,
            "thresholds": [],
            "summary_items": [],
            "conclusion": "",
            "tables": [{
                "monitoring_item": "支护结构顶部水平位移",
                "category": "水平位移",
                "monitor_date": "",
                "monitor_count": "",
                "point_count": 1,
                "equipment_type": "",
                "equipment_model": "",
                "borehole_id": "",
                "borehole_depth": None,
                "table_unit": "mm",
                "initial_value_reliable": True,
                "points": [{
                    "point_id": "S1",
                    "initial_value": 0.0,
                    "previous_value": None,
                    "current_value": 1.0,
                    "current_change": 1.0,
                    "cumulative_change": 1.0,
                    "change_rate": 0.1,
                    "safety_status": "正常",
                }],
                "deep_points": [],
                "statistics": {},
            }],
        }
        with (
            patch("src.tools.llm_parser._split_chunks", return_value=["chunk-1", "chunk-2"]),
            patch("src.tools.llm_parser.cfg.LLM_PARSE_MAX_PARALLEL", 1),
            patch("src.tools.llm_parser.cfg.LLM_PARSE_RESULT_RETRIES", 0),
            patch(
                "src.tools.llm_parser.call_chat_completion",
                side_effect=[json.dumps(good_chunk, ensure_ascii=False), "null"],
            ),
        ):
            report = parse_report_with_llm("ignored")

        self.assertEqual(len(report.tables), 1)
        self.assertEqual(report.project_name, "项目X")

    def test_build_report_robust_to_null_nested_values(self):
        from src.tools.llm_parser import _build_report

        data = {
            "project_name": "X",
            "thresholds": [None, {"item_name": "水平位移", "warning_value": 10}],
            "summary_items": [None],
            "tables": [
                None,
                {
                    "monitoring_item": "支护结构顶部水平位移",
                    "category": "水平位移",
                    "statistics": None,
                    "points": None,
                    "deep_points": None,
                },
                {
                    "monitoring_item": "周边地面沉降",
                    "category": "沉降",
                    "points": [None, {"point_id": "P1", "cumulative_change": 1.0}],
                    "statistics": {},
                },
            ],
        }

        report = _build_report(data)
        items = [table.monitoring_item for table in report.tables]
        self.assertIn("支护结构顶部水平位移", items)
        self.assertIn("周边地面沉降", items)


if __name__ == "__main__":
    unittest.main()
