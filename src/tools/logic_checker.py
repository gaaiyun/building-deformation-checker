"""
逻辑检查工具

检查内容：
1. 安全状态判定：根据报警值/控制值判定累计变化量和变化速率是否超标
2. 汇总表与分表一致性：简报中的统计结果 vs 各分表的统计结果
3. 数据完整性：测点数量、编号一致性等

Uses LLM semantic matching for threshold/table/summary correspondence
instead of hardcoded keyword dictionaries.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from src.config import FLOAT_TOLERANCE
from src.models.data_models import (
    CheckIssue,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    ThresholdConfig,
)
from src.tools.extraction_quality import annotate_issues_for_table

logger = logging.getLogger(__name__)


def _safe_float_from_str(s: str) -> Optional[float]:
    if not s or s in ("/", "--", "-", "——", "N/A", ""):
        return None
    cleaned = re.sub(r"[a-zA-Z/\s]", "", s)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _fmt(v: Optional[float], p: int = 2) -> str:
    return f"{v:.{p}f}" if v is not None else "N/A"


# ── LLM Semantic Matching ────────────────────────────────────

def _build_semantic_maps(report: MonitoringReport) -> None:
    """
    Use LLM to build semantic mappings between:
    - threshold names <-> table monitoring item names
    - summary item names <-> table monitoring item names
    Results are cached on the report object.
    """
    if report.threshold_map and report.summary_map:
        return

    threshold_names = [th.item_name for th in report.thresholds]
    table_names = list(set(t.monitoring_item for t in report.tables))
    summary_names = [si.monitoring_item for si in report.summary_items]

    if not threshold_names and not summary_names:
        return

    prompt = (
        "以下是一份建筑变形监测报告中提取的三组名称，请建立它们之间的对应关系。\n\n"
        f"阈值配置项: {json.dumps(threshold_names, ensure_ascii=False)}\n"
        f"数据表监测项: {json.dumps(table_names, ensure_ascii=False)}\n"
        f"简报汇总项: {json.dumps(summary_names, ensure_ascii=False)}\n\n"
        "返回JSON:\n"
        '{"threshold_to_tables": {"阈值名": ["对应数据表名", ...]}, '
        '"summary_to_tables": {"汇总项名": ["对应数据表名", ...]}}\n'
        "如果某个阈值/汇总项找不到对应的数据表，映射为空列表。"
        "注意不同公司对同一监测项的称呼可能不同，需要语义匹配。"
        '例如"坡顶水平位移及沉降"对应"支护结构顶部水平位移"和"支护结构顶部竖向位移"。'
        '"深层水平位移"/"支护桩测斜"/"测斜"是同一类。'
    )

    from openai import OpenAI
    import src.config as cfg

    timeout_sec = getattr(cfg, "LLM_TIMEOUT_NORMAL", 90)
    max_retries = getattr(cfg, "LLM_MAX_RETRIES", 2)
    backoff_sec = getattr(cfg, "LLM_RETRY_BACKOFF_SEC", 10)
    client = OpenAI(api_key=cfg.LLM_API_KEY, base_url=cfg.LLM_BASE_URL, max_retries=0)

    for attempt in range(1 + max_retries):
        try:
            resp = client.chat.completions.create(
                model=cfg.LLM_MODEL,
                messages=[
                    {"role": "system", "content": "你是建筑变形监测领域专家，擅长识别不同表述的同义关系。返回纯JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=4000,
                timeout=timeout_sec,
            )
            raw = resp.choices[0].message.content or ""
            raw = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', raw, flags=re.DOTALL).strip()
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
                report.threshold_map = data.get("threshold_to_tables", {})
                report.summary_map = data.get("summary_to_tables", {})
                logger.info(
                    "LLM语义匹配完成: %d阈值映射, %d汇总映射",
                    len(report.threshold_map), len(report.summary_map),
                )
                return
        except Exception as e:
            if attempt < max_retries:
                backoff = backoff_sec * (2 ** attempt)
                logger.warning("LLM语义匹配失败，%ds 后重试: %s", backoff, e)
                time.sleep(backoff)
            else:
                logger.warning("LLM语义匹配失败，回退到关键词匹配: %s", e)

    _build_fallback_maps(report, threshold_names, table_names, summary_names)


def _build_fallback_maps(report, threshold_names, table_names, summary_names):
    """Keyword-based fallback when LLM is unavailable."""
    keywords_groups = [
        ["深层", "测斜"],
        ["水平位移", "顶部水平", "坡顶水平", "基坑顶位移"],
        ["竖向位移", "顶部竖向", "坡顶沉降", "基坑顶沉降"],
        ["地面沉降", "道路沉降", "周边地面", "周边道路"],
        ["管线"],
        ["水位", "地下水"],
        ["锚索", "拉力", "轴力"],
    ]

    def _match_group(name):
        for i, group in enumerate(keywords_groups):
            if any(k in name for k in group):
                return i
        return -1

    report.threshold_map = {}
    for th_name in threshold_names:
        th_group = _match_group(th_name)
        matched = [tn for tn in table_names if _match_group(tn) == th_group and th_group >= 0]
        if not matched:
            matched = [tn for tn in table_names if th_name in tn or tn in th_name]
        report.threshold_map[th_name] = matched

    report.summary_map = {}
    for s_name in summary_names:
        s_group = _match_group(s_name)
        matched = [tn for tn in table_names if _match_group(tn) == s_group and s_group >= 0]
        if not matched:
            matched = [tn for tn in table_names if s_name in tn or tn in s_name]
        report.summary_map[s_name] = matched


def _find_threshold_semantic(report: MonitoringReport, table_name: str) -> Optional[ThresholdConfig]:
    """Find matching threshold using semantic map."""
    for th in report.thresholds:
        mapped_tables = report.threshold_map.get(th.item_name, [])
        if table_name in mapped_tables:
            return th
        if th.item_name in table_name or table_name in th.item_name:
            return th
    return None


def _find_matched_tables(report: MonitoringReport, summary_item_name: str) -> list[MonitoringTable]:
    """Find matching tables for a summary item using semantic map."""
    mapped_names = report.summary_map.get(summary_item_name, [])
    matched = []
    for t in report.tables:
        if t.monitoring_item in mapped_names:
            matched.append(t)
    if not matched:
        for t in report.tables:
            if summary_item_name in t.monitoring_item or t.monitoring_item in summary_item_name:
                matched.append(t)
    return matched


# ── Check Functions ──────────────────────────────────────────

def check_ocr_damage(report: MonitoringReport, issues: list[CheckIssue]) -> None:
    """Gap 3: 当 extraction_diagnostics 含 ocr_damage_findings 时，升级为 warning。

    analyze_extraction_quality 在 raw_text 上跑 detect_ocr_damage，把结果写到
    diagnostics["ocr_damage_findings"]；本函数把那些发现暴露为最终报告 warning。
    """
    diagnostics = report.extraction_diagnostics or {}
    findings = diagnostics.get("ocr_damage_findings") or []
    if not findings:
        return

    # 一条总警告 + 最多 5 条具体定位
    issues.append(CheckIssue(
        severity="warning",
        table_name="报告整体",
        point_id="OCR",
        field_name="OCR 损毁",
        expected_value="OCR 输出无大段重复",
        actual_value=f"{len(findings)} 处疑似损毁",
        message=(
            f"OCR 提取检出 {len(findings)} 处疑似损毁（重复字符/重复行/卡死）。"
            "工具核对结果不可信，建议重新提取或人工核对原 PDF。"
        ),
        suspected_source="extraction",
    ))
    for d in findings[:5]:
        issues.append(CheckIssue(
            severity="warning",
            table_name="报告整体",
            point_id=f"位置 {d.get('position', '?')}",
            field_name="OCR 损毁",
            expected_value="正常输出",
            actual_value=d.get("type", "unknown"),
            message=d.get("message", ""),
            suspected_source="extraction",
        ))


def check_report_extractability(report: MonitoringReport, issues: list[CheckIssue]) -> None:
    """Flag reports where extraction produced no verifiable monitoring table."""
    if report.tables:
        return

    diagnostics = report.extraction_diagnostics or {}
    method = diagnostics.get("method", "unknown")
    selected_profile = diagnostics.get("selected_profile", "")
    detail = f"提取方式: {method}"
    if selected_profile:
        detail += f" ({selected_profile})"
    if diagnostics.get("clean_chars") is not None:
        detail += f"，清洗后文本 {diagnostics.get('clean_chars')} 字符"
    if diagnostics.get("llm_chunk_parse_failures"):
        detail += f"，LLM 分块解析失败 {diagnostics.get('llm_chunk_parse_failures')} 段"

    issues.append(CheckIssue(
        severity="warning",
        table_name="报告整体",
        point_id="ALL",
        field_name="数据表识别",
        expected_value="至少 1 张可核对的监测数据表",
        actual_value="0 张",
        message=(
            "未识别到可计算核对的监测数据表，不能将“0 个错误”等同于报告通过。"
            f"请确认 PDF 是否为监测报告、是否需要启用 OCR，或检查提取调试目录。{detail}"
        ),
        suspected_source="extraction",
    ))


_PROXIMITY_THRESHOLD = 0.80
"""触发"接近预警值"warning 的最低比例：|值| / 限值 >= 0.80 即提示"""

_PROXIMITY_EPSILON = 1e-9
"""浮点容差：4.8/6.0=0.7999... 应视为 0.80"""


def check_safety_status(report: MonitoringReport, issues: list[CheckIssue]) -> None:
    for table_index, table in enumerate(report.tables):
        threshold = _find_threshold_semantic(report, table.monitoring_item)
        if threshold is None:
            continue

        table_issues: list[CheckIssue] = []
        for pt in table.points:
            if not pt.safety_status:
                continue
            should_be = "正常"
            # 容忍 LLM 误抽出的负 threshold（如 -30 应为 30）：用 abs 比较，
            # 但 ≤0 (含 None) 表示真无阈值，跳过
            abs_warn = abs(threshold.warning_value) if threshold.warning_value else 0
            abs_ctrl = abs(threshold.control_value) if threshold.control_value else 0
            abs_rate_limit = abs(threshold.rate_limit) if threshold.rate_limit else 0
            if pt.cumulative_change is not None and abs_warn > 0:
                abs_cum = abs(pt.cumulative_change)
                if abs_ctrl > 0 and abs_cum >= abs_ctrl:
                    should_be = "控制"
                elif abs_cum >= abs_warn:
                    should_be = "报警"
            if pt.change_rate is not None and abs_rate_limit > 0:
                if abs(pt.change_rate) >= abs_rate_limit:
                    if should_be == "正常":
                        should_be = "报警"

            reported = pt.safety_status.strip()
            if reported == "正常" and should_be != "正常":
                table_issues.append(CheckIssue(
                    severity="error", table_name=table.monitoring_item,
                    point_id=pt.point_id, field_name="安全状态",
                    expected_value=should_be, actual_value=reported,
                    message=(
                        f"安全状态判定有误: 累计={_fmt(pt.cumulative_change)}, "
                        f"速率={_fmt(pt.change_rate, 3)}, "
                        f"报警值={_fmt(threshold.warning_value)}, "
                        f"控制值={_fmt(threshold.control_value)} → 应为 {should_be}"
                    ),
                ))
            elif reported != "正常" and should_be == "正常":
                table_issues.append(CheckIssue(
                    severity="warning", table_name=table.monitoring_item,
                    point_id=pt.point_id, field_name="安全状态",
                    expected_value=should_be, actual_value=reported,
                    message=f"安全状态可能过严: 数据正常但标记为 {reported}",
                ))
            elif reported == "正常" and should_be == "正常":
                # 新增：接近预警值的 proximity warning（≥80% 预警 / 限值时提示）
                proximity_msg = _proximity_message(pt, threshold)
                if proximity_msg:
                    table_issues.append(CheckIssue(
                        severity="warning", table_name=table.monitoring_item,
                        point_id=pt.point_id, field_name="安全状态",
                        expected_value="接近预警", actual_value="正常",
                        message=proximity_msg,
                    ))
        annotate_issues_for_table(report, table_issues, table_index, default_source="report")
        issues.extend(table_issues)


def _proximity_message(pt, threshold) -> str | None:
    """检查累计或速率是否接近报警值（≥80%）。

    返回包含具体百分比的提示文字；不接近返回 None。

    优先级：累计接近 > 速率接近（一行只生成一条 proximity 提示，避免噪音）。
    """
    # 累计接近预警值
    # 注：用 abs(threshold.warning_value) 容忍 LLM 偶发抽出负值（如 -30 应为 30）
    abs_warn = abs(threshold.warning_value) if threshold.warning_value else 0
    if pt.cumulative_change is not None and abs_warn > 0:
        ratio = abs(pt.cumulative_change) / abs_warn
        if (_PROXIMITY_THRESHOLD - _PROXIMITY_EPSILON) <= ratio < 1.0:
            return (
                f"累计变化 {pt.cumulative_change:.2f} 已接近预警值 "
                f"{abs_warn:.1f}（达 {ratio:.0%}），建议加密观测"
            )
    # 速率接近限值（同样容忍负值）
    abs_rate_limit = abs(threshold.rate_limit) if threshold.rate_limit else 0
    if pt.change_rate is not None and abs_rate_limit > 0:
        ratio = abs(pt.change_rate) / abs_rate_limit
        if (_PROXIMITY_THRESHOLD - _PROXIMITY_EPSILON) <= ratio < 1.0:
            return (
                f"变化速率 {pt.change_rate:.3f} mm/d 已接近速率限值 "
                f"{abs_rate_limit:.2f} mm/d（达 {ratio:.0%}），建议加密观测"
            )
    return None


def check_summary_consistency(report: MonitoringReport, issues: list[CheckIssue]) -> None:
    if not report.summary_items:
        return

    for si in report.summary_items:
        matched = _find_matched_tables(report, si.monitoring_item)
        if not matched:
            issues.append(CheckIssue(
                severity="warning", table_name="简报汇总", point_id="N/A",
                field_name=si.monitoring_item,
                expected_value="有对应分表", actual_value="未找到",
                message=f"汇总项 [{si.monitoring_item}] 未找到对应分表",
                suspected_source="logic",
            ))
            continue

        is_anchor = any(
            t.category in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE)
            for t in matched
        )

        if is_anchor:
            all_forces = []
            for t in matched:
                for pt in t.points:
                    if pt.current_value is not None:
                        all_forces.append((pt.point_id, pt.current_value))
            if all_forces:
                act_max_id, act_max = max(all_forces, key=lambda x: x[1])
                act_min_id, act_min = min(all_forces, key=lambda x: x[1])
                s_pos = _safe_float_from_str(si.positive_max)
                s_neg = _safe_float_from_str(si.negative_max)
                tol = FLOAT_TOLERANCE * 2
                if s_pos is not None and not (abs(act_max - s_pos) <= tol or abs(act_min - s_pos) <= tol):
                    issues.append(CheckIssue(
                        severity="warning", table_name="简报汇总",
                        point_id=si.monitoring_item, field_name="锚索力值",
                        expected_value=f"max={act_max_id}/{_fmt(act_max, 1)}, min={act_min_id}/{_fmt(act_min, 1)}",
                        actual_value=f"{si.positive_max_id}={si.positive_max}",
                        message="锚索汇总值与分表不一致，请人工确认",
                        suspected_source="report",
                    ))
            continue

        all_cum = []
        for t in matched:
            for pt in t.points:
                if pt.cumulative_change is not None:
                    all_cum.append((pt.point_id, pt.cumulative_change))
            for dp in t.deep_points:
                if dp.current_cumulative is not None:
                    all_cum.append((f"深度{dp.depth}m", dp.current_cumulative))

        if not all_cum:
            continue

        pos_vals = [(point_id, value) for point_id, value in all_cum if value > 0]
        neg_vals = [(point_id, value) for point_id, value in all_cum if value < 0]
        summary_pos = _safe_float_from_str(si.positive_max)
        summary_neg = _safe_float_from_str(si.negative_max)
        tol = FLOAT_TOLERANCE

        if summary_pos is not None:
            if not pos_vals:
                # 行业惯例：所有值同为负时，"正方向最大"可能填绝对值最小的负值
                if neg_vals:
                    closest_id, closest_val = max(neg_vals, key=lambda x: x[1])
                    if abs(closest_val - summary_pos) <= tol:
                        issues.append(CheckIssue(
                            severity="info", table_name="简报汇总",
                            point_id=si.monitoring_item, field_name="正方向最大",
                            expected_value="无正值",
                            actual_value=f"{si.positive_max_id}={si.positive_max}",
                            message=f"分表中不存在正值，汇总表正方向最大填写了绝对值最小的负值（行业惯例）",
                            suspected_source="report",
                        ))
                    else:
                        issues.append(CheckIssue(
                            severity="warning", table_name="简报汇总",
                            point_id=si.monitoring_item, field_name="正方向最大",
                            expected_value=f"无正值，最接近0: {closest_id}={_fmt(closest_val)}",
                            actual_value=f"{si.positive_max_id}={si.positive_max}",
                            message="分表中不存在正值，汇总表正方向最大与最接近0的负值不一致",
                            suspected_source="report",
                        ))
                else:
                    issues.append(CheckIssue(
                        severity="warning", table_name="简报汇总",
                        point_id=si.monitoring_item, field_name="正方向最大",
                        expected_value="-",
                        actual_value=f"{si.positive_max_id}={si.positive_max}",
                        message="分表中无数据，汇总表正方向最大应为空",
                        suspected_source="report",
                    ))
            else:
                actual_pos_id, actual_pos = max(pos_vals, key=lambda x: x[1])
                if abs(actual_pos - summary_pos) > tol:
                    issues.append(CheckIssue(
                        severity="error", table_name="简报汇总",
                        point_id=si.monitoring_item, field_name="正方向最大",
                        expected_value=f"{actual_pos_id}={_fmt(actual_pos)}",
                        actual_value=f"{si.positive_max_id}={si.positive_max}",
                        message="汇总表正方向最大与分表不一致",
                        suspected_source="report",
                    ))

        if summary_neg is not None:
            if not neg_vals:
                # 行业惯例：所有值同为正时，"负方向最大"可能填最小正值
                if pos_vals:
                    closest_id, closest_val = min(pos_vals, key=lambda x: x[1])
                    if abs(closest_val - summary_neg) <= tol:
                        issues.append(CheckIssue(
                            severity="info", table_name="简报汇总",
                            point_id=si.monitoring_item, field_name="负方向最大",
                            expected_value="无负值",
                            actual_value=f"{si.negative_max_id}={si.negative_max}",
                            message=f"分表中不存在负值，汇总表负方向最大填写了最小正值（行业惯例）",
                            suspected_source="report",
                        ))
                    else:
                        issues.append(CheckIssue(
                            severity="warning", table_name="简报汇总",
                            point_id=si.monitoring_item, field_name="负方向最大",
                            expected_value=f"无负值，最小正值: {closest_id}={_fmt(closest_val)}",
                            actual_value=f"{si.negative_max_id}={si.negative_max}",
                            message="分表中不存在负值，汇总表负方向最大与最小正值不一致",
                            suspected_source="report",
                        ))
                else:
                    issues.append(CheckIssue(
                        severity="warning", table_name="简报汇总",
                        point_id=si.monitoring_item, field_name="负方向最大",
                        expected_value="-",
                        actual_value=f"{si.negative_max_id}={si.negative_max}",
                        message="分表中无数据，汇总表负方向最大应为空",
                        suspected_source="report",
                    ))
            else:
                actual_neg_id, actual_neg = min(neg_vals, key=lambda x: x[1])
                if abs(actual_neg - summary_neg) > tol:
                    issues.append(CheckIssue(
                        severity="error", table_name="简报汇总",
                        point_id=si.monitoring_item, field_name="负方向最大",
                        expected_value=f"{actual_neg_id}={_fmt(actual_neg)}",
                        actual_value=f"{si.negative_max_id}={si.negative_max}",
                        message="汇总表负方向最大与分表不一致",
                        suspected_source="report",
                    ))


def check_point_count(report: MonitoringReport, issues: list[CheckIssue]) -> None:
    for table_index, table in enumerate(report.tables):
        if table.point_count <= 0:
            continue
        actual = len(table.points) if table.points else len(table.deep_points)
        if actual != table.point_count:
            name = table.monitoring_item
            if table.borehole_id:
                name += f"({table.borehole_id})"
            table_issues = [CheckIssue(
                severity="warning", table_name=name, point_id="ALL",
                field_name="监测点数量",
                expected_value=str(table.point_count), actual_value=str(actual),
                message=f"表头声明 {table.point_count} 个点, 实际 {actual} 行",
            )]
            annotate_issues_for_table(report, table_issues, table_index, default_source="report")
            issues.extend(table_issues)


def run_logic_checks(report: MonitoringReport) -> list[CheckIssue]:
    issues: list[CheckIssue] = []

    check_report_extractability(report, issues)
    check_ocr_damage(report, issues)  # Gap 3: OCR 损毁警告
    if not report.tables:
        return issues

    logger.info("=== 语义匹配 ===")
    _build_semantic_maps(report)

    logger.info("=== 安全状态判定检查 ===")
    check_safety_status(report, issues)
    logger.info("=== 汇总表一致性检查 ===")
    check_summary_consistency(report, issues)
    logger.info("=== 监测点数量检查 ===")
    check_point_count(report, issues)
    return issues
