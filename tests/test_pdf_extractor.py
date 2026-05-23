import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.config as config
import src.tools.pdf_extractor as pdf_extractor

_clean_ocr_markdown = pdf_extractor._clean_ocr_markdown


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class PdfExtractorTests(unittest.TestCase):
    def test_clean_ocr_markdown_compresses_html_heavy_tables(self):
        markdown = """
<div style='font-size:14px'>监测成果表</div>
<div style='font-size:12px'>第 1 页</div>
<table style='width:100%'>
  <tr>
    <td style='text-align:center;word-wrap:break-word;'>点号</td>
    <td style='text-align:center;word-wrap:break-word;'>本次测值</td>
    <td style='text-align:center;word-wrap:break-word;'>累计变化</td>
  </tr>
  <tr>
    <td>S1</td>
    <td>12.30</td>
    <td>0.50</td>
  </tr>
</table>
<div><img src='x.jpg' /></div>
        """

        clean_text, stats = _clean_ocr_markdown(markdown)

        self.assertIn("监测成果表", clean_text)
        self.assertIn("| 点号 | 本次测值 | 累计变化 |", clean_text)
        self.assertIn("| S1 | 12.30 | 0.50 |", clean_text)
        self.assertLess(len(clean_text), len(markdown))
        self.assertEqual(stats["table_count"], 1)
        self.assertGreater(stats["markup_ratio"], 0.5)

    def test_clean_ocr_markdown_drops_chart_axis_noise(self):
        markdown = """
<div>监测数据成果曲线图</div>
<table>
  <tr><td>10-0-10-20-30-40-50</td><td>2024-03-01</td><td>2024-03-26</td></tr>
</table>
<div>【支护结构顶部水平位移】监测数据成果表</div>
<table>
  <tr><td colspan="3">监测数据成果曲线图</td></tr>
  <tr><td>18-16-14-12-10-8-6-4-2-0</td><td>2024-03-08</td><td>2024-03-26</td></tr>
  <tr><td>![](images/chart.jpg)</td><td></td><td></td></tr>
  <tr><td>测点编号</td><td>累计变化量(mm)</td><td>变化速率(mm/d)</td></tr>
  <tr><td>S7</td><td>13.2</td><td>0.82</td></tr>
</table>
        """

        clean_text, stats = _clean_ocr_markdown(markdown)

        self.assertNotIn("10-0-10-20-30-40-50", clean_text)
        self.assertNotIn("18-16-14-12-10-8-6-4-2-0", clean_text)
        self.assertNotIn("![](images/chart.jpg)", clean_text)
        self.assertIn("【支护结构顶部水平位移】监测数据成果表", clean_text)
        self.assertIn("| 测点编号 | 累计变化量(mm) | 变化速率(mm/d) |", clean_text)
        self.assertIn("| S7 | 13.2 | 0.82 |", clean_text)
        self.assertEqual(stats["table_count"], 1)
        self.assertEqual(stats["dropped_table_count"], 1)

    def test_paddle_async_payload_is_whitelisted_and_jsonl_is_collected(self):
        post_calls = []

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            post_calls.append({
                "url": url,
                "headers": headers,
                "data": data,
                "files": files,
                "timeout": timeout,
            })
            return FakeResponse({"code": 0, "data": {"jobId": "ocrjob-1"}})

        jsonl_text = "\n".join([
            json.dumps({
                "result": {
                    "layoutParsingResults": [
                        {"markdown": {"text": "<table><tr><td>A</td></tr></table>"}}
                    ]
                }
            }),
            json.dumps({
                "result": {
                    "layoutParsingResults": [
                        {"markdown": {"text": "plain page", "images": {"x": "url"}}}
                    ]
                }
            }),
        ])

        def fake_get(url, headers=None, timeout=None):
            if url == "https://example.test/jobs/ocrjob-1":
                return FakeResponse({
                    "code": 0,
                    "data": {
                        "state": "done",
                        "extractProgress": {"totalPages": 2, "extractedPages": 2},
                        "resultUrl": {"jsonUrl": "https://example.test/result.jsonl"},
                    },
                })
            if url == "https://example.test/result.jsonl":
                return FakeResponse(text=jsonl_text)
            raise AssertionError(f"unexpected GET {url}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            with (
                patch.object(pdf_extractor, "PADDLE_OCR_TOKEN", "test-token"),
                patch.object(pdf_extractor, "PADDLE_OCR_ASYNC_JOB_URL", "https://example.test/jobs"),
                patch.object(pdf_extractor, "PADDLE_OCR_MODEL", "PaddleOCR-VL-1.5"),
                patch.object(pdf_extractor, "PADDLE_OCR_POLL_INTERVAL_SEC", 0),
                patch.object(pdf_extractor, "PADDLE_OCR_POLL_TIMEOUT_SEC", 5),
                patch.object(pdf_extractor.requests, "post", side_effect=fake_post),
                patch.object(pdf_extractor.requests, "get", side_effect=fake_get),
            ):
                result = pdf_extractor._call_paddle_ocr_async(
                    str(pdf_path),
                    {
                        "useDocOrientationClassify": False,
                        "useDocUnwarping": False,
                        "useChartRecognition": False,
                        "useSealRecognition": True,
                    },
                )

        self.assertEqual(post_calls[0]["url"], "https://example.test/jobs")
        self.assertEqual(post_calls[0]["headers"]["Authorization"], "bearer test-token")
        self.assertEqual(post_calls[0]["data"]["model"], "PaddleOCR-VL-1.5")
        self.assertEqual(
            json.loads(post_calls[0]["data"]["optionalPayload"]),
            {
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useChartRecognition": False,
            },
        )
        self.assertEqual(len(result["layoutParsingResults"]), 2)
        self.assertEqual(result["layoutParsingResults"][0]["outputImages"], {})
        self.assertEqual(result["_metadata"]["api"], "async")
        self.assertEqual(result["_metadata"]["jobId"], "ocrjob-1")

    def test_paddle_async_failure_can_fall_back_to_legacy(self):
        legacy_result = {"layoutParsingResults": [{"markdown": {"text": "legacy"}}]}
        with (
            # token 必须非空，否则 _call_paddle_ocr 早期退出（见 test_paddle_ocr_no_token.py）
            patch.object(pdf_extractor, "PADDLE_OCR_TOKEN", "fake_token"),
            patch.object(pdf_extractor, "PADDLE_OCR_USE_ASYNC", True),
            patch.object(pdf_extractor, "PADDLE_OCR_ENABLE_LEGACY_FALLBACK", True),
            patch.object(pdf_extractor, "_call_paddle_ocr_async", side_effect=RuntimeError("async down")),
            patch.object(pdf_extractor, "_call_paddle_ocr_legacy", return_value=legacy_result),
        ):
            self.assertIs(pdf_extractor._call_paddle_ocr("x.pdf", {}), legacy_result)

    def test_paddle_debug_cache_skips_remote_call_when_fingerprint_matches(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdf_path = Path(tmp_dir) / "sample.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            cache_dir = Path(tmp_dir) / "ocr_debug"
            page_stats = [{
                "page": 1,
                "raw_chars": 12,
                "clean_chars": 11,
                "plain_chars": 11,
                "markup_chars": 1,
                "markup_ratio": 0.1,
                "line_count": 1,
                "table_count": 0,
                "dropped_table_count": 0,
                "table_rows": 0,
                "table_cells": 0,
                "chart_noise_lines_removed": 0,
                "markdown_images": 0,
                "output_images": 0,
            }]
            pdf_extractor._write_debug_artifacts(
                str(cache_dir),
                raw_pages=["raw markdown"],
                clean_pages=["clean page"],
                page_stats=page_stats,
                request_profile={
                    "selected_profile": "table",
                    "profile_fingerprint": pdf_extractor._profile_fingerprint(
                        pdf_extractor.PADDLE_TABLE_PROFILE
                    ),
                    "ocr_cleaner_version": pdf_extractor.OCR_CLEANER_VERSION,
                    "paddle_api": "async",
                    "paddle_model": "PaddleOCR-VL-1.5",
                    "pdf_fingerprint": pdf_extractor._pdf_fingerprint(str(pdf_path)),
                },
                selected_profile="table",
            )

            with (
                patch.object(pdf_extractor, "PADDLE_OCR_USE_CACHE", True),
                patch.object(pdf_extractor, "_call_paddle_ocr", side_effect=AssertionError("remote call")),
            ):
                result = pdf_extractor._extract_with_paddle_profile(
                    str(pdf_path),
                    "table",
                    pdf_extractor.PADDLE_TABLE_PROFILE,
                    str(cache_dir),
                )

        self.assertEqual(result.pages, ["clean page"])
        self.assertTrue(result.diagnostics["ocr_cache_hit"])
        self.assertEqual(result.diagnostics["page_count"], 1)

    def test_minimax_models_are_available(self):
        self.assertIn("MiniMax-M2.7", config.AVAILABLE_MODELS)
        self.assertIn("MiniMax-M2.7-highspeed", config.AVAILABLE_MODELS)


if __name__ == "__main__":
    unittest.main()
