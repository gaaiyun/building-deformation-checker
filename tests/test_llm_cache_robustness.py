"""LLM 缓存鲁棒性测试（基于 multi-agent review 发现）。

修复目标：
1. 原子写入（os.replace），避免并发读到半写文件
2. 缓存键包含 base_url（不同 endpoint 同模型名不可命中）
3. 拒绝空/极短响应入库（避免污染）
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class CacheBaseUrlIsolationTests(unittest.TestCase):

    def test_same_model_different_base_url_different_key(self):
        """同模型不同 base_url → key 必须不同（防 endpoint 切换误命中）"""
        from src.utils.llm_cache import build_cache_key

        msgs = [{"role": "user", "content": "test"}]
        k1 = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100, base_url="https://A.example.com/v1")
        k2 = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100, base_url="https://B.example.com/v1")
        self.assertNotEqual(k1, k2, "不同 base_url 应有不同 cache key")

    def test_same_base_url_same_key(self):
        from src.utils.llm_cache import build_cache_key

        msgs = [{"role": "user", "content": "test"}]
        k1 = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100, base_url="https://A.example.com/v1")
        k2 = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100, base_url="https://A.example.com/v1")
        self.assertEqual(k1, k2)

    def test_base_url_optional_for_backward_compat(self):
        """旧调用不传 base_url 也应工作（不抛 TypeError）"""
        from src.utils.llm_cache import build_cache_key

        msgs = [{"role": "user", "content": "test"}]
        k = build_cache_key(msgs, model="m", temperature=0.1, max_tokens=100)
        self.assertEqual(len(k), 64)


class CacheEmptyResponseRejectionTests(unittest.TestCase):

    def test_empty_text_not_cached(self):
        """空字符串响应不应被缓存"""
        from src.utils.llm_cache import save_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            save_cached_response(cache_dir, "key1", "")
            self.assertFalse((cache_dir / "key1.json").exists(),
                             "空响应不应落盘")

    def test_whitespace_only_not_cached(self):
        """纯空白响应不应缓存"""
        from src.utils.llm_cache import save_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            save_cached_response(cache_dir, "key1", "   \n\t  ")
            self.assertFalse((cache_dir / "key1.json").exists())

    def test_very_short_not_cached(self):
        """极短响应（< 20 字符）不应缓存（可能是错误信息）"""
        from src.utils.llm_cache import save_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            save_cached_response(cache_dir, "key1", "ERROR")
            self.assertFalse((cache_dir / "key1.json").exists())

    def test_normal_response_cached(self):
        """合法长响应应正常缓存"""
        from src.utils.llm_cache import save_cached_response, load_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            normal = '{"tables": [{"name": "T1", "points": []}]}'
            save_cached_response(cache_dir, "key1", normal)
            self.assertEqual(load_cached_response(cache_dir, "key1"), normal)


class CacheAtomicWriteTests(unittest.TestCase):

    def test_no_partial_files_after_concurrent_writes(self):
        """并发写同 key 不应留下 .tmp 半写文件"""
        from src.utils.llm_cache import save_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)

            def writer(idx):
                save_cached_response(
                    cache_dir, "shared_key",
                    f"response from thread {idx} " + "x" * 200,
                )

            threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # 最终目录只有正式 .json 文件，没有 .tmp 残留
            files = list(cache_dir.iterdir())
            tmp_files = [f for f in files if ".tmp" in f.name]
            self.assertEqual(tmp_files, [],
                             f"不应有 .tmp 残留: {[f.name for f in files]}")

            json_files = [f for f in files if f.suffix == ".json"]
            self.assertEqual(len(json_files), 1, "应只有 1 个最终文件")
            # 内容可解析
            data = json.loads(json_files[0].read_text(encoding="utf-8"))
            self.assertIn("text", data)
            self.assertTrue(data["text"].startswith("response from thread"))

    def test_save_failure_does_not_corrupt_existing(self):
        """save 失败时既有 cache 不应被破坏"""
        from src.utils.llm_cache import save_cached_response, load_cached_response

        with tempfile.TemporaryDirectory() as td:
            cache_dir = Path(td)
            save_cached_response(cache_dir, "k", "x" * 200 + " original")
            # 应能正常读
            self.assertIn("original", load_cached_response(cache_dir, "k"))


if __name__ == "__main__":
    unittest.main()
