"""
LLM 表格理解模块
将 PDF 提取的非结构化文本发送给 LLM，输出标准化 JSON。

Architecture:
  1. Split text into manageable chunks (multi-strategy)
  2. Call A: Extract metadata (project info, thresholds, summary) from first chunk
  3. Call B: Extract tables from each chunk
  4. Merge and build MonitoringReport
"""

from __future__ import annotations
import json, logging, re
from typing import Any
from openai import OpenAI
import src.config as cfg
from src.models.data_models import (
    DeepDisplacementPoint, MeasurementPoint, MonitoringCategory,
    MonitoringReport, MonitoringTable, ReportSummaryItem,
    StatisticsSummary, ThresholdConfig,
)

logger = logging.getLogger(__name__)
client = OpenAI(api_key=cfg.LLM_API_KEY, base_url=cfg.LLM_BASE_URL)

SYSTEM_PROMPT = """\
你是建筑变形监测报告数据提取专家。从监测报告文本中精确提取所有监测数据表格，转为标准化JSON。

## 核心规则
1. 必须提取报告中的每一张监测数据表格，不能遗漏。数值必须原样提取。
2. 通过语义理解识别监测项类型（不同公司表述差异大，不要硬套名称）：
   识别后填入category字段，可选值: "水平位移","竖向位移","沉降","水位","锚索拉力","支撑轴力","深层水平位移","测斜","裂缝","其他"

## 关于初始值
- 有些表没有"初始值"列，只有"本次变化量"和"累计变化量"，此时 initial_value 设 null
- 累计变化量是从项目首测以来的总变化，不一定能通过表中两列算出

## 关于深层位移 / 测斜表（重要）
- 深层位移表常见列为“上次累计 / 本次累计 / 本期变化”，此时:
  - previous_cumulative = 上次累计
  - current_cumulative = 本次累计
  - current_change = 本期变化
  - change_rate = null
- 只有当表头明确出现“变化速率 / mm/d”等速率列时，才填写 change_rate
- 如果统计块写的是“最大变化位移”，填入 statistics.max_change_id / max_change_value
- 不要把“本期变化”误填到 change_rate，也不要把“最大变化位移”误填到 max_rate_value

## 关于正负号(极其重要)
- 正负号代表**方向**不代表大小！
- 统计"正方向最大"=正值中最大; "负方向最大"=负值中绝对值最大(值最小)
- "最大变化速率"=所有速率中绝对值最大的，保留原始正负号

## 关于 interval_days
- 如果报告明确写了日期范围(如"2024-03-17至2024-03-26")，计算间隔天数填入interval_days
- 如果曲线图有日期刻度，用最近两个日期算间隔; 否则设null

## 关于 table_unit（新增重要字段）
- 观察每张表的数据列头单位：如"(m)"则为"m"，"(mm)"则为"mm"，"(kN)"则为"kN"
- 如果初始值/本次值的单位是m（高程数据），table_unit填"m"
- 如果数据单位是mm（常见变形），table_unit填"mm"
- 如果是力学数据，table_unit填"kN"
- 如果无法判断，填"mm"

## 输出JSON结构
```json
{
  "project_name":"","monitoring_company":"","report_number":"",
  "monitoring_period":"","monitoring_date":"","interval_days":null,
  "thresholds":[{"item_name":"","warning_value":null,"control_value":null,"rate_limit":null}],
  "summary_items":[{"monitoring_item":"","negative_max":"","negative_max_id":"","positive_max":"","positive_max_id":"","max_rate":"","max_rate_id":"","safety_status":""}],
  "tables":[{
    "monitoring_item":"","category":"水平位移","monitor_date":"","monitor_count":"","point_count":0,
    "equipment_type":"","equipment_model":"","borehole_id":"","borehole_depth":null,
    "table_unit":"mm",
    "initial_value_reliable":true,
    "points":[{"point_id":"","initial_value":null,"previous_value":null,"current_value":null,"current_change":null,"cumulative_change":null,"change_rate":null,"safety_status":""}],
    "deep_points":[{"depth":0,"previous_cumulative":null,"current_cumulative":null,"current_change":null,"change_rate":null}],
    "statistics":{"positive_max_id":"","positive_max_value":null,"negative_max_id":"","negative_max_value":null,"max_rate_id":"","max_rate_value":null,"max_change_id":"","max_change_value":null,"max_force_id":"","max_force_value":null,"min_force_id":"","min_force_value":null}
  }],
  "conclusion":""
}
```
深层位移表points为空，数据放deep_points。必须返回合法JSON，无注释无额外文字。
"""


def _extract_json_from_response(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    text = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', text, flags=re.DOTALL).strip()
    if not text.startswith("{"):
        idx = text.find("{")
        if idx >= 0:
            text = text[idx:]
    if not text.endswith("}"):
        idx = text.rfind("}")
        if idx >= 0:
            text = text[:idx + 1]
    return json.loads(text)


def _sf(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        v = re.sub(r'[a-zA-Z·/\s]', '', v.replace("mm", "").replace("kN", "").replace("KN", ""))
        if v in ("", "--", "-", "——", "N/A", "/"):
            return None
        try:
            return float(v)
        except ValueError:
            return None
    return None


def _sid(v) -> str:
    return str(v) if v and str(v).lower() not in ("none", "null") else ""


def _cat(s: str) -> MonitoringCategory:
    mapping = {
        "水平位移": MonitoringCategory.HORIZONTAL_DISP,
        "竖向位移": MonitoringCategory.VERTICAL_DISP,
        "沉降": MonitoringCategory.SETTLEMENT,
        "水位": MonitoringCategory.WATER_LEVEL,
        "锚索拉力": MonitoringCategory.ANCHOR_FORCE,
        "支撑轴力": MonitoringCategory.STRUT_FORCE,
        "深层水平位移": MonitoringCategory.DEEP_HORIZONTAL,
        "测斜": MonitoringCategory.PILE_INCLINE,
        "裂缝": MonitoringCategory.CRACK,
    }
    for k, v in mapping.items():
        if k in s:
            return v
    return MonitoringCategory.OTHER


def _build_report(data: dict) -> MonitoringReport:
    r = MonitoringReport(
        project_name=data.get("project_name", ""),
        monitoring_company=data.get("monitoring_company", ""),
        report_number=data.get("report_number", ""),
        monitoring_period=data.get("monitoring_period", ""),
        monitoring_date=data.get("monitoring_date", ""),
        conclusion=data.get("conclusion", ""),
    )
    for th in data.get("thresholds", []):
        r.thresholds.append(ThresholdConfig(
            item_name=th.get("item_name", ""),
            warning_value=_sf(th.get("warning_value")),
            control_value=_sf(th.get("control_value")),
            rate_limit=_sf(th.get("rate_limit")),
        ))
    for si in data.get("summary_items", []):
        r.summary_items.append(ReportSummaryItem(
            monitoring_item=si.get("monitoring_item", ""),
            negative_max=str(si.get("negative_max", "")),
            negative_max_id=si.get("negative_max_id", ""),
            positive_max=str(si.get("positive_max", "")),
            positive_max_id=si.get("positive_max_id", ""),
            max_rate=str(si.get("max_rate", "")),
            max_rate_id=si.get("max_rate_id", ""),
            safety_status=si.get("safety_status", ""),
        ))
    global_interval = _sf(data.get("interval_days"))

    for tb in data.get("tables", []):
        t = MonitoringTable(
            monitoring_item=tb.get("monitoring_item", ""),
            category=_cat(tb.get("category", "")),
            monitor_date=tb.get("monitor_date", ""),
            monitor_count=tb.get("monitor_count", ""),
            point_count=tb.get("point_count", 0),
            equipment_type=tb.get("equipment_type", ""),
            equipment_model=tb.get("equipment_model", ""),
            borehole_id=tb.get("borehole_id", ""),
            borehole_depth=_sf(tb.get("borehole_depth")),
        )
        for pt in tb.get("points", []):
            t.points.append(MeasurementPoint(
                point_id=str(pt.get("point_id", "")),
                initial_value=_sf(pt.get("initial_value")),
                previous_value=_sf(pt.get("previous_value")),
                current_value=_sf(pt.get("current_value")),
                current_change=_sf(pt.get("current_change")),
                cumulative_change=_sf(pt.get("cumulative_change")),
                change_rate=_sf(pt.get("change_rate")),
                safety_status=str(pt.get("safety_status", "")),
            ))
        for dp in tb.get("deep_points", []):
            t.deep_points.append(DeepDisplacementPoint(
                depth=float(dp.get("depth", 0)),
                previous_cumulative=_sf(dp.get("previous_cumulative")),
                current_cumulative=_sf(dp.get("current_cumulative")),
                current_change=_sf(dp.get("current_change")),
                change_rate=_sf(dp.get("change_rate")),
            ))
        s = tb.get("statistics", {})
        t.statistics = StatisticsSummary(
            positive_max_id=_sid(s.get("positive_max_id")),
            positive_max_value=_sf(s.get("positive_max_value")),
            negative_max_id=_sid(s.get("negative_max_id")),
            negative_max_value=_sf(s.get("negative_max_value")),
            max_rate_id=_sid(s.get("max_rate_id")),
            max_rate_value=_sf(s.get("max_rate_value")),
            max_change_id=_sid(s.get("max_change_id")),
            max_change_value=_sf(s.get("max_change_value")),
            max_force_id=_sid(s.get("max_force_id")),
            max_force_value=_sf(s.get("max_force_value")),
            min_force_id=_sid(s.get("min_force_id")),
            min_force_value=_sf(s.get("min_force_value")),
        )

        table_unit = tb.get("table_unit", "mm")
        initial_reliable = tb.get("initial_value_reliable", True)
        from src.tools.table_analyzer import build_verification_config
        t.verification_config = build_verification_config(
            t, table_unit, initial_reliable, global_interval
        )
        r.tables.append(t)
    return r


def _split_chunks(text: str, max_chars: int = 28000) -> list[str]:
    """
    Multi-strategy text splitting:
    1. Try page markers (--- 第 N 页)
    2. Try table boundaries (【xxx】监测数据)
    3. Fallback: character-count split with overlap
    """
    pages = re.split(r"(?=--- 第 \d+ 页)", text)
    pages = [p for p in pages if p.strip()]

    if len(pages) > 1:
        chunks, cur = [], ""
        for p in pages:
            if len(cur) + len(p) > max_chars and cur:
                chunks.append(cur)
                cur = p
            else:
                cur += p
        if cur:
            chunks.append(cur)
        if chunks:
            return chunks

    table_sections = re.split(r"(?=【[^】]+】.*?(?:监测|成果|数据))", text)
    table_sections = [s for s in table_sections if s.strip()]
    if len(table_sections) > 1:
        chunks, cur = [], ""
        for s in table_sections:
            if len(cur) + len(s) > max_chars and cur:
                chunks.append(cur)
                cur = s
            else:
                cur += s
        if cur:
            chunks.append(cur)
        if chunks:
            return chunks

    if len(text) <= max_chars:
        return [text]

    overlap = 500
    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        if end < len(text):
            break_point = text.rfind("\n", start + max_chars - 2000, end)
            if break_point > start:
                end = break_point
        chunks.append(text[start:end])
        start = end - overlap
    return chunks if chunks else [text]


def parse_report_with_llm(raw_text: str) -> MonitoringReport:
    chunks = _split_chunks(raw_text)
    logger.info("文本分为 %d 个片段发送给 LLM", len(chunks))
    all_tables, first = [], {}
    for i, chunk in enumerate(chunks):
        logger.info("正在处理第 %d/%d 段 (%d字符)...", i + 1, len(chunks), len(chunk))
        msg = (
            f"以下是监测报告第{i + 1}/{len(chunks)}段，请提取所有监测数据表格。"
            f"无表格则tables返回空列表。\n\n```\n{chunk}\n```"
        )
        try:
            resp = client.chat.completions.create(
                model=cfg.LLM_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": msg},
                ],
                temperature=0.1,
                max_tokens=32000,
                timeout=300,
            )
        except Exception as e:
            logger.error("第%d段LLM调用失败: %s", i + 1, e)
            continue
        raw = resp.choices[0].message.content or ""
        try:
            parsed = _extract_json_from_response(raw)
        except json.JSONDecodeError as e:
            logger.error("第%d段JSON解析失败: %s", i + 1, e)
            continue
        if i == 0:
            first = parsed
        else:
            for key in ("thresholds", "summary_items"):
                existing = first.get(key, [])
                new_items = parsed.get(key, [])
                if new_items and not existing:
                    first[key] = new_items
        all_tables.extend(parsed.get("tables", []))
    first["tables"] = all_tables
    report = _build_report(first)
    report.raw_text = raw_text
    logger.info(
        "解析完成: %s, %d张表, %d阈值, %d汇总",
        report.project_name, len(report.tables),
        len(report.thresholds), len(report.summary_items),
    )
    return report


def verify_report_with_llm(report_md: str, raw_text: str) -> str:
    text_preview = raw_text[:6000]
    msg = (
        "以下是监测报告自动检查结果和原始文本。请审核是否有遗漏或误判。"
        "注意正负号代表方向不代表大小。\n\n"
        f"## 检查报告\n{report_md}\n\n"
        f"## 原始文本(前6000字)\n```\n{text_preview}\n```\n\n请给出审核意见。"
    )
    try:
        resp = client.chat.completions.create(
            model=cfg.LLM_MODEL,
            messages=[
                {"role": "system", "content": "你是建筑工程监测领域资深专家。正负号代表方向不代表大小。"},
                {"role": "user", "content": msg},
            ],
            temperature=0.3,
            max_tokens=4000,
            timeout=120,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.error("AI审核调用失败: %s", e)
        return f"AI审核调用失败: {e}"
