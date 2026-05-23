"""PaddleOCR token 缺失时的早期退出测试。

旧行为：
- PADDLE_OCR_TOKEN 空字符串
- _call_paddle_ocr_async() 抛 ValueError "PADDLE_OCR_TOKEN is required..."
- fallback 到 _call_paddle_ocr_legacy()，发送请求得到 401 Unauthorized
- 多余的 HTTP 调用 + 嘈杂日志

新行为：
- token 空时直接 raise ValueError，不尝试 legacy
- 调用方（extract_pdf）应能继续 fallback 到 pdfplumber
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class PaddleOcrNoTokenTests(unittest.TestCase):

    def test_no_token_skips_legacy_fallback(self):
        """token 空时不应再尝试 legacy API"""
        import src.tools.pdf_extractor as pe

        with patch.object(pe, "PADDLE_OCR_TOKEN", ""):
            with patch.object(pe, "_call_paddle_ocr_legacy") as mock_legacy:
                with self.assertRaises(ValueError) as ctx:
                    pe._call_paddle_ocr("dummy.pdf", {})
                self.assertIn("PADDLE_OCR_TOKEN", str(ctx.exception))
                mock_legacy.assert_not_called()

    def test_no_token_async_disabled_still_raises_early(self):
        """async 关闭 + 无 token → 也应早期失败"""
        import src.tools.pdf_extractor as pe

        with patch.object(pe, "PADDLE_OCR_TOKEN", ""):
            with patch.object(pe, "PADDLE_OCR_USE_ASYNC", False):
                with patch.object(pe, "_call_paddle_ocr_legacy") as mock_legacy:
                    with self.assertRaises(ValueError) as ctx:
                        pe._call_paddle_ocr("dummy.pdf", {})
                    self.assertIn("PADDLE_OCR_TOKEN", str(ctx.exception))
                    mock_legacy.assert_not_called()

    def test_valid_token_still_calls_async(self):
        """有 token 时正常调用 async（mock 不实际请求）"""
        import src.tools.pdf_extractor as pe

        with patch.object(pe, "PADDLE_OCR_TOKEN", "fake_token_123"):
            with patch.object(pe, "_call_paddle_ocr_async") as mock_async:
                mock_async.return_value = {"layoutParsingResults": []}
                result = pe._call_paddle_ocr("dummy.pdf", {})
                mock_async.assert_called_once()
                self.assertIn("layoutParsingResults", result)


if __name__ == "__main__":
    unittest.main()
