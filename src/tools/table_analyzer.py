"""
Per-table dynamic verification config builder.

Instead of hardcoding tolerance/severity by MonitoringCategory,
this module determines verification parameters based on each table's
actual data characteristics (unit, value ranges, column structure).

For complex cases, an optional LLM call enriches the config.
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
    Build verification config for a single table based on its data characteristics.
    Replaces all hardcoded category branches in checkers.
    """
    cfg = TableVerificationConfig(
        unit=table_unit,
        cumulative_tolerance=FLOAT_TOLERANCE,
        rate_tolerance=RATE_TOLERANCE,
        interval_days=global_interval,
        initial_value_reliable=initial_reliable,
        severity_for_cumulative="error",
    )

    if table_unit == "m":
        cfg.unit_conversion = 1000.0
        cfg.cumulative_tolerance = max(FLOAT_TOLERANCE * 5, 1.0)
        cfg.severity_for_cumulative = "warning"
        cfg.initial_value_reliable = False
    elif table_unit == "kN":
        cfg.unit_conversion = 1.0
        cfg.cumulative_tolerance = FLOAT_TOLERANCE
        cfg.severity_for_cumulative = "error"

    cat = table.category
    if cat == MonitoringCategory.WATER_LEVEL:
        cfg.cumulative_tolerance = max(FLOAT_TOLERANCE * 50, 10.0)
        cfg.severity_for_cumulative = "warning"
        cfg.initial_value_reliable = False
    elif cat in (MonitoringCategory.VERTICAL_DISP, MonitoringCategory.SETTLEMENT):
        if cfg.unit != "m":
            _detect_elevation_from_data(table, cfg)
    elif cat in (MonitoringCategory.ANCHOR_FORCE, MonitoringCategory.STRUT_FORCE):
        cfg.unit = "kN"
        cfg.unit_conversion = 1.0
        cfg.severity_for_cumulative = "error"

    return cfg


def _detect_elevation_from_data(table: MonitoringTable, cfg: TableVerificationConfig):
    """
    Heuristic: if initial/current values look like elevation (small absolute numbers
    in meters), set m->mm conversion even if LLM didn't flag it.
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


def enrich_configs_with_llm(report: MonitoringReport) -> None:
    """
    Optional LLM enrichment pass: for tables where heuristic config may be wrong,
    ask LLM for advice on tolerance/unit/reliability.

    Called after initial parsing. Uses a single batch LLM call for efficiency.
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
                if ratio < 0.01 or ratio > 100:
                    tables_needing_review.append(i)

    if not tables_needing_review:
        return

    from openai import OpenAI
    from src.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    table_summaries = []
    for idx in tables_needing_review:
        t = report.tables[idx]
        pts_sample = t.points[:2]
        pts_str = "; ".join(
            f"[{p.point_id}: init={p.initial_value}, cur={p.current_value}, "
            f"cum={p.cumulative_change}]"
            for p in pts_sample
        )
        table_summaries.append(
            f"表{idx}: {t.monitoring_item} (category={t.category.value}), "
            f"unit={t.verification_config.unit}, 测点样例: {pts_str}"
        )

    prompt = (
        "以下监测数据表的初始值与累计变化关系异常，请判断每张表：\n"
        "1. 数据单位是什么？(mm/m/kN)\n"
        "2. 初始值是否可用于计算累计变化？\n"
        "3. 累计变化计算不符时应报error还是warning？\n\n"
        + "\n".join(table_summaries)
        + "\n\n返回JSON数组，每个元素: "
        '{"table_idx":0,"unit":"mm","initial_reliable":true,"severity":"error"}'
    )

    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "你是建筑变形监测数据分析专家。"},
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
            logger.info("LLM enriched %d table configs", len(results))
    except Exception as e:
        logger.warning("LLM config enrichment failed (non-fatal): %s", e)
