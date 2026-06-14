"""建筑变形监测报告检查智能体 — Streamlit Web UI

稳态要点：
1. 全状态走 st.session_state，浏览器 tab 切换 / 重连不丢
2. 后台 threading.Thread 跑流水线，主脚本 rerun 不会杀任务
3. st.download_button 触发 rerun 后界面保持完成态，可继续导出/换 PDF
"""

from __future__ import annotations

import io
import hashlib
import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Optional

# .env 加载必须在导入 src.* 之前，让全局配置读到环境变量
from src.utils.dotenv_loader import load_dotenv  # noqa: E402
load_dotenv()

import streamlit as st

import src.config as cfg
from gui_desktop.settings_store import load_settings, save_settings
from src.core import PipelineResult, RuntimeConfig, run_pipeline
from src.tools.export_formats import generate_docx, generate_html, generate_intermediate_xlsx
from src.tools.extraction_quality import append_issue_source_hint


APP_TITLE = "建筑变形监测报告核验台"
APP_SUBTITLE = "城安物联 · PDF 监测报告智能核验"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
APP_LOGO_PATH = ASSETS_DIR / "city_safety_iot_logo.png"
APP_ICON_PATH = ASSETS_DIR / "city_safety_iot_icon.png"

LLM_PROVIDER_PRESETS = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
    },
    "MiniMax": {
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M2.7",
        "models": ["MiniMax-M2.7", "MiniMax-M2.7-highspeed"],
    },
    "自定义 OpenAI 兼容": {
        "base_url": "",
        "model": "",
        "models": [],
    },
}

LLM_MODEL_OPTIONS = list(dict.fromkeys([
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-chat",
    "deepseek-reasoner",
    *getattr(cfg, "AVAILABLE_MODELS", []),
]))
CUSTOM_MODEL_LABEL = "自定义模型 ID"

PADDLE_MODEL_OPTIONS = [
    "PaddleOCR-VL-1.6",
    "PaddleOCR-VL-1.5",
    "PaddleOCR-VL",
    "PP-StructureV3",
    "PP-OCRv5",
]
LEGACY_PADDLE_DEFAULT_MODELS = {"PaddleOCR-VL-1.5", "PaddleOCR-VL"}


def _streamlit_supports_width_stretch() -> bool:
    match = re.match(r"^(\d+)\.(\d+)", getattr(st, "__version__", "0.0"))
    if not match:
        return False
    major, minor = (int(match.group(1)), int(match.group(2)))
    return (major, minor) >= (1, 55)


def _stretch_kwargs() -> dict[str, object]:
    if _streamlit_supports_width_stretch():
        return {"width": "stretch"}
    return {"use_container_width": True}


# ── 日志：通过 list 串接到 session_state ──────────────────────
class _ListLogHandler(logging.Handler):
    def __init__(self, target_list: list):
        super().__init__()
        self._target = target_list

    def emit(self, record):
        try:
            self._target.append(self.format(record))
        except Exception:
            self.handleError(record)


# ── 全局后台任务注册表（进程内单例，活在所有 rerun 之间）──────
# Streamlit 会反复执行脚本文件，普通模块级 dict 在部分版本/启动方式下会被重建。
# cache_resource 返回同一个 Python 对象，用来保存 thread 句柄和共享锁。
@st.cache_resource(show_spinner=False)
def _task_registry() -> dict[str, object]:
    return {"tasks": {}, "lock": threading.Lock()}


def _get_task(task_id: str) -> Optional[dict]:
    registry = _task_registry()
    with registry["lock"]:
        return registry["tasks"].get(task_id)


def _delete_task(task_id: str) -> None:
    registry = _task_registry()
    with registry["lock"]:
        registry["tasks"].pop(task_id, None)


def _has_running_tasks() -> bool:
    registry = _task_registry()
    with registry["lock"]:
        return bool(registry["tasks"])


# ── Session State 初始化 ─────────────────────────────────────
def _init_state() -> None:
    ss = st.session_state
    if "settings_loaded" not in ss:
        saved = load_settings()
        ss.cfg_llm_base_url = saved.get("llm_base_url", cfg.LLM_BASE_URL)
        ss.cfg_llm_api_key = saved.get("llm_api_key", cfg.LLM_API_KEY)
        ss.cfg_llm_model = saved.get("llm_model", cfg.LLM_MODEL)
        ss.cfg_paddle_token = saved.get("paddle_ocr_token", cfg.PADDLE_OCR_TOKEN)
        ss.cfg_paddle_model = _normalize_paddle_model(saved.get("paddle_ocr_model", cfg.PADDLE_OCR_MODEL))
        ss.cfg_paddle_cache = bool(saved.get("paddle_ocr_use_cache", True))
        ss.cfg_paddle_async = bool(saved.get("paddle_ocr_use_async", True))
        ss.cfg_skip_self_verify = bool(saved.get("skip_self_verify", False))
        ss.cfg_skip_ai_review = bool(saved.get("skip_ai_review", False))
        ss.cfg_ocr_mode = _ocr_mode_from_settings(saved)
        ss.cfg_llm_provider = _infer_llm_provider(ss.cfg_llm_base_url)
        ss.cfg_llm_model_choice = (
            ss.cfg_llm_model if ss.cfg_llm_model in LLM_MODEL_OPTIONS else CUSTOM_MODEL_LABEL
        )
        ss.cfg_paddle_model_choice = (
            ss.cfg_paddle_model if ss.cfg_paddle_model in PADDLE_MODEL_OPTIONS else PADDLE_MODEL_OPTIONS[0]
        )
        ss.cfg_llm_cache = bool(saved.get(
            "llm_use_cache",
            os.environ.get("LLM_USE_CACHE", "1").lower() not in {"0", "false", "no", "off"},
        ))
        ss.cfg_llm_parse_max_parallel = int(saved.get(
            "llm_parse_max_parallel",
            getattr(cfg, "LLM_PARSE_MAX_PARALLEL", 4),
        ) or 4)
        ss.cfg_fresh_run = False
        ss.settings_loaded = True
    if "task_id" not in ss:
        ss.task_id = None
    if "task_state" not in ss:
        ss.task_state = "idle"  # idle / running / done / failed / cancelled
    if "result" not in ss:
        ss.result = None
    if "pdf_path" not in ss:
        ss.pdf_path = None
    if "pdf_name" not in ss:
        ss.pdf_name = None
    if "pdf_signature" not in ss:
        ss.pdf_signature = None
    if "log_lines" not in ss:
        ss.log_lines = []
    if "progress" not in ss:
        ss.progress = {"step_id": "", "label": "", "percent": 0, "detail": ""}


def _infer_llm_provider(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if base == "https://api.deepseek.com":
        return "DeepSeek"
    if base == "https://api.minimaxi.com/v1":
        return "MiniMax"
    return "自定义 OpenAI 兼容"


def _normalize_paddle_model(model: str | None) -> str:
    model = (model or "").strip()
    if not model or model in LEGACY_PADDLE_DEFAULT_MODELS:
        return "PaddleOCR-VL-1.6"
    return model


def _ocr_mode_from_settings(settings: dict) -> str:
    if settings.get("use_ocr"):
        return "强制 PaddleOCR"
    if settings.get("prefer_ocr"):
        return "优先 PaddleOCR"
    return "优先 pdfplumber"


def _apply_llm_provider() -> None:
    provider = st.session_state.get("cfg_llm_provider", "DeepSeek")
    preset = LLM_PROVIDER_PRESETS.get(provider, {})
    if preset.get("base_url"):
        st.session_state.cfg_llm_base_url = preset["base_url"]
    if preset.get("model"):
        st.session_state.cfg_llm_model = preset["model"]
        st.session_state.cfg_llm_model_choice = (
            preset["model"] if preset["model"] in LLM_MODEL_OPTIONS else CUSTOM_MODEL_LABEL
        )


def _apply_llm_model_choice() -> None:
    choice = st.session_state.get("cfg_llm_model_choice", CUSTOM_MODEL_LABEL)
    if choice != CUSTOM_MODEL_LABEL:
        st.session_state.cfg_llm_model = choice


def _apply_paddle_model_choice() -> None:
    choice = st.session_state.get("cfg_paddle_model_choice", PADDLE_MODEL_OPTIONS[0])
    st.session_state.cfg_paddle_model = choice


# ── 启动后台任务 ─────────────────────────────────────────────
def _start_pipeline(cfg: RuntimeConfig, *, llm_use_cache: bool) -> str:
    task_id = uuid.uuid4().hex
    progress_box = {"step_id": "init", "label": "排队中", "percent": 0, "detail": ""}
    logs: list[str] = []
    result_box: dict[str, Optional[PipelineResult]] = {"result": None}
    cancel_event = threading.Event()

    def progress_callback(step_id: str, label: str, percent: int, detail: str) -> None:
        progress_box["step_id"] = step_id
        progress_box["label"] = label
        progress_box["percent"] = percent
        progress_box["detail"] = detail

    def worker():
        old_llm_cache = os.environ.get("LLM_USE_CACHE")
        old_paddle_cache = os.environ.get("PADDLE_OCR_USE_CACHE")
        os.environ["LLM_USE_CACHE"] = "1" if llm_use_cache else "0"
        os.environ["PADDLE_OCR_USE_CACHE"] = "1" if cfg.paddle_ocr_use_cache else "0"
        log_handler = _ListLogHandler(logs)
        log_handler.setLevel(logging.INFO)
        log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
        root = logging.getLogger()
        root.addHandler(log_handler)
        try:
            result_box["result"] = run_pipeline(
                cfg, progress_callback=progress_callback, cancel_event=cancel_event
            )
        except Exception as exc:
            logging.getLogger(__name__).exception("后台流水线异常")
            r = PipelineResult()
            r.error_message = f"{type(exc).__name__}: {exc}"
            result_box["result"] = r
        finally:
            root.removeHandler(log_handler)
            if old_llm_cache is None:
                os.environ.pop("LLM_USE_CACHE", None)
            else:
                os.environ["LLM_USE_CACHE"] = old_llm_cache
            if old_paddle_cache is None:
                os.environ.pop("PADDLE_OCR_USE_CACHE", None)
            else:
                os.environ["PADDLE_OCR_USE_CACHE"] = old_paddle_cache

    thread = threading.Thread(target=worker, daemon=True, name=f"pipeline-{task_id[:8]}")
    task_payload = {
        "thread": thread,
        "progress": progress_box,
        "logs": logs,
        "result_box": result_box,
        "cancel_event": cancel_event,
        "started_at": time.time(),
        "llm_use_cache": llm_use_cache,
    }
    registry = _task_registry()
    with registry["lock"]:
        if registry["tasks"]:
            raise RuntimeError("当前进程已有任务运行。为避免不同用户配置互相覆盖，请等待当前任务结束。")
        registry["tasks"][task_id] = task_payload
    try:
        thread.start()
    except Exception:
        _delete_task(task_id)
        raise
    return task_id


def _cancel_current_task() -> None:
    tid = st.session_state.task_id
    if not tid:
        return
    task = _get_task(tid)
    if task:
        task["cancel_event"].set()


def _uploads_dir() -> Path:
    path = Path("output") / "streamlit_uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _store_uploaded_pdf(uploaded_file) -> None:
    data = uploaded_file.getvalue()
    signature = hashlib.sha256(data).hexdigest()
    if (
        st.session_state.pdf_path
        and st.session_state.pdf_name == uploaded_file.name
        and st.session_state.get("pdf_signature") == signature
    ):
        return

    old_path = st.session_state.get("pdf_path")
    if old_path:
        try:
            old = Path(old_path)
            if old.exists() and old.parent == _uploads_dir():
                old.unlink()
        except Exception:
            pass

    safe_suffix = Path(uploaded_file.name).suffix or ".pdf"
    tmp_path = _uploads_dir() / f"{uuid.uuid4().hex}{safe_suffix}"
    tmp_path.write_bytes(data)
    st.session_state.pdf_path = str(tmp_path)
    st.session_state.pdf_name = uploaded_file.name
    st.session_state.pdf_signature = signature


def _current_settings_payload() -> dict:
    ocr_mode = st.session_state.get("cfg_ocr_mode", "优先 pdfplumber")
    return {
        "llm_api_key": st.session_state.get("cfg_llm_api_key", ""),
        "llm_base_url": st.session_state.get("cfg_llm_base_url", "https://api.deepseek.com"),
        "llm_model": st.session_state.get("cfg_llm_model", "deepseek-v4-flash"),
        "paddle_ocr_token": st.session_state.get("cfg_paddle_token", ""),
        "paddle_ocr_model": st.session_state.get("cfg_paddle_model", "PaddleOCR-VL-1.6"),
        "paddle_ocr_use_async": bool(st.session_state.get("cfg_paddle_async", True)),
        "paddle_ocr_use_cache": bool(st.session_state.get("cfg_paddle_cache", True)),
        "use_ocr": ocr_mode == "强制 PaddleOCR",
        "prefer_ocr": ocr_mode == "优先 PaddleOCR",
        "skip_self_verify": bool(st.session_state.get("cfg_skip_self_verify", False)),
        "skip_ai_review": bool(st.session_state.get("cfg_skip_ai_review", False)),
        "llm_use_cache": bool(st.session_state.get("cfg_llm_cache", True)),
        "llm_parse_max_parallel": int(st.session_state.get("cfg_llm_parse_max_parallel", 4) or 4),
    }


# ── UI 辅助 ──────────────────────────────────────────────────
def _render_log_tail(container, lines: list[str], max_lines: int = 12) -> None:
    if not lines:
        container.caption("等待运行日志...")
        return
    tail = "\n".join(lines[-max_lines:])
    container.code(tail, language="text")


def _render_issues_section(title: str, issues: list) -> None:
    if not issues:
        st.success(f"{title} - 全部通过")
        return

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos = [i for i in issues if i.severity == "info"]

    cols = st.columns(3)
    cols[0].metric("错误", len(errors))
    cols[1].metric("警告", len(warnings))
    cols[2].metric("提示", len(infos))

    grouped = defaultdict(list)
    for issue in issues:
        grouped[issue.table_name].append(issue)

    for table_name, table_issues in grouped.items():
        err_count = sum(1 for i in table_issues if i.severity == "error")
        warn_count = sum(1 for i in table_issues if i.severity == "warning")
        badge_parts = []
        if err_count:
            badge_parts.append(f"E{err_count}")
        if warn_count:
            badge_parts.append(f"W{warn_count}")
        badge = f"[{' / '.join(badge_parts)}]" if badge_parts else ""

        with st.expander(f"**{table_name}** {badge}", expanded=bool(err_count)):
            for issue in table_issues:
                message = append_issue_source_hint(issue.message, issue.suspected_source)
                if issue.severity == "error":
                    st.error(f"**{issue.point_id}** | {issue.field_name}: {message}")
                elif issue.severity == "warning":
                    st.warning(f"**{issue.point_id}** | {issue.field_name}: {message}")
                else:
                    st.info(message)


def _render_extraction_diagnostics(report) -> None:
    diagnostics = getattr(report, "extraction_diagnostics", None) or {}
    if not diagnostics:
        return
    st.markdown("### 提取诊断")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("原始字符", f"{diagnostics.get('raw_chars', 0):,}")
    c2.metric("清洗后字符", f"{diagnostics.get('clean_chars', 0):,}")
    c3.metric("压缩率", f"{diagnostics.get('compression_ratio', 0.0):.1%}")
    c4.metric("异常页", f"{len(diagnostics.get('high_markup_pages', []))}")
    c5.metric("异常表", f"{diagnostics.get('abnormal_table_count', 0)}")
    method = diagnostics.get("method", "unknown")
    profile = diagnostics.get("selected_profile", "")
    st.caption(f"提取方式: {method}" + (f" ({profile})" if profile else ""))


def _render_analysis_plan(plan: list[dict]) -> None:
    if not plan:
        st.info("未生成分析计划")
        return
    for p in plan:
        notes = " ⚠️ " + "; ".join(p["special_notes"]) if p.get("special_notes") else ""
        with st.expander(f"Table {p.get('table_index', '?')}: {p.get('table_name', '?')}{notes}",
                         expanded=bool(p.get('special_notes'))):
            cols = st.columns(3)
            cols[0].markdown(f"**类别**: {p.get('category', '-')}")
            cols[1].markdown(f"**测点**: {p.get('point_count', 0)}")
            cols[2].markdown(f"**单位**: {p.get('unit', '-')}")
            for m in p.get("verification_methods", []):
                st.code(f"{m['name']}: {m['formula']}  (tol={m.get('tolerance', '-')}, severity={m.get('severity', '-')})",
                        language=None)


# ─── Streamlit 主流程 ────────────────────────────────────────
st.set_page_config(
    page_title=APP_TITLE,
    layout="wide",
    initial_sidebar_state="expanded",
)

_init_state()

st.markdown("""
<style>
.stApp { background: #f4f7fb; }
.block-container { padding-top: 1.4rem; }
.app-hero { background: #ffffff; border: 1px solid #d8dee9; border-radius: 12px;
            padding: 18px 22px; margin-bottom: 16px;
            box-shadow: 0 8px 24px rgba(15, 23, 42, 0.05); }
.app-hero h1 { margin: 0; font-size: 28px; color: #0f2f5f; }
.app-hero p { margin: 6px 0 0 0; color: #4b5563; }
.app-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
.app-kpi { border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px 12px; background: #f8fafc; }
.app-kpi b { display:block; color:#0f2f5f; }
.app-kpi span { color:#6b7280; font-size: 12px; }
section[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #e5e7eb; }
div[data-testid="stButton"] > button[kind="primary"] {
  background: #0b5cab;
  border-color: #0b5cab;
  color: #ffffff;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
  background: #083f86;
  border-color: #083f86;
  color: #ffffff;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="app-hero">
  <h1>建筑变形监测报告核验台</h1>
  <p>支持后台运行、实时进度、日志追踪、AI 复核，以及 Markdown / Word / HTML / Excel 中间层下载。</p>
  <div class="app-kpis">
    <div class="app-kpi"><b>OpenAI 兼容</b><span>DeepSeek / MiniMax / 自定义模型</span></div>
    <div class="app-kpi"><b>PaddleOCR-VL</b><span>扫描件与图片型 PDF 备选</span></div>
    <div class="app-kpi"><b>规则核算</b><span>累计值、速率、统计、逻辑一致性</span></div>
    <div class="app-kpi"><b>本机密钥保存</b><span>敏感信息优先进入系统 keyring</span></div>
  </div>
</div>
""", unsafe_allow_html=True)


# ── 侧边栏 ───────────────────────────────────────────────────
with st.sidebar:
    if APP_LOGO_PATH.exists():
        st.image(str(APP_LOGO_PATH), **_stretch_kwargs())
    st.caption(APP_SUBTITLE)
    st.header("运行设置")

    with st.expander("LLM 接口", expanded=True):
        st.selectbox(
            "服务商",
            list(LLM_PROVIDER_PRESETS.keys()),
            key="cfg_llm_provider",
            on_change=_apply_llm_provider,
        )
        llm_base_url = st.text_input(
            "Base URL",
            key="cfg_llm_base_url",
        )
        llm_api_key = st.text_input(
            "API Key",
            type="password",
            key="cfg_llm_api_key",
        )
        st.selectbox(
            "常用模型",
            [*LLM_MODEL_OPTIONS, CUSTOM_MODEL_LABEL],
            key="cfg_llm_model_choice",
            on_change=_apply_llm_model_choice,
        )
        llm_model = st.text_input(
            "模型 ID",
            key="cfg_llm_model",
        )
        llm_use_cache = st.toggle(
            "复用 LLM 缓存",
            key="cfg_llm_cache",
        )

    with st.expander("PaddleOCR（可选）", expanded=True):
        paddle_ocr_token = st.text_input(
            "Token",
            type="password",
            key="cfg_paddle_token",
        )
        st.selectbox(
            "常用模型",
            PADDLE_MODEL_OPTIONS,
            key="cfg_paddle_model_choice",
            on_change=_apply_paddle_model_choice,
        )
        paddle_ocr_model = st.text_input(
            "模型 ID",
            key="cfg_paddle_model",
        )
        paddle_ocr_use_cache = st.toggle(
            "复用 OCR 缓存",
            key="cfg_paddle_cache",
        )
        paddle_ocr_use_async = st.toggle(
            "使用异步 API",
            key="cfg_paddle_async",
        )
        st.caption("实测建议：数字型表格 PDF 默认走 pdfplumber；扫描件或文本层质量差时再启用 PaddleOCR。")

    c_save, c_reset = st.columns(2)
    if c_save.button("保存配置", **_stretch_kwargs()):
        save_settings(_current_settings_payload())
        st.success("已保存。API Key/Token 优先进入系统 keyring。")
    if c_reset.button("DeepSeek 默认", **_stretch_kwargs()):
        st.session_state.cfg_llm_provider = "DeepSeek"
        _apply_llm_provider()
        st.rerun()

    st.divider()
    ocr_mode = st.radio(
        "PDF 提取方式",
        ["优先 pdfplumber", "优先 PaddleOCR", "强制 PaddleOCR"],
        key="cfg_ocr_mode",
    )
    use_ocr_flag = ocr_mode == "强制 PaddleOCR"
    prefer_ocr_flag = ocr_mode != "优先 pdfplumber"
    auto_fallback_flag = not use_ocr_flag

    st.divider()
    skip_self_verify = st.checkbox(
        "跳过 AI 自验证 (Step 6, 快 30%)",
        key="cfg_skip_self_verify",
    )
    skip_ai_review = st.checkbox(
        "跳过 AI 最终审核 (Step 7)",
        key="cfg_skip_ai_review",
    )
    llm_parse_max_parallel = int(st.number_input(
        "LLM 分块并发数",
        min_value=1,
        max_value=8,
        step=1,
        key="cfg_llm_parse_max_parallel",
        help="长 PDF 会拆成多段并行解析；DeepSeek 默认建议 4，遇到限流可调低。",
    ))
    fresh_run = st.checkbox(
        "从头测试（禁用 LLM/OCR 缓存）",
        key="cfg_fresh_run",
    )


def _build_runtime_config(pdf_path: str) -> RuntimeConfig:
    effective_paddle_cache = bool(paddle_ocr_use_cache and not fresh_run)
    return RuntimeConfig(
        pdf_path=pdf_path,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_parse_max_parallel=llm_parse_max_parallel,
        paddle_ocr_token=paddle_ocr_token,
        paddle_ocr_model=paddle_ocr_model,
        paddle_ocr_use_async=paddle_ocr_use_async,
        paddle_ocr_use_cache=effective_paddle_cache,
        use_ocr=use_ocr_flag,
        prefer_ocr=prefer_ocr_flag,
        auto_fallback=auto_fallback_flag,
        skip_self_verify=skip_self_verify,
        skip_ai_review=skip_ai_review,
    )


# ── 任务态轮询：从后台 dict 同步到 session_state ─────────────
def _sync_task_state() -> None:
    tid = st.session_state.task_id
    if not tid:
        return
    task = _get_task(tid)
    if not task:
        # 任务被清理（不应发生）
        st.session_state.result = PipelineResult(
            success=False,
            error_message="后台任务状态丢失，请返回首页重新上传并检查；如重复出现，请提交运行日志。",
        )
        st.session_state.task_state = "failed"
        return

    # 同步进度
    st.session_state.progress = dict(task["progress"])
    # 同步日志（深拷贝避免线程竞争）
    st.session_state.log_lines = list(task["logs"])

    # 检查是否完成
    if not task["thread"].is_alive():
        result = task["result_box"]["result"]
        st.session_state.result = result
        if result is None:
            st.session_state.task_state = "failed"
        elif result.cancelled:
            st.session_state.task_state = "cancelled"
        elif result.success:
            st.session_state.task_state = "done"
        else:
            st.session_state.task_state = "failed"
        # 结果与日志已经复制到 session_state，可以释放后台线程对象和日志引用。
        _delete_task(tid)


# ── 主区 ────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "上传监测报告 PDF",
    type=["pdf"],
    accept_multiple_files=False,
    key="pdf_uploader",
)

# Idle 态：显示 uploader & 开始按钮
if st.session_state.task_state == "idle":
    if uploaded is not None:
        # 把上传的 PDF 写到临时文件并记到 session_state
        _store_uploaded_pdf(uploaded)
        st.session_state.pdf_name = uploaded.name
        st.success(f"已载入：**{uploaded.name}** ({uploaded.size / 1024:.0f} KB)")

        can_run = bool(llm_api_key.strip() and llm_base_url.strip() and llm_model.strip())
        if not can_run:
            st.warning("请先在侧边栏填写 API Key / Base URL / 模型 ID")

        if st.button("🚀 开始检查", type="primary", **_stretch_kwargs(),
                     disabled=not can_run):
            if _has_running_tasks():
                st.warning("当前进程已有任务运行。为避免不同用户配置互相覆盖，请等待当前任务结束。")
                st.stop()
            try:
                with st.spinner("正在启动后台检查任务..."):
                    cfg = _build_runtime_config(st.session_state.pdf_path)
                    task_id = _start_pipeline(cfg, llm_use_cache=bool(llm_use_cache and not fresh_run))
            except Exception as exc:
                st.session_state.result = PipelineResult(
                    success=False,
                    error_message=f"任务启动失败：{type(exc).__name__}: {exc}",
                )
                st.session_state.task_state = "failed"
                st.session_state.progress = {
                    "step_id": "error",
                    "label": "启动失败",
                    "percent": 0,
                    "detail": str(exc),
                }
                st.rerun()
            st.session_state.task_id = task_id
            st.session_state.task_state = "running"
            st.session_state.result = None
            st.session_state.progress = {
                "step_id": "init",
                "label": "启动后台任务",
                "percent": 1,
                "detail": "任务已提交，正在进入 PDF 提取",
            }
            st.session_state.log_lines = []
            st.session_state.pop("xlsx_export_key", None)
            st.session_state.pop("xlsx_export_bytes", None)
            st.toast("已开始检查，后台任务运行中")
            st.rerun()
    else:
        st.info("👆 请先上传 PDF 文件")


# Running 态：进度面板 + 自动刷新
elif st.session_state.task_state == "running":
    # 关键：fragment 内部渲染可见元素，确保每秒刷新的是进度条和日志本身。
    @st.fragment(run_every=1.0)
    def _render_running_status():
        _sync_task_state()
        if st.session_state.task_state != "running":
            st.rerun()  # 任务结束，立刻重跑切换到 done 视图
            return

        progress = st.session_state.progress
        st.markdown(f"### {progress.get('label') or '正在处理...'}")
        if detail := progress.get("detail"):
            st.caption(detail)
        st.progress(min(max(progress.get("percent", 0), 0), 100))
        st.caption("后台任务仍在运行。切换浏览器标签页或下载历史结果不会中断当前任务。")

        log_container = st.empty()
        _render_log_tail(log_container, st.session_state.log_lines)

        cancel_col, _ = st.columns([1, 5])
        if cancel_col.button("取消", **_stretch_kwargs(), key="btn_cancel_running"):
            _cancel_current_task()
            st.info("已请求取消，当前 LLM/OCR 请求返回后会停止。")

    _render_running_status()


# Done 态：完整结果展示 + 可反复导出
elif st.session_state.task_state == "done":
    result: PipelineResult = st.session_state.result

    # 关键修复：完成后所有结果都在 session_state，下载触发 rerun 不丢
    # 同时可以再次上传 PDF（uploader 已显示在顶部）
    uploaded_signature = hashlib.sha256(uploaded.getvalue()).hexdigest() if uploaded is not None else None
    if uploaded is not None and uploaded_signature != st.session_state.get("pdf_signature"):
        # 用户上传了新 PDF，提示重新运行
        st.info(f"已检测到新文件：**{uploaded.name}** — 点击下方按钮处理新 PDF。")
        if st.button("🔄 处理新 PDF", type="primary", **_stretch_kwargs()):
            # 重置状态准备启动新任务
            _store_uploaded_pdf(uploaded)
            st.session_state.task_state = "idle"
            st.session_state.result = None
            st.rerun()

    # 主结果区
    st.success(
        f"✓ 完成 — 错误 {len(result.errors)} / 警告 {len(result.warnings)} / 提示 {len(result.infos)} "
        f"·  用时 {result.duration_sec:.1f}s"
    )

    # Metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("错误", len(result.errors))
    m2.metric("警告", len(result.warnings))
    m3.metric("提示", len(result.infos))
    m4.metric("数据表", len(result.report.tables) if result.report else 0)
    m5.metric("用时", f"{result.duration_sec:.0f}s")

    _render_extraction_diagnostics(result.report)

    # 8 个 tab
    tab_calc, tab_stats, tab_logic, tab_plan, tab_ai, tab_md, tab_log = st.tabs(
        ["计算验证", "统计验证", "逻辑检查", "分析计划 (ReAct)", "AI 最终审核", "Markdown 源", "运行日志"]
    )
    with tab_calc:
        _render_issues_section("计算验证", result.calc_issues)
    with tab_stats:
        _render_issues_section("统计验证", result.stats_issues)
    with tab_logic:
        _render_issues_section("逻辑检查", result.logic_issues)
    with tab_plan:
        _render_analysis_plan(result.analysis_plan)
    with tab_ai:
        if result.ai_review:
            st.markdown(result.ai_review)
        else:
            st.info("未启用或未生成 AI 最终审核")
    with tab_md:
        st.code(result.final_md, language="markdown")
    with tab_log:
        st.text("\n".join(st.session_state.log_lines) if st.session_state.log_lines else "无日志")

    # ── 导出（关键修复：rerun 时仍能保留这里） ─────────────────
    st.markdown("---")
    st.subheader("导出检查报告")
    pdf_stem = Path(st.session_state.pdf_name or "report.pdf").stem

    docx_bytes = generate_docx(result.final_md, result.report, result.errors, result.warnings)

    html_content = generate_html(
        result.final_md,
        getattr(result.report, "project_name", "") or "检查报告",
    )
    export_key = (
        f"{st.session_state.get('pdf_signature', '')}:"
        f"{len(result.final_md or '')}:"
        f"{len(result.calc_issues)}-{len(result.stats_issues)}-{len(result.logic_issues)}:"
        f"{len(result.report.tables) if result.report else 0}"
    )
    if st.session_state.get("xlsx_export_key") != export_key:
        st.session_state.xlsx_export_bytes = generate_intermediate_xlsx(
            result.report,
            calc_issues=result.calc_issues,
            stats_issues=result.stats_issues,
            logic_issues=result.logic_issues,
            analysis_plan=result.analysis_plan,
        )
        st.session_state.xlsx_export_key = export_key
    xlsx_bytes = st.session_state.xlsx_export_bytes

    dl1, dl2, dl3, dl4, dl_new = st.columns([1, 1, 1, 1, 1])

    with dl1:
        st.download_button(
            "下载 Markdown",
            data=result.final_md,
            file_name=f"{pdf_stem}_检查报告.md",
            mime="text/markdown",
            **_stretch_kwargs(),
            key="dl_md",
        )
    with dl2:
        st.download_button(
            "下载 Word",
            data=docx_bytes,
            file_name=f"{pdf_stem}_检查报告.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            **_stretch_kwargs(),
            key="dl_docx",
        )
    with dl3:
        st.download_button(
            "下载 HTML",
            data=html_content,
            file_name=f"{pdf_stem}_检查报告.html",
            mime="text/html",
            **_stretch_kwargs(),
            key="dl_html",
        )
    with dl4:
        st.download_button(
            "下载 Excel中间层",
            data=xlsx_bytes,
            file_name=f"{pdf_stem}_Excel中间层.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            **_stretch_kwargs(),
            key="dl_xlsx",
        )
    with dl_new:
        if st.button("🆕 新建任务", **_stretch_kwargs(), key="btn_new_task"):
            st.session_state.task_state = "idle"
            st.session_state.task_id = None
            st.session_state.result = None
            st.session_state.pdf_path = None
            st.session_state.pdf_name = None
            st.session_state.log_lines = []
            st.session_state.pop("xlsx_export_key", None)
            st.session_state.pop("xlsx_export_bytes", None)
            st.rerun()


# Cancelled / Failed
elif st.session_state.task_state in ("cancelled", "failed"):
    if st.session_state.task_state == "cancelled":
        st.warning("任务已取消")
    else:
        st.error("任务失败")
        if st.session_state.result and st.session_state.result.error_message:
            st.code(st.session_state.result.error_message, language=None)

    uploaded_signature = hashlib.sha256(uploaded.getvalue()).hexdigest() if uploaded is not None else None
    if uploaded is not None and uploaded_signature != st.session_state.get("pdf_signature"):
        st.info(f"已检测到新文件：**{uploaded.name}** — 点击下方按钮处理新 PDF。")
        if st.button("🔄 处理新 PDF", type="primary", **_stretch_kwargs(), key="btn_failed_new_pdf"):
            _store_uploaded_pdf(uploaded)
            st.session_state.task_state = "idle"
            st.session_state.task_id = None
            st.session_state.result = None
            st.session_state.log_lines = []
            st.session_state.pop("xlsx_export_key", None)
            st.session_state.pop("xlsx_export_bytes", None)
            st.rerun()

    if st.session_state.log_lines:
        with st.expander("查看运行日志", expanded=False):
            st.text("\n".join(st.session_state.log_lines))

    if st.button("🔄 返回首页", type="primary"):
        st.session_state.task_state = "idle"
        st.session_state.task_id = None
        st.session_state.result = None
        st.rerun()
