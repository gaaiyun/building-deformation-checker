"""gui_desktop.settings_store 单元测试

把 _config_dir 重定向到临时目录，验证加载/保存的往返、缺失文件、损坏 JSON、
环境变量回退等场景。完全离线，不触碰真实 APPDATA。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui_desktop import settings_store
from gui_desktop.settings_store import (
    _DEFAULTS,
    _InMemoryKeyring,
    load_settings,
    save_settings,
)


class TestSettingsStore(unittest.TestCase):
    """临时目录 + 内存 keyring 双重隔离：每个用例独立 tmp_dir，从不触碰真实
    Windows Credential Manager / macOS Keychain。"""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="settings_test_"))
        # 备份并清除可能影响测试的环境变量
        self._env_backup = {
            k: os.environ.pop(k, None)
            for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "PADDLE_OCR_TOKEN")
        }
        # 把 _config_dir 重定向到 tmp_dir
        self._cfg_patcher = patch.object(
            settings_store, "_config_dir", return_value=self.tmp_dir
        )
        self._cfg_patcher.start()
        # 把 keyring backend 替换成内存版（防止污染真实 vault）
        self._kr_patcher = patch.object(
            settings_store, "_keyring_backend", _InMemoryKeyring()
        )
        self._kr_patcher.start()

    def tearDown(self):
        self._cfg_patcher.stop()
        self._kr_patcher.stop()
        # 还原环境变量
        for k, v in self._env_backup.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # ── load_settings：默认值 ──────────────────────────────────
    def test_returns_defaults_when_file_missing(self):
        out = load_settings()
        for key, default_val in _DEFAULTS.items():
            self.assertEqual(out[key], default_val, f"key={key} 不匹配默认值")

    def test_returns_dict(self):
        out = load_settings()
        self.assertIsInstance(out, dict)

    def test_defaults_keys_complete(self):
        out = load_settings()
        # 所有 _DEFAULTS 中的 key 都应该出现
        for k in _DEFAULTS:
            self.assertIn(k, out)

    # ── save + load 往返 ──────────────────────────────────────
    def test_save_then_load_roundtrip(self):
        data = dict(_DEFAULTS)
        data["llm_api_key"] = "sk-roundtrip-123"
        data["llm_model"] = "test-model-x"
        data["use_ocr"] = True
        save_settings(data)

        out = load_settings()
        self.assertEqual(out["llm_api_key"], "sk-roundtrip-123")
        self.assertEqual(out["llm_model"], "test-model-x")
        self.assertTrue(out["use_ocr"])

    def test_save_creates_parent_directory(self):
        nested_dir = self.tmp_dir / "deep" / "nested"
        with patch.object(settings_store, "_config_dir", return_value=nested_dir):
            save_settings({"llm_api_key": "x"})
            self.assertTrue((nested_dir / "settings.json").exists())

    def test_save_writes_utf8_json(self):
        # 注：API key 类敏感字段会走 keyring 不再落 JSON；
        #     这里改测非敏感字段的 UTF-8 写入。
        data = dict(_DEFAULTS)
        data["llm_model"] = "中文模型名-测试"
        data["last_pdf_dir"] = "D:/项目/中文路径"
        save_settings(data)

        with (self.tmp_dir / "settings.json").open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded["llm_model"], "中文模型名-测试")
        self.assertEqual(loaded["last_pdf_dir"], "D:/项目/中文路径")

    def test_api_key_stored_in_keyring_not_json(self):
        """安全要求：API key 永远不能写入 JSON 文件，必须存 keyring。"""
        data = dict(_DEFAULTS)
        data["llm_api_key"] = "sk-secret-must-not-leak"
        data["paddle_ocr_token"] = "paddle-tok-456"
        save_settings(data)

        # JSON 文件里不应出现明文密钥
        with (self.tmp_dir / "settings.json").open("r", encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("sk-secret-must-not-leak", raw)
        self.assertNotIn("paddle-tok-456", raw)

        # 但 load_settings 应仍能拿到（从内存 keyring 中读出）
        out = load_settings()
        self.assertEqual(out["llm_api_key"], "sk-secret-must-not-leak")
        self.assertEqual(out["paddle_ocr_token"], "paddle-tok-456")

    # ── 损坏 JSON 处理 ────────────────────────────────────────
    def test_corrupt_json_returns_defaults(self):
        # 写入完全无法解析的内容
        (self.tmp_dir / "settings.json").write_text(
            "{this is not valid JSON @#$%", encoding="utf-8"
        )
        out = load_settings()
        # 应返回默认值，不抛异常
        for k, v in _DEFAULTS.items():
            self.assertEqual(out[k], v)

    def test_empty_file_returns_defaults(self):
        (self.tmp_dir / "settings.json").write_text("", encoding="utf-8")
        out = load_settings()
        self.assertEqual(out["llm_api_key"], _DEFAULTS["llm_api_key"])

    def test_unreadable_file_returns_defaults(self):
        # 写入合法 JSON 但是 list 而非 dict，触发 .items() AttributeError
        (self.tmp_dir / "settings.json").write_text("[1,2,3]", encoding="utf-8")
        out = load_settings()
        # 不应抛异常，应返回默认值
        self.assertIsInstance(out, dict)
        # 由于此用例没有保存过密钥到 keyring，敏感字段应为默认空串
        self.assertEqual(out["llm_api_key"], _DEFAULTS["llm_api_key"])
        self.assertEqual(out["paddle_ocr_token"], _DEFAULTS["paddle_ocr_token"])

    # ── 环境变量回退（仅在配置文件缺失时生效）──────────────────
    def test_env_var_fallback_for_api_key(self):
        os.environ["LLM_API_KEY"] = "env-key-456"
        out = load_settings()
        self.assertEqual(out["llm_api_key"], "env-key-456")

    def test_env_var_fallback_for_base_url(self):
        os.environ["LLM_BASE_URL"] = "https://custom.example.com/v1"
        out = load_settings()
        self.assertEqual(out["llm_base_url"], "https://custom.example.com/v1")

    def test_env_var_fallback_for_model(self):
        os.environ["LLM_MODEL"] = "env-model-z"
        out = load_settings()
        self.assertEqual(out["llm_model"], "env-model-z")

    def test_env_var_fallback_for_paddle_token(self):
        os.environ["PADDLE_OCR_TOKEN"] = "env-paddle-tok"
        out = load_settings()
        self.assertEqual(out["paddle_ocr_token"], "env-paddle-tok")

    def test_env_var_ignored_when_file_exists(self):
        # 文件存在时，环境变量不该覆盖文件中的值
        save_settings({**_DEFAULTS, "llm_api_key": "from-file"})
        os.environ["LLM_API_KEY"] = "from-env"
        out = load_settings()
        self.assertEqual(out["llm_api_key"], "from-file")

    # ── 缺失字段补默认 ────────────────────────────────────────
    def test_missing_fields_filled_from_defaults(self):
        # 故意只保存一两个字段
        partial = {"llm_api_key": "only-this"}
        with (self.tmp_dir / "settings.json").open("w", encoding="utf-8") as f:
            json.dump(partial, f)

        out = load_settings()
        # 保留显式字段
        self.assertEqual(out["llm_api_key"], "only-this")
        # 其它字段应补默认值
        self.assertEqual(out["llm_model"], _DEFAULTS["llm_model"])
        self.assertEqual(out["llm_base_url"], _DEFAULTS["llm_base_url"])
        self.assertEqual(out["output_dir"], _DEFAULTS["output_dir"])
        self.assertEqual(out["llm_timeout_normal"], _DEFAULTS["llm_timeout_normal"])

    def test_extra_unknown_field_preserved(self):
        """如果用户/旧版本写入了未知字段，加载时应保留（不丢失）"""
        data = {**_DEFAULTS, "future_unknown_field": "foo"}
        save_settings(data)
        out = load_settings()
        self.assertEqual(out.get("future_unknown_field"), "foo")


if __name__ == "__main__":
    unittest.main()
