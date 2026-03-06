"""
单表动态验证配置生成器

核心理念：不硬编码容差/严重级别，而是根据每张表的实际数据特征动态决定。

关于累计变化量计算的重要说明：
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
公式: 累计变化 = 本次测值 - 初始测值

但在实际报告中，存在以下特殊情况：

1. 高程数据（竖向位移/沉降）：
   - 初始值和本次值单位是 m（如 -2.70184m）
   - 累计变化量单位是 mm（如 31.21mm）
   - 需要乘以1000转换: (本次高程 - 初始高程) × 1000
   - 但因为高程只有5位小数(精度0.01mm)，经过几十期累积，
     误差可能达到数mm，所以不能用error级别来判断

2. 水位数据：
   - 报告中的"初始值"可能不是项目建设初期的首次测量值
   - 而是某个特定基准期的值
   - 因此 (本次 - 初始) 可能与报告的"累计变化"完全不同
   - 这种情况只能标记为warning，不能判定为error

3. 锚索拉力/支撑轴力：
   - 单位是 kN，不需要单位转换
   - 初始值可靠，累计变化 = 本次内力 - 初始内力
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config import FLOAT_TOLERANCE, RATE_TOLERANCE
from src.models.data_models import (
    MonitoringCategory,
    MonitoringTable,
    MonitoringReport,
    TableVerificationConfig,
)

logger = logging.getLogger(__name__)


def build_verification_config(
    table: MonitoringTable,
    table_unit: str = "mm",
    initial_reliable: bool = True,
    global_interval: Optional[float] = None,
) -> TableVerificationConfig:
    """
    根据表格数据特征，动态生成验证配置。

    取代之前在 calculation_checker 里的硬编码分支:
        if is_elevation: tol = xxx
        elif is_water: tol = yyy

    现在由 LLM 告知 table_unit，再结合数据特征自动决定。

    参数:
        table: 监测数据表
        table_unit: LLM识别的数据单位 ("mm"/"m"/"kN")
        initial_reliable: LLM判断初始值是否可用于计算累计变化
        global_interval: 从报告元数据提取的全局监测间隔天数
    """
    cfg = TableVerificationConfig(
        unit=table_unit,
        cumulative_tolerance=FLOAT_TOLERANCE,
        rate_tolerance=RATE_TOLERANCE,
        interval_days=global_interval,
        initial_value_reliable=initial_reliable,
        severity_for_cumulative="error",
    )

    # 高程类数据：单位m，需要×1000转mm，容差放大，降级为warning
    if table_unit == "m":
        cfg.unit_conversion = 1000.0
        cfg.cumulative_tolerance = max(FLOAT_TOLERANCE * 5, 1.0)
        cfg.severity_for_cumulative = "warning"
        cfg.initial_value_reliable = False
    elif table_unit == "kN":
        cfg.unit_conversion = 1.0
        cfg.cumulative_tolerance = FLOAT_TOLERANCE
        cfg.severity_for_cumulative = "error"

    # 按监测类别做细分调整
    cat = table.category
    if cat == MonitoringCategory.WATER_LEVEL:
        # 水位数据的"初始"含义可能不同于建设初期，放宽容差
        cfg.cumulative_tolerance = max(FLOAT_TOLERANCE * 50, 10.0)
        cfg.severity_for_cumulative = "warning"
        cfg.initial_value_reliable = False
    elif cat in (MonitoringCategory.VERTICAL_DISP, MonitoringCategory.SETTLEMENT):
        # 竖向位移/沉降：如果LLM没标记为m，用启发式检测
        if cfg.unit != "m":
            _detect_elevation_from_data(table, cfg)
    elif cat in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
        # 锚索拉力/支撑轴力：初始值可靠，直接算
        cfg.unit = "kN"
        cfg.unit_conversion = 1.0
        cfg.severity_for_cumulative = "error"

    return cfg


def _detect_elevation_from_data(table: MonitoringTable, cfg: TableVerificationConfig):
    """
    启发式检测：如果初始值/本次值看起来像高程（绝对值小于100的数值），
    而累计变化量是mm级别的较大数值，则判断为高程数据。

    例如：
        初始高程 = -2.70184 (m)
        本次高程 = -2.70242 (m)
        累计变化 = 31.21 (mm)
        → (本次-初始)*1000 = -0.58mm ≠ 31.21mm
        → 说明初始值可能不是真正的项目初始基准
        → 标记为 warning 而非 error
    """
    for pt in table.points[:3]:
        if pt.initial_value is not None and abs(pt.initial_value) < 100:
            if pt.cumulative_change is not None and abs(pt.cumulative_change) > 1:
                cfg.unit = "m"
                cfg.unit_conversion = 1000.0
                cfg.cumulative_tolerance = max(FLOAT_TOLERANCE * 5, 1.0)
                cfg.severity_for_cumulative = "warning"
                cfg.initial_value_reliable = False
                break


def generate_analysis_plan(report: MonitoringReport) -> list[dict]:
    """
    ReAct 风格分析计划：逐表检查字段、数据特征、验证策略。
    纯 Python 分析（无 LLM 调用），基于已构建的 TableVerificationConfig。

    返回每张表一个 dict，包含:
      table_name, category, point_count, is_deep,
      fields_detected, data_sample, unit_analysis, initial_value_analysis,
      verification_methods, special_notes, interval_info
    """
    from collections import Counter

    plans: list[dict] = []

    for idx, table in enumerate(report.tables):
        cfg = table.verification_config
        is_deep = bool(table.deep_points)

        # ── a) 表标识 ────────────────────────────────────
        name = table.monitoring_item
        if table.borehole_id:
            name += f" ({table.borehole_id})"
        point_count = len(table.deep_points) if is_deep else len(table.points)

        # ── b) 字段检测矩阵 ──────────────────────────────
        fields: dict[str, bool] = {}
        if is_deep and table.deep_points:
            dp = table.deep_points[0]
            fields = {
                "depth": dp.depth is not None,
                "previous_cumulative": dp.previous_cumulative is not None,
                "current_cumulative": dp.current_cumulative is not None,
                "change_rate": dp.change_rate is not None,
            }
        elif table.points:
            pt = table.points[0]
            fields = {
                "initial_value": pt.initial_value is not None,
                "previous_value": pt.previous_value is not None,
                "current_value": pt.current_value is not None,
                "current_change": pt.current_change is not None,
                "cumulative_change": pt.cumulative_change is not None,
                "change_rate": pt.change_rate is not None,
                "safety_status": bool(pt.safety_status),
            }

        # ── c) 数据样本（前2个测点）────────────────────────
        samples: list[str] = []
        if is_deep:
            for dp in table.deep_points[:2]:
                parts = [f"深度{dp.depth}m"]
                if dp.previous_cumulative is not None:
                    parts.append(f"上次累计={dp.previous_cumulative:.2f}")
                if dp.current_cumulative is not None:
                    parts.append(f"本次累计={dp.current_cumulative:.2f}")
                if dp.change_rate is not None:
                    parts.append(f"速率={dp.change_rate:.3f}")
                samples.append(", ".join(parts))
        else:
            for pt in table.points[:2]:
                parts = [pt.point_id]
                if pt.initial_value is not None:
                    parts.append(f"初始={pt.initial_value:.5g}")
                if pt.previous_value is not None:
                    parts.append(f"上次={pt.previous_value:.5g}")
                if pt.current_value is not None:
                    parts.append(f"本次={pt.current_value:.5g}")
                if pt.current_change is not None:
                    parts.append(f"本次变化={pt.current_change:.2f}")
                if pt.cumulative_change is not None:
                    parts.append(f"累计={pt.cumulative_change:.2f}")
                if pt.change_rate is not None:
                    parts.append(f"速率={pt.change_rate:.4f}")
                samples.append(", ".join(parts))

        # ── d) 单位分析 ──────────────────────────────────
        if cfg.unit == "m" and cfg.unit_conversion == 1000.0:
            conversion_note = "高程数据(m), 需×1000转换为mm"
        elif cfg.unit == "kN":
            conversion_note = "力学数据(kN), 无需转换"
        else:
            conversion_note = "无需转换"

        # ── e) 初始值可靠性分析 ───────────────────────────
        cat = table.category
        if cat in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
            reliability_reason = "锚索/支撑: 初始内力可靠，累计变化=本次内力-初始内力"
        elif cfg.unit == "m" or cat in (MonitoringCategory.VERTICAL_DISP, MonitoringCategory.SETTLEMENT):
            if not cfg.initial_value_reliable:
                reliability_reason = (
                    "高程数据: 表中初始高程可能非项目首测基准，"
                    "精度仅5位小数(0.01mm)，经多期累积误差可达数mm"
                )
            else:
                reliability_reason = "初始值可靠，可直接计算累计变化"
        elif cat == MonitoringCategory.WATER_LEVEL:
            reliability_reason = (
                "水位数据: 初始基准可能因施工阶段改变，"
                "(本次-初始)可能与报告累计变化完全不同"
            )
        elif not cfg.initial_value_reliable:
            reliability_reason = "初始基准可能不同，需人工确认"
        else:
            reliability_reason = "初始值可靠，可直接计算累计变化"

        # ── f) 监测间隔推断 ──────────────────────────────
        interval_days = cfg.interval_days
        interval_source = "报告日期范围"
        if interval_days is None:
            interval_days = _infer_interval_from_table(table)
            interval_source = "从数据反推(众数)" if interval_days else "待推断"

        # ── g) 验证方法列表 ──────────────────────────────
        methods: list[dict] = []
        if cat in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
            methods.append({
                "name": "锚索累计变化验证",
                "formula": "本次内力 - 初始内力",
                "tolerance": f"{cfg.cumulative_tolerance}kN",
                "severity": "error",
            })
        elif is_deep:
            interval_str = f"{interval_days:.0f}" if interval_days else "?"
            methods.append({
                "name": "深层位移速率验证",
                "formula": f"abs(本次累计 - 上次累计) / {interval_str}天",
                "tolerance": f"{cfg.rate_tolerance}mm/d",
                "severity": "error",
            })
        else:
            conv_suffix = f" × {cfg.unit_conversion:.0f}" if cfg.unit_conversion != 1.0 else ""
            methods.append({
                "name": "累计变化量验证",
                "formula": f"(本次测值 - 初始测值){conv_suffix}",
                "tolerance": f"{cfg.cumulative_tolerance}mm",
                "severity": cfg.severity_for_cumulative,
            })
            interval_str = f"{interval_days:.0f}" if interval_days else "?"
            methods.append({
                "name": "变化速率验证",
                "formula": f"本次变化量 / {interval_str}天",
                "tolerance": f"{cfg.rate_tolerance}mm/d",
                "severity": "error",
            })

        # 统计验证对所有表通用
        stats_desc = "正/负方向最大值, 最大速率"
        if is_deep:
            stats_desc += " (豁免跨表引用检查)"
        elif cat in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
            stats_desc = "最大/最小内力"
        methods.append({
            "name": "统计验证",
            "formula": stats_desc,
            "tolerance": f"{FLOAT_TOLERANCE}",
            "severity": "error",
        })

        # ── h) 特殊说明 ─────────────────────────────────
        notes: list[str] = []
        if is_deep:
            notes.append("深层位移表豁免跨表引用检查(行业惯例, 全局最大引用跨孔位数据)")
            notes.append("速率比较使用绝对值(忽略方向符号), 避免正负号导致误报")
        if cfg.unit == "m" and not cfg.initial_value_reliable:
            notes.append("高程数据累计变化不一致仅标warning, 因初始值可能非项目首测基准")
        if cat == MonitoringCategory.WATER_LEVEL:
            notes.append(f"水位容差放大至{cfg.cumulative_tolerance}mm, 初始基准可能不同")
        if interval_days and interval_source == "从数据反推(众数)":
            notes.append("监测间隔从数据反推(取众数), 个别测点间隔不同时降级为warning")

        plans.append({
            "table_index": idx + 1,
            "table_name": name,
            "category": cat.value,
            "point_count": point_count,
            "is_deep": is_deep,
            "fields_detected": fields,
            "data_sample": samples,
            "unit": cfg.unit,
            "unit_conversion": cfg.unit_conversion,
            "conversion_note": conversion_note,
            "initial_reliable": cfg.initial_value_reliable,
            "reliability_reason": reliability_reason,
            "interval_days": interval_days,
            "interval_source": interval_source,
            "verification_methods": methods,
            "tolerance": cfg.cumulative_tolerance,
            "severity": cfg.severity_for_cumulative,
            "special_notes": notes,
        })

    logger.info("分析计划已生成: %d 张表", len(plans))
    return plans


def _infer_interval_from_table(table: MonitoringTable) -> Optional[float]:
    """从表中测点数据反推监测间隔天数（取众数），兼容普通表和深层位移表"""
    from collections import Counter

    intervals: list[float] = []
    if table.deep_points:
        for dp in table.deep_points:
            if (
                dp.previous_cumulative is not None
                and dp.current_cumulative is not None
                and dp.change_rate is not None
                and abs(dp.change_rate) > 1e-6
            ):
                diff = abs(dp.current_cumulative - dp.previous_cumulative)
                if diff > 1e-6:
                    inferred = diff / abs(dp.change_rate)
                    if 0.5 < inferred < 365:
                        intervals.append(round(inferred))
    else:
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


def enrich_configs_with_llm(report: MonitoringReport) -> None:
    """
    可选的 LLM 增强配置步骤。

    对于启发式判断不够准确的表格（如累计变化与计算值差异巨大），
    请求 LLM 分析数据特征，给出更准确的配置建议。

    只在发现异常时才调用LLM，减少API调用次数。
    """
    tables_needing_review = []
    for i, table in enumerate(report.tables):
        if table.deep_points:
            continue
        if not table.points:
            continue
        sample = table.points[0]
        has_initial = sample.initial_value is not None
        has_cumulative = sample.cumulative_change is not None
        if has_initial and has_cumulative:
            expected = sample.current_value - sample.initial_value if sample.current_value else None
            if expected is not None:
                ratio = abs(expected / sample.cumulative_change) if sample.cumulative_change else 0
                # 如果计算值与报告值相差超过100倍，说明可能有单位或基准问题
                if ratio < 0.01 or ratio > 100:
                    tables_needing_review.append(i)

    if not tables_needing_review:
        return

    from openai import OpenAI
    import src.config as cfg

    client = OpenAI(api_key=cfg.LLM_API_KEY, base_url=cfg.LLM_BASE_URL)

    table_summaries = []
    for idx in tables_needing_review:
        t = report.tables[idx]
        pts_sample = t.points[:2]
        pts_str = "; ".join(
            f"[{p.point_id}: 初始={p.initial_value}, 本次={p.current_value}, "
            f"累计变化={p.cumulative_change}]"
            for p in pts_sample
        )
        table_summaries.append(
            f"表{idx}: {t.monitoring_item} (类别={t.category.value}), "
            f"当前单位={t.verification_config.unit}, 测点样例: {pts_str}"
        )

    prompt = (
        "以下监测数据表的初始值与累计变化关系异常，请判断每张表：\n"
        "1. 数据单位是什么？(mm/m/kN)\n"
        "2. 初始值是否可用于计算累计变化？（有些报告的初始值不是项目首次测量值）\n"
        "3. 累计变化计算不符时应报error还是warning？\n\n"
        + "\n".join(table_summaries)
        + "\n\n返回JSON数组，每个元素: "
        '{"table_idx":0,"unit":"mm","initial_reliable":true,"severity":"error"}'
    )

    try:
        resp = client.chat.completions.create(
            model=cfg.LLM_MODEL,
            messages=[
                {"role": "system", "content": "你是建筑变形监测数据分析专家。返回纯JSON，不要添加其他文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2000,
            timeout=60,
        )
        import json, re
        raw = resp.choices[0].message.content or ""
        raw = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', raw, flags=re.DOTALL).strip()
        m = re.search(r'\[.*\]', raw, re.DOTALL)
        if m:
            results = json.loads(m.group())
            for item in results:
                idx = item.get("table_idx")
                if idx is not None and 0 <= idx < len(report.tables):
                    cfg = report.tables[idx].verification_config
                    if item.get("unit"):
                        cfg.unit = item["unit"]
                        cfg.unit_conversion = 1000.0 if item["unit"] == "m" else 1.0
                    if "initial_reliable" in item:
                        cfg.initial_value_reliable = item["initial_reliable"]
                    if item.get("severity"):
                        cfg.severity_for_cumulative = item["severity"]
            logger.info("LLM 增强了 %d 张表的验证配置", len(results))
    except Exception as e:
        logger.warning("LLM 配置增强失败（不影响主流程）: %s", e)
