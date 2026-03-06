"""
建筑变形监测报告检查智能体 — Streamlit Web UI
"""

import logging
import tempfile
from pathlib import Path

import streamlit as st

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("app")

st.set_page_config(page_title="建筑变形监测报告检查", page_icon="🏗️", layout="wide")

st.title("🏗️ 建筑变形监测报告检查智能体")
st.caption("上传监测报告PDF，自动提取数据、验证计算、检查统计和逻辑")

with st.sidebar:
    st.header("⚙️ 设置")
    use_ocr = st.checkbox("使用 PaddleOCR（扫描件）", value=False)
    do_ai_review = st.checkbox("AI 最终审核", value=True)
    do_self_verify = st.checkbox("AI 自验证（确认错误）", value=True)
    st.divider()
    st.markdown("**计算公式**")
    st.markdown("""
    - 本次变化 = 本次测值 − 上次测值
    - 累计变化 = 本次测值 − 初始测值
    - 变化速率 = 本次变化 / 时间(天)
    """)
    st.divider()
    st.markdown("**注意**")
    st.markdown("正负号代表**方向**，不代表大小")

uploaded = st.file_uploader("上传监测报告 PDF", type=["pdf"], accept_multiple_files=False)

if uploaded is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded.read())
        tmp_path = tmp.name

    pdf_name = Path(uploaded.name).stem

    if st.button("🚀 开始检查", type="primary", use_container_width=True):
        progress = st.progress(0, text="准备中...")
        status_area = st.empty()

        try:
            # Step 1
            progress.progress(5, text="Step 1/8: 提取PDF内容...")
            status_area.info("正在提取PDF文本...")
            from src.tools.pdf_extractor import extract_pdf
            raw_text = extract_pdf(tmp_path, use_ocr=use_ocr)
            status_area.success(f"PDF提取完成，共 {len(raw_text)} 字符")

            # Step 2
            progress.progress(10, text="Step 2/8: LLM结构化解析...")
            status_area.info("正在调用AI解析表格数据（可能需要1-3分钟）...")
            from src.tools.llm_parser import parse_report_with_llm
            report = parse_report_with_llm(raw_text)
            report.raw_text = raw_text

            # Step 2b: Enrich configs
            progress.progress(35, text="Step 2b/8: 动态配置优化...")
            from src.tools.table_analyzer import enrich_configs_with_llm
            enrich_configs_with_llm(report)
            status_area.success(f"解析完成: {report.project_name}，{len(report.tables)}张表")

            # Step 3
            progress.progress(40, text="Step 3/8: 计算验证...")
            from src.tools.calculation_checker import run_calculation_checks
            calc_issues = run_calculation_checks(report)

            # Step 4
            progress.progress(50, text="Step 4/8: 统计验证...")
            from src.tools.statistics_checker import run_statistics_checks
            stats_issues = run_statistics_checks(report)

            # Step 5
            progress.progress(55, text="Step 5/8: 逻辑检查（语义匹配）...")
            from src.tools.logic_checker import run_logic_checks
            logic_issues = run_logic_checks(report)

            # Step 6: Self-verify
            all_issues = calc_issues + stats_issues + logic_issues
            if do_self_verify:
                errors = [i for i in all_issues if i.severity == "error"]
                if errors:
                    progress.progress(65, text="Step 6/8: AI自验证...")
                    status_area.info(f"AI正在确认 {len(errors)} 个错误...")
                    from src.tools.self_verifier import verify_errors_with_llm
                    all_issues = verify_errors_with_llm(report, all_issues)

            # Step 7
            ai_review = ""
            if do_ai_review:
                progress.progress(75, text="Step 7/8: AI最终审核...")
                status_area.info("AI专家审核中...")
                from src.tools.report_generator import generate_report_md
                from src.tools.llm_parser import verify_report_with_llm
                prelim = generate_report_md(report, calc_issues, stats_issues, logic_issues)
                ai_review = verify_report_with_llm(prelim, raw_text)

            progress.progress(90, text="Step 8/8: 生成检查报告...")
            from src.tools.report_generator import generate_report_md, save_report
            final_md = generate_report_md(report, calc_issues, stats_issues, logic_issues, ai_review)
            output_path = f"output/{pdf_name}_检查报告.md"
            save_report(final_md, output_path)

            progress.progress(100, text="检查完成!")
            status_area.empty()

            # ── 结果展示 ──────────────────────────────
            errors = [i for i in all_issues if i.severity == "error"]
            warnings = [i for i in all_issues if i.severity == "warning"]
            infos = [i for i in all_issues if i.severity == "info"]

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("数据表", f"{len(report.tables)} 张")
            col2.metric("错误", f"{len(errors)} 个", delta=None if not errors else f"-{len(errors)}", delta_color="inverse")
            col3.metric("警告", f"{len(warnings)} 个")
            col4.metric("提示", f"{len(infos)} 个")

            tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 检查报告", "🔢 计算验证", "📈 统计验证", "🔍 逻辑检查", "🤖 AI审核"])

            with tab1:
                st.markdown(final_md)

            with tab2:
                if not calc_issues:
                    st.success("计算验证全部通过")
                else:
                    for issue in calc_issues:
                        if issue.severity == "error":
                            st.error(f"**{issue.table_name}** | {issue.point_id} | {issue.message}")
                        elif issue.severity == "warning":
                            st.warning(f"**{issue.table_name}** | {issue.point_id} | {issue.message}")
                        else:
                            st.info(issue.message)

            with tab3:
                if not stats_issues:
                    st.success("统计验证全部通过")
                else:
                    for issue in stats_issues:
                        if issue.severity == "error":
                            st.error(f"**{issue.table_name}** | {issue.point_id} | {issue.message}")
                        else:
                            st.warning(f"**{issue.table_name}** | {issue.message}")

            with tab4:
                if not logic_issues:
                    st.success("逻辑检查全部通过")
                else:
                    for issue in logic_issues:
                        if issue.severity == "error":
                            st.error(f"**{issue.table_name}** | {issue.point_id} | {issue.message}")
                        elif issue.severity == "warning":
                            st.warning(f"**{issue.table_name}** | {issue.point_id} | {issue.message}")
                        else:
                            st.info(issue.message)

            with tab5:
                if ai_review:
                    st.markdown(ai_review)
                else:
                    st.info("未启用AI审核")

            st.divider()
            st.download_button(
                label="📥 下载检查报告 (Markdown)",
                data=final_md,
                file_name=f"{pdf_name}_检查报告.md",
                mime="text/markdown",
                use_container_width=True,
            )

        except Exception as e:
            progress.empty()
            status_area.empty()
            st.error(f"处理过程中出错: {e}")
            logger.exception("处理失败")
else:
    st.info("👆 请上传一个监测报告PDF文件开始检查")

    with st.expander("ℹ️ 系统说明"):
        st.markdown("""
### 支持的监测项类型
- 支护结构顶部水平位移 / 基坑顶位移
- 支护结构顶部竖向位移 / 基坑顶沉降
- 周边地面沉降 / 道路沉降
- 管线沉降
- 地下水位 / 水位监测
- 锚索拉力 / 支撑轴力
- 深层水平位移 / 支护桩测斜

### 检查内容
1. **计算验证**: 逐条验证累计变化量、变化速率（动态容差）
2. **统计验证**: 验证最大值/最小值/最大速率统计（方向性+跨表检查）
3. **逻辑检查**: 安全状态判定、汇总表一致性（AI语义匹配）
4. **AI自验证**: 对检出的错误进行二次确认，减少误报
5. **AI审核**: 由AI专家对检查结果做最终确认
        """)
