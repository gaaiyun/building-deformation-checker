"""Gap 3 修复测试：OCR 损毁检测

基线发现：恒大中心 PDF p.5 表 4 数据被 4080+ 字符长度的 '0' blob 替换；
红土创新广场 CX12 sheet OCR 输出大量重复行（200+ 次）。

需在 PDF 提取阶段识别这些异常 OCR 模式，触发 warning，提示后续工具
'OCR 失败，结果不可信'，而不是装作'全部通过'。

检测规则：
1. **单字符 blob**：连续 ≥200 个同一字符（如 "00000...00"）
2. **行级重复**：同一非空行连续重复 ≥50 次
3. **页面空数据**：页应含表但全部 < 10 字符
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tools.extraction_quality import detect_ocr_damage


class OcrDamageDetectionTests(unittest.TestCase):

    def test_clean_text_no_damage(self):
        """正常文本应返回空（无异常）"""
        text = "监测点 S1\n初始高程 -2.70184\n本次高程 -2.70242\n本次变化 -0.58\n累计变化 31.21"
        damage = detect_ocr_damage(text)
        self.assertEqual(damage, [], f"正常文本不应报损毁：{damage}")

    def test_hengda_4080_zero_blob(self):
        """恒大中心 4080 字符 '0' blob：连续 200+ 同字符 → 应识别"""
        # 模拟 4080 个 '0'
        damaged = "前正常内容\n" + ("0" * 4080) + "\n后正常内容"
        damage = detect_ocr_damage(damaged)
        self.assertGreaterEqual(len(damage), 1, "4080 字符 blob 应被识别")
        # 应包含 '重复字符' 类型
        msgs = [d.get("message", "") for d in damage]
        self.assertTrue(
            any("重复字符" in m or "blob" in m.lower() or "连续" in m for m in msgs),
            f"应说明是重复字符类型：{msgs}",
        )

    def test_short_repeat_not_flagged(self):
        """短重复（如 OCR 表格分隔线 '----' 200 个）不应触发误报"""
        text = "正常文本\n" + "-" * 30 + "\n更多内容"
        damage = detect_ocr_damage(text)
        self.assertEqual(damage, [], "30 个 '-' 不应触发")

    def test_long_dashes_separator_in_normal_range(self):
        """OCR 分隔符常见 50-100 个 '-'，不应误报"""
        text = "段1\n" + "-" * 100 + "\n段2\n" + "-" * 80 + "\n段3"
        damage = detect_ocr_damage(text)
        self.assertEqual(damage, [], "≤199 字符的分隔符不应触发")

    def test_row_repeat_50_times(self):
        """同一非空行重复 50+ 次（如红土 CX12 案例）→ 应识别"""
        row = "-0.02 -0.04 -0.01\n"
        damaged = "正常表头\n" + row * 60 + "结尾"
        damage = detect_ocr_damage(damaged)
        self.assertGreaterEqual(len(damage), 1, "60 次行重复应被识别")
        msgs = [d.get("message", "") for d in damage]
        self.assertTrue(
            any("重复" in m or "duplicate" in m.lower() for m in msgs),
            f"应说明是行重复：{msgs}",
        )

    def test_few_row_repeats_not_flagged(self):
        """少量重复行（< 50 次）不应触发"""
        row = "0.5 0.3 0.1\n"
        text = "前\n" + row * 10 + "后"
        damage = detect_ocr_damage(text)
        self.assertEqual(damage, [], "10 次行重复不应触发")

    def test_empty_text_returns_empty(self):
        """空文本不抛错"""
        self.assertEqual(detect_ocr_damage(""), [])
        self.assertEqual(detect_ocr_damage(None), [])

    def test_returns_dict_with_location_info(self):
        """检测结果应含 location/position 信息便于定位"""
        damaged = "前\n" + ("X" * 500) + "\n后"
        damage = detect_ocr_damage(damaged)
        self.assertGreaterEqual(len(damage), 1)
        # 每条记录应有 message + position/offset
        d = damage[0]
        self.assertIn("message", d)
        self.assertTrue("position" in d or "offset" in d or "line" in d,
                        f"应含位置信息：{d}")

    def test_zh_text_with_repetition_still_works(self):
        """中文文本里的重复也能识别"""
        text = "项目名称\n" + ("正常正常正常正常正常正常" * 200) + "结尾"
        damage = detect_ocr_damage(text)
        self.assertGreaterEqual(len(damage), 1, "中文长重复应被识别")


class OcrDamageEndToEndTests(unittest.TestCase):
    """端到端：raw_text 含 OCR 损毁 → diagnostics 记录 → logic 报告 warning"""

    def test_extraction_quality_records_damage(self):
        from src.models.data_models import MonitoringReport
        from src.tools.extraction_quality import analyze_extraction_quality

        report = MonitoringReport()
        report.raw_text = "前\n" + ("0" * 4080) + "\n后"
        analyze_extraction_quality(report)
        self.assertGreaterEqual(
            report.extraction_diagnostics.get("ocr_damage_count", 0), 1,
            "diagnostics 应记录 ocr_damage_findings",
        )

    def test_logic_check_emits_ocr_damage_warning(self):
        from src.models.data_models import MonitoringReport, MonitoringTable, MonitoringCategory, TableVerificationConfig
        from src.tools.logic_checker import run_logic_checks

        # 加一张普通表（让 logic_checks 不止 extractability 检查）
        table = MonitoringTable(
            monitoring_item="假表",
            category=MonitoringCategory.HORIZONTAL_DISP,
            verification_config=TableVerificationConfig(),
        )
        report = MonitoringReport(tables=[table])
        report.raw_text = "前\n" + ("0" * 4080) + "\n后"
        report.extraction_diagnostics = {
            "ocr_damage_findings": [
                {"type": "repeat_char", "message": "OCR 损毁疑似：连续 4080 个 '0'", "position": 3}
            ],
            "ocr_damage_count": 1,
        }
        issues = run_logic_checks(report)
        damage_issues = [i for i in issues if i.field_name == "OCR 损毁"]
        self.assertGreaterEqual(len(damage_issues), 1, "logic_checks 应包含 OCR 损毁 warning")
        self.assertEqual(damage_issues[0].severity, "warning")


class OcrCacheDirectoryScanTests(unittest.TestCase):
    """关键架构修复：当 pdfplumber 取胜时 raw_text 干净，但 OCR 缓存可能被污染。

    恒大中心 PDF 实际案例：
    - extraction_method = pdfplumber（文字层 PDF 可直接取出干净文本）
    - OCR 缓存目录 output/恒大中心..._ocr_debug/clean/page_005.txt 含 4009 char '0' blob
    - 旧实现只检 raw_text → 漏报；需扩展扫描 debug_dir/clean/*.txt
    """

    def _make_temp_ocr_dir(self, tmpdir: Path, *, damaged_page: int, damage: str):
        """构造仿真 OCR debug 目录：N 个 clean/page_XXX.txt，其中一页含损毁内容"""
        clean = tmpdir / "clean"
        clean.mkdir(parents=True, exist_ok=True)
        # 几个正常页
        for i in range(1, 5):
            (clean / f"page_{i:03d}.txt").write_text(
                f"正常页面 {i} 内容\n测点 S{i} 累计变化 0.5\n", encoding="utf-8"
            )
        # 一页损毁
        (clean / f"page_{damaged_page:03d}.txt").write_text(damage, encoding="utf-8")
        return tmpdir

    def test_scans_ocr_cache_when_raw_text_clean(self):
        """raw_text 干净（pdfplumber 取胜），但 OCR 缓存含 blob → 应检测"""
        import tempfile
        from src.models.data_models import MonitoringReport
        from src.tools.extraction_quality import analyze_extraction_quality

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td) / "fake_ocr_debug"
            self._make_temp_ocr_dir(
                tmpdir,
                damaged_page=5,
                damage="前\n" + ("0" * 4080) + "\n后",
            )

            report = MonitoringReport()
            report.raw_text = "干净的 pdfplumber 文本，无任何重复"
            report.extraction_diagnostics = {"debug_dir": str(tmpdir)}
            analyze_extraction_quality(report)

            count = report.extraction_diagnostics.get("ocr_damage_count", 0)
            self.assertGreaterEqual(count, 1,
                                    f"应扫描 OCR 缓存目录并发现损毁：{report.extraction_diagnostics}")
            findings = report.extraction_diagnostics.get("ocr_damage_findings", [])
            # 至少一条记录来自 OCR 缓存（应有 source/page 标识）
            cache_findings = [f for f in findings if f.get("source") == "ocr_cache" or "page_005" in str(f)]
            self.assertGreaterEqual(len(cache_findings), 1,
                                    f"应标明损毁来源为 OCR 缓存：{findings}")

    def test_no_double_count_when_raw_text_also_damaged(self):
        """raw_text 和 OCR 缓存都有损毁 → 应都记录，但区分来源"""
        import tempfile
        from src.models.data_models import MonitoringReport
        from src.tools.extraction_quality import analyze_extraction_quality

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td) / "fake_ocr_debug"
            self._make_temp_ocr_dir(
                tmpdir,
                damaged_page=3,
                damage="X" * 500,
            )

            report = MonitoringReport()
            report.raw_text = "前\n" + ("Y" * 600) + "\n后"
            report.extraction_diagnostics = {"debug_dir": str(tmpdir)}
            analyze_extraction_quality(report)

            findings = report.extraction_diagnostics.get("ocr_damage_findings", [])
            self.assertGreaterEqual(len(findings), 2,
                                    f"raw_text + OCR 缓存均损毁，应记录两类：{findings}")

    def test_no_findings_when_debug_dir_missing(self):
        """debug_dir 不存在/为空 → 不应报错"""
        from src.models.data_models import MonitoringReport
        from src.tools.extraction_quality import analyze_extraction_quality

        report = MonitoringReport()
        report.raw_text = "干净文本"
        report.extraction_diagnostics = {"debug_dir": "/nonexistent/path"}
        analyze_extraction_quality(report)
        self.assertEqual(
            report.extraction_diagnostics.get("ocr_damage_count", 0), 0,
            "不存在的目录不应触发"
        )

    def test_no_findings_when_clean_dir_pages_all_normal(self):
        """OCR 缓存全部干净 → 不应触发"""
        import tempfile
        from src.models.data_models import MonitoringReport
        from src.tools.extraction_quality import analyze_extraction_quality

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td) / "fake_ocr_debug"
            clean = tmpdir / "clean"
            clean.mkdir(parents=True)
            for i in range(1, 10):
                (clean / f"page_{i:03d}.txt").write_text(
                    f"页面 {i} 正常监测数据\n", encoding="utf-8"
                )

            report = MonitoringReport()
            report.raw_text = "干净"
            report.extraction_diagnostics = {"debug_dir": str(tmpdir)}
            analyze_extraction_quality(report)
            self.assertEqual(
                report.extraction_diagnostics.get("ocr_damage_count", 0), 0,
                "全干净的 OCR 缓存不应触发"
            )

    def test_real_hengda_ocr_cache_detected(self):
        """真实数据回归：恒大中心 OCR 缓存目录 → 应检出 4009 char '0' blob"""
        from src.models.data_models import MonitoringReport
        from src.tools.extraction_quality import analyze_extraction_quality

        hengda_dir = ROOT / "output" / "恒大中心基坑支护工程地铁监测报告第209期（第3616次）_ocr_debug"
        if not hengda_dir.exists():
            self.skipTest(f"真实 OCR 缓存不存在，跳过：{hengda_dir}")

        report = MonitoringReport()
        report.raw_text = "假设 pdfplumber 给的干净文本"  # 模拟主线流程
        report.extraction_diagnostics = {"debug_dir": str(hengda_dir)}
        analyze_extraction_quality(report)

        count = report.extraction_diagnostics.get("ocr_damage_count", 0)
        self.assertGreaterEqual(count, 1,
                                f"恒大缓存应识别为损毁：{report.extraction_diagnostics}")
        findings = report.extraction_diagnostics.get("ocr_damage_findings", [])
        # 应能定位到 page_005
        zero_blob = [f for f in findings
                     if f.get("type") == "repeat_char" and f.get("char") == "0"]
        self.assertGreaterEqual(len(zero_blob), 1,
                                f"应识别 '0' 字符 blob：{findings}")

    def test_real_hengda_endtoend_emits_logic_warning(self):
        """端到端整合：模拟 pipeline → 恒大缓存损毁应在 run_logic_checks 产生 warning"""
        from src.models.data_models import (
            MonitoringCategory, MonitoringReport, MonitoringTable, TableVerificationConfig,
        )
        from src.tools.extraction_quality import analyze_extraction_quality
        from src.tools.logic_checker import run_logic_checks

        hengda_dir = ROOT / "output" / "恒大中心基坑支护工程地铁监测报告第209期（第3616次）_ocr_debug"
        if not hengda_dir.exists():
            self.skipTest(f"真实 OCR 缓存不存在，跳过：{hengda_dir}")

        # 加一张普通表，避免 run_logic_checks 走 empty path
        table = MonitoringTable(
            monitoring_item="伪表",
            category=MonitoringCategory.HORIZONTAL_DISP,
            verification_config=TableVerificationConfig(),
        )
        report = MonitoringReport(tables=[table])
        report.raw_text = "pdfplumber 提取的干净文本，与 OCR 失败无关"
        report.extraction_diagnostics = {"debug_dir": str(hengda_dir)}

        # Step 1: extraction_quality 触发缓存扫描
        analyze_extraction_quality(report)
        self.assertGreaterEqual(report.extraction_diagnostics.get("ocr_damage_count", 0), 1)

        # Step 2: logic_checker 把 diagnostics 翻成 warning
        issues = run_logic_checks(report)
        damage_warnings = [i for i in issues if i.field_name == "OCR 损毁"]
        self.assertGreaterEqual(len(damage_warnings), 1,
                                f"应产生 OCR 损毁 warning，但实际 issues 字段：{[i.field_name for i in issues]}")
        # 至少一条 warning 应提及缓存损毁字样（来自 page_005 0 blob）
        msgs = [w.message for w in damage_warnings]
        self.assertTrue(
            any("0" in m or "blob" in m.lower() or "重复" in m for m in msgs),
            f"warning 消息应反映损毁内容：{msgs}",
        )


if __name__ == "__main__":
    unittest.main()
