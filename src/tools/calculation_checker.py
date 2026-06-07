"""
计算验证工具

核心公式：
  本次变化 = 本次测值 - 上次测值
  累计变化 = 本次测值 - 初始测值
  变化速率 = 本次变化 / 时间间隔（天）

Uses TableVerificationConfig for adaptive tolerance/severity per table,
instead of hardcoded category branches.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from statistics import median
from typing import Optional

from src.config import RATE_TOLERANCE
from src.tools.extraction_quality import annotate_issues_for_table
from src.models.data_models import (
    CheckIssue,
    MonitoringCategory,
    MonitoringReport,
    MonitoringTable,
    TableVerificationConfig,
)

logger = logging.getLogger(__name__)


def _calendar_date_key(date_text: str) -> str:
    """从监测日期文本中提取日历日，用于避免同日 AM/PM 被误配成跨期。"""
    text = (date_text or "").strip()
    match = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return text
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _close_enough(a: Optional[float], b: Optional[float], tol: float) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= tol


def _fmt(v: Optional[float], precision: int = 4) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{precision}f}"


def _infer_interval_days(table: MonitoringTable) -> Optional[float]:
    """从表中反推监测间隔天数（取众数）"""
    intervals: list[float] = []
    for pt in table.points:
        if (
            pt.current_change is not None
            and pt.change_rate is not None
            and abs(pt.change_rate) > 1e-6
        ):
            interval = pt.current_change / pt.change_rate
            if 0.5 < abs(interval) < 365:
                intervals.append(round(abs(interval)))
    if not intervals:
        return None
    return Counter(intervals).most_common(1)[0][0]


def _interval_confidence(table: MonitoringTable, candidate_days: float) -> float:
    """计算 candidate_days 的"行级支持率"。

    对表里每个有效行，反推 row_interval = |current_change / change_rate|；
    若 row_interval 与 candidate_days 在 ±20% 容差内，记为"支持"。
    返回 支持数 / 有效行总数。

    用法：用于在配置值（来自报告日期范围）和推断值（来自实际数据）冲突时
    判断哪个更可信。
    """
    if candidate_days is None or candidate_days <= 0:
        return 0.0
    supports = 0
    total = 0
    tol = max(0.5, candidate_days * 0.2)  # 至少 ±0.5 天，否则按 20%
    for pt in table.points:
        if (
            pt.current_change is not None
            and pt.change_rate is not None
            and abs(pt.change_rate) > 1e-6
        ):
            row_interval = abs(pt.current_change / pt.change_rate)
            if 0.5 < row_interval < 365:
                total += 1
                if abs(row_interval - candidate_days) <= tol:
                    supports += 1
    return supports / total if total else 0.0


def _choose_interval_days(
    table: MonitoringTable,
    configured_interval: Optional[float],
) -> Optional[float]:
    """选择用于速率验证的"权威"监测间隔。

    决策树：
        1. 推断值与配置值都没有 → 返回 None（调用方会跳过速率验证）
        2. 只有其中一个 → 返回那个
        3. 两个差距 ≤2 天 → 优先用推断值（更精确，能去除小数级偏差）
        4. 两个差距 >2 天 → 比较行级支持率：
           - 若推断值支持率 ≥50% → 信推断（典型场景：报告日期范围跨多期，
             如鱼珠乐天的"日期范围 7 天但每期 2 天"）
           - 若配置值支持率 ≥推断值支持率 → 信配置
           - 否则 → 信推断（数据 > 元数据）

    历史背景：
        - v1: 差距 >2 天时盲信 configured，导致多期模板（错误版/正确版都是
          多期块）的全部行都触发"反推间隔≈2天"警告，34+ 行误报。
        - v2: 引入行级支持率仲裁，多期场景下能正确识别每期内部的真实间隔。
    """
    inferred_interval = _infer_interval_days(table)

    if configured_interval is None and inferred_interval is None:
        return None
    if configured_interval is None:
        return inferred_interval
    if inferred_interval is None:
        return configured_interval

    if abs(inferred_interval - configured_interval) <= 2:
        return inferred_interval  # 微小偏差，优先用推断（更精确）

    # 显著差距：用行级支持率仲裁
    cfg_support = _interval_confidence(table, configured_interval)
    inf_support = _interval_confidence(table, inferred_interval)

    if inf_support >= 0.5 and inf_support > cfg_support:
        logger.info(
            "表 [%s] 间隔仲裁: 推断 %.0f 天(支持率 %.0f%%) > 配置 %.0f 天(支持率 %.0f%%)，采纳推断值",
            table.monitoring_item,
            inferred_interval, inf_support * 100,
            configured_interval, cfg_support * 100,
        )
        return inferred_interval
    return configured_interval


def check_cumulative_change(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """
    验证累计变化量 = 本次测值 - 初始测值。
    Uses table.verification_config for tolerance and severity.
    """
    cfg = table.verification_config

    if not cfg.initial_value_reliable:
        logger.info("表 [%s] 初始基准不可靠，跳过累计变化量验证", table.monitoring_item)
        return

    for pt in table.points:
        if pt.initial_value is None or pt.current_value is None or pt.cumulative_change is None:
            continue

        if cfg.unit_conversion != 1.0:
            expected = (pt.current_value - pt.initial_value) * cfg.unit_conversion
        else:
            expected = pt.current_value - pt.initial_value

        tol = cfg.cumulative_tolerance
        if abs(pt.cumulative_change) > 10:
            tol = max(tol, abs(pt.cumulative_change) * 0.05)

        if not _close_enough(expected, pt.cumulative_change, tol):
            severity = cfg.severity_for_cumulative

            hint = ""
            if cfg.unit == "m":
                hint = "（高程精度限制，可能需人工确认）"
            elif not cfg.initial_value_reliable:
                hint = "（初始基准可能不同，需人工确认）"

            issues.append(CheckIssue(
                severity=severity,
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="累计变化量",
                expected_value=_fmt(expected, 2),
                actual_value=_fmt(pt.cumulative_change, 2),
                message=(
                    f"累计变化量与初始/本次测值推算不符: "
                    f"({_fmt(pt.current_value, 5)} - {_fmt(pt.initial_value, 5)})"
                    f"{' × 1000' if cfg.unit_conversion != 1.0 else ''}"
                    f" = {_fmt(expected, 2)}, 报告值 = {_fmt(pt.cumulative_change, 2)}"
                    f"{hint}"
                ),
            ))


def check_change_rate(
    table: MonitoringTable,
    issues: list[CheckIssue],
    interval_days: Optional[float] = None,
) -> None:
    """验证 变化速率 = 本次变化量 / 间隔天数"""
    cfg = table.verification_config

    if interval_days is None:
        interval_days = _choose_interval_days(table, cfg.interval_days)
    else:
        interval_days = _choose_interval_days(table, interval_days)
    if interval_days is None:
        logger.warning("表 [%s] 无法推断监测间隔天数，跳过速率验证", table.monitoring_item)
        issues.append(CheckIssue(
            severity="info",
            table_name=table.monitoring_item,
            point_id="ALL",
            field_name="变化速率",
            expected_value="N/A",
            actual_value="N/A",
            message="无法推断监测间隔天数，跳过速率验证",
        ))
        return

    logger.info("表 [%s] 推断监测间隔 = %.0f 天", table.monitoring_item, interval_days)
    rate_tol = cfg.rate_tolerance

    for pt in table.points:
        if pt.current_change is None or pt.change_rate is None:
            continue
        if abs(pt.current_change) < 1e-6:
            continue

        expected_rate = pt.current_change / interval_days

        if not _close_enough(expected_rate, pt.change_rate, rate_tol):
            pt_interval = abs(pt.current_change / pt.change_rate) if abs(pt.change_rate) > 1e-6 else 0
            pt_interval_r = round(pt_interval)
            interval_is_clean = abs(pt_interval - pt_interval_r) <= 0.5
            if pt_interval_r != interval_days and 1 <= pt_interval_r <= 365 and interval_is_clean:
                severity = "warning"
                msg = (
                    f"变化速率与多数测点间隔({interval_days:.0f}天)不一致: "
                    f"本次变化({_fmt(pt.current_change, 2)}) / {interval_days:.0f}天 = {_fmt(expected_rate, 3)}, "
                    f"报告值 = {_fmt(pt.change_rate, 3)} (反推间隔≈{pt_interval_r}天，可能该点上次监测时间不同)"
                )
            else:
                severity = "error"
                msg = (
                    f"变化速率计算不符: "
                    f"本次变化({_fmt(pt.current_change, 2)}) / {interval_days:.0f}天 "
                    f"= {_fmt(expected_rate, 3)}, 报告值 = {_fmt(pt.change_rate, 3)}"
                )
            issues.append(CheckIssue(
                severity=severity,
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="变化速率",
                expected_value=_fmt(expected_rate, 3),
                actual_value=_fmt(pt.change_rate, 3),
                message=msg,
            ))


def check_deep_displacement_rate(
    table: MonitoringTable,
    issues: list[CheckIssue],
    interval_days: Optional[float] = None,
) -> None:
    """深层水平位移速率验证（仅在存在速率列时执行）。"""
    if not table.deep_points:
        return
    if not any(dp.change_rate is not None for dp in table.deep_points):
        return

    cfg = table.verification_config

    # 收集行级反推 intervals（用于推断与置信度计算）
    rates_data: list[float] = []
    for dp in table.deep_points:
        diff = None
        if dp.current_change is not None:
            diff = abs(dp.current_change)
        elif dp.previous_cumulative is not None and dp.current_cumulative is not None:
            diff = abs(dp.current_cumulative - dp.previous_cumulative)
        if (
            diff is not None
            and dp.change_rate is not None
            and abs(dp.change_rate) > 1e-6
            and diff > 1e-6
        ):
            inferred = diff / dp.change_rate
            if 0.5 < abs(inferred) < 365:
                rates_data.append(round(abs(inferred)))

    inferred_interval = Counter(rates_data).most_common(1)[0][0] if rates_data else None

    if interval_days is None:
        interval_days = cfg.interval_days

    # 仲裁：configured vs inferred（深层位移版，逻辑同 _choose_interval_days）
    if interval_days is None and inferred_interval is None:
        chosen_interval = None
    elif interval_days is None:
        chosen_interval = inferred_interval
    elif inferred_interval is None:
        chosen_interval = interval_days
    elif abs(inferred_interval - interval_days) <= 2:
        chosen_interval = inferred_interval  # 微小差异优先用推断
    else:
        # 显著差距：用行级支持率仲裁
        total = len(rates_data)
        cfg_sup = sum(1 for r in rates_data if abs(r - interval_days) <= max(0.5, interval_days * 0.2)) / total if total else 0
        inf_sup = sum(1 for r in rates_data if abs(r - inferred_interval) <= max(0.5, inferred_interval * 0.2)) / total if total else 0
        if inf_sup >= 0.5 and inf_sup > cfg_sup:
            logger.info(
                "深层位移表 [%s] 间隔仲裁: 推断 %.0f 天(支持率 %.0f%%) > 配置 %.0f 天(支持率 %.0f%%)",
                table.monitoring_item, inferred_interval, inf_sup * 100,
                interval_days, cfg_sup * 100,
            )
            chosen_interval = inferred_interval
        else:
            chosen_interval = interval_days

    table_label = table.monitoring_item
    if table.borehole_id:
        table_label += f"({table.borehole_id})"

    if chosen_interval is None:
        issues.append(CheckIssue(
            severity="info",
            table_name=table_label,
            point_id="ALL",
            field_name="变化速率",
            expected_value="N/A",
            actual_value="N/A",
            message="深层位移表无法推断监测间隔天数，跳过速率验证",
        ))
        return

    interval_days = chosen_interval
    logger.info("深层位移表 [%s] 推断监测间隔 = %.0f 天", table_label, interval_days)

    for dp in table.deep_points:
        if dp.change_rate is None:
            continue

        if dp.current_change is not None:
            diff = dp.current_change
        elif dp.previous_cumulative is not None and dp.current_cumulative is not None:
            diff = dp.current_cumulative - dp.previous_cumulative
        else:
            continue

        expected_rate = diff / interval_days

        if not _close_enough(abs(expected_rate), abs(dp.change_rate), cfg.rate_tolerance):
            issues.append(CheckIssue(
                severity="error",
                table_name=table_label,
                point_id=f"深度{dp.depth}m",
                field_name="变化速率",
                expected_value=_fmt(expected_rate, 3),
                actual_value=_fmt(dp.change_rate, 3),
                message=(
                    f"深层位移速率不符: "
                    f"({_fmt(dp.current_cumulative, 2)} - {_fmt(dp.previous_cumulative, 2)}) "
                    f"/ {interval_days:.0f} = {_fmt(expected_rate, 3)}, "
                    f"报告值 = {_fmt(dp.change_rate, 3)}"
                ),
            ))


def check_deep_displacement_change(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """深层位移本期变化验证：本期变化 = 本次累计 - 上次累计。"""
    if not table.deep_points:
        return

    cfg = table.verification_config
    table_label = table.monitoring_item
    if table.borehole_id:
        table_label += f"({table.borehole_id})"

    for dp in table.deep_points:
        if (
            dp.previous_cumulative is None
            or dp.current_cumulative is None
            or dp.current_change is None
        ):
            continue

        expected_change = dp.current_cumulative - dp.previous_cumulative
        if not _close_enough(expected_change, dp.current_change, cfg.cumulative_tolerance):
            issues.append(CheckIssue(
                severity="error",
                table_name=table_label,
                point_id=f"深度{dp.depth}m",
                field_name="本期变化",
                expected_value=_fmt(expected_change, 3),
                actual_value=_fmt(dp.current_change, 3),
                message=(
                    f"深层位移本期变化不符: "
                    f"({_fmt(dp.current_cumulative, 2)} - {_fmt(dp.previous_cumulative, 2)}) "
                    f"= {_fmt(expected_change, 3)}, 报告值 = {_fmt(dp.current_change, 3)}"
                ),
            ))


def check_anchor_force(
    table: MonitoringTable,
    issues: list[CheckIssue],
) -> None:
    """锚索拉力: 累计变化量 = 本次内力 - 初始内力"""
    cfg = table.verification_config

    for pt in table.points:
        if pt.initial_value is None or pt.current_value is None or pt.cumulative_change is None:
            continue

        expected = pt.current_value - pt.initial_value
        if not _close_enough(expected, pt.cumulative_change, cfg.cumulative_tolerance):
            issues.append(CheckIssue(
                severity="error",
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="累计变化量",
                expected_value=_fmt(expected, 1),
                actual_value=_fmt(pt.cumulative_change, 1),
                message=(
                    f"锚索拉力累计变化不符: "
                    f"本次({_fmt(pt.current_value, 1)}) - 初始({_fmt(pt.initial_value, 1)}) "
                    f"= {_fmt(expected, 1)}, 报告值 = {_fmt(pt.cumulative_change, 1)}"
                ),
            ))


def _pair_equal(a, b, tol: float = 1e-6) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _is_value_copy(t1: MonitoringTable, t2: MonitoringTable) -> bool:
    """Return True when a later period is a multi-point placeholder copy."""
    common = 0
    t1_by_id = {point.point_id: point for point in t1.points if point.point_id}
    for point2 in t2.points:
        if not point2.point_id:
            continue
        point1 = t1_by_id.get(point2.point_id)
        if point1 is None:
            continue
        if not _pair_equal(point1.current_change, point2.current_change):
            return False
        if not _pair_equal(point1.cumulative_change, point2.cumulative_change):
            return False
        common += 1
    return common >= 2


def check_cross_period_continuity(
    report: MonitoringReport,
    issues: list[CheckIssue],
) -> None:
    """跨期累计连续性验证：``累计_{N+1} = 累计_N + 本次_{N+1}``

    适用于"横向多期布局且无独立初始值列"的模板（如展誉立柱沉降、基坑顶水平位移）。
    LLM 把同 monitoring_item 的不同期拆成不同 table 后，本函数按 monitor_date
    排序，对每个测点检查跨期累计的连续性。

    设计要点：
        - 仅对 ``points`` 表生效（深层位移走自己的 prev/current 连续性逻辑）
        - 按 monitoring_item + borehole_id 分组
        - 同组少于 2 期 → 不做检查（向后兼容）
        - 容差：max(0.15, |累计| * 5%)，与单期 cumulative 验证保持一致

    历史背景：
        - 展誉模板的"立柱沉降/基坑顶水平位移/地下水位/建筑物倾斜"等表无独立
          初始值列，单期 ``(current - initial)`` 公式不适用
        - 跨期连续性是这类模板的唯一可靠数学校核手段
    """
    from collections import defaultdict

    groups: dict[tuple[str, str], list[MonitoringTable]] = defaultdict(list)
    for t in report.tables:
        if not t.points:
            continue  # 深层位移走自己的路径
        groups[(t.monitoring_item or "", t.borehole_id or "")].append(t)

    for (item, bh), tables in groups.items():
        if len(tables) < 2:
            continue
        # 按 monitor_date 排序（缺失日期排最后，按 monitor_count 备用）
        sorted_tbls = sorted(
            tables,
            key=lambda t: (
                (t.monitor_date or "9999"),
                (t.monitor_count or ""),
            ),
        )

        for n_tbl, n1_tbl in zip(sorted_tbls, sorted_tbls[1:]):
            # 跨期连续性：n+1 累计 = n 累计 + n+1 本次
            #
            # 关键防御：同日期同次数的表是 LLM 多次抽取的"同一期"重复（如不同
            # chunk 都拿到同一份数据），不能视为前后两期。跳过。
            n_date = (n_tbl.monitor_date or "").strip()
            n1_date = (n1_tbl.monitor_date or "").strip()
            n_count = (n_tbl.monitor_count or "").strip()
            n1_count = (n1_tbl.monitor_count or "").strip()
            n_date_key = _calendar_date_key(n_date)
            n1_date_key = _calendar_date_key(n1_date)
            if n_date_key and n_date_key == n1_date_key:
                continue
            if _is_value_copy(n_tbl, n1_tbl):
                continue

            n_cums = {
                p.point_id: p.cumulative_change
                for p in n_tbl.points
                if p.cumulative_change is not None and p.point_id
            }
            if not n_cums:
                continue

            label_n = n_date or n_count or "前一期"
            label_n1 = n1_date or n1_count or "本期"

            for pt in n1_tbl.points:
                if (
                    pt.cumulative_change is None
                    or pt.current_change is None
                    or not pt.point_id
                ):
                    continue
                prev_cum = n_cums.get(pt.point_id)
                if prev_cum is None:
                    continue

                expected = prev_cum + pt.current_change
                opposite_sign_expected = prev_cum - pt.current_change
                tol = max(0.15, abs(pt.cumulative_change) * 0.05)

                if not _close_enough(expected, pt.cumulative_change, tol):
                    if _close_enough(opposite_sign_expected, pt.cumulative_change, tol):
                        issues.append(CheckIssue(
                            severity="info",
                            table_name=item or "未命名表",
                            point_id=pt.point_id,
                            field_name="跨期累计连续性",
                            expected_value=_fmt(opposite_sign_expected, 2),
                            actual_value=_fmt(pt.cumulative_change, 2),
                            message=(
                                f"跨期累计按相反本次变化符号可连续: "
                                f"{label_n}累计({_fmt(prev_cum, 2)}) - {label_n1}本次({_fmt(pt.current_change, 2)}) "
                                f"= {_fmt(opposite_sign_expected, 2)}，疑似该表本次变化方向约定与累计增量相反"
                            ),
                        ))
                        continue
                    issues.append(CheckIssue(
                        severity="error",
                        table_name=item or "未命名表",
                        point_id=pt.point_id,
                        field_name="跨期累计连续性",
                        expected_value=_fmt(expected, 2),
                        actual_value=_fmt(pt.cumulative_change, 2),
                        message=(
                            f"跨期累计不连续: "
                            f"{label_n}累计({_fmt(prev_cum, 2)}) + {label_n1}本次({_fmt(pt.current_change, 2)}) "
                            f"= {_fmt(expected, 2)}, 但 {label_n1}累计报告值 = {_fmt(pt.cumulative_change, 2)}"
                        ),
                    ))


_ANOMALY_OUTLIER_MULTIPLIER = 3.0
"""单期变化离群阈值：|cc| > N × median(|cc|) 视为离群"""

_ANOMALY_INCONSISTENT_MULTIPLIER = 3.0
"""本次 vs 累计不协调阈值：|cc| > N × max(|cum|, 0.5) 视为可疑"""

_ANOMALY_MIN_POINTS = 4
"""至少需 N 个测点统计才有意义；少于此跳过"""

_ANOMALY_MIN_ABS_VALUE = 0.5
"""绝对值阈值：|cc| < 此值的'离群'通常是 OCR 噪音或自然测量误差，跳过"""


def check_current_change_anomaly(
    report: MonitoringReport,
    issues: list[CheckIssue],
) -> None:
    """单期变化幅度异常检测（适用普通 points 表，深层位移不在此检查）

    两类异常：
    1. **行间离群**：|current_change_i| > 3 × median(|cc|) 且 |cc_i| >= 0.5
       例：监测报告测试 M5 -23.9 vs 其它 ≤ 0.4
    2. **本次 vs 累计 不协调**：|current_change| > 3 × max(|cumulative_change|, 0.5)
       例：M5 |cc|=23.9, |cum|=1.7，cc 比 cum 大 14 倍 → 暗示数据/OCR 错

    严重度：均为 warning（建议人工复核，不阻断）。
    """
    for table_index, table in enumerate(report.tables):
        # 收集有效点（同时有 current_change 与 cumulative_change）
        valid_pts = [
            pt for pt in table.points
            if pt.current_change is not None and pt.cumulative_change is not None
        ]
        if not valid_pts:
            continue

        # 离群需统计分布（≥4 点），不协调检查单行即可
        do_outlier = len(valid_pts) >= _ANOMALY_MIN_POINTS
        median_cc = 0.0
        if do_outlier:
            abs_ccs = [abs(pt.current_change) for pt in valid_pts]
            # 用 statistics.median 而不是 [n//2]，正确处理偶数样本（取两中数均值）
            median_cc = median(abs_ccs)

        table_issues: list[CheckIssue] = []
        for pt in valid_pts:
            abs_cc = abs(pt.current_change)
            abs_cum = abs(pt.cumulative_change)

            # 跳过绝对值过小的（噪音范围）
            if abs_cc < _ANOMALY_MIN_ABS_VALUE:
                continue

            # 检查 1（≥4 点）：离群
            outlier = (
                do_outlier
                and abs_cc > _ANOMALY_OUTLIER_MULTIPLIER * max(median_cc, _ANOMALY_MIN_ABS_VALUE)
            )
            # 检查 2（任何样本量）：本次 vs 累计 不协调
            inconsistent = abs_cc > _ANOMALY_INCONSISTENT_MULTIPLIER * max(abs_cum, _ANOMALY_MIN_ABS_VALUE)

            if not (outlier or inconsistent):
                continue

            tags = []
            if outlier:
                tags.append(f"本次变化 {pt.current_change} 远超其它测点中位数 {median_cc:.3f}")
            if inconsistent:
                tags.append(f"本次变化 |{pt.current_change}| ≫ |累计 {pt.cumulative_change}|（≥{_ANOMALY_INCONSISTENT_MULTIPLIER}×）")

            table_issues.append(CheckIssue(
                severity="warning",
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="单期变化幅度",
                expected_value="符合行间分布",
                actual_value=_fmt(pt.current_change, 3),
                message="单期变化异常：" + "；".join(tags) + "，建议核对原 PDF",
            ))

        annotate_issues_for_table(report, table_issues, table_index, default_source="report")
        issues.extend(table_issues)


def run_calculation_checks(report: MonitoringReport) -> list[CheckIssue]:
    """对报告中的所有表格运行计算验证"""
    issues: list[CheckIssue] = []

    for table_index, table in enumerate(report.tables):
        logger.info("=== 计算验证: %s ===", table.monitoring_item)
        table_issues: list[CheckIssue] = []

        if table.category in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
            check_anchor_force(table, table_issues)
        elif table.deep_points:
            check_deep_displacement_change(table, table_issues)
            check_deep_displacement_rate(table, table_issues)
        else:
            check_cumulative_change(table, table_issues)
            check_change_rate(table, table_issues)

        annotate_issues_for_table(report, table_issues, table_index, default_source="report")
        issues.extend(table_issues)

    # 跨表检查：跨期累计连续性（对横向多期布局且无独立初始值列的模板特别有用）
    cross_period_issues: list[CheckIssue] = []
    check_cross_period_continuity(report, cross_period_issues)
    issues.extend(cross_period_issues)

    # 单期变化异常（Gap 2）：识别离群本次变化 + 本次/累计不协调
    anomaly_issues: list[CheckIssue] = []
    check_current_change_anomaly(report, anomaly_issues)
    issues.extend(anomaly_issues)

    # 符号一致性（Gap 6）：本次-初始 与 累计变化 应同号
    sign_issues: list[CheckIssue] = []
    check_sign_consistency(report, sign_issues)
    issues.extend(sign_issues)

    return issues


_SIGN_MIN_DIFF_MM = 0.5
"""(current - initial) 在 mm 量级下小于该阈值时跳过，避免噪声误报"""

_SIGN_MAGNITUDE_RATIO_MAX = 10.0
"""|cumulative| / |current-initial| 超过该比例时跳过，因为 'initial' 列可能
是 '上期测值' 而非 '项目首测'。两者量级相差过大时不应直接比较符号。"""


def check_sign_consistency(
    report: MonitoringReport,
    issues: list[CheckIssue],
) -> None:
    """检查每个测点的 (current - initial) 与 cumulative_change 符号是否一致。

    示例：监测报告测试 G2
    - initial=9.51112 m, current=9.52275 m → 差 +11.63 mm (上升)
    - 报告 cumulative=-17.45 mm (下沉)
    - 符号矛盾 → ERROR

    设计要点：
    - 对所有有 initial+current+cumulative 字段的表格都检查
      （即使 initial_value_reliable=False：高程精度的不可靠不影响符号正确性）
    - initial_value_reliable=True → severity=error
    - initial_value_reliable=False → severity=warning（保留怀疑空间）
    - 差值小于 ~0.5 mm 时跳过（高程表噪声范围）
    - 独立于"数量级异常"60% 阈值，弥补单点孤立错号
    """
    for table in report.tables:
        cfg = table.verification_config
        unit_conv = cfg.unit_conversion if cfg.unit_conversion else 1.0
        is_reliable = bool(cfg.initial_value_reliable)

        for pt in table.points:
            if pt.initial_value is None or pt.current_value is None or pt.cumulative_change is None:
                continue
            diff_mm = (pt.current_value - pt.initial_value) * unit_conv
            if abs(diff_mm) < _SIGN_MIN_DIFF_MM:
                continue
            if abs(pt.cumulative_change) < _SIGN_MIN_DIFF_MM:
                continue
            # 量级悬殊（>10×）：'initial' 列可能是'上期'而非'项目首测'，跳过避免误报
            larger = max(abs(diff_mm), abs(pt.cumulative_change))
            smaller = min(abs(diff_mm), abs(pt.cumulative_change))
            if smaller > 0 and (larger / smaller) > _SIGN_MAGNITUDE_RATIO_MAX:
                continue
            if (diff_mm > 0) == (pt.cumulative_change > 0):
                continue

            severity = "error" if is_reliable else "warning"
            uncertainty_hint = "" if is_reliable else "（初始基准标记不可靠，请人工确认是否为同基准比较）"
            issues.append(CheckIssue(
                severity=severity,
                table_name=table.monitoring_item,
                point_id=pt.point_id,
                field_name="符号一致性",
                expected_value=f"sign≈{'+' if diff_mm > 0 else '-'}",
                actual_value=f"{pt.cumulative_change:+.2f}",
                message=(
                    f"符号矛盾：本次-初始 = {diff_mm:+.2f} mm "
                    f"(本次 {pt.current_value} - 初始 {pt.initial_value})，"
                    f"但累计标 {pt.cumulative_change:+.2f}，疑似 OCR/列错位或数据错"
                    f"{uncertainty_hint}"
                ),
            ))
