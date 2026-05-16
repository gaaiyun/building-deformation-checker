"""src.utils.dotenv_loader 单元测试 - 验证 .env 解析与加载行为"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.dotenv_loader import _parse_line, load_dotenv


class TestParseLine(unittest.TestCase):
    """逐行解析的边界用例"""

    def test_simple_kv(self):
        self.assertEqual(_parse_line("KEY=value"), ("KEY", "value"))

    def test_double_quoted_value(self):
        self.assertEqual(_parse_line('KEY="with spaces"'), ("KEY", "with spaces"))

    def test_single_quoted_value(self):
        self.assertEqual(_parse_line("KEY='single quoted'"), ("KEY", "single quoted"))

    def test_inline_comment(self):
        self.assertEqual(_parse_line("KEY=val  # comment"), ("KEY", "val"))

    def test_quoted_value_preserves_hash(self):
        # 引号内的 # 不算注释
        self.assertEqual(_parse_line('KEY="val#not-comment"'), ("KEY", "val#not-comment"))

    def test_whole_line_comment(self):
        self.assertIsNone(_parse_line("# whole comment"))

    def test_empty_line(self):
        self.assertIsNone(_parse_line(""))

    def test_whitespace_only(self):
        self.assertIsNone(_parse_line("   \t  "))

    def test_no_equals_sign(self):
        self.assertIsNone(_parse_line("NO_EQUALS"))

    def test_key_with_underscore_and_digits(self):
        self.assertEqual(_parse_line("LLM_API_KEY_2=sk-123"), ("LLM_API_KEY_2", "sk-123"))

    def test_invalid_key_with_special_chars(self):
        self.assertIsNone(_parse_line("KEY-WITH-DASH=value"))

    def test_value_with_spaces_no_quotes(self):
        # 没引号的值，行内 # 算注释，前面的算 value
        self.assertEqual(_parse_line("KEY=foo bar  # comment"), ("KEY", "foo bar"))


class TestLoadDotenv(unittest.TestCase):
    """完整加载流程"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="dotenv_test_"))
        self._env_backup = {
            k: os.environ.pop(k, None)
            for k in ("DOTENV_TEST_KEY_1", "DOTENV_TEST_KEY_2", "DOTENV_TEST_KEY_3")
        }

    def tearDown(self):
        for k, v in self._env_backup.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_loads_simple_env(self):
        path = self.tmp_dir / ".env"
        path.write_text("DOTENV_TEST_KEY_1=value1\nDOTENV_TEST_KEY_2=value2\n", encoding="utf-8")
        n = load_dotenv(path)
        self.assertEqual(n, 2)
        self.assertEqual(os.environ["DOTENV_TEST_KEY_1"], "value1")
        self.assertEqual(os.environ["DOTENV_TEST_KEY_2"], "value2")

    def test_returns_zero_when_file_missing(self):
        n = load_dotenv(self.tmp_dir / "nope.env")
        self.assertEqual(n, 0)

    def test_does_not_override_existing_env_by_default(self):
        os.environ["DOTENV_TEST_KEY_1"] = "from-shell"
        (self.tmp_dir / ".env").write_text("DOTENV_TEST_KEY_1=from-dotenv\n", encoding="utf-8")
        load_dotenv(self.tmp_dir / ".env")
        self.assertEqual(os.environ["DOTENV_TEST_KEY_1"], "from-shell")

    def test_override_flag_overrides_existing(self):
        os.environ["DOTENV_TEST_KEY_1"] = "from-shell"
        (self.tmp_dir / ".env").write_text("DOTENV_TEST_KEY_1=from-dotenv\n", encoding="utf-8")
        load_dotenv(self.tmp_dir / ".env", override=True)
        self.assertEqual(os.environ["DOTENV_TEST_KEY_1"], "from-dotenv")

    def test_skips_comments_and_blank_lines(self):
        content = """\
# 这是注释
DOTENV_TEST_KEY_1=value1

# 另一条注释
DOTENV_TEST_KEY_2=value2
"""
        (self.tmp_dir / ".env").write_text(content, encoding="utf-8")
        n = load_dotenv(self.tmp_dir / ".env")
        self.assertEqual(n, 2)

    def test_utf8_chinese_values(self):
        (self.tmp_dir / ".env").write_text(
            "DOTENV_TEST_KEY_1=中文值\nDOTENV_TEST_KEY_2=英文 mixed 中英\n",
            encoding="utf-8",
        )
        load_dotenv(self.tmp_dir / ".env")
        self.assertEqual(os.environ["DOTENV_TEST_KEY_1"], "中文值")
        self.assertEqual(os.environ["DOTENV_TEST_KEY_2"], "英文 mixed 中英")

    def test_skips_malformed_lines(self):
        content = """\
GOOD_KEY=ok
malformed line without equals
ANOTHER=fine
"""
        # 这里用一个非测试常量 key 名（避免污染）
        path = self.tmp_dir / ".env"
        path.write_text(content, encoding="utf-8")
        # 备份并清除可能存在的环境变量
        backup = {k: os.environ.pop(k, None) for k in ("GOOD_KEY", "ANOTHER")}
        try:
            n = load_dotenv(path)
            self.assertEqual(n, 2)  # 跳过 malformed 那行
            self.assertEqual(os.environ.get("GOOD_KEY"), "ok")
            self.assertEqual(os.environ.get("ANOTHER"), "fine")
        finally:
            for k, v in backup.items():
                if v is not None:
                    os.environ[k] = v
                else:
                    os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
