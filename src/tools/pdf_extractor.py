"""
PDF 数据提取工具

两种提取方式:
1. pdfplumber: 适用于文字版 PDF，直接提取文本和表格
2. PaddleOCR: 适用于扫描件，通过 API 进行版式分析
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Optional

import pdfplumber
import requests

from src.config import PADDLE_OCR_TOKEN, PADDLE_OCR_URL

logger = logging.getLogger(__name__)


def extract_text_with_pdfplumber(pdf_path: str) -> str:
    """用 pdfplumber 提取文字版 PDF 的全部文本（按页分隔）"""
    pages_text: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            pages_text.append(f"--- 第 {i} 页 / 共 {len(pdf.pages)} 页 ---\n{text}")
    return "\n\n".join(pages_text)


def extract_tables_with_pdfplumber(pdf_path: str) -> list[dict]:
    """用 pdfplumber 提取每页的表格，返回 [{page, table_index, rows}, ...]"""
    results: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                results.append({
                    "page": i,
                    "table_index": j,
                    "rows": table,
                })
    return results


def extract_with_paddle_ocr(
    pdf_path: str,
    output_dir: Optional[str] = None,
) -> list[str]:
    """
    调用 PaddleOCR 版式分析 API（适用于扫描件）。
    返回每页的 markdown 文本列表。
    """
    with open(pdf_path, "rb") as f:
        file_data = base64.b64encode(f.read()).decode("ascii")

    headers = {
        "Authorization": f"token {PADDLE_OCR_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "file": file_data,
        "fileType": 0,  # PDF
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }

    logger.info("调用 PaddleOCR API 解析 %s ...", pdf_path)
    resp = requests.post(PADDLE_OCR_URL, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    result = resp.json()["result"]

    md_pages: list[str] = []
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    for i, res in enumerate(result["layoutParsingResults"]):
        md_text = res["markdown"]["text"]
        md_pages.append(md_text)

        if output_dir:
            md_file = Path(output_dir) / f"page_{i + 1}.md"
            md_file.write_text(md_text, encoding="utf-8")
            for img_path, img_url in res["markdown"]["images"].items():
                full_path = Path(output_dir) / img_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                img_bytes = requests.get(img_url, timeout=30).content
                full_path.write_bytes(img_bytes)

    logger.info("PaddleOCR 解析完成，共 %d 页", len(md_pages))
    return md_pages


def extract_pdf(
    pdf_path: str,
    use_ocr: bool = False,
    ocr_output_dir: Optional[str] = None,
) -> str:
    """
    统一入口：提取 PDF 内容为纯文本。
    use_ocr=True 时走 PaddleOCR，否则走 pdfplumber。
    """
    if use_ocr:
        pages = extract_with_paddle_ocr(pdf_path, ocr_output_dir)
        return "\n\n".join(
            f"--- 第 {i} 页 ---\n{p}" for i, p in enumerate(pages, 1)
        )
    return extract_text_with_pdfplumber(pdf_path)
