"""共享的报告导出格式：DOCX 与 HTML

把原本嵌在 app.py 内的 _generate_docx / _generate_html 抽出，
让 Streamlit、PySide6 桌面、CLI 等不同 UI 共用同一套导出逻辑。
"""

from __future__ import annotations

import io
from datetime import datetime

from src.tools.extraction_quality import append_issue_source_hint


def generate_docx(md_content: str, report, errors: list, warnings: list) -> bytes:
    """生成 Word 文档（.docx 二进制内容）"""
    from docx import Document
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()

    def set_run_style(run, *, size: float = 10.5, bold: bool = False) -> None:
        run.font.name = "Microsoft YaHei"
        r_pr = run._element.get_or_add_rPr()
        r_fonts = r_pr.rFonts
        if r_fonts is None:
            r_fonts = OxmlElement("w:rFonts")
            r_pr.append(r_fonts)
        r_fonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)

    def style_paragraph(
        paragraph,
        *,
        size: float = 10.5,
        bold: bool = False,
        align=WD_ALIGN_PARAGRAPH.LEFT,
        space_before: float = 0,
        space_after: float = 4,
        line_spacing: float = 1.35,
    ) -> None:
        paragraph.alignment = align
        paragraph.paragraph_format.space_before = Pt(space_before)
        paragraph.paragraph_format.space_after = Pt(space_after)
        paragraph.paragraph_format.line_spacing = line_spacing
        if not paragraph.runs:
            run = paragraph.add_run("")
            set_run_style(run, size=size, bold=bold)
        for run in paragraph.runs:
            set_run_style(run, size=size, bold=bool(run.bold) or bold)

    def shade_cell(cell, fill: str = "F3F4F6") -> None:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:fill"), fill)
        tc_pr.append(shd)

    def set_cell_text(cell, text: str, *, bold: bool = False, align=WD_ALIGN_PARAGRAPH.LEFT, fill: str | None = None) -> None:
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = align
        run = p.add_run(text if text else "-")
        set_run_style(run, size=10.5, bold=bold)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.2
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        if fill:
            shade_cell(cell, fill)

    for section in doc.sections:
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.2)
        section.left_margin = Cm(2.4)
        section.right_margin = Cm(2.4)

    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Microsoft YaHei"
    normal_rpr = normal_style._element.get_or_add_rPr()
    normal_rfonts = normal_rpr.rFonts
    if normal_rfonts is None:
        normal_rfonts = OxmlElement("w:rFonts")
        normal_rpr.append(normal_rfonts)
    normal_rfonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    normal_style.font.size = Pt(10.5)
    normal_style.font.color.rgb = RGBColor(0x00, 0x00, 0x00)

    title = doc.add_paragraph()
    title_run = title.add_run("建筑变形监测报告检查报告")
    set_run_style(title_run, size=18, bold=True)
    style_paragraph(title, size=18, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)

    subtitle = doc.add_paragraph()
    subtitle_run = subtitle.add_run("自动核验输出文件")
    set_run_style(subtitle_run, size=10.5)
    style_paragraph(subtitle, size=10.5, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=14)

    overview_heading = doc.add_paragraph()
    overview_run = overview_heading.add_run("一、报告概览")
    set_run_style(overview_run, size=13, bold=True)
    style_paragraph(overview_heading, size=13, bold=True, space_after=6)

    overview = doc.add_table(rows=5, cols=2, style="Table Grid")
    overview.alignment = WD_TABLE_ALIGNMENT.CENTER
    overview_rows = [
        ("项目名称", report.project_name or "-"),
        ("监测单位", report.monitoring_company or "-"),
        ("报告编号", report.report_number or "-"),
        ("监测日期", report.monitoring_date or "-"),
        ("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    ]
    for row_idx, (label, value) in enumerate(overview_rows):
        set_cell_text(overview.cell(row_idx, 0), label, bold=True, fill="F3F4F6")
        set_cell_text(overview.cell(row_idx, 1), str(value))

    doc.add_paragraph("")

    diagnostics = report.extraction_diagnostics or {}
    extraction_label = diagnostics.get("method", "unknown")
    if diagnostics.get("selected_profile"):
        extraction_label += f" / {diagnostics['selected_profile']}"

    summary_heading = doc.add_paragraph()
    summary_run = summary_heading.add_run("二、检查摘要")
    set_run_style(summary_run, size=13, bold=True)
    style_paragraph(summary_heading, size=13, bold=True, space_after=6)

    summary = doc.add_table(rows=5, cols=4, style="Table Grid")
    summary.alignment = WD_TABLE_ALIGNMENT.CENTER
    for idx, header in enumerate(["指标", "数值", "指标", "数值"]):
        set_cell_text(summary.cell(0, idx), header, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, fill="EDEFF3")

    summary_pairs = [
        ("数据表", str(len(report.tables)), "错误", str(len(errors))),
        ("警告", str(len(warnings)), "提取方式", extraction_label),
        ("原始字符", f"{diagnostics.get('raw_chars', 0):,}", "清洗后字符", f"{diagnostics.get('clean_chars', 0):,}"),
        ("压缩率", f"{diagnostics.get('compression_ratio', 0.0):.1%}", "疑似异常表", str(diagnostics.get("abnormal_table_count", 0))),
    ]
    for row_idx, values in enumerate(summary_pairs, start=1):
        for col_idx, value in enumerate(values):
            align = WD_ALIGN_PARAGRAPH.CENTER if col_idx % 2 else WD_ALIGN_PARAGRAPH.LEFT
            set_cell_text(summary.cell(row_idx, col_idx), value, align=align)

    if report.table_extraction_flags:
        diag_note = doc.add_paragraph()
        diag_run = diag_note.add_run(
            "提取提示："
            + "；".join(
                f"{report.tables[idx].monitoring_item}{f' ({report.tables[idx].borehole_id})' if report.tables[idx].borehole_id else ''}：{'；'.join(flags)}"
                for idx, flags in sorted(report.table_extraction_flags.items())
                if idx < len(report.tables)
            )
        )
        set_run_style(diag_run, size=10)
        style_paragraph(diag_note, size=10, space_before=4, space_after=6)

    doc.add_paragraph("")

    issues_heading = doc.add_paragraph()
    issues_run = issues_heading.add_run("三、问题清单")
    set_run_style(issues_run, size=13, bold=True)
    style_paragraph(issues_heading, size=13, bold=True, space_after=6)

    def add_issue_table(title_text: str, issue_list: list) -> None:
        title_p = doc.add_paragraph()
        title_r = title_p.add_run(title_text)
        set_run_style(title_r, size=11.5, bold=True)
        style_paragraph(title_p, size=11.5, bold=True, space_before=2, space_after=4)
        if not issue_list:
            empty_p = doc.add_paragraph()
            empty_r = empty_p.add_run("未发现需关注的问题。")
            set_run_style(empty_r, size=10.5)
            style_paragraph(empty_p, size=10.5, space_after=6)
            return
        table = doc.add_table(rows=1, cols=5, style="Table Grid")
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        headers = ["序号", "表名", "测点", "字段", "说明"]
        for idx, header in enumerate(headers):
            set_cell_text(table.cell(0, idx), header, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, fill="EDEFF3")
        for idx, issue in enumerate(issue_list, start=1):
            row = table.add_row().cells
            set_cell_text(row[0], str(idx), align=WD_ALIGN_PARAGRAPH.CENTER)
            set_cell_text(row[1], issue.table_name)
            set_cell_text(row[2], issue.point_id or "-", align=WD_ALIGN_PARAGRAPH.CENTER)
            set_cell_text(row[3], issue.field_name or "-", align=WD_ALIGN_PARAGRAPH.CENTER)
            set_cell_text(row[4], append_issue_source_hint(issue.message, issue.suspected_source))
        doc.add_paragraph("")

    add_issue_table("3.1 错误项", errors)
    add_issue_table("3.2 警告项", warnings)

    conclusion_heading = doc.add_paragraph()
    conclusion_run = conclusion_heading.add_run("四、结论")
    set_run_style(conclusion_run, size=13, bold=True)
    style_paragraph(conclusion_heading, size=13, bold=True, space_after=6)

    if report.conclusion:
        original_conclusion = doc.add_paragraph()
        original_run = original_conclusion.add_run(f"报告原文结论：{report.conclusion}")
        set_run_style(original_run, size=10.5)
        style_paragraph(original_conclusion, size=10.5, space_after=4)

    system_conclusion = doc.add_paragraph()
    if errors:
        result_text = f"自动检查结论：共发现 {len(errors)} 条错误、{len(warnings)} 条警告，建议结合原始报告进行人工复核。"
    elif warnings:
        result_text = f"自动检查结论：未发现错误，存在 {len(warnings)} 条警告，建议按需复核。"
    else:
        result_text = "自动检查结论：本次自动检查未发现错误或警告。"
    result_run = system_conclusion.add_run(result_text)
    set_run_style(result_run, size=10.5)
    style_paragraph(system_conclusion, size=10.5, space_after=10)

    footer = doc.add_paragraph()
    footer_run = footer.add_run("本文件由建筑变形监测报告核验台自动生成。")
    set_run_style(footer_run, size=9)
    style_paragraph(footer, size=9, align=WD_ALIGN_PARAGRAPH.CENTER, space_before=8, space_after=0)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def generate_html(md_content: str, project_name: str) -> str:
    """生成可打印的 HTML 报告（可在浏览器中打印为 PDF）"""
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


__all__ = ["generate_docx", "generate_html"]
