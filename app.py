"""建筑变形监测报告检查智能体 — Streamlit Web UI v2

重写要点（修复 v1 全部 3 个 bug）：
1. 全状态走 st.session_state，浏览器 tab 切换 / 重连不丢
2. 后台 threading.Thread 跑流水线，主脚本 rerun 不会杀任务
3. st.download_button 触发 rerun 后界面保持完成态，可继续导出/换 PDF
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
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

from src.core import PipelineResult, RuntimeConfig, run_pipeline
from src.tools.export_formats import generate_docx, generate_html
from src.tools.extraction_quality import append_issue_source_hint


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
# 注意：Streamlit 1.55+ 的 rerun 会重新执行整个脚本，但模块级变量保留在内存中。
# session_state 仍是首选，但 thread 句柄不能跨 session 共享，所以放在模块级 dict。
_BACKGROUND_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()


def _register_task(task_id: str, payload: dict) -> None:
    with _TASKS_LOCK:
        _BACKGROUND_TASKS[task_id] = payload


def _get_task(task_id: str) -> Optional[dict]:
    with _TASKS_LOCK:
        return _BACKGROUND_TASKS.get(task_id)


def _delete_task(task_id: str) -> None:
    with _TASKS_LOCK:
        _BACKGROUND_TASKS.pop(task_id, None)


# ── Session State 初始化 ─────────────────────────────────────
def _init_state() -> None:
    ss = st.session_state
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
    if "log_lines" not in ss:
        ss.log_lines = []
    if "progress" not in ss:
        ss.progress = {"step_id": "", "label": "", "percent": 0, "detail": ""}


# ── 启动后台任务 ─────────────────────────────────────────────
def _start_pipeline(cfg: RuntimeConfig) -> str:
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

    thread = threading.Thread(target=worker, daemon=True, name=f"pipeline-{task_id[:8]}")
    thread.start()

    _register_task(task_id, {
        "thread": thread,
        "progress": progress_box,
        "logs": logs,
        "result_box": result_box,
        "cancel_event": cancel_event,
        "started_at": time.time(),
    })
    return task_id


def _cancel_current_task() -> None:
    tid = st.session_state.task_id
    if not tid:
        return
    task = _get_task(tid)
    if task:
        task["cancel_event"].set()


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
    page_title="建筑变形监测报告核验台 v2",
    layout="wide",
    initial_sidebar_state="expanded",
)

_init_state()

st.markdown("""
<style>
.stApp { background: linear-gradient(180deg, #f4f7fb 0%, #eef3f8 100%); }
.app-hero { background: linear-gradient(135deg, #ffffff 0%, #f4f8ff 100%);
            border: 1px solid #d8dee9; border-radius: 18px; padding: 22px 24px;
            margin-bottom: 16px; box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05); }
.app-hero h1 { margin: 0; font-size: 28px; }
.app-hero p { margin: 6px 0 0 0; color: #5b6472; }
.app-hero .badge { display:inline-block; background:#e0f2fe; color:#075985;
                   padding:2px 8px; border-radius:6px; font-size:11px; margin-left:8px; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="app-hero">
  <h1>建筑变形监测报告核验台 <span class="badge">v2 修复版</span></h1>
  <p>支持后台运行：切换 tab、下载报告、上传新 PDF 都不会中断或丢失结果。</p>
</div>
""", unsafe_allow_html=True)


# ── 侧边栏 ───────────────────────────────────────────────────
with st.sidebar:
    import src.config as cfg
    st.header("⚙ 运行设置")

    with st.expander("LLM 接口", expanded=True):
        llm_base_url = st.text_input(
            "Base URL",
            value=st.session_state.get("cfg_llm_base_url",
                                       os.getenv("LLM_BASE_URL", cfg.LLM_BASE_URL)),
            key="cfg_llm_base_url",
        )
        llm_api_key = st.text_input(
            "API Key",
            value=st.session_state.get("cfg_llm_api_key",
                                       os.getenv("LLM_API_KEY", cfg.LLM_API_KEY)),
            type="password",
            key="cfg_llm_api_key",
        )
        llm_model = st.text_input(
            "模型",
            value=st.session_state.get("cfg_llm_model",
                                       os.getenv("LLM_MODEL", "MiniMax-M2.7-highspeed")),
            key="cfg_llm_model",
        )

    with st.expander("PaddleOCR（可选）", expanded=False):
        paddle_ocr_token = st.text_input(
            "Token",
            value=st.session_state.get("cfg_paddle_token",
                                       os.getenv("PADDLE_OCR_TOKEN", cfg.PADDLE_OCR_TOKEN)),
            type="password",
            key="cfg_paddle_token",
        )
        paddle_ocr_model = st.text_input(
            "模型",
            value=st.session_state.get(
                "cfg_paddle_model",
                os.getenv("PADDLE_OCR_MODEL", cfg.PADDLE_OCR_MODEL),
            ),
            key="cfg_paddle_model",
        )
        paddle_ocr_use_cache = st.toggle(
            "复用 OCR 缓存", value=st.session_state.get("cfg_paddle_cache", True),
            key="cfg_paddle_cache",
        )
        paddle_ocr_use_async = st.toggle(
            "使用异步 API", value=st.session_state.get("cfg_paddle_async", True),
            key="cfg_paddle_async",
        )

    st.divider()
    ocr_mode = st.radio(
        "PDF 提取方式",
        ["优先 pdfplumber", "优先 PaddleOCR", "强制 PaddleOCR"],
        index=0,
        key="cfg_ocr_mode",
    )
    use_ocr_flag = ocr_mode == "强制 PaddleOCR"
    prefer_ocr_flag = ocr_mode != "优先 pdfplumber"
    auto_fallback_flag = not use_ocr_flag

    st.divider()
    skip_self_verify = st.checkbox(
        "跳过 AI 自验证 (Step 6, 快 30%)",
        value=st.session_state.get("cfg_skip_self_verify", False),
        key="cfg_skip_self_verify",
    )
    skip_ai_review = st.checkbox(
        "跳过 AI 最终审核 (Step 7)",
        value=st.session_state.get("cfg_skip_ai_review", False),
        key="cfg_skip_ai_review",
    )


def _build_runtime_config(pdf_path: str) -> RuntimeConfig:
    return RuntimeConfig(
        pdf_path=pdf_path,
        llm_api_key=llm_api_key,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        paddle_ocr_token=paddle_ocr_token,
        paddle_ocr_model=paddle_ocr_model,
        paddle_ocr_use_async=paddle_ocr_use_async,
        paddle_ocr_use_cache=paddle_ocr_use_cache,
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
        # 任务完成后保留少量时间窗口可访问，再删除
        # 这里直接保留到 session_state，但移除 _BACKGROUND_TASKS 中的引用以释放线程对象
        # （logs/result 已经拷贝到 session_state）


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
        if st.session_state.pdf_path is None or st.session_state.pdf_name != uploaded.name:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(uploaded.read())
                st.session_state.pdf_path = tmp.name
            st.session_state.pdf_name = uploaded.name
        st.success(f"已载入：**{uploaded.name}** ({uploaded.size / 1024:.0f} KB)")

        can_run = bool(llm_api_key.strip() and llm_base_url.strip() and llm_model.strip())
        if not can_run:
            st.warning("请先在侧边栏填写 API Key / Base URL / 模型 ID")

        if st.button("🚀 开始检查", type="primary", use_container_width=True,
                     disabled=not can_run):
            cfg = _build_runtime_config(st.session_state.pdf_path)
            task_id = _start_pipeline(cfg)
            st.session_state.task_id = task_id
            st.session_state.task_state = "running"
            st.session_state.result = None
            st.session_state.progress = {"step_id": "init", "label": "启动", "percent": 0, "detail": ""}
            st.session_state.log_lines = []
            st.rerun()
    else:
        st.info("👆 请先上传 PDF 文件")


# Running 态：进度面板 + 自动刷新
elif st.session_state.task_state == "running":
    _sync_task_state()

    progress = st.session_state.progress
    st.markdown(f"### {progress.get('label') or '正在处理...'}")
    if detail := progress.get("detail"):
        st.caption(detail)
    st.progress(min(max(progress.get("percent", 0), 0), 100))

    log_container = st.empty()
    _render_log_tail(log_container, st.session_state.log_lines)

    cancel_col, _, refresh_col = st.columns([1, 4, 1])
    if cancel_col.button("取消", use_container_width=True):
        _cancel_current_task()
        st.info("已请求取消")

    # 关键：使用 fragment 每秒自动刷新进度，不需要用户操作
    @st.fragment(run_every=1.0)
    def _auto_refresh():
        _sync_task_state()
        if st.session_state.task_state != "running":
            st.rerun()  # 任务结束，立刻重跑切换到 done 视图

    _auto_refresh()


# Done 态：完整结果展示 + 可反复导出
elif st.session_state.task_state == "done":
    result: PipelineResult = st.session_state.result

    # 关键修复：完成后所有结果都在 session_state，下载触发 rerun 不丢
    # 同时可以再次上传 PDF（uploader 已显示在顶部）
    if uploaded is not None and uploaded.name != st.session_state.pdf_name:
        # 用户上传了新 PDF，提示重新运行
        st.info(f"已检测到新文件：**{uploaded.name}** — 点击下方按钮处理新 PDF。")
        if st.button("🔄 处理新 PDF", type="primary", use_container_width=True):
            # 重置状态准备启动新任务
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(uploaded.read())
                st.session_state.pdf_path = tmp.name
            st.session_state.pdf_name = uploaded.name
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

    @st.cache_data(show_spinner=False)
    def _cache_docx(md: str, errors_count: int, warnings_count: int, report_signature: str) -> bytes:
        # 缓存键基于 report.project_name 等不变量，避免每次 rerun 重生成
        return generate_docx(md, result.report, result.errors, result.warnings)

    docx_bytes = _cache_docx(
        result.final_md,
        len(result.errors),
        len(result.warnings),
        f"{getattr(result.report, 'project_name', '')}|{getattr(result.report, 'report_number', '')}",
    )

    html_content = generate_html(
        result.final_md,
        getattr(result.report, "project_name", "") or "检查报告",
    )

    dl1, dl2, dl3, _, dl_new = st.columns([1, 1, 1, 1, 1])

    with dl1:
        st.download_button(
            "📄 Markdown",
            data=result.final_md,
            file_name=f"{pdf_stem}_检查报告.md",
            mime="text/markdown",
            use_container_width=True,
            key="dl_md",
        )
    with dl2:
        st.download_button(
            "📝 Word",
            data=docx_bytes,
            file_name=f"{pdf_stem}_检查报告.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
            key="dl_docx",
        )
    with dl3:
        st.download_button(
            "🌐 HTML",
            data=html_content,
            file_name=f"{pdf_stem}_检查报告.html",
            mime="text/html",
            use_container_width=True,
            key="dl_html",
        )
    with dl_new:
        if st.button("🆕 新建任务", use_container_width=True, key="btn_new_task"):
            st.session_state.task_state = "idle"
            st.session_state.task_id = None
            st.session_state.result = None
            st.session_state.pdf_path = None
            st.session_state.pdf_name = None
            st.session_state.log_lines = []
            st.rerun()


# Cancelled / Failed
elif st.session_state.task_state in ("cancelled", "failed"):
    if st.session_state.task_state == "cancelled":
        st.warning("任务已取消")
    else:
        st.error("任务失败")
        if st.session_state.result and st.session_state.result.error_message:
            st.code(st.session_state.result.error_message, language=None)

    if st.session_state.log_lines:
        with st.expander("查看运行日志", expanded=False):
            st.text("\n".join(st.session_state.log_lines))

    if st.button("🔄 返回首页", type="primary"):
        st.session_state.task_state = "idle"
        st.session_state.task_id = None
        st.session_state.result = None
        st.rerun()
