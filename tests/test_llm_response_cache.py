"""LLM 响应磁盘缓存测试

动机：开发迭代中反复跑同一 PDF（恒大 99 页 = 918s/run）。LLM 调用是瓶颈，
确定性 prompt（temperature ≤ 0.3）的响应可以缓存。

设计：
- 缓存键 = SHA256(model + temperature + max_tokens + json(messages))
- 缓存文件 = <cache_dir>/<sha256>.json，存 {"text": str, "ts": iso8601}
- 命中：直接返回 text，不调 API
- 未命中：调 API，成功才写缓存
- temperature > 0.3 或 cache 关闭时跳过缓存
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class LlmCacheKeyTests(unittest.TestCase):
    """缓存键生成"""

    def test_same_inputs_same_key(self):
        from src.utils.llm_cache import build_cache_key
        msgs = [{"role": "user", "content": "hello"}]
        k1 = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100)
        k2 = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100)
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 64, "应为 SHA256 hex 长度")

    def test_different_messages_different_key(self):
        from src.utils.llm_cache import build_cache_key
        k1 = build_cache_key([{"role": "user", "content": "A"}], model="m", temperature=0.1, max_tokens=100)
        k2 = build_cache_key([{"role": "user", "content": "B"}], model="m", temperature=0.1, max_tokens=100)
        self.assertNotEqual(k1, k2)

    def test_different_model_different_key(self):
        from src.utils.llm_cache import build_cache_key
        msgs = [{"role": "user", "content": "hello"}]
        k1 = build_cache_key(msgs, model="model-A", temperature=0.1, max_tokens=100)
        k2 = build_cache_key(msgs, model="model-B", temperature=0.1, max_tokens=100)
        self.assertNotEqual(k1, k2)

    def test_different_temperature_different_key(self):
        from src.utils.llm_cache import build_cache_key
        msgs = [{"role": "user", "content": "hello"}]
        k1 = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100)
        k2 = build_cache_key(msgs, model="m", temperature=0.2, max_tokens=100)
        self.assertNotEqual(k1, k2)


class LlmCacheStorageTests(unittest.TestCase):
    """磁盘读写"""

    def test_save_and_load_roundtrip(self):
        from src.utils.llm_cache import save_cached_response, load_cached_response
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            key = "abc123"
            # 文本必须 ≥20 字符（rejection of empty/short responses）
            content = "hello LLM response - 长度足够缓存"
            save_cached_response(cache_dir, key, content)
            self.assertEqual(load_cached_response(cache_dir, key), content)

    def test_load_miss_returns_none(self):
        from src.utils.llm_cache import load_cached_response
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(load_cached_response(Path(td), "nonexistent_key"))

    def test_save_creates_directory(self):
        from src.utils.llm_cache import save_cached_response
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td) / "deep" / "nested" / "cache"
            # 文本必须 ≥20 字符
            save_cached_response(cache_dir, "k", "value-长度足够-测试目录创建-cache test ok")
            self.assertTrue((cache_dir / "k.json").exists())

    def test_corrupted_cache_file_returns_none(self):
        from src.utils.llm_cache import load_cached_response
        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            (cache_dir / "bad.json").write_text("not valid json {{{", encoding="utf-8")
            self.assertIsNone(load_cached_response(cache_dir, "bad"))

    def test_short_valid_json_is_cached(self):
        from src.utils.llm_cache import load_cached_response, save_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            content = '{"tables":[]}'
            save_cached_response(cache_dir, "k", content)
            self.assertEqual(load_cached_response(cache_dir, "k"), content)

    def test_short_non_json_still_rejected(self):
        from src.utils.llm_cache import load_cached_response, save_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            save_cached_response(cache_dir, "k", "出错了")
            self.assertIsNone(load_cached_response(cache_dir, "k"))


class LlmClientCacheIntegrationTests(unittest.TestCase):
    """call_chat_completion 与缓存的整合"""

    def setUp(self):
        # 准备临时缓存目录
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.tmpdir.name)
        # 设置环境变量
        os.environ["LLM_CACHE_DIR"] = str(self.cache_dir)
        os.environ["LLM_USE_CACHE"] = "1"

    def tearDown(self):
        self.tmpdir.cleanup()
        os.environ.pop("LLM_CACHE_DIR", None)
        os.environ.pop("LLM_USE_CACHE", None)

    def test_cache_miss_calls_api(self):
        """首次调用 → 没缓存 → 调 API → 写缓存"""
        from src.utils import llm_client
        call_count = {"n": 0}

        class FakeResp:
            class Choice:
                class Message:
                    content = "fake LLM response with longer content for cache test (more than 20 chars)"
                message = Message()
            choices = [Choice()]

        def fake_create(**kw):
            call_count["n"] += 1
            return FakeResp()

        msgs = [{"role": "user", "content": "test prompt"}]
        import src.config as fake_cfg
        with patch("src.utils.llm_client.OpenAI") as MockOpenAI:
            instance = MockOpenAI.return_value
            instance.chat.completions.create = fake_create
            with patch.multiple(
                fake_cfg,
                LLM_API_KEY="k",
                LLM_BASE_URL="x",
                LLM_MODEL="m",
                LLM_TIMEOUT_NORMAL=90,
                LLM_MAX_RETRIES=2,
                LLM_RETRY_BACKOFF_SEC=1,
                create=True,
            ):
                result = llm_client.call_chat_completion(msgs, temperature=0.1)

        self.assertEqual(result, "fake LLM response with longer content for cache test (more than 20 chars)")
        self.assertEqual(call_count["n"], 1)
        # 缓存目录应有一个 .json 文件
        self.assertEqual(len(list(self.cache_dir.glob("*.json"))), 1)

    def test_cache_hit_skips_api(self):
        """二次调用同 prompt → 命中缓存 → 不调 API"""
        from src.utils import llm_client
        from src.utils.llm_cache import build_cache_key, save_cached_response
        msgs = [{"role": "user", "content": "test prompt"}]
        key = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=4000, base_url="x")
        save_cached_response(self.cache_dir, key, "cached response with sufficient length for caching reliably")

        call_count = {"n": 0}

        def fake_create(**kw):
            call_count["n"] += 1
            raise RuntimeError("应该不被调到 — placeholder长度足够")

        import src.config as fake_cfg
        with patch("src.utils.llm_client.OpenAI") as MockOpenAI:
            instance = MockOpenAI.return_value
            instance.chat.completions.create = fake_create
            with patch.multiple(
                fake_cfg,
                LLM_API_KEY="k",
                LLM_BASE_URL="x",
                LLM_MODEL="m",
                LLM_TIMEOUT_NORMAL=90,
                LLM_MAX_RETRIES=2,
                LLM_RETRY_BACKOFF_SEC=1,
                create=True,
            ):
                result = llm_client.call_chat_completion(msgs, temperature=0.1)

        self.assertEqual(result, "cached response with sufficient length for caching reliably")
        self.assertEqual(call_count["n"], 0, "命中缓存时不应调 API")

    def test_high_temperature_bypasses_cache(self):
        """temperature > 0.3 → 不命中也不写缓存（响应非确定）"""
        from src.utils import llm_client
        from src.utils.llm_cache import build_cache_key, save_cached_response

        msgs = [{"role": "user", "content": "high temp"}]
        # 预先写一个 0.1 的缓存
        key_low = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=4000, base_url="x")
        save_cached_response(self.cache_dir, key_low, "low temp cached - 长度足够缓存ok")

        call_count = {"n": 0}

        class FakeResp:
            class Choice:
                class Message:
                    content = "fresh from API with longer content meeting minimum cache length"
                message = Message()
            choices = [Choice()]

        def fake_create(**kw):
            call_count["n"] += 1
            return FakeResp()

        import src.config as fake_cfg
        with patch("src.utils.llm_client.OpenAI") as MockOpenAI:
            instance = MockOpenAI.return_value
            instance.chat.completions.create = fake_create
            with patch.multiple(
                fake_cfg,
                LLM_API_KEY="k",
                LLM_BASE_URL="x",
                LLM_MODEL="m",
                LLM_TIMEOUT_NORMAL=90,
                LLM_MAX_RETRIES=2,
                LLM_RETRY_BACKOFF_SEC=1,
                create=True,
            ):
                result = llm_client.call_chat_completion(msgs, temperature=0.7)

        self.assertEqual(result, "fresh from API with longer content meeting minimum cache length")
        self.assertEqual(call_count["n"], 1, "高温调用应直调 API")

    def test_cache_disabled_via_env(self):
        """LLM_USE_CACHE=0 → 关闭缓存"""
        from src.utils import llm_client
        from src.utils.llm_cache import build_cache_key, save_cached_response

        os.environ["LLM_USE_CACHE"] = "0"
        msgs = [{"role": "user", "content": "no cache"}]
        key = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=4000, base_url="x")
        save_cached_response(self.cache_dir, key, "should not be returned - 长度足够缓存ok")

        class FakeResp:
            class Choice:
                class Message:
                    content = "fresh from API with longer content meeting minimum cache length"
                message = Message()
            choices = [Choice()]

        def fake_create(**kw):
            return FakeResp()

        import src.config as fake_cfg
        with patch("src.utils.llm_client.OpenAI") as MockOpenAI:
            instance = MockOpenAI.return_value
            instance.chat.completions.create = fake_create
            with patch.multiple(
                fake_cfg,
                LLM_API_KEY="k",
                LLM_BASE_URL="x",
                LLM_MODEL="m",
                LLM_TIMEOUT_NORMAL=90,
                LLM_MAX_RETRIES=2,
                LLM_RETRY_BACKOFF_SEC=1,
                create=True,
            ):
                result = llm_client.call_chat_completion(msgs, temperature=0.1)

        self.assertEqual(result, "fresh from API with longer content meeting minimum cache length")


if __name__ == "__main__":
    unittest.main()
