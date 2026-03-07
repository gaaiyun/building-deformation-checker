"""
PDF 数据提取工具。

默认优先使用 PaddleOCR，并在 OCR 输出后做结构压缩与调试落盘，
避免直接把超重 HTML markdown 喂给 LLM。
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import pdfplumber
import requests

from src.config import PADDLE_OCR_TOKEN, PADDLE_OCR_URL

logger = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE = 50
PADDLE_TABLE_PROFILE = {
    "markdownIgnoreLabels": [
        "header",
        "header_image",
        "footer",
        "footer_image",
        "number",
        "footnote",
        "aside_text",
    ],
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useLayoutDetection": True,
    "useChartRecognition": False,
    "useSealRecognition": True,
    "useOcrForImageBlock": False,
    "mergeTables": True,
    "relevelTitles": True,
    "layoutShapeMode": "auto",
    "promptLabel": "table",
    "repetitionPenalty": 1,
    "temperature": 0,
    "topP": 1,
    "minPixels": 147384,
    "maxPixels": 2822400,
    "layoutNms": True,
    "restructurePages": True,
    "visualize": False,
}
PADDLE_PRIMARY_PROFILE = {
    "markdownIgnoreLabels": [
        "header",
        "header_image",
        "footer",
        "footer_image",
        "number",
        "footnote",
        "aside_text",
    ],
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useLayoutDetection": False,
    "useChartRecognition": False,
    "useSealRecognition": True,
    "useOcrForImageBlock": False,
    "mergeTables": True,
    "relevelTitles": True,
    "layoutShapeMode": "auto",
    "promptLabel": "ocr",
    "repetitionPenalty": 1,
    "temperature": 0,
    "topP": 1,
    "minPixels": 147384,
    "maxPixels": 2822400,
    "layoutNms": True,
    "restructurePages": True,
    "visualize": False,
}
PADDLE_FALLBACK_PROFILE = {
    "markdownIgnoreLabels": [
        "header",
        "header_image",
        "footer",
        "footer_image",
        "number",
        "footnote",
        "aside_text",
    ],
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useLayoutDetection": True,
    "useChartRecognition": False,
    "useSealRecognition": True,
    "useOcrForImageBlock": False,
    "layoutShapeMode": "auto",
    "repetitionPenalty": 1,
    "temperature": 0,
    "topP": 1,
    "layoutNms": True,
    "visualize": False,
}
TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL | re.IGNORECASE)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
AXIS_NOISE_RE = re.compile(r"^[\d\-./年月日:~\sA-Za-z△☐○●]+$")
POINT_LEGEND_RE = re.compile(r"^[-•]\s*[A-Za-z]+\d+$")
MARKDOWN_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\([^)]+\)$")
KEEP_MARKERS = (
    "项目名称",
    "监测单位",
    "监测项",
    "监测项目",
    "监测日期",
    "监测次数",
    "监测点数量",
    "监测数据成果汇总",
    "数据统计",
    "备注",
    "测点编号",
    "测点深度",
    "测孔编号",
    "测孔深度",
    "深度",
    "点号",
    "初始",
    "上次累计量",
    "本次累计量",
    "本次变化量",
    "累计变化量",
    "本期变化",
    "变化速率",
    "安全状态",
    "最大变化速率",
    "最大变化位移",
    "最大内力",
    "最小内力",
    "当次累计",
)
CHART_SECTION_MARKERS = ("监测数据成果曲线图", "位移—深度曲线", "位移-深度曲线")


@dataclass
class PDFExtractionResult:
    text: str
    pages: list[str]
    method: str
    selected_profile: str
    diagnostics: dict
    debug_output_dir: str = ""


def _normalize_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def _clean_cell_text(cell_html: str) -> str:
    cell_html = re.sub(r"<br\s*/?>", " / ", cell_html, flags=re.IGNORECASE)
    cell_html = re.sub(r"<img[^>]*>", "", cell_html, flags=re.IGNORECASE)
    return _normalize_text(TAG_RE.sub("", cell_html))


def _contains_keep_marker(text: str) -> bool:
    return any(marker in text for marker in KEEP_MARKERS)


def _looks_like_axis_noise(text: str) -> bool:
    normalized = text.strip()
    if not normalized or _contains_keep_marker(normalized):
        return False
    if POINT_LEGEND_RE.fullmatch(normalized):
        return True
    digit_count = sum(ch.isdigit() for ch in normalized)
    if digit_count == 0:
        return False
    hyphen_count = normalized.count("-")
    if hyphen_count >= 10:
        return True
    if AXIS_NOISE_RE.fullmatch(normalized):
        return digit_count / max(len(normalized), 1) >= 0.35
    return False


def _should_drop_table(rows: list[list[str]]) -> bool:
    non_empty_cells = [cell for row in rows for cell in row if cell]
    if not non_empty_cells:
        return True
    if any(_contains_keep_marker(cell) for cell in non_empty_cells):
        return False
    axis_like_cells = sum(1 for cell in non_empty_cells if _looks_like_axis_noise(cell))
    single_value_rows = sum(1 for row in rows if sum(1 for cell in row if cell) <= 1)
    axis_ratio = axis_like_cells / len(non_empty_cells)
    single_row_ratio = single_value_rows / len(rows)
    return axis_ratio >= 0.8 or (axis_ratio >= 0.5 and single_row_ratio >= 0.6)


def _is_markdown_image(text: str) -> bool:
    return bool(MARKDOWN_IMAGE_RE.fullmatch(text.strip()))


def _filter_table_rows(rows: list[list[str]]) -> tuple[list[list[str]], int]:
    filtered_rows: list[list[str]] = []
    dropped_rows = 0
    in_chart_section = False

    for cells in rows:
        non_empty = [cell for cell in cells if cell]
        if not non_empty:
            continue

        row_text = " ".join(non_empty)
        if any(marker in row_text for marker in CHART_SECTION_MARKERS):
            in_chart_section = True
            dropped_rows += 1
            continue

        if all(_is_markdown_image(cell) for cell in non_empty):
            dropped_rows += 1
            continue

        if in_chart_section:
            if _contains_keep_marker(row_text):
                in_chart_section = False
            elif _looks_like_axis_noise(row_text) or all(_looks_like_axis_noise(cell) for cell in non_empty):
                dropped_rows += 1
                continue

        filtered_rows.append(cells)

    return filtered_rows, dropped_rows


def _convert_table_html(table_html: str) -> tuple[list[str], dict]:
    lines: list[str] = []
    rows: list[list[str]] = []
    for row_html in ROW_RE.findall(table_html):
        cells = [_clean_cell_text(cell_html) for cell_html in CELL_RE.findall(row_html)]
        while cells and cells[-1] == "":
            cells.pop()
        if not cells or not any(cell for cell in cells):
            continue
        rows.append(cells)
    rows, dropped_rows = _filter_table_rows(rows)
    row_count = len(rows)
    cell_count = sum(len(row) for row in rows)
    if _should_drop_table(rows):
        return [], {"row_count": row_count, "cell_count": cell_count, "dropped": True, "dropped_rows": dropped_rows}
    for cells in rows:
        lines.append("| " + " | ".join(cell or " " for cell in cells) + " |")
    return lines, {"row_count": row_count, "cell_count": cell_count, "dropped": False, "dropped_rows": dropped_rows}


def _clean_non_table_html(text: str) -> list[str]:
    text = re.sub(r"<img[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(div|p|section|article|header|footer|aside|span|strong|em|b|i|h[1-6])[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = _normalize_text(raw_line)
        if line:
            lines.append(line)
    return lines


def _clean_ocr_markdown(markdown_text: str) -> tuple[str, dict]:
    table_summaries: list[dict] = []
    table_blocks: list[list[str]] = []

    def _replace_table(match: re.Match[str]) -> str:
        table_lines, summary = _convert_table_html(match.group(0))
        table_index = len(table_blocks)
        table_blocks.append(table_lines)
        table_summaries.append(summary)
        return f"\n__TABLE_BLOCK_{table_index}__\n"

    replaced = TABLE_RE.sub(_replace_table, markdown_text)
    clean_lines: list[str] = []
    chart_noise_lines_removed = 0
    in_chart_section = False
    for raw_line in _clean_non_table_html(replaced):
        table_match = re.fullmatch(r"__TABLE_BLOCK_(\d+)__", raw_line)
        if table_match:
            table_index = int(table_match.group(1))
            table_lines = table_blocks[table_index]
            if table_lines:
                clean_lines.extend(table_lines)
                in_chart_section = False
            continue

        if any(marker in raw_line for marker in CHART_SECTION_MARKERS):
            in_chart_section = True
            chart_noise_lines_removed += 1
            continue

        if in_chart_section and _looks_like_axis_noise(raw_line):
            chart_noise_lines_removed += 1
            continue

        if raw_line == "Image" or _is_markdown_image(raw_line):
            chart_noise_lines_removed += 1
            continue

        if _contains_keep_marker(raw_line):
            in_chart_section = False

        clean_lines.append(raw_line)

    deduped_lines: list[str] = []
    previous_line = ""
    for line in clean_lines:
        if line == previous_line and not line.startswith("|"):
            continue
        deduped_lines.append(line)
        previous_line = line

    clean_text = "\n".join(deduped_lines).strip()
    plain_text = _normalize_text(TAG_RE.sub("", markdown_text))
    diagnostics = {
        "raw_chars": len(markdown_text),
        "clean_chars": len(clean_text),
        "plain_chars": len(plain_text),
        "markup_chars": len(markdown_text) - len(plain_text),
        "markup_ratio": round((len(markdown_text) - len(plain_text)) / len(markdown_text), 4) if markdown_text else 0.0,
        "line_count": len(deduped_lines),
        "table_count": sum(1 for summary in table_summaries if not summary.get("dropped")),
        "dropped_table_count": sum(1 for summary in table_summaries if summary.get("dropped")),
        "table_rows": sum(summary["row_count"] for summary in table_summaries if not summary.get("dropped")),
        "table_cells": sum(summary["cell_count"] for summary in table_summaries if not summary.get("dropped")),
        "chart_noise_lines_removed": chart_noise_lines_removed + sum(summary.get("dropped_rows", 0) for summary in table_summaries),
    }
    return clean_text, diagnostics


def _assess_text_quality(text: str, page_count: int) -> bool:
    if page_count == 0:
        return False
    avg_chars = len(text) / page_count
    if avg_chars < MIN_CHARS_PER_PAGE:
        return False
    key_markers = ("监测", "测点", "变化", "累计", "速率")
    return sum(1 for marker in key_markers if marker in text) >= 2


def _compute_identical_page_pairs(pages: list[str]) -> list[tuple[int, int]]:
    normalized = ["\n".join(line.strip() for line in page.splitlines() if line.strip()) for page in pages]
    identical_pairs: list[tuple[int, int]] = []
    for i in range(len(normalized)):
        if not normalized[i]:
            continue
        for j in range(i + 1, len(normalized)):
            if normalized[i] == normalized[j]:
                identical_pairs.append((i + 1, j + 1))
    return identical_pairs


def _write_debug_artifacts(
    debug_output_dir: str,
    raw_pages: list[str],
    clean_pages: list[str],
    page_stats: list[dict],
    request_profile: dict,
    selected_profile: str,
) -> None:
    debug_dir = Path(debug_output_dir)
    raw_dir = debug_dir / "raw"
    clean_dir = debug_dir / "clean"
    raw_dir.mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)

    for page_index, raw_page in enumerate(raw_pages, 1):
        (raw_dir / f"page_{page_index:03d}.md").write_text(raw_page, encoding="utf-8")
    for page_index, clean_page in enumerate(clean_pages, 1):
        (clean_dir / f"page_{page_index:03d}.txt").write_text(clean_page, encoding="utf-8")

    (debug_dir / "stats.json").write_text(
        json.dumps({
            "selected_profile": selected_profile,
            "pages": page_stats,
            "identical_page_pairs": _compute_identical_page_pairs(clean_pages),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (debug_dir / "request_profile.json").write_text(
        json.dumps(request_profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def extract_text_with_pdfplumber(pdf_path: str) -> str:
    """用 pdfplumber 提取文字版 PDF 的全部文本（按页分隔）。"""
    pages_text: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            pages_text.append(f"--- 第 {i} 页 / 共 {len(pdf.pages)} 页 ---\n{text}")
    return "\n\n".join(pages_text)


def extract_tables_with_pdfplumber(pdf_path: str) -> list[dict]:
    """用 pdfplumber 提取每页的表格，返回结构化数据。"""
    results: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                results.append({"page": i, "table_index": j, "rows": table})
    return results


def _call_paddle_ocr(pdf_path: str, profile: dict) -> dict:
    file_data = base64.b64encode(Path(pdf_path).read_bytes()).decode("ascii")
    payload = {"file": file_data, "fileType": 0, **profile}
    headers = {
        "Authorization": f"token {PADDLE_OCR_TOKEN}",
        "Content-Type": "application/json",
    }
    logger.info("正在调用 PaddleOCR API 解析 %s ...", pdf_path)
    response = requests.post(PADDLE_OCR_URL, json=payload, headers=headers, timeout=300)
    response.raise_for_status()
    return response.json()["result"]


def _extract_with_paddle_profile(
    pdf_path: str,
    profile_name: str,
    profile: dict,
    debug_output_dir: Optional[str] = None,
) -> PDFExtractionResult:
    result = _call_paddle_ocr(pdf_path, profile)
    raw_pages: list[str] = []
    clean_pages: list[str] = []
    page_stats: list[dict] = []

    for page_index, page_result in enumerate(result["layoutParsingResults"], 1):
        raw_markdown = page_result["markdown"]["text"]
        clean_text, stats = _clean_ocr_markdown(raw_markdown)
        raw_pages.append(raw_markdown)
        clean_pages.append(clean_text)
        page_stats.append({
            "page": page_index,
            **stats,
            "markdown_images": len(page_result["markdown"].get("images", {})),
            "output_images": len(page_result.get("outputImages") or {}),
        })

    joined_text = "\n\n".join(
        f"--- 第 {page_index} 页 ---\n{page_text}"
        for page_index, page_text in enumerate(clean_pages, 1)
    )
    diagnostics = {
        "pages": page_stats,
        "page_count": len(clean_pages),
        "raw_chars": sum(item["raw_chars"] for item in page_stats),
        "clean_chars": sum(item["clean_chars"] for item in page_stats),
        "plain_chars": sum(item["plain_chars"] for item in page_stats),
        "high_markup_pages": [item["page"] for item in page_stats if item["markup_ratio"] >= 0.9],
        "identical_page_pairs": _compute_identical_page_pairs(clean_pages),
    }
    diagnostics["compression_ratio"] = round(
        diagnostics["clean_chars"] / diagnostics["raw_chars"], 4
    ) if diagnostics["raw_chars"] else 0.0

    if debug_output_dir:
        _write_debug_artifacts(
            debug_output_dir,
            raw_pages,
            clean_pages,
            page_stats,
            {"selected_profile": profile_name, "profile": profile},
            profile_name,
        )

    logger.info(
        "PaddleOCR 解析完成，共 %d 页，原始字符 %d，清洗后字符 %d",
        len(clean_pages), diagnostics["raw_chars"], diagnostics["clean_chars"],
    )
    return PDFExtractionResult(
        text=joined_text,
        pages=clean_pages,
        method="paddle_ocr",
        selected_profile=profile_name,
        diagnostics=diagnostics,
        debug_output_dir=debug_output_dir or "",
    )


def extract_with_paddle_ocr(
    pdf_path: str,
    output_dir: Optional[str] = None,
) -> list[str]:
    """
    调用 PaddleOCR，并返回清洗后的逐页文本。
    默认使用表格优先 profile；调试信息按需写入 output_dir。
    """
    result = _extract_with_paddle_profile(
        pdf_path,
        profile_name="table",
        profile=PADDLE_TABLE_PROFILE,
        debug_output_dir=output_dir,
    )
    return result.pages


def extract_pdf(
    pdf_path: str,
    use_ocr: bool = False,
    prefer_ocr: bool = False,
    auto_fallback: bool = True,
    ocr_output_dir: Optional[str] = None,
    return_details: bool = False,
) -> str | PDFExtractionResult:
    """
    统一入口：提取 PDF 内容为纯文本。

    默认优先使用 pdfplumber；若文本层质量不足则自动切换 OCR。
    当 prefer_ocr/use_ocr 为真时，先尝试 PaddleOCR，再回退 pdfplumber。
    """
    if use_ocr or prefer_ocr:
        mode = "强制 PaddleOCR" if use_ocr else "优先 PaddleOCR"
        logger.info("使用 %s 模式提取", mode)
        attempts: list[dict] = []
        for profile_name, profile in (
            ("table", PADDLE_TABLE_PROFILE),
            ("primary", PADDLE_PRIMARY_PROFILE),
            ("fallback", PADDLE_FALLBACK_PROFILE),
        ):
            try:
                debug_dir = ocr_output_dir if profile_name == "table" else None
                result = _extract_with_paddle_profile(
                    pdf_path,
                    profile_name=profile_name,
                    profile=profile,
                    debug_output_dir=debug_dir,
                )
                attempts.append({
                    "profile": profile_name,
                    "clean_chars": result.diagnostics["clean_chars"],
                    "page_count": result.diagnostics["page_count"],
                    "compression_ratio": result.diagnostics["compression_ratio"],
                })
                result.diagnostics["attempts"] = attempts
                result.diagnostics["debug_dir"] = result.debug_output_dir
                if profile_name == "primary" and not auto_fallback:
                    return result if return_details else result.text
                if _assess_text_quality(result.text, result.diagnostics["page_count"]):
                    return result if return_details else result.text
                logger.warning("OCR %s profile 清洗后文本质量一般，尝试回退 profile", profile_name)
            except Exception as exc:
                attempts.append({"profile": profile_name, "error": str(exc)})
                logger.warning("PaddleOCR %s profile 失败: %s", profile_name, exc)
                if use_ocr and not auto_fallback:
                    raise

        logger.warning("PaddleOCR 不可用或质量不足，回退 pdfplumber")
        text = extract_text_with_pdfplumber(pdf_path)
        diagnostics = {
            "page_count": text.count("--- 第 "),
            "raw_chars": len(text),
            "clean_chars": len(text),
            "plain_chars": len(text),
            "compression_ratio": 1.0,
            "high_markup_pages": [],
            "identical_page_pairs": [],
            "attempts": attempts,
            "debug_dir": ocr_output_dir or "",
            "method": "pdfplumber",
        }
        result = PDFExtractionResult(
            text=text,
            pages=[text],
            method="pdfplumber",
            selected_profile="pdfplumber",
            diagnostics=diagnostics,
            debug_output_dir=ocr_output_dir or "",
        )
        return result if return_details else result.text

    logger.info("使用 pdfplumber 模式提取")
    text = extract_text_with_pdfplumber(pdf_path)
    if auto_fallback and not _assess_text_quality(text, max(1, text.count("--- 第 "))):
        logger.info("pdfplumber 提取效果不佳，自动切换到 PaddleOCR")
        attempts: list[dict] = []
        for profile_name, profile in (
            ("table", PADDLE_TABLE_PROFILE),
            ("primary", PADDLE_PRIMARY_PROFILE),
            ("fallback", PADDLE_FALLBACK_PROFILE),
        ):
            try:
                debug_dir = ocr_output_dir if profile_name == "table" else None
                result = _extract_with_paddle_profile(
                    pdf_path,
                    profile_name=profile_name,
                    profile=profile,
                    debug_output_dir=debug_dir,
                )
                attempts.append({
                    "profile": profile_name,
                    "clean_chars": result.diagnostics["clean_chars"],
                    "page_count": result.diagnostics["page_count"],
                    "compression_ratio": result.diagnostics["compression_ratio"],
                })
                result.diagnostics["attempts"] = attempts
                result.diagnostics["debug_dir"] = result.debug_output_dir
                if _assess_text_quality(result.text, result.diagnostics["page_count"]):
                    return result if return_details else result.text
                logger.warning("自动切换 OCR 后，%s profile 质量一般，继续尝试下一 profile", profile_name)
            except Exception as exc:
                attempts.append({"profile": profile_name, "error": str(exc)})
                logger.warning("自动切换 OCR 时，%s profile 失败: %s", profile_name, exc)

    result = PDFExtractionResult(
        text=text,
        pages=[text],
        method="pdfplumber",
        selected_profile="pdfplumber",
        diagnostics={
            "page_count": text.count("--- 第 "),
            "raw_chars": len(text),
            "clean_chars": len(text),
            "plain_chars": len(text),
            "compression_ratio": 1.0,
            "high_markup_pages": [],
            "identical_page_pairs": [],
            "attempts": [],
            "debug_dir": ocr_output_dir or "",
            "method": "pdfplumber",
        },
        debug_output_dir=ocr_output_dir or "",
    )
    return result if return_details else result.text
