"""
PDF 数据提取工具

两种提取方式，支持智能切换:
1. pdfplumber: 适用于文字版 PDF，直接提取文本和表格
2. PaddleOCR: 适用于扫描件或pdfplumber效果不好时，通过 API 进行版式分析

智能策略：
- 默认用 pdfplumber 提取
- 如果提取的文本太短或表格太少，自动切换到 PaddleOCR
- 用户也可以手动指定用 PaddleOCR
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

# 如果每页平均提取字符数低于此阈值，认为 pdfplumber 效果不好
MIN_CHARS_PER_PAGE = 50


def extract_text_with_pdfplumber(pdf_path: str) -> str:
    """用 pdfplumber 提取文字版 PDF 的全部文本（按页分隔）"""
    pages_text: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            pages_text.append(f"--- 第 {i} 页 / 共 {len(pdf.pages)} 页 ---\n{text}")
    return "\n\n".join(pages_text)


def extract_tables_with_pdfplumber(pdf_path: str) -> list[dict]:
    """用 pdfplumber 提取每页的表格，返回结构化数据"""
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
    调用 PaddleOCR 版式分析 API（适用于扫描件或表格提取效果不好的PDF）。
    返回每页的 markdown 文本列表。

    PaddleOCR 的版式分析能力更强，特别是对于：
    - 扫描件PDF
    - 复杂表格布局
    - 图文混排的页面
    """
    with open(pdf_path, "rb") as f:
        file_data = base64.b64encode(f.read()).decode("ascii")

    headers = {
        "Authorization": f"token {PADDLE_OCR_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "file": file_data,
        "fileType": 0,  # PDF文档设为0，图片设为1
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useChartRecognition": False,
    }

    logger.info("正在调用 PaddleOCR API 解析 %s ...", pdf_path)
    resp = requests.post(PADDLE_OCR_URL, json=payload, headers=headers, timeout=300)
    resp.raise_for_status()
    result = resp.json()["result"]

    md_pages: list[str] = []
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    for i, res in enumerate(result["layoutParsingResults"]):
        md_text = res["markdown"]["text"]
        md_pages.append(md_text)

        # 保存每页的 markdown 和图片到输出目录
        if output_dir:
            md_file = Path(output_dir) / f"page_{i + 1}.md"
            md_file.write_text(md_text, encoding="utf-8")
            for img_path, img_url in res["markdown"]["images"].items():
                full_path = Path(output_dir) / img_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    img_bytes = requests.get(img_url, timeout=30).content
                    full_path.write_bytes(img_bytes)
                except Exception:
                    pass

    logger.info("PaddleOCR 解析完成，共 %d 页", len(md_pages))
    return md_pages


def _assess_pdfplumber_quality(text: str, pdf_path: str) -> bool:
    """
    评估 pdfplumber 提取质量。
    返回 True 表示质量可接受，False 表示需要切换到 OCR。

    判断标准：
    1. 每页平均字符数是否足够
    2. 是否包含关键的监测表格标志词
    """
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)

    if total_pages == 0:
        return False

    avg_chars = len(text) / total_pages
    if avg_chars < MIN_CHARS_PER_PAGE:
        logger.warning(
            "pdfplumber 提取质量不佳: 平均每页仅 %.0f 字符 (阈值 %d)",
            avg_chars, MIN_CHARS_PER_PAGE,
        )
        return False

    # 检查是否包含监测报告的关键标志词
    key_markers = ["监测", "测点", "变化", "累计", "速率"]
    found = sum(1 for m in key_markers if m in text)
    if found < 2:
        logger.warning("pdfplumber 提取的文本中缺少关键监测词汇，可能是扫描件")
        return False

    return True


def extract_pdf(
    pdf_path: str,
    use_ocr: bool = False,
    auto_fallback: bool = True,
    ocr_output_dir: Optional[str] = None,
) -> str:
    """
    统一入口：提取 PDF 内容为纯文本。

    参数:
        pdf_path: PDF文件路径
        use_ocr: 是否强制使用 PaddleOCR
        auto_fallback: 当 pdfplumber 效果不好时是否自动切换到 OCR
        ocr_output_dir: OCR结果保存目录

    提取策略:
        1. 如果 use_ocr=True，直接使用 PaddleOCR
        2. 否则先尝试 pdfplumber
        3. 如果 auto_fallback=True 且 pdfplumber 效果不佳，自动切换到 PaddleOCR
    """
    if use_ocr:
        logger.info("使用 PaddleOCR 模式提取")
        pages = extract_with_paddle_ocr(pdf_path, ocr_output_dir)
        return "\n\n".join(
            f"--- 第 {i} 页 ---\n{p}" for i, p in enumerate(pages, 1)
        )

    # 先尝试 pdfplumber
    logger.info("使用 pdfplumber 模式提取")
    text = extract_text_with_pdfplumber(pdf_path)

    # 评估质量，必要时自动切换到 OCR
    if auto_fallback and not _assess_pdfplumber_quality(text, pdf_path):
        logger.info("pdfplumber 提取效果不佳，自动切换到 PaddleOCR")
        try:
            pages = extract_with_paddle_ocr(pdf_path, ocr_output_dir)
            ocr_text = "\n\n".join(
                f"--- 第 {i} 页 ---\n{p}" for i, p in enumerate(pages, 1)
            )
            if len(ocr_text) > len(text):
                logger.info("PaddleOCR 提取结果更优，使用 OCR 版本")
                return ocr_text
            else:
                logger.info("PaddleOCR 结果未明显优于 pdfplumber，保留原结果")
        except Exception as e:
            logger.warning("PaddleOCR 调用失败，使用 pdfplumber 结果: %s", e)

    return text
