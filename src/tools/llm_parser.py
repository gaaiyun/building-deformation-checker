"""
LLM 表格理解模块
将 PDF 提取的非结构化文本发送给 LLM，输出标准化 JSON。
"""

from __future__ import annotations
import json, logging, re
from typing import Any
from openai import OpenAI
from src.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from src.models.data_models import (
    DeepDisplacementPoint, MeasurementPoint, MonitoringCategory,
    MonitoringReport, MonitoringTable, ReportSummaryItem,
    StatisticsSummary, ThresholdConfig,
)

logger = logging.getLogger(__name__)
client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

SYSTEM_PROMPT = """\
你是建筑变形监测报告数据提取专家。从监测报告文本中精确提取所有监测数据表格，转为标准化JSON。

## 核心规则
1. 必须提取报告中的每一张监测数据表格，不能遗漏。数值必须原样提取。
2. 识别监测项类型（不同公司表述不同，需语义理解）:
   "支护结构顶部水平位移"/"基坑顶位移监测"/"坡顶水平位移" → "水平位移"
   "支护结构顶部竖向位移"/"基坑顶沉降监测" → "竖向位移"
   "周边地面沉降"/"道路沉降"/"道路沉降监测" → "沉降"
   "管线沉降" → "沉降"; "地下水位"/"水位监测" → "水位"
   "锚索拉力"/"支撑轴力" → "锚索拉力"或"支撑轴力"
   "深层水平位移"/"支护桩测斜"/"测斜" → "深层水平位移"
   "支护桩内力" → "支撑轴力"; "立柱位移"/"立柱沉降" → "竖向位移"

## 关于初始值
- 有些表没有"初始值"列，只有"本次变化量"和"累计变化量"，此时 initial_value 设 null
- 累计变化量是从项目首测以来的总变化，不一定能通过表中两列算出

## 关于正负号(极其重要)
- 正负号代表**方向**不代表大小！ "+"向基坑内,"-"向基坑外; 沉降"-"下沉,"+"上升
- 统计"正方向最大"=正值中最大; "负方向最大"=负值中绝对值最大(值最小)
- "最大变化速率"=所有速率中绝对值最大的，保留原始正负号

## 关于 interval_days
- 如果报告明确写了日期范围(如"2024-03-17至2024-03-26")，计算间隔天数填入interval_days
- 如果有曲线图可见日期刻度，用最近两个日期算间隔; 否则设null

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
    "points":[{"point_id":"","initial_value":null,"previous_value":null,"current_value":null,"current_change":null,"cumulative_change":null,"change_rate":null,"safety_status":""}],
    "deep_points":[{"depth":0,"previous_cumulative":null,"current_cumulative":null,"change_rate":null}],
    "statistics":{"positive_max_id":"","positive_max_value":null,"negative_max_id":"","negative_max_value":null,"max_rate_id":"","max_rate_value":null,"max_force_id":"","max_force_value":null,"min_force_id":"","min_force_value":null}
  }],
  "conclusion":""
}
```
深层位移表points为空，数据放deep_points。必须返回合法JSON，无注释无额外文字。
"""


def _extract_json_from_response(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if m: text = m.group(1).strip()
    text = re.sub(r'<think(?:ing)?>.*?</think(?:ing)?>', '', text, flags=re.DOTALL).strip()
    if not text.startswith("{"): text = text[text.find("{"):]
    if not text.endswith("}"): text = text[:text.rfind("}")+1]
    return json.loads(text)

def _sf(v: Any) -> float | None:
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    if isinstance(v, str):
        v = re.sub(r'[a-zA-Z·/\s]', '', v.replace("mm","").replace("kN","").replace("KN",""))
        if v in ("","--","-","——","N/A","/"): return None
        try: return float(v)
        except ValueError: return None
    return None

def _cat(s: str) -> MonitoringCategory:
    for k,v in {"水平位移":MonitoringCategory.HORIZONTAL_DISP,"竖向位移":MonitoringCategory.VERTICAL_DISP,
        "沉降":MonitoringCategory.SETTLEMENT,"水位":MonitoringCategory.WATER_LEVEL,
        "锚索拉力":MonitoringCategory.ANCHOR_FORCE,"支撑轴力":MonitoringCategory.STRUT_FORCE,
        "深层水平位移":MonitoringCategory.DEEP_HORIZONTAL,"测斜":MonitoringCategory.PILE_INCLINE,"裂缝":MonitoringCategory.CRACK}.items():
        if k in s: return v
    return MonitoringCategory.OTHER

def _build_report(data: dict) -> MonitoringReport:
    r = MonitoringReport(project_name=data.get("project_name",""),monitoring_company=data.get("monitoring_company",""),
        report_number=data.get("report_number",""),monitoring_period=data.get("monitoring_period",""),
        monitoring_date=data.get("monitoring_date",""),conclusion=data.get("conclusion",""))
    for th in data.get("thresholds",[]):
        r.thresholds.append(ThresholdConfig(item_name=th.get("item_name",""),warning_value=_sf(th.get("warning_value")),
            control_value=_sf(th.get("control_value")),rate_limit=_sf(th.get("rate_limit"))))
    for si in data.get("summary_items",[]):
        r.summary_items.append(ReportSummaryItem(monitoring_item=si.get("monitoring_item",""),
            negative_max=str(si.get("negative_max","")),negative_max_id=si.get("negative_max_id",""),
            positive_max=str(si.get("positive_max","")),positive_max_id=si.get("positive_max_id",""),
            max_rate=str(si.get("max_rate","")),max_rate_id=si.get("max_rate_id",""),safety_status=si.get("safety_status","")))
    for tb in data.get("tables",[]):
        t = MonitoringTable(monitoring_item=tb.get("monitoring_item",""),category=_cat(tb.get("category","")),
            monitor_date=tb.get("monitor_date",""),monitor_count=tb.get("monitor_count",""),
            point_count=tb.get("point_count",0),equipment_type=tb.get("equipment_type",""),
            equipment_model=tb.get("equipment_model",""),borehole_id=tb.get("borehole_id",""),
            borehole_depth=_sf(tb.get("borehole_depth")))
        for pt in tb.get("points",[]):
            t.points.append(MeasurementPoint(point_id=str(pt.get("point_id","")),initial_value=_sf(pt.get("initial_value")),
                previous_value=_sf(pt.get("previous_value")),current_value=_sf(pt.get("current_value")),
                current_change=_sf(pt.get("current_change")),cumulative_change=_sf(pt.get("cumulative_change")),
                change_rate=_sf(pt.get("change_rate")),safety_status=str(pt.get("safety_status",""))))
        for dp in tb.get("deep_points",[]):
            t.deep_points.append(DeepDisplacementPoint(depth=float(dp.get("depth",0)),
                previous_cumulative=_sf(dp.get("previous_cumulative")),current_cumulative=_sf(dp.get("current_cumulative")),
                change_rate=_sf(dp.get("change_rate"))))
        s = tb.get("statistics",{})
        def _sid(v): return str(v) if v and str(v).lower() not in ("none","null") else ""
        t.statistics = StatisticsSummary(positive_max_id=_sid(s.get("positive_max_id")),positive_max_value=_sf(s.get("positive_max_value")),
            negative_max_id=_sid(s.get("negative_max_id")),negative_max_value=_sf(s.get("negative_max_value")),
            max_rate_id=_sid(s.get("max_rate_id")),max_rate_value=_sf(s.get("max_rate_value")),
            max_force_id=_sid(s.get("max_force_id")),max_force_value=_sf(s.get("max_force_value")),
            min_force_id=_sid(s.get("min_force_id")),min_force_value=_sf(s.get("min_force_value")))
        r.tables.append(t)
    return r

def _split_chunks(text: str, max_chars: int = 30000) -> list[str]:
    pages = re.split(r"(?=--- 第 \d+ 页)", text)
    pages = [p for p in pages if p.strip()]
    chunks, cur = [], ""
    for p in pages:
        if len(cur)+len(p)>max_chars and cur: chunks.append(cur); cur=p
        else: cur+=p
    if cur: chunks.append(cur)
    return chunks if chunks else [text]

def parse_report_with_llm(raw_text: str) -> MonitoringReport:
    chunks = _split_chunks(raw_text)
    logger.info("文本分为 %d 个片段发送给 LLM", len(chunks))
    all_tables, first = [], {}
    for i, chunk in enumerate(chunks):
        logger.info("正在处理第 %d/%d 段 (%d字符)...", i+1, len(chunks), len(chunk))
        msg = f"以下是监测报告第{i+1}/{len(chunks)}段，请提取所有监测数据表格。无表格则tables返回空列表。\n\n```\n{chunk}\n```"
        resp = client.chat.completions.create(model=LLM_MODEL,
            messages=[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":msg}],
            temperature=0.1, max_tokens=32000)
        raw = resp.choices[0].message.content or ""
        try:
            parsed = _extract_json_from_response(raw)
        except json.JSONDecodeError as e:
            logger.error("第%d段JSON解析失败: %s", i+1, e); continue
        if i==0: first=parsed
        all_tables.extend(parsed.get("tables",[]))
    first["tables"]=all_tables
    report = _build_report(first)
    report.raw_text = raw_text
    logger.info("解析完成: %s, %d张表, %d阈值, %d汇总", report.project_name, len(report.tables), len(report.thresholds), len(report.summary_items))
    return report

def verify_report_with_llm(report_md: str, raw_text: str) -> str:
    msg = f"以下是监测报告自动检查结果和原始文本。请审核是否有遗漏或误判。注意正负号代表方向不代表大小。\n\n## 检查报告\n{report_md}\n\n## 原始文本(前4000字)\n```\n{raw_text[:4000]}\n```\n\n请给出审核意见。"
    resp = client.chat.completions.create(model=LLM_MODEL,
        messages=[{"role":"system","content":"你是建筑工程监测领域资深专家。正负号代表方向不代表大小。"},
                  {"role":"user","content":msg}], temperature=0.3, max_tokens=4000)
    return resp.choices[0].message.content or ""
