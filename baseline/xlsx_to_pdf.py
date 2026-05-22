"""用 Excel COM 把 XLSX 批量转 PDF（最高保真度）。

为什么用 Excel COM 而不是 LibreOffice？
    Excel COM 在 Windows + 安装了 Office 时表现最稳定，对中文字体、合并单元格、
    图表、印章等保真度最高。LibreOffice 在某些复杂版式上会丢失图片或样式。

输出：
    test_pdfs/<原文件名>.pdf

注意：
    转换全部 sheet（含曲线图）；对监测核查只关心数据 sheet，但保留图纸便于人工对照。
    XlFixedFormatType = 0 (PDF)；Quality = 0 (Standard, 较小体积)
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import win32com.client as win32

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "test_pdfs"
OUT_DIR.mkdir(exist_ok=True)

FILES = [
    "质安模板-错误版.xlsx",
    "质安模板-正确版.xlsx",
    "深工勘模板-错误版.xlsx",
    "深工勘模板-正确版.xlsx",
    "展誉模板-错误版.xlsx",
    "展誉模板-正确版.xlsx",
]

# Excel constants
XL_FIXED_FORMAT_TYPE_PDF = 0
XL_QUALITY_STANDARD = 0
XL_QUALITY_MINIMUM = 1


def convert(src: Path, dst: Path, excel) -> None:
    """打开 XLSX，整本另存为 PDF（所有 sheet）"""
    wb = excel.Workbooks.Open(str(src), ReadOnly=True, UpdateLinks=0)
    try:
        # IgnorePrintAreas=False 保留各 sheet 的打印区域设置
        wb.ExportAsFixedFormat(
            Type=XL_FIXED_FORMAT_TYPE_PDF,
            Filename=str(dst),
            Quality=XL_QUALITY_STANDARD,
            IncludeDocProperties=True,
            IgnorePrintAreas=False,
            OpenAfterPublish=False,
        )
    finally:
        wb.Close(SaveChanges=False)


def main() -> int:
    print("启动 Excel...")
    excel = win32.gencache.EnsureDispatch("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    excel.ScreenUpdating = False

    success = 0
    failed = 0
    try:
        for fname in FILES:
            src = ROOT / fname
            dst = OUT_DIR / (Path(fname).stem + ".pdf")
            if not src.exists():
                print(f"  ❌ 缺文件: {fname}")
                failed += 1
                continue
            if dst.exists():
                print(f"  ⏭️  已存在跳过: {dst.name}")
                success += 1
                continue
            try:
                print(f"  🔄 转换中: {fname} → {dst.name}")
                convert(src, dst, excel)
                size_mb = dst.stat().st_size / (1024 * 1024)
                print(f"     ✅ 完成 ({size_mb:.1f} MB)")
                success += 1
            except Exception as e:
                print(f"     ❌ 失败: {e}")
                failed += 1
    finally:
        excel.Quit()

    print(f"\n汇总: 成功 {success} / 失败 {failed} / 总数 {len(FILES)}")
    print(f"输出目录: {OUT_DIR}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
