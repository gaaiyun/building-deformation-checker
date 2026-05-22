"""提取质量分析与问题归因工具。"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Optional

from src.models.data_models import CheckIssue, MonitoringCategory, MonitoringReport, MonitoringTable

SOURCE_HINTS = {
    "extraction": "（可能为 PDF 提取或列匹配问题，建议核对原文）",
    "logic": "（可能为规则边界或逻辑匹配问题，建议人工复核）",
}


_OCR_REPEAT_CHAR_THRESHOLD = 200
"""连续 ≥N 个同一字符视为 OCR blob 损毁（如恒大中心 4080 个 '0'）"""

_OCR_REPEAT_LINE_THRESHOLD = 50
"""同一非空行重复 ≥N 次视为 OCR 卡死（如红土 CX12 案例）"""

_OCR_DAMAGE_MIN_LINE_LENGTH = 3
"""行级重复检测的最短行长度（避免短分隔符 '---' 误触发）"""


def detect_ocr_damage(text: str | None) -> list[dict]:
    """识别 OCR 输出的典型损毁模式。

    返回每条损毁的字典列表：
        [{"type": "repeat_char"|"repeat_line", "message": str, "position": int, "length": int}, ...]

    损毁类型：
        - **repeat_char**：连续 ≥200 个同一字符（如 4080 个 '0'）
        - **repeat_line**：同一非空行连续重复 ≥50 次

    设计原则：
        - 保守阈值（200/50）避免误伤正常分隔符与表格边线
        - 跳过过短行（< 3 字符），如 '---', '   '
        - 返回结构化结果便于上游 UI 渲染

    用于上游 pipeline 在 OCR 提取后立即调用，发现损毁时降级为 warning
    标记 'OCR 失败，结果不可信'，而非装作'全部通过'。
    """
    if not text:
        return []

    findings: list[dict] = []

    # 检测 1a：连续重复单字符（blob，如恒大 4080 个 '0'）
    # 匹配同一字符连续出现 N+ 次（任何字符，包括 0/-/空格但要排除换行）
    for m in re.finditer(r"([^\s\n])\1{" + str(_OCR_REPEAT_CHAR_THRESHOLD - 1) + ",}", text):
        char = m.group(1)
        length = len(m.group(0))
        findings.append({
            "type": "repeat_char",
            "message": f"OCR 损毁疑似：连续 {length} 个 '{char}' 字符 blob（重复字符）",
            "position": m.start(),
            "length": length,
            "char": char,
        })

    # 检测 1b：短字串重复（如中文 "正常正常..." 或英文 "abab..."）
    # 匹配 2-10 字符的短字串连续重复 100+ 次（≈ 整体 200+ 字符）
    pattern_substr = r"(.{2,10}?)\1{99,}"
    for m in re.finditer(pattern_substr, text, re.DOTALL):
        substr = m.group(1)
        # 跳过包含换行的"伪重复"，让行级检测处理
        if "\n" in substr:
            continue
        repeat = len(m.group(0)) // len(substr)
        findings.append({
            "type": "repeat_substring",
            "message": (
                f"OCR 损毁疑似：短字串 '{substr}' 连续重复 {repeat} 次"
                f"（共 {len(m.group(0))} 字符）"
            ),
            "position": m.start(),
            "length": len(m.group(0)),
            "substring": substr,
            "repeat_count": repeat,
        })

    # 检测 2：行级重复（同一非空行连续重复 N 次以上）
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if len(line) < _OCR_DAMAGE_MIN_LINE_LENGTH:
            i += 1
            continue
        # 看接下来连续多少行内容完全一样
        repeat_count = 1
        j = i + 1
        while j < len(lines) and lines[j].strip() == line:
            repeat_count += 1
            j += 1
        if repeat_count >= _OCR_REPEAT_LINE_THRESHOLD:
            # 计算大致 char offset
            position = sum(len(lines[k]) + 1 for k in range(i))  # +1 for \n
            findings.append({
                "type": "repeat_line",
                "message": (
                    f"OCR 损毁疑似：同一行连续重复 {repeat_count} 次"
                    f"（行内容：{line[:30]}{'...' if len(line) > 30 else ''}）"
                ),
                "position": position,
                "line": i + 1,
                "repeat_count": repeat_count,
            })
            i = j  # 跳过整个重复块
        else:
            i += 1

    return findings


def detect_ocr_damage_in_cache(debug_dir: str | Path | None) -> list[dict]:
    """扫描 OCR 缓存目录的 clean/page_*.txt，识别每页是否含损毁。

    架构动机：当 PDF 含文字层时 pipeline 优选 pdfplumber，得到的 raw_text 干净；
    但 OCR 缓存可能因 PaddleOCR-VL 异步任务返回失败被污染（如恒大 4009 char '0' blob）。
    单纯检 raw_text 会漏报。此函数补充扫描磁盘缓存。

    Args:
        debug_dir: OCR 调试目录路径（含 clean/、raw/、stats.json）

    Returns:
        每条记录形如 detect_ocr_damage 的字典，并额外含 `source="ocr_cache"`
        和 `page` 字段（如 "page_005.txt"）。
    """
    if not debug_dir:
        return []
    debug_path = Path(debug_dir)
    clean_dir = debug_path / "clean"
    if not clean_dir.exists() or not clean_dir.is_dir():
        return []

    findings: list[dict] = []
    for page_file in sorted(clean_dir.glob("page_*.txt")):
        try:
            text = page_file.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        page_findings = detect_ocr_damage(text)
        for f in page_findings:
            f["source"] = "ocr_cache"
            f["page"] = page_file.name
            f["page_path"] = str(page_file)
            # 在 message 前加页面标识便于报警
            original_msg = f.get("message", "")
            f["message"] = f"OCR 缓存 {page_file.name}: {original_msg}"
            findings.append(f)
    return findings


def _non_null_ratio(values: list[object]) -> float:
    if not values:
        return 0.0
    non_null = sum(1 for value in values if value not in (None, "", "N/A"))
    return non_null / len(values)


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def infer_source_from_reason(reason: str) -> str:
    text = _normalize_text(reason)
    if not text:
        return ""
    extraction_markers = ("提取", "ocr", "错列", "列错位", "分页", "版面", "单位", "映射")
    logic_markers = ("逻辑", "规则", "边界", "约定", "统计口径", "判定")
    lower_text = text.lower()
    if any(marker in lower_text for marker in extraction_markers):
        return "extraction"
    if any(marker in text for marker in logic_markers):
        return "logic"
    return "report"


def determine_issue_source(
    report: MonitoringReport,
    table_index: Optional[int],
    default_source: str = "report",
) -> str:
    if table_index is not None and report.table_extraction_flags.get(table_index):
        return "extraction"
    return default_source


def annotate_issues_for_table(
    report: MonitoringReport,
    issues: list[CheckIssue],
    table_index: Optional[int],
    default_source: str = "report",
) -> list[CheckIssue]:
    source = determine_issue_source(report, table_index, default_source=default_source)
    for issue in issues:
        if not issue.suspected_source:
            issue.suspected_source = source
    return issues


def append_issue_source_hint(message: str, suspected_source: str) -> str:
    hint = SOURCE_HINTS.get(suspected_source, "")
    if not hint or hint in message:
        return message
    return f"{message} {hint}"


def analyze_extraction_quality(report: MonitoringReport) -> MonitoringReport:
    flags_by_table: dict[int, list[str]] = {}
    diagnostics = dict(report.extraction_diagnostics or {})
    pages = diagnostics.get("pages", [])
    high_markup_pages = [page["page"] for page in pages if page.get("markup_ratio", 0) >= 0.9]
    duplicate_pages = diagnostics.get("identical_page_pairs", [])

    for idx, table in enumerate(report.tables):
        flags: list[str] = []
        actual_count = len(table.points) if table.points else len(table.deep_points)
        if table.point_count and actual_count and table.point_count != actual_count:
            flags.append(f"表头测点数 {table.point_count} 与实际解析行数 {actual_count} 不一致")

        if table.points:
            field_values = {
                "initial_value": [pt.initial_value for pt in table.points],
                "current_value": [pt.current_value for pt in table.points],
                "cumulative_change": [pt.cumulative_change for pt in table.points],
                "current_change": [pt.current_change for pt in table.points],
                "change_rate": [pt.change_rate for pt in table.points],
            }

            required_fields = {"initial_value", "current_value", "cumulative_change"}
            if table.category not in {MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE}:
                required_fields.add("change_rate")

            sparse_fields = []
            for field in required_fields:
                values = field_values.get(field, [])
                if values and _non_null_ratio(values) < 0.5:
                    sparse_fields.append(field)
            if sparse_fields:
                flags.append(f"关键列空值较多: {', '.join(sparse_fields)}")

            ratios: list[float] = []
            for pt in table.points[: min(len(table.points), 10)]:
                if pt.initial_value is None or pt.current_value is None or pt.cumulative_change in (None, 0):
                    continue
                computed = pt.current_value - pt.initial_value
                if abs(computed) < 1e-9:
                    continue
                ratio = abs(pt.cumulative_change / computed)
                ratios.append(ratio)
            if ratios:
                mismatch_count = sum(1 for ratio in ratios if ratio > 100 or ratio < 0.01)
                if mismatch_count / len(ratios) >= 0.6:
                    flags.append("累计变化与测值差数量级异常，疑似列映射或单位错误")

        if table.deep_points:
            field_values = {
                "previous_cumulative": [dp.previous_cumulative for dp in table.deep_points],
                "current_cumulative": [dp.current_cumulative for dp in table.deep_points],
                "current_change": [dp.current_change for dp in table.deep_points],
                "change_rate": [dp.change_rate for dp in table.deep_points],
            }
            sparse_fields = []
            for field in ("previous_cumulative", "current_cumulative"):
                values = field_values[field]
                if values and _non_null_ratio(values) < 0.5:
                    sparse_fields.append(field)

            change_ratio = _non_null_ratio(field_values["current_change"])
            rate_ratio = _non_null_ratio(field_values["change_rate"])
            # 深层位移表可能只有 previous_cumulative + current_cumulative + change_rate
            # 没有 current_change 列是正常的，只有两者都缺才标记
            if change_ratio < 0.5 and rate_ratio < 0.5:
                sparse_fields.append("current_change 和 change_rate 均缺失")
            if sparse_fields:
                flags.append(f"深层表关键列空值较多: {', '.join(sparse_fields)}")

            ratios: list[float] = []
            for dp in table.deep_points:
                if dp.current_change is None or dp.change_rate in (None, 0):
                    continue
                ratios.append(abs(dp.change_rate / dp.current_change))
            if ratios:
                ratio_median = median(ratios)
                if 0.8 <= ratio_median <= 1.2:
                    flags.append("深层表变化速率疑似误映射为本期变化")

        if flags:
            flags_by_table[idx] = flags

    diagnostics["high_markup_pages"] = high_markup_pages
    diagnostics["duplicate_pages"] = duplicate_pages
    diagnostics["abnormal_table_count"] = len(flags_by_table)
    diagnostics["flagged_table_indexes"] = sorted(flags_by_table)

    # OCR 损毁检测（Gap 3）：识别如恒大 4080 个 '0' blob 或红土 CX12 行重复
    # 两个来源：(a) report.raw_text（最终选用的提取结果） (b) OCR 缓存目录（含未选用的 OCR 输出）
    # 即使 pdfplumber 取胜，OCR 缓存被污染依然值得告警——说明 OCR 不可信，禁止下次 fallback
    all_damages: list[dict] = []
    raw_text = getattr(report, "raw_text", "") or ""
    if raw_text:
        for f in detect_ocr_damage(raw_text):
            f.setdefault("source", "raw_text")
            all_damages.append(f)

    debug_dir = diagnostics.get("debug_dir") or ""
    if debug_dir:
        all_damages.extend(detect_ocr_damage_in_cache(debug_dir))

    if all_damages:
        diagnostics["ocr_damage_findings"] = all_damages
        diagnostics["ocr_damage_count"] = len(all_damages)

    report.table_extraction_flags = flags_by_table
    report.extraction_diagnostics = diagnostics
    return report
