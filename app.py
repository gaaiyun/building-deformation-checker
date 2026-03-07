"""
建筑变形监测报告检查智能体 — Streamlit Web UI

功能:
- 上传PDF监测报告，自动提取数据并检查
- 实时进度与日志显示
- 多格式导出（Markdown / Word / HTML）
- 分类展示检查结果
"""

import io
import logging
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import streamlit as st
from src.tools.extraction_quality import append_issue_source_hint

# ── 日志配置：捕获到 StreamHandler 供界面显示 ──────────
log_records: list[str] = []


class StreamlitLogHandler(logging.Handler):
    """把日志记录收集到列表，供 UI 实时展示"""
    def emit(self, record):
        log_records.append(self.format(record))


handler = StreamlitLogHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(handler)
logger = logging.getLogger("app")


# ── 辅助函数（必须在 Streamlit UI 逻辑之前定义）─────────

def _render_issues(title: str, issues: list) -> None:
    """按表名分组展示检查问题"""
    if not issues:
        st.success(f"{title}全部通过 ✅")
        return

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos = [i for i in issues if i.severity == "info"]

    if errors:
        st.error(f"发现 {len(errors)} 个错误")
    if warnings:
        st.warning(f"发现 {len(warnings)} 个警告")
    if infos:
        st.info(f"发现 {len(infos)} 个提示")

    grouped = defaultdict(list)
    for issue in issues:
        grouped[issue.table_name].append(issue)

    for table_name, table_issues in grouped.items():
        err_count = sum(1 for i in table_issues if i.severity == "error")
        warn_count = sum(1 for i in table_issues if i.severity == "warning")
        badge = ""
        if err_count:
            badge += f"❌{err_count} "
        if warn_count:
            badge += f"⚠️{warn_count}"

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
    diagnostics = report.extraction_diagnostics or {}
    if not diagnostics:
        return

    st.markdown("### 🧾 提取诊断")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("原始字符", f"{diagnostics.get('raw_chars', 0):,}")
    c2.metric("清洗后字符", f"{diagnostics.get('clean_chars', 0):,}")
    ratio = diagnostics.get("compression_ratio", 0.0)
    c3.metric("压缩率", f"{ratio:.1%}")
    c4.metric("异常页", f"{len(diagnostics.get('high_markup_pages', []))}")
    c5.metric("异常表", f"{diagnostics.get('abnormal_table_count', 0)}")

    method = diagnostics.get("method", "unknown")
    profile = diagnostics.get("selected_profile", "")
    label = f"{method} ({profile})" if profile else method
    st.caption(f"提取方式: {label}")

    attempts = diagnostics.get("attempts", [])
    if attempts:
        st.markdown("**提取尝试链路**")
        for attempt in attempts:
            if attempt.get("error"):
                st.warning(f"{attempt['profile']}: {attempt['error']}")
            else:
                st.caption(
                    f"{attempt['profile']}: clean={attempt.get('clean_chars', 0):,} chars, "
                    f"pages={attempt.get('page_count', 0)}, "
                    f"compression={attempt.get('compression_ratio', 0.0):.1%}"
                )

    high_markup_pages = diagnostics.get("high_markup_pages", [])
    if high_markup_pages:
        page_label = "，".join(str(page + 1) for page in high_markup_pages[:12])
        suffix = " ..." if len(high_markup_pages) > 12 else ""
        st.caption(f"高 markup 页: 第 {page_label} 页{suffix}")

    debug_dir = diagnostics.get("debug_dir", "")
    if debug_dir:
        st.markdown("**OCR 调试目录**")
        st.code(debug_dir, language=None)

    flagged_tables = report.table_extraction_flags or {}
    if flagged_tables:
        st.markdown("**疑似提取异常表**")
        for table_index, flags in sorted(flagged_tables.items()):
            if table_index >= len(report.tables):
                continue
            table = report.tables[table_index]
            table_name = table.monitoring_item
            if table.borehole_id:
                table_name += f" ({table.borehole_id})"
            st.warning(f"{table_name}: {'；'.join(flags)}")


def _render_analysis_plan(analysis_plan: list[dict]) -> None:
    """ReAct 风格渲染每张表的分析计划：Thought → Observation → Action"""
    if not analysis_plan:
        st.info("未生成分析计划")
        return

    FIELD_LABELS = {
        "initial_value": "初始值",
        "previous_value": "上次值",
        "current_value": "本次值",
        "current_change": "本次变化",
        "cumulative_change": "累计变化",
        "change_rate": "速率",
        "safety_status": "安全状态",
        "depth": "深度",
        "previous_cumulative": "上次累计",
        "current_cumulative": "本次累计",
    }

    for plan in analysis_plan:
        has_notes = bool(plan["special_notes"])
        title_badge = " ⚠️" if has_notes else ""
        expander_title = (
            f"Table {plan['table_index']}: {plan['table_name']} "
            f"({plan['category']} | {plan['point_count']}个测点){title_badge}"
        )

        with st.expander(expander_title, expanded=has_notes):
            # ── Thought: 字段识别 ──────────────────────
            st.markdown("**📋 Thought — 字段识别**")
            fields = plan["fields_detected"]
            if fields:
                cols = st.columns(len(fields))
                for i, (field_key, detected) in enumerate(fields.items()):
                    label = FIELD_LABELS.get(field_key, field_key)
                    icon = "✅" if detected else "❌"
                    cols[i].markdown(f"<div style='text-align:center'><b>{label}</b><br/>{icon}</div>", unsafe_allow_html=True)
            st.markdown("")

            # ── Observation: 数据样本 ──────────────────
            st.markdown("**🔍 Observation — 数据样本**")
            for sample in plan["data_sample"]:
                st.code(sample, language=None)

            # ── Observation: 单位与基准分析 ─────────────
            st.markdown("**🧠 Observation — 单位与基准分析**")
            unit_text = f"单位: **{plan['unit']}**"
            if plan["unit_conversion"] != 1.0:
                unit_text += f" → mm (×{plan['unit_conversion']:.0f}转换)"
            else:
                unit_text += f" ({plan['conversion_note']})"
            st.markdown(unit_text)

            reliable_icon = "✅" if plan["initial_reliable"] else "⚠️"
            st.markdown(f"初始值: {reliable_icon} {plan['reliability_reason']}")

            if plan["interval_days"]:
                st.markdown(f"监测间隔: **{plan['interval_days']:.0f}天** ({plan['interval_source']})")
            else:
                st.markdown(f"监测间隔: ⏳ {plan['interval_source']}")
            st.markdown("")

            # ── Action: 验证规则 ────────────────────────
            st.markdown("**🎯 Action — 将执行的验证规则**")
            for method in plan["verification_methods"]:
                icon = "⚠️" if method["severity"] == "warning" else "✅"
                st.markdown(
                    f"{icon} **{method['name']}** = `{method['formula']}`, "
                    f"容差={method['tolerance']}, 级别={method['severity']}"
                )

            # ── 特殊说明 ────────────────────────────────
            if plan["special_notes"]:
                st.markdown("")
                st.markdown("**📝 特殊说明**")
                for note in plan["special_notes"]:
                    st.warning(f"• {note}", icon="📝")


def _generate_docx(md_content: str, report, errors: list, warnings: list) -> bytes:
    """生成 Word 文档"""
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    title = doc.add_heading("建筑变形监测报告检查报告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"项目名称: {report.project_name}")
    doc.add_paragraph(f"监测单位: {report.monitoring_company}")
    doc.add_paragraph(f"报告编号: {report.report_number}")
    doc.add_paragraph(f"监测日期: {report.monitoring_date}")
    doc.add_paragraph(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    doc.add_heading("检查结果统计", level=1)
    table = doc.add_table(rows=4, cols=2, style="Table Grid")
    table.cell(0, 0).text = "类别"
    table.cell(0, 1).text = "数量"
    table.cell(1, 0).text = "错误"
    table.cell(1, 1).text = str(len(errors))
    table.cell(2, 0).text = "警告"
    table.cell(2, 1).text = str(len(warnings))
    table.cell(3, 0).text = "合计"
    table.cell(3, 1).text = str(len(errors) + len(warnings))

    if errors:
        doc.add_heading("错误详情", level=1)
        for i, err in enumerate(errors, 1):
            p = doc.add_paragraph()
            run = p.add_run(f"{i}. [{err.table_name}] {err.point_id} - {err.field_name}")
            run.bold = True
            run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)
            doc.add_paragraph(f"   {err.message}", style="List Bullet")

    if warnings:
        doc.add_heading("警告详情", level=1)
        for i, warn in enumerate(warnings, 1):
            p = doc.add_paragraph()
            run = p.add_run(f"{i}. [{warn.table_name}] {warn.point_id} - {warn.field_name}")
            run.bold = True
            run.font.color.rgb = RGBColor(0xCC, 0x88, 0x00)
            doc.add_paragraph(f"   {warn.message}", style="List Bullet")

    doc.add_heading("结论", level=1)
    if report.conclusion:
        doc.add_paragraph(f"报告原文结论: {report.conclusion}")
    if errors:
        doc.add_paragraph(f"自动检查结论: 发现 {len(errors)} 处错误和 {len(warnings)} 处警告，建议复核。")
    else:
        doc.add_paragraph("自动检查结论: 监测报告数据计算与统计结果验证通过。")

    doc.add_paragraph("\n本报告由建筑变形监测报告检查智能体自动生成", style="Intense Quote")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _generate_html(md_content: str, project_name: str) -> str:
    """生成可打印的 HTML 报告"""
    import markdown

    html_body = markdown.markdown(
        md_content,
        extensions=["tables", "fenced_code"],
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>{project_name} - 检查报告</title>
    <style>
        body {{ font-family: "Microsoft YaHei", "SimHei", sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; color: #333; }}
        h1 {{ color: #1a5276; border-bottom: 3px solid #1a5276; padding-bottom: 10px; }}
        h2 {{ color: #2c3e50; border-bottom: 1px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
        table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
        th {{ background-color: #f2f2f2; font-weight: bold; }}
        tr:nth-child(even) {{ background-color: #fafafa; }}
        blockquote {{ border-left: 4px solid #3498db; margin: 15px 0; padding: 10px 20px; background: #ecf6fd; }}
        code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; }}
        hr {{ border: none; border-top: 1px solid #ddd; margin: 30px 0; }}
        @media print {{
            body {{ max-width: 100%; padding: 0; }}
            h1 {{ page-break-before: avoid; }}
            table {{ page-break-inside: avoid; }}
        }}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""


# ── 页面配置 ──────────────────────────────────────────
st.set_page_config(
    page_title="建筑变形监测报告检查",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 自定义样式 ────────────────────────────────────────
st.markdown("""
<style>
    .stMetric > div { border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; }
    .step-done { color: #28a745; }
    .step-running { color: #fd7e14; }
    div[data-testid="stExpander"] details summary p { font-weight: 600; }
</style>
""", unsafe_allow_html=True)

st.title("🏗️ 建筑变形监测报告检查智能体")
st.caption("上传监测报告PDF → AI自动提取数据 → 逐条验证计算 → 生成检查报告")

# ── 侧边栏设置 ────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 检查设置")

    ocr_mode = st.radio(
        "PDF提取方式",
        ["优先 PaddleOCR（表格 profile）", "仅 pdfplumber", "强制 PaddleOCR"],
        index=0,
        help="默认先用表格优先的 PaddleOCR profile，必要时依次回退到其他 OCR profile 和 pdfplumber",
    )
    use_ocr = ocr_mode == "强制 PaddleOCR"
    prefer_ocr = ocr_mode != "仅 pdfplumber"
    auto_fallback = ocr_mode != "仅 pdfplumber"

    st.divider()
    st.subheader("🤖 AI 模型")
    from src.config import AVAILABLE_MODELS
    selected_model = st.selectbox(
        "选择 LLM 模型",
        AVAILABLE_MODELS,
        index=0,
        help="Coding Plan 支持的模型，不同模型在理解表格和语义匹配上各有优劣",
    )

    st.divider()
    st.subheader("AI 功能")
    do_self_verify = st.checkbox("AI 自验证（确认错误）", value=True, help="对检出的错误用AI二次确认，减少误报")
    do_ai_review = st.checkbox("AI 最终审核", value=False, help="由AI专家对检查结果做最终整体评估")

    st.divider()
    st.subheader("📐 核心计算公式")
    st.code("本次变化 = 本次测值 − 上次测值\n累计变化 = 本次测值 − 初始测值\n变化速率 = 本次变化 / 时间(天)", language=None)
    st.info("⚠️ 正负号代表**方向**，不代表大小", icon="ℹ️")

# ── 文件上传区 ────────────────────────────────────────
uploaded = st.file_uploader(
    "📄 上传监测报告 PDF",
    type=["pdf"],
    accept_multiple_files=False,
    help="支持文字版PDF和扫描件PDF",
)

if uploaded is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded.read())
        tmp_path = tmp.name

    pdf_name = Path(uploaded.name).stem
    st.success(f"已上传: **{uploaded.name}** ({uploaded.size / 1024:.0f} KB)")

    if st.button("🚀 开始检查", type="primary", use_container_width=True):
        log_records.clear()

        # 运行时切换模型
        from src.config import set_model
        set_model(selected_model)

        # ── 实时进度容器 ──────────────────────────────
        progress_bar = st.progress(0)
        status_container = st.status(f"正在检查报告（模型: {selected_model}）...", expanded=True)
        start_time = time.time()

        try:
            # ━━ Step 1: PDF 提取 ━━━━━━━━━━━━━━━━━━━━━
            with status_container:
                st.write("📄 **Step 1/8** — 提取 PDF 内容...")
            progress_bar.progress(5)

            from src.tools.pdf_extractor import extract_pdf
            extraction_result = extract_pdf(
                tmp_path,
                use_ocr=use_ocr,
                prefer_ocr=prefer_ocr,
                auto_fallback=auto_fallback,
                ocr_output_dir=f"output/{pdf_name}_ocr_debug",
                return_details=True,
            )
            raw_text = extraction_result.text
            extraction_result.diagnostics.setdefault("method", extraction_result.method)
            extraction_result.diagnostics.setdefault("selected_profile", extraction_result.selected_profile)
            extraction_result.diagnostics.setdefault("debug_dir", extraction_result.debug_output_dir)

            with status_container:
                st.write(
                    "  ✅ 提取完成: "
                    f"{len(raw_text):,} 字符 "
                    f"({extraction_result.method}/{extraction_result.selected_profile}, "
                    f"{extraction_result.diagnostics.get('raw_chars', 0):,} → "
                    f"{extraction_result.diagnostics.get('clean_chars', 0):,})"
                )

            # ━━ Step 2: LLM 结构化解析 ━━━━━━━━━━━━━━━
            with status_container:
                st.write("🤖 **Step 2/8** — AI 结构化解析（可能需要1-3分钟）...")
            progress_bar.progress(10)

            from src.tools.llm_parser import parse_report_with_llm
            report = parse_report_with_llm(raw_text)
            report.raw_text = raw_text
            report.extraction_diagnostics = extraction_result.diagnostics

            from src.tools.extraction_quality import analyze_extraction_quality
            analyze_extraction_quality(report)

            import src.config as cfg
            step_delay = getattr(cfg, "LLM_STEP_DELAY_SEC", 0)
            if step_delay > 0:
                with status_container:
                    st.write(f"  ⏳ 等待 {step_delay} 秒以避免限流...")
                time.sleep(step_delay)

            from src.tools.table_analyzer import enrich_configs_with_llm
            enrich_configs_with_llm(report)

            with status_container:
                st.write(f"  ✅ 解析完成: **{report.project_name}** — {len(report.tables)} 张表, "
                         f"{len(report.thresholds)} 项阈值, {len(report.summary_items)} 项汇总")
            progress_bar.progress(35)

            # ━━ Step 2.5: 表格分析计划 ━━━━━━━━━━━━━━━━
            with status_container:
                st.write("🧠 **Step 3/8** — 分析表格结构与验证策略...")

            from src.tools.table_analyzer import generate_analysis_plan
            analysis_plan = generate_analysis_plan(report)

            with status_container:
                st.write(f"  ✅ 分析完成: {len(analysis_plan)} 张表的验证策略已制定")
            progress_bar.progress(42)

            # ━━ Step 4: 计算验证 ━━━━━━━━━━━━━━━━━━━━━
            with status_container:
                st.write("🔢 **Step 4/8** — 计算验证...")
            progress_bar.progress(45)

            from src.tools.calculation_checker import run_calculation_checks
            calc_issues = run_calculation_checks(report)

            with status_container:
                st.write(f"  ✅ 计算验证: {len(calc_issues)} 个问题")

            # ━━ Step 5: 统计验证 ━━━━━━━━━━━━━━━━━━━━━
            with status_container:
                st.write("📈 **Step 5/8** — 统计验证...")
            progress_bar.progress(55)

            from src.tools.statistics_checker import run_statistics_checks
            stats_issues = run_statistics_checks(report)

            with status_container:
                st.write(f"  ✅ 统计验证: {len(stats_issues)} 个问题")

            # ━━ Step 6: 逻辑检查 ━━━━━━━━━━━━━━━━━━━━━
            with status_container:
                st.write("🔍 **Step 6/8** — 逻辑检查（AI语义匹配）...")
            progress_bar.progress(60)

            from src.tools.logic_checker import run_logic_checks
            logic_issues = run_logic_checks(report)

            with status_container:
                st.write(f"  ✅ 逻辑检查: {len(logic_issues)} 个问题")

            # ━━ Step 7: AI 自验证 ━━━━━━━━━━━━━━━━━━━━
            all_issues = calc_issues + stats_issues + logic_issues
            if do_self_verify:
                errors_to_verify = [i for i in all_issues if i.severity == "error"]
                if errors_to_verify:
                    step_delay = getattr(cfg, "LLM_STEP_DELAY_SEC", 0)
                    if step_delay > 0:
                        with status_container:
                            st.write(f"  ⏳ 等待 {step_delay} 秒以避免限流...")
                        time.sleep(step_delay)

                    with status_container:
                        st.write(f"🔄 **Step 7/8** — AI 自验证（{len(errors_to_verify)} 个错误）...")
                    progress_bar.progress(70)

                    from src.tools.self_verifier import verify_errors_with_llm
                    all_issues = verify_errors_with_llm(report, all_issues)

                    with status_container:
                        st.write("  ✅ 自验证完成")

            # ━━ Step 8: AI 最终审核 ━━━━━━━━━━━━━━━━━━
            ai_review = ""
            if do_ai_review:
                with status_container:
                    st.write("🧑‍💼 **Step 8/8** — AI 专家最终审核...")
                progress_bar.progress(80)

                from src.tools.report_generator import generate_report_md
                from src.tools.llm_parser import verify_report_with_llm
                prelim = generate_report_md(report, calc_issues, stats_issues, logic_issues, analysis_plan=analysis_plan)
                ai_review = verify_report_with_llm(prelim, raw_text)

                with status_container:
                    st.write("  ✅ AI 审核完成")

            # ━━ 生成报告 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            progress_bar.progress(90)
            from src.tools.report_generator import generate_report_md, save_report
            final_md = generate_report_md(report, calc_issues, stats_issues, logic_issues, ai_review, analysis_plan)
            output_path = f"output/{pdf_name}_检查报告.md"
            save_report(final_md, output_path)

            elapsed = time.time() - start_time
            progress_bar.progress(100)
            status_container.update(label=f"✅ 检查完成！耗时 {elapsed:.0f} 秒", state="complete", expanded=False)

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 结果展示区
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

            errors = [i for i in all_issues if i.severity == "error"]
            warnings = [i for i in all_issues if i.severity == "warning"]
            infos = [i for i in all_issues if i.severity == "info"]

            st.markdown("---")
            st.subheader("📊 检查结果总览")

            # ── 指标卡片 ──────────────────────────────
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("📋 数据表", f"{len(report.tables)} 张")
            c2.metric("❌ 错误", f"{len(errors)}", delta=f"-{len(errors)}" if errors else None, delta_color="inverse")
            c3.metric("⚠️ 警告", f"{len(warnings)}")
            c4.metric("ℹ️ 提示", f"{len(infos)}")
            c5.metric("⏱️ 耗时", f"{elapsed:.0f}s")
            _render_extraction_diagnostics(report)

            # ── 选项卡 ───────────────────────────────
            tab_report, tab_extract, tab_plan, tab_calc, tab_stats, tab_logic, tab_ai, tab_log = st.tabs([
                "📊 检查报告", "🧾 提取诊断", "🧠 分析计划", "🔢 计算验证", "📈 统计验证",
                "🔍 逻辑检查", "🤖 AI审核", "📋 运行日志",
            ])

            with tab_report:
                st.markdown(final_md)

            with tab_extract:
                _render_extraction_diagnostics(report)

            with tab_plan:
                _render_analysis_plan(analysis_plan)

            with tab_calc:
                _render_issues("计算验证", calc_issues)

            with tab_stats:
                _render_issues("统计验证", stats_issues)

            with tab_logic:
                _render_issues("逻辑检查", logic_issues)

            with tab_ai:
                if ai_review:
                    st.markdown(ai_review)
                else:
                    st.info("未启用AI审核，可在侧边栏开启")

            with tab_log:
                st.text("\n".join(log_records) if log_records else "暂无日志")

            # ── 导出按钮 ──────────────────────────────
            st.markdown("---")
            st.subheader("📥 导出检查报告")

            dl_col1, dl_col2, dl_col3 = st.columns(3)

            with dl_col1:
                st.download_button(
                    label="📄 下载 Markdown",
                    data=final_md,
                    file_name=f"{pdf_name}_检查报告.md",
                    mime="text/markdown",
                    use_container_width=True,
                )

            with dl_col2:
                docx_bytes = _generate_docx(final_md, report, errors, warnings)
                st.download_button(
                    label="📝 下载 Word (docx)",
                    data=docx_bytes,
                    file_name=f"{pdf_name}_检查报告.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )

            with dl_col3:
                html_content = _generate_html(final_md, report.project_name)
                st.download_button(
                    label="🌐 下载 HTML (可打印PDF)",
                    data=html_content,
                    file_name=f"{pdf_name}_检查报告.html",
                    mime="text/html",
                    use_container_width=True,
                )

        except Exception as e:
            progress_bar.empty()
            status_container.update(label="❌ 处理失败", state="error")
            st.error(f"处理过程中出错: {e}")
            logger.exception("处理失败")

            with st.expander("📋 查看运行日志"):
                st.text("\n".join(log_records))

else:
    # ── 未上传文件时的欢迎页面 ────────────────────────
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.info("👆 请上传一个监测报告 PDF 文件开始检查")

        st.markdown("### 🔍 检查流程")
        st.markdown("""
1. **PDF 提取** — 默认先走 PaddleOCR 表格 profile，自动清洗图表噪声并落盘调试信息
2. **AI 结构化解析** — 用大语言模型理解不同公司的表格格式，提取为标准数据
3. **表格分析计划** — ReAct风格分析每张表的字段、单位、验证策略（透明展示AI理解过程）
4. **计算验证** — 逐条验证累计变化量、变化速率和深层位移本期变化（动态容差适配不同数据类型）
5. **统计验证** — 验证最大值/最小值统计，支持同监测项多页合并后再判定
6. **逻辑检查** — AI语义匹配阈值与分表，检查安全状态判定
7. **AI 自验证** — 对检出的错误进行二次确认，大幅减少误报
8. **生成报告** — 多格式导出（Markdown / Word / HTML）
        """)

    with col_right:
        st.markdown("### 📋 支持的监测项")
        st.markdown("""
- 支护结构顶部水平位移
- 支护结构顶部竖向位移
- 周边地面沉降 / 道路沉降
- 管线沉降
- 地下水位
- 锚索拉力 / 支撑轴力
- 深层水平位移 / 测斜
- 立柱位移 / 沉降
- 裂缝监测
        """)

        st.markdown("### ✨ 核心特点")
        st.markdown("""
- 🧠 AI 语义理解，适配不同公司格式
- 📐 动态容差，不硬编码规则
- 🔄 二次验证，减少误报
- 📊 多格式导出
        """)
