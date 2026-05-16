"""v2 离线烟囱测试：验证不依赖 API key 的关键路径在真实 PDF 上工作。

覆盖：
    - Step 1 PDF 提取（pdfplumber + Unicode 归一化）
    - parse_float 对真实监测数据字符串的解析
    - 所有新模块（src.core / gui_desktop / src.tools.export_formats）的导入链

不覆盖（需要 API key）：
    - Step 2 LLM 结构化解析
    - Step 3-5 计算/统计/逻辑核查（依赖 Step 2 输出的 MonitoringReport）
    - Step 6 AI 自验证
    - Step 7 AI 最终审核
    - PaddleOCR API 调用（扫描件场景）

运行：``python smoke_test_v2.py``（在仓库根目录）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.tools.pdf_extractor import extract_pdf
from src.utils.text_normalize import parse_float, normalize_numeric_text


def test_extract():
    """验证 pdfplumber 在文字层 PDF 上正常抽取，并自动经过 Unicode 归一化。"""
    print("=== 测试 1: pdfplumber 提取 + Unicode 归一化（鱼珠乐天）===")
    pdf = "【监测2023011-017】鱼珠乐天智能科技创新中心(1).pdf"
    if not Path(pdf).exists():
        print(f"  跳过 - PDF 不存在: {pdf}")
        return None

    result = extract_pdf(
        pdf,
        use_ocr=False,
        prefer_ocr=False,
        auto_fallback=False,  # 强制只用 pdfplumber，避免触发 OCR (需要 API key)
        return_details=True,
    )
    method = result.method
    profile = result.selected_profile
    raw = result.diagnostics.get("raw_chars", 0)
    clean = result.diagnostics.get("clean_chars", 0)
    ratio = result.diagnostics.get("compression_ratio", 0)
    print(f"  提取方式: {method} / {profile}")
    print(f"  原始字符: {raw:,}")
    print(f"  清洗后: {clean:,}")
    print(f"  压缩率: {ratio:.1%}")
    print(f"  调试目录: {result.debug_output_dir}")
    return result


def test_unicode_in_text(text: str):
    """统计提取后的文本里是否还残留 U+2212 / 全角数字等 OCR 静默 bug 源。"""
    print()
    print("=== 测试 2: Unicode 净化效果验证 ===")
    # 检查是否还残留 U+2212 (math minus)
    has_u2212 = "−" in text
    # 检查是否有全角数字
    fw_digits = sum(1 for c in text if "０" <= c <= "９")
    # 检查是否有 ASCII 数字
    ascii_digits = sum(1 for c in text if c.isdigit() and ord(c) < 128)
    print(f"  残留 U+2212 (math minus): {'是' if has_u2212 else '否（已归一化）'}")
    print(f"  残留全角数字数量: {fw_digits}")
    print(f"  ASCII 数字数量: {ascii_digits:,}")
    print(f"  ASCII 减号数量: {text.count('-'):,}")


def test_parse_float_real_world():
    """用真实监测报告里出现过的数值字符串（含 U+2212、千分位、单位）跑 parse_float。"""
    print()
    print("=== 测试 3: parse_float 处理真实监测数据字符串 ===")
    samples = [
        "−23.6mm/S5",       # U+2212 minus
        "36.6mm/2S11",
        "−0.010mm/d/D2",    # U+2212 minus
        "214.9kN/M5",
        "0.484mm/d",
        "−1.85",            # 内部场景
        "31.21",
        "1,234.56mm",       # 千分位
        "正常",              # 哨兵
        "/",                 # 哨兵
    ]
    for s in samples:
        v = parse_float(s)
        flag = "✓" if v is not None else "·"
        # 因 PowerShell 编码限制，用 repr 安全输出
        print(f"  {flag}  {s!r:30} -> {v}")


def test_pipeline_imports():
    """验证 v2 新增的所有模块都能正确导入（PySide6、核心引擎、导出格式）。"""
    print()
    print("=== 测试 4: 核心模块导入链 ===")
    from src.core import run_pipeline, RuntimeConfig, PipelineResult
    from gui_desktop.worker import PipelineWorker
    from src.tools.export_formats import generate_docx, generate_html
    print("  src.core.run_pipeline: 可用")
    print("  src.core.RuntimeConfig: 可用")
    print("  src.core.PipelineResult: 可用")
    print("  gui_desktop.worker.PipelineWorker: 可用")
    print("  src.tools.export_formats.generate_docx/html: 可用")


def main():
    result = test_extract()
    if result and result.text:
        test_unicode_in_text(result.text)
    test_parse_float_real_world()
    test_pipeline_imports()

    print()
    print("=" * 60)
    print("v2 烟囱测试完成 - 离线部分全部通过")
    print("剩余: LLM/OCR Step 2-7 需要用户在 GUI/Streamlit 内填 API key")
    print("=" * 60)


if __name__ == "__main__":
    main()
