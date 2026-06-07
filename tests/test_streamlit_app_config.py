from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_app_uses_branded_deepseek_and_paddle_defaults():
    text = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "建筑变形监测报告核验台 v2" not in text
    assert "v2 修复版" not in text
    assert "city_safety_iot_logo.png" in text
    assert "https://api.deepseek.com" in text
    assert "deepseek-v4-flash" in text
    assert "deepseek-v4-pro" in text
    assert "PaddleOCR-VL-1.6" in text
    assert "save_settings(_current_settings_payload())" in text
    assert "LLM_USE_CACHE" in text
    assert "cfg_llm_parse_max_parallel" in text
    assert "LLM 分块并发数" in text
    assert "PADDLE_OCR_USE_CACHE" in text
    assert "hashlib.sha256" in text
    assert "output\") / \"streamlit_uploads" in text
    assert 'st.expander("PaddleOCR（可选）", expanded=True)' in text
    assert "cfg_fresh_run" in text
    assert "正在启动后台检查任务" in text
    assert "已开始检查，后台任务运行中" in text
    assert "任务启动失败" in text
    assert "@st.cache_resource" in text
    assert "def _task_registry" in text
    assert "后台任务状态丢失" in text
    assert "if registry[\"tasks\"]" in text
    assert "registry[\"tasks\"][task_id] = task_payload" in text
    assert "thread.start()" in text


def test_streamlit_app_keeps_report_download_exports_available():
    text = (ROOT / "app.py").read_text(encoding="utf-8")

    assert "generate_docx(" in text
    assert "generate_html(" in text
    assert '"下载 Markdown"' in text
    assert '"下载 Word"' in text
    assert '"下载 HTML"' in text
    assert "_检查报告.md" in text
    assert "_检查报告.docx" in text
    assert "_检查报告.html" in text
    assert "mime=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document\"" in text


def test_streamlit_dependency_supports_fragment_api():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "streamlit>=1.37.0" in req


def test_cli_defaults_are_deepseek_compatible(monkeypatch):
    for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)

    spec = importlib.util.spec_from_file_location("bdc_main", ROOT / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bdc_main"] = module
    spec.loader.exec_module(module)

    parser = module._build_parser()
    args = parser.parse_args(["sample.pdf"])
    cfg = module._make_runtime_config(args)

    assert cfg.llm_base_url == "https://api.deepseek.com"
    assert cfg.llm_model == "deepseek-v4-flash"
