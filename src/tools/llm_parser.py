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
import json, logging, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional
import src.config as cfg
from src.models.data_models import (
    DeepDisplacementPoint, MeasurementPoint, MonitoringCategory,
    MonitoringReport, MonitoringTable, ReportSummaryItem,
    StatisticsSummary, ThresholdConfig,
)
from src.utils.llm_client import call_chat_completion

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
你是建筑变形监测报告数据提取专家。从监测报告文本中精确提取所有监测数据表格，转为标准化JSON。

## 核心规则
1. 必须提取报告中的每一张监测数据表格，不能遗漏。数值必须原样提取。
2. 通过语义理解识别监测项类型（不同公司表述差异大，不要硬套名称）：
   识别后填入category字段，可选值: "水平位移","竖向位移","沉降","水位","锚索拉力","支撑轴力","深层水平位移","测斜","裂缝","其他"
3. 输出前必须在内部逐页逐表清点：每个表题、每条线路/方向、每个监测点编号组、每个监测日期都要形成独立 table。
4. 连续出现的同类表格不能抽样、省略或只输出最后一期。例如 3号点X方向、4号点X方向、5号点X方向是三组不同表；2022-05-16 至 2022-05-22 是 7 张独立表。
5. 只提取真实数据表。不要把“成果曲线图”的坐标刻度、图例、曲线说明当成监测数据表；除非曲线图附近同时有完整测点编号和数值行。
6. point_count 是完整性契约：普通表 points 数量必须等于该期表内全部测点数；深层位移表 deep_points 数量必须等于全部深度行数。禁止 point_count 写 23 却只输出 W3 等少数测点。
7. 横向多期表展开后，每一期都必须重复输出该表的全部测点。即使输出很长，也不能只保留首行、统计涉及的测点或代表性测点。
8. “表7-1 监测结果表”、监测结果汇总表、简报汇总只用于 thresholds 和 summary_items；它们不是逐测点原始数据表，不能放入 tables。
9. 为节省输出长度，points/deep_points 中值为 null 或空字符串的键可以省略，但任何测点行、深度行、日期表都不能省略。

## 关于初始值
- 有些表没有"初始值"列，只有"本次变化量"和"累计变化量"，此时 initial_value 设 null
- 累计变化量是从项目首测以来的总变化，不一定能通过表中两列算出
- 如果没有写明初始值，监测时间段第一天视为初始基准日；第一天的累计变化量应等于本次变化量

## 关于横向多期布局（重要）
- 某些模板把多次监测（如"第220次/第221次/第222次"或"第172次..第177次"）**横向**排列
  在同一表里，每期占 3 列（本次变化 / 累计变化 / 变化速率）
- **必须**把每期作为**独立的 table** 输出（不要合并成"宽表"）
  - 每张 table 的 monitor_date 设为该期的日期（从 row 6 或日期行抽取）
  - 每张 table 的 monitor_count 设为该期次（如"第220次"）
  - 每张 table 的 points 只包含该期的列：current_change / cumulative_change / change_rate
  - 如果 sheet 含 6 期数据 → 输出 6 张 table，monitoring_item 相同但 monitor_date 不同
- 横向布局通常**没有显式 initial_value 列**：
  - 第一列可能是测点编号，第二列直接就是第一期的本次变化（不是初始值！）
  - 此时 initial_value 设 null，由跨期累计连续性来核对
- **不要把测点编号列误填到 initial_value**
- 有些表在所有“本次/累计/变化速率”三列组之后，还有最右侧独立的“本期变化量”汇总列，后面再接“变化速率报警值/报警值/控制值”。
  - 这个最右侧“本期变化量”表示整个报告时段的变化，不属于最后一个监测日期，当前 JSON 无对应字段，忽略即可。
  - change_rate 必须取同一期三列组中的第三列“变化速率(mm/d)”，最右侧独立的“本期变化量”不能填入 change_rate。
  - 例如最后一期行尾为 `-0.13 -5.78 -0.13 0.11 3 40 50`，应提取 current_change=-0.13、cumulative_change=-5.78、change_rate=-0.13；0.11 是报告期汇总变化，不能错填。

## 关于锚索拉力 / 支撑轴力
- 标准总力表若明确为“初始内力 / 本次内力 / 累计变化”，可设置 initial_value_reliable=true，并按本次内力减初始内力核对。
- 若横向表每期三列明确为“本次(kN) / 测值(kN) / 变化速率(kN/d)”：
  - current_change=本次，current_value=测值，cumulative_change=测值，change_rate=变化速率；
  - initial_value 保留表中“初始测值”，但 initial_value_reliable=false，因为该初始测值与后续变化测值不是可直接相减的同一累计口径；
  - 这类表通过跨期“前一期测值 + 本次变化 = 本期测值”核对，禁止强行用本期测值减初始测值；
  - 最右侧独立“本期变化量”仍是报告期汇总，不得填入 cumulative_change 或 change_rate。

## 关于深层位移 / 测斜表（重要）
- 深层位移表常见列为“上次累计 / 本次累计 / 本期变化”，此时:
  - previous_cumulative = 上次累计
  - current_cumulative = 本次累计
  - current_change = 本期变化
  - change_rate = null
- 只有当表头明确出现“变化速率 / mm/d”等速率列时，才填写 change_rate
- 如果统计块写的是“最大变化位移”，填入 statistics.max_change_id / max_change_value
- 不要把“本期变化”误填到 change_rate，也不要把“最大变化位移”误填到 max_rate_value
- **极其重要**：如果深层位移表没有明确的“数据统计”区域，statistics所有字段设null/空，不要把最后一行数据当成统计
- 深层位移表的statistics中，positive_max_id/negative_max_id应该是深度值（如“3.5”），不要填成整数行号
- 如果深层位移表只有3列（上次累计/本次累计/变化速率），current_change设null

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
    """从 LLM 响应中提取 JSON。处理常见的 LLM 输出污染：

    - ```json ... ``` 代码块包裹
    - <thinking> ... </thinking> 思考块
    - JSON 前后的解释性文字
    - 末尾不完整截断
    - 字符串里未转义的换行（LLM 在长文本输出时常见）
    - 数字字段后多余的小数点（如 "0.0." 应为 "0.0"）
    - 数字 + 单位（如 12.5mm 应为 12.5）

    解析失败时尝试 2 轮 fallback 修复后再失败。
    """
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

    # 三段式解析：strict json → repair + json → json5（容错解析器）
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # 第一轮：启发式修复后再 strict json
        repaired = _repair_llm_json(text, exc)
        if repaired is not None:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        # 第二轮：json5 容错解析（处理缺逗号、单引号、未引号 key 等 LLM 常见输出）
        try:
            import json5  # 轻量依赖，能解析比标准 JSON 更宽松的格式
            return json5.loads(text)
        except Exception:
            try:
                # 修复后的文本再 json5
                if repaired:
                    return json5.loads(repaired)
            except Exception:
                pass
        # 全部失败，重新抛原始错误
        raise


def _repair_llm_json(text: str, exc: json.JSONDecodeError) -> str | None:
    """启发式修复 LLM 偶尔生成的不合法 JSON。

    覆盖场景（按频度递减）：
    1. 数字字段后多余的小数点或单位（"0.0." → "0.0"；"12.5mm" → "12.5"）
    2. 数组/对象末尾的尾随逗号（",}" → "}"；",]" → "]"）
    3. 字符串值里出现未转义的换行
    4. 半截字符串（最后一个 " 之后无配对）→ 截断到 exc.pos 之前
    """
    repaired = text

    # 1. 去掉数字后的多余字符（如 "0.5mm,"、"3.4." 等）
    # 顺序很重要：先剥单位，再清尾部多余点
    # 1a. 数字 + 单位（仅当后面是分隔符且前面不是字母时；保护字符串值如 "5m"）
    #     注：当前 lookahead 已含 `,\s\]}`，闭合引号 `"` 不在内，因此字符串内不会触发
    repaired = re.sub(r'(?<![A-Za-z])(-?\d+\.?\d*)(mm/?d?|kN|KN|kn|cm|m)\b(?=[,\s\]}])', r'\1', repaired)
    # 1b. 双小数点（'12.5.' → '12.5'）
    repaired = re.sub(r'(-?\d+\.\d+)\.(?=[,\s\]}])', r'\1', repaired)
    # 1c. 尾随单点（'12.' → '12'）— 必须放在 1a/1b 之后，避免剥掉合法 '12.5'
    repaired = re.sub(r'(-?\d+)\.(?=[,\s\]}])', r'\1', repaired)

    # 2. 尾随逗号
    repaired = re.sub(r',(\s*[\]}])', r'\1', repaired)

    # 2.5. 键值对之间缺逗号（MiniMax 偶发输出）
    # 模式：<value 结尾> <空白> "key": → 插入逗号
    # value 结尾 = 数字结尾 / 字符串闭合 / 数组]结尾 / 对象}结尾 / null/true/false 结尾
    repaired = re.sub(
        r'(["\d\}\]ltnse])\s*\n\s*("[A-Za-z_][\w_]*":)',
        r'\1,\n  \2',
        repaired,
    )
    # 同样模式但同一行（更激进，可能误伤）
    repaired = re.sub(
        r'("|\d|\}|\])\s{2,}("[A-Za-z_][\w_]*":)',
        r'\1, \2',
        repaired,
    )

    # 3. 字符串内的换行
    def _escape_newline_in_string(m: re.Match) -> str:
        content = m.group(0)
        return content.replace('\n', '\\n').replace('\r', '\\r')
    repaired = re.sub(r'"[^"]*"', _escape_newline_in_string, repaired)

    # 如果错误位置在文本末端附近，尝试截断闭合
    if exc.pos and exc.pos > len(repaired) * 0.5:
        # 找最后一个完整对象的结束位置（在 exc.pos 之前）
        search_text = repaired[:exc.pos + 1] if exc.pos < len(repaired) else repaired
        last_close = max(search_text.rfind('}'), search_text.rfind(']'))

        # 若已有 close bracket：截到那里，补齐剩余未闭合
        # 否则：保留全文，从原始 text 扫 stack 闭合（适配深嵌套早期截断）
        if last_close > 0:
            truncated = repaired[:last_close + 1]
        else:
            # 处理末尾未完成 token（如 '"unfinished'）
            # 找到最后一个完整 token 的位置，避免在字符串中间断开
            truncated = _trim_to_last_safe_token(repaired)

        truncated = _close_unmatched_brackets(truncated)
        if truncated != repaired:
            return truncated

    return repaired if repaired != text else None


def _close_unmatched_brackets(text: str) -> str:
    """根据字符串内未闭合的 { 和 [ 反序追加 } 和 ] 闭合。

    考虑字符串引号转义。如果未结束的字符串存在，先 close 字符串。
    """
    stack = []
    in_string = False
    escape = False
    string_open_pos = -1
    last_safe = 0  # 最近一个合法 token 边界（数字/}/]/字符串闭合后）

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            if in_string:
                in_string = False
                last_safe = i + 1
            else:
                in_string = True
                string_open_pos = i
            continue
        if in_string:
            continue
        if ch in '{[':
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
                last_safe = i + 1
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()
                last_safe = i + 1
        elif ch.isdigit() or ch in '.-eE':
            last_safe = i + 1

    # 如果终态还在字符串里，截到该字符串开始前
    if in_string and string_open_pos >= 0:
        text = text[:string_open_pos].rstrip(', \t\n')

    closers = []
    for opener in reversed(stack):
        closers.append('}' if opener == '{' else ']')
    return text + ''.join(closers)


def _trim_to_last_safe_token(text: str) -> str:
    """对未完成的尾部 token 截断（如最后一个数字写一半）。

    目前只做最保守处理：移除尾部空白。完整 token 切割已在 _close_unmatched_brackets 内处理。
    """
    return text.rstrip()


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


def _text(v) -> str:
    return "" if v is None else str(v)


def _cat(s: str) -> MonitoringCategory:
    mapping = [
        ("深层水平位移", MonitoringCategory.DEEP_HORIZONTAL),
        ("支护桩测斜", MonitoringCategory.PILE_INCLINE),
        ("锚索拉力", MonitoringCategory.ANCHOR_FORCE),
        ("支撑轴力", MonitoringCategory.STRUT_FORCE),
        ("水平位移", MonitoringCategory.HORIZONTAL_DISP),
        ("竖向位移", MonitoringCategory.VERTICAL_DISP),
        ("沉降", MonitoringCategory.SETTLEMENT),
        ("水位", MonitoringCategory.WATER_LEVEL),
        ("测斜", MonitoringCategory.PILE_INCLINE),
        ("裂缝", MonitoringCategory.CRACK),
    ]
    for k, v in mapping:
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
    for th in data.get("thresholds") or []:
        if not isinstance(th, dict):
            continue
        r.thresholds.append(ThresholdConfig(
            item_name=th.get("item_name", ""),
            warning_value=_sf(th.get("warning_value")),
            control_value=_sf(th.get("control_value")),
            rate_limit=_sf(th.get("rate_limit")),
        ))
    for si in data.get("summary_items") or []:
        if not isinstance(si, dict):
            continue
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

    for tb in data.get("tables") or []:
        if not isinstance(tb, dict):
            continue
        t = MonitoringTable(
            monitoring_item=_text(tb.get("monitoring_item")),
            category=_cat(tb.get("category", "")),
            monitor_date=_text(tb.get("monitor_date")),
            monitor_count=_text(tb.get("monitor_count")),
            point_count=tb.get("point_count", 0),
            equipment_type=_text(tb.get("equipment_type")),
            equipment_model=_text(tb.get("equipment_model")),
            borehole_id=_sid(tb.get("borehole_id")),
            borehole_depth=_sf(tb.get("borehole_depth")),
            source_chunk=int(tb.get("_source_chunk") or 0),
            source_pages=_text(tb.get("_source_pages")),
        )
        for pt in tb.get("points") or []:
            if not isinstance(pt, dict):
                continue
            t.points.append(MeasurementPoint(
                point_id=str(pt.get("point_id", "")),
                initial_value=_sf(pt.get("initial_value")),
                previous_value=_sf(pt.get("previous_value")),
                current_value=_sf(pt.get("current_value")),
                current_change=_sf(pt.get("current_change")),
                cumulative_change=_sf(pt.get("cumulative_change")),
                change_rate=_sf(pt.get("change_rate")),
                safety_status=str(pt.get("safety_status", "")),
                source_chunk=int(pt.get("_source_chunk") or 0),
                source_page=int(pt["_source_page"]) if pt.get("_source_page") is not None else None,
                source_row_text=_text(pt.get("_source_row_text")),
                source_field_map=_text(pt.get("_source_field_map")),
            ))
        for dp in tb.get("deep_points") or []:
            if not isinstance(dp, dict):
                continue
            # 鲁棒解析 depth：LLM 可能返回 "01-1" 等非数字格式，原 float() 会抛 ValueError
            # 中断整个 pipeline。改用 _sf 容错解析，缺/坏的 depth 跳过该点而非崩溃。
            depth_val = _sf(dp.get("depth"))
            if depth_val is None:
                logger.warning(
                    "LLM 返回的 deep_point depth 无法解析为数字，已跳过: depth=%r",
                    dp.get("depth"),
                )
                continue
            t.deep_points.append(DeepDisplacementPoint(
                depth=depth_val,
                previous_cumulative=_sf(dp.get("previous_cumulative")),
                current_cumulative=_sf(dp.get("current_cumulative")),
                current_change=_sf(dp.get("current_change")),
                change_rate=_sf(dp.get("change_rate")),
                source_chunk=int(dp.get("_source_chunk") or 0),
                source_page=int(dp["_source_page"]) if dp.get("_source_page") is not None else None,
                source_row_text=_text(dp.get("_source_row_text")),
                source_field_map=_text(dp.get("_source_field_map")),
            ))
        s = tb.get("statistics") or {}
        if not isinstance(s, dict):
            s = {}
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


def _split_long_text(text: str, max_chars: int, overlap: int = 500) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            break_point = text.rfind("\n", start + max_chars - min(2000, max_chars // 3), end)
            if break_point > start:
                end = break_point
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _pack_segments(segments: list[str], max_chars: int) -> list[str]:
    chunks, cur = [], ""
    for segment in segments:
        if len(segment) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_split_long_text(segment, max_chars))
            continue
        if len(cur) + len(segment) > max_chars and cur:
            chunks.append(cur)
            cur = segment
        else:
            cur += segment
    if cur:
        chunks.append(cur)
    return chunks


_STRUCTURED_TABLE_START_RE = re.compile(
    r"(?m)^(?=(?:"
    r"附表\s*\d+(?:\s*[-－—]\s*\d+){1,3}[^\r\n]{0,120}?(?:观测结果表|结果表|成果表)"
    r"|【[^】\r\n]+】[^\r\n]{0,80}?(?:监测数据(?:成果)?|数据成果|监测成果)"
    r"))"
)


def _split_structured_table_sections(text: str, max_chars: int) -> list[str]:
    """按明确表题切分；同一监测项的连续续表可合并，不同监测项严格隔离。"""
    starts = [match.start() for match in _STRUCTURED_TABLE_START_RE.finditer(text)]
    if len(starts) < 2:
        return []

    chunks: list[str] = []
    prefix = text[:starts[0]]
    if prefix.strip():
        chunks.extend(_split_long_text(prefix, max_chars))

    group_max_chars = min(
        max_chars,
        int(getattr(cfg, "LLM_STRUCTURED_GROUP_CHARS", 4500) or 4500),
    )
    current = ""
    current_family = ""
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        section = text[start:end]
        # 页码标记通常位于表题之前，因此按表题起点切分时会被错误挂到
        # 上一段末尾。先移除段尾的下一页标记，再补回当前表最近的页码。
        section = re.sub(
            r"\n?---\s*第\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?\s*---\s*$",
            "\n",
            section,
        )
        page_matches = list(_PAGE_MARKER_RE.finditer(text, 0, start))
        if page_matches:
            marker = page_matches[-1].group(0)
            if not section.lstrip().startswith(marker):
                section = f"{marker}\n{section}"
        if not section.strip():
            continue

        first_line = section.splitlines()[0].strip()
        appendix_match = re.match(
            r"^附表\s*(\d+(?:\s*[-－—]\s*\d+){1,3})",
            first_line,
        )
        bracket_match = re.match(r"^(【[^】]+】)", first_line)
        if appendix_match:
            parts = re.split(r"\s*[-－—]\s*", appendix_match.group(1))
            family = "appendix:" + "-".join(parts[:-1])
        elif bracket_match:
            family = "bracket:" + bracket_match.group(1)
        else:
            family = f"section:{index}"

        if len(section) > max_chars:
            if current:
                chunks.append(current)
                current = ""
                current_family = ""
            chunks.extend(_split_long_text(section, max_chars))
            continue

        if current and (family != current_family or len(current) + len(section) > group_max_chars):
            chunks.append(current)
            current = ""
        current += section
        current_family = family

    if current:
        chunks.append(current)
    return chunks


def _split_chunks(text: str, max_chars: int | None = None) -> list[str]:
    """
    Multi-strategy text splitting:
    1. Prefer explicit structured table headings and keep each table isolated
    2. Try page markers (--- 第 N 页)
    3. Try loose table boundaries (【xxx】监测数据)
    4. Fallback: character-count split with overlap
    """
    max_chars = max_chars or getattr(cfg, "LLM_PARSE_CHUNK_CHARS", 18000)

    structured_sections = _split_structured_table_sections(text, max_chars)
    if structured_sections:
        return structured_sections

    pages = re.split(r"(?=--- 第 \d+ 页)", text)
    pages = [p for p in pages if p.strip()]

    if len(pages) > 1:
        chunks = _pack_segments(pages, max_chars)
        if chunks:
            return chunks

    table_sections = re.split(r"(?=【[^】]+】.*?(?:监测|成果|数据))", text)
    table_sections = [s for s in table_sections if s.strip()]
    if len(table_sections) > 1:
        chunks = _pack_segments(table_sections, max_chars)
        if chunks:
            return chunks

    return _split_long_text(text, max_chars) or [text]


def _record_key(item: dict, key_fields: tuple[str, ...]) -> str:
    values = [str(item.get(field, "")).strip() for field in key_fields]
    if any(values):
        return "|".join(values)
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _merge_records(existing: list[dict], new_items: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    merged = list(existing or [])
    seen = {_record_key(item, key_fields) for item in merged if isinstance(item, dict)}
    for item in new_items or []:
        if not isinstance(item, dict):
            continue
        key = _record_key(item, key_fields)
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
    return merged


def _dump_failed_llm_response(
    chunk_index: int,
    total_chunks: int,
    raw: str,
    error: Exception,
) -> None:
    """保存解析失败的原始 LLM 输出，便于排查具体 chunk。"""
    try:
        from pathlib import Path
        debug_dir = Path("output") / "llm_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        from time import strftime
        ts = strftime("%Y%m%d_%H%M%S")
        dump_path = debug_dir / f"chunk{chunk_index + 1}_failed_{ts}.txt"
        dump_path.write_text(
            f"# LLM 调用失败的原始响应\n"
            f"# 错误: {error}\n"
            f"# Chunk index: {chunk_index + 1}/{total_chunks}\n"
            f"# 长度: {len(raw)} 字符\n\n"
            f"{raw}",
            encoding="utf-8",
        )
        logger.info("已保存失败的 LLM 响应到: %s", dump_path)
    except Exception as dump_exc:
        logger.warning("无法保存调试 dump: %s", dump_exc)


def _parse_chunk_with_llm(
    chunk_index: int,
    total_chunks: int,
    chunk: str,
) -> dict[str, Any] | None:
    """调用 LLM 解析单个文本片段，返回 JSON dict；失败返回 None。"""
    logger.info(
        "正在处理第 %d/%d 段 (%d字符)...",
        chunk_index + 1,
        total_chunks,
        len(chunk),
    )
    base_msg = (
        f"以下是监测报告第{chunk_index + 1}/{total_chunks}段，请提取所有监测数据表格。"
        "请先在内部清点本段所有表题、监测点组和日期，再输出 JSON；"
        "相似表不能只抽一张代表，缺失一张日期表也视为错误。"
        "无表格则tables返回空列表。\n\n"
        f"```\n{chunk}\n```"
    )
    result_retries = max(0, int(getattr(cfg, "LLM_PARSE_RESULT_RETRIES", 1) or 0))
    retry_reason = ""
    for result_attempt in range(result_retries + 1):
        msg = base_msg
        if retry_reason:
            msg += (
                f"\n\n上一轮结果未通过完整性检查：{retry_reason}。"
                "请重新从原文逐表提取，不得复用截断或省略的结果。"
            )
        raw = call_chat_completion(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": msg},
            ],
            timeout=getattr(cfg, "LLM_PARSE_TIMEOUT_SEC", 300),
            max_tokens=getattr(cfg, "LLM_PARSE_MAX_TOKENS", 32000),
            max_retries=getattr(cfg, "LLM_MAX_RETRIES", 2),
            temperature=0.1,
            stream=True,
        )
        if raw is None:
            retry_reason = "模型调用未返回内容"
            if result_attempt < result_retries:
                logger.warning("第%d段无响应，重新请求完整结果", chunk_index + 1)
                continue
            logger.error("第%d段LLM调用失败: 所有重试均未成功", chunk_index + 1)
            return None
        try:
            parsed = _extract_json_from_response(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            retry_reason = f"JSON 无法解析: {exc}"
            if result_attempt < result_retries:
                logger.warning("第%d段JSON解析失败，重新请求: %s", chunk_index + 1, exc)
                continue
            logger.error("第%d段JSON解析失败: %s", chunk_index + 1, exc)
            _dump_failed_llm_response(chunk_index, total_chunks, raw, exc)
            return None
        if not isinstance(parsed, dict):
            retry_reason = f"返回类型为 {type(parsed).__name__}，不是 JSON 对象"
            if result_attempt < result_retries:
                continue
            logger.error("第%d段解析结果非 JSON 对象 (%s)，跳过", chunk_index + 1, type(parsed).__name__)
            return None

        _normalize_known_column_semantics(chunk, parsed)
        retry_reason = _chunk_result_incomplete_reason(chunk, parsed)
        if retry_reason:
            if result_attempt < result_retries:
                logger.warning("第%d段结果不完整，重新请求: %s", chunk_index + 1, retry_reason)
                continue
            logger.error("第%d段结果不完整: %s", chunk_index + 1, retry_reason)
            _dump_failed_llm_response(
                chunk_index,
                total_chunks,
                raw,
                ValueError(retry_reason),
            )
            return None
        _annotate_source_provenance(chunk, parsed, chunk_index)
        return parsed
    return None


def _normalize_known_column_semantics(chunk: str, parsed: dict[str, Any]) -> None:
    """用明确原文表头修正稳定可判定的列语义，避免模型把尾部汇总列错映射。"""
    force_reading_layout = "kn" in chunk.lower() and re.search(
        r"本次(?:\s*\([^)]*\))?\s*测值(?:\s*\([^)]*\))?\s*变化速率",
        chunk,
        flags=re.IGNORECASE,
    )
    if not force_reading_layout:
        return

    for table in parsed.get("tables") or []:
        if not isinstance(table, dict):
            continue
        label = f"{table.get('monitoring_item', '')} {table.get('category', '')}"
        if not any(keyword in label for keyword in ("锚索", "拉力", "支撑", "轴力")):
            continue
        table["initial_value_reliable"] = False
        for point in table.get("points") or []:
            if not isinstance(point, dict):
                continue
            if point.get("current_value") is not None:
                point["cumulative_change"] = point["current_value"]


_PAGE_MARKER_RE = re.compile(
    r"---\s*第\s*(\d+)\s*页(?:\s*/\s*共\s*\d+\s*页)?\s*---"
)


def _source_page_at_line(lines: list[str], line_index: int) -> int | None:
    page = None
    for line in lines[:line_index + 1]:
        match = _PAGE_MARKER_RE.search(line)
        if match:
            page = int(match.group(1))
    return page


_BACKWARD_SCAN_MAX = 60


def _backward_window(lines: list[str], from_index: int) -> list[str]:
    """从 from_index 向上扫描非页标记行，最多回溯 _BACKWARD_SCAN_MAX 行，允许跨 1 个页界。"""
    window: list[str] = []
    page_markers = 0
    for index in range(from_index - 1, max(-1, from_index - _BACKWARD_SCAN_MAX - 1), -1):
        line = lines[index].strip()
        if _PAGE_MARKER_RE.search(line):
            page_markers += 1
            if page_markers > 1:
                break
            continue
        window.append(line)
    return window


def _candidate_value_tokens(record: dict[str, Any]) -> list[str]:
    tokens: set[str] = set()
    for key in (
        "initial_value", "previous_value", "current_value", "current_change",
        "cumulative_change", "change_rate", "depth", "previous_cumulative",
        "current_cumulative",
    ):
        value = record.get(key)
        if value is None or value == "":
            continue
        text = str(value).strip()
        if text:
            tokens.add(text)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        tokens.update({f"{number:g}", f"{number:.1f}", f"{number:.2f}"})
    return [token for token in tokens if token]


def _find_source_row_detail(
    lines: list[str],
    *,
    identifier: str,
    record: dict[str, Any],
    monitor_date: str = "",
) -> tuple[int | None, int | None, str]:
    identifier = identifier.strip()
    if not identifier:
        return None, None, ""
    boundary = re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])",
        flags=re.IGNORECASE,
    )
    tokens = _candidate_value_tokens(record)
    target_date = _normalize_source_date(monitor_date)
    candidates: list[tuple[int, int, int, int, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not boundary.search(stripped):
            continue
        value_score = sum(1 for token in tokens if token in stripped)
        table_score = int(stripped.startswith("|") and stripped.endswith("|"))
        nearby_dates: set[str] = set()
        page_markers = 0
        for prior_index in range(index - 1, max(-1, index - _BACKWARD_SCAN_MAX - 1), -1):
            prior_line = lines[prior_index].strip()
            if _PAGE_MARKER_RE.search(prior_line):
                page_markers += 1
                if page_markers > 1:
                    break
                continue
            line_dates = _SOURCE_DATE_RE.findall(prior_line)
            if line_dates:
                nearby_dates = {
                    _normalize_source_date(value)
                    for value in line_dates
                }
                break
        date_score = int(bool(target_date) and target_date in nearby_dates)
        candidates.append((date_score, value_score, table_score, -index, stripped))
    if not candidates:
        return None, None, ""
    _, _, _, negative_index, row_text = max(candidates)
    line_index = -negative_index
    return _source_page_at_line(lines, line_index), line_index, row_text


def _split_source_cells(row_text: str) -> list[str]:
    stripped_row = row_text.strip()
    if not stripped_row:
        return []
    if stripped_row.startswith("|"):
        return [cell.strip() for cell in stripped_row.strip("|").split("|")]
    return re.split(r"\s+", stripped_row)


_SOURCE_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "previous_cumulative": ("上次累计", "上期累计"),
    "current_cumulative": ("本次累计", "本期累计"),
    "current_change": ("本次变化", "本期变化", "单次变化"),
    "cumulative_change": ("累计变化", "累计位移", "累计沉降"),
    "change_rate": ("变化速率", "变形速率", "速率"),
    "initial_value": ("初始断面距离", "初始高程", "初始值", "初始内力"),
    "previous_value": ("上次值", "上期值", "上次高程", "上期高程"),
    "current_value": (
        "本次断面距离", "本次高程", "本次值", "本期值", "实测值",
        "本次内力", "本期轴力值",
    ),
    "depth": ("深度",),
}


def _header_field_columns(cells: list[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, cell in enumerate(cells, start=1):
        normalized = re.sub(r"\s+", "", cell)
        for field, aliases in _SOURCE_HEADER_ALIASES.items():
            if field not in columns and any(alias in normalized for alias in aliases):
                columns[field] = index
                break
    return columns


def _nearest_source_header(
    lines: list[str],
    row_index: int | None,
    row_cell_count: int,
) -> list[str]:
    if row_index is None or row_cell_count <= 0:
        return []
    window = _backward_window(lines, row_index)

    has_point_label = any(
        any(label in line for label in ("测点编号", "测点", "孔号", "测孔编号"))
        for line in window
    )
    has_status_label = any("安全状态" in line or line == "状态" for line in window)
    for line in window:
        cells = _split_source_cells(line)
        field_count = len(_header_field_columns(cells))
        if field_count < 2:
            continue
        if len(cells) == row_cell_count:
            return cells
        if len(cells) == row_cell_count - 2 and has_point_label and has_status_label:
            return ["测点编号", *cells, "安全状态"]
        if len(cells) == row_cell_count - 1 and has_point_label:
            return ["测点编号", *cells]
    return []


_SOURCE_DATE_RE = re.compile(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}")


def _normalize_source_date(value: str) -> str:
    match = _SOURCE_DATE_RE.search(str(value or ""))
    if not match:
        return ""
    parts = re.split(r"[-/.]", match.group(0))
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def _wide_metric_field(token: str) -> str:
    normalized = re.sub(r"\s+", "", token)
    if normalized in {"本次", "本次变化", "本次变化量", "本期变化", "本期变化量"}:
        return "current_change"
    if normalized in {"累计", "累计变化", "累计变化量", "累计位移", "累计沉降"}:
        return "cumulative_change"
    if normalized in {"变化速率", "变形速率", "本期速率", "速率"}:
        return "change_rate"
    if normalized in {"测值", "轴力值", "本次值", "本期值", "本期轴力值", "当前值"}:
        return "current_value"
    return ""


def _wide_date_group_columns(
    lines: list[str],
    row_index: int | None,
    monitor_date: str,
    row_text: str = "",
) -> dict[str, int]:
    target_date = _normalize_source_date(monitor_date)
    if row_index is None or not target_date:
        return {}

    window = _backward_window(lines, row_index)

    dates: list[str] = []
    for line in window:
        line_dates = [_normalize_source_date(value) for value in _SOURCE_DATE_RE.findall(line)]
        if line_dates:
            dates = line_dates
            break
    if not dates or target_date not in dates:
        return {}

    metric_fields: list[str] = []
    for line in window:
        fields = [field for field in (_wide_metric_field(token) for token in _split_source_cells(line)) if field]
        if len(fields) >= len(dates) * 3:
            metric_fields = fields[:3]
            break
    if len(metric_fields) != 3 or len(set(metric_fields)) != 3:
        return {}

    has_initial_column = any(
        any(token == "初始" or token.startswith("初始值") or token.startswith("初始高程")
            for token in _split_source_cells(line))
        for line in window
    )
    prefix_columns = 2 if has_initial_column else 1
    for line in window:
        tokens = [token.upper() for token in _split_source_cells(line)]
        if tokens == ["X", "Y"]:
            prefix_columns = 3
            break
    if len(dates) == 1 and not (
        prefix_columns >= 2 and len(_split_source_cells(row_text)) >= 9
    ):
        return {}

    date_index = dates.index(target_date)
    start_column = prefix_columns + date_index * len(metric_fields) + 1
    return {
        field: start_column + offset
        for offset, field in enumerate(metric_fields)
    }


def _source_field_map(
    record: dict[str, Any],
    row_text: str,
    header_cells: list[str] | None = None,
    preferred_mapping: dict[str, int] | None = None,
) -> str:
    cells = _split_source_cells(row_text)
    if not cells:
        return ""
    mapping: dict[str, int] = {}
    fields = (
        "initial_value", "previous_value", "current_value", "current_change",
        "cumulative_change", "change_rate", "depth", "previous_cumulative",
        "current_cumulative",
    )
    preferred_columns = {
        **(preferred_mapping or {}),
        **{
            field: column
            for field, column in _header_field_columns(header_cells or []).items()
            if field not in (preferred_mapping or {})
        },
    }
    for field, column in preferred_columns.items():
        value = record.get(field)
        if value is None or value == "" or column > len(cells):
            continue
        try:
            expected = float(value)
            actual = float(cells[column - 1])
        except (TypeError, ValueError):
            continue
        if abs(actual - expected) <= 1e-9:
            mapping[field] = column

    for field in fields:
        if field in mapping:
            continue
        value = record.get(field)
        if value is None or value == "":
            continue
        try:
            expected = float(value)
        except (TypeError, ValueError):
            continue
        matches: list[int] = []
        for index, cell in enumerate(cells, start=1):
            try:
                actual = float(cell)
            except (TypeError, ValueError):
                continue
            if abs(actual - expected) <= 1e-9:
                matches.append(index)
        if len(matches) == 1:
            mapping[field] = matches[0]
    return json.dumps(mapping, ensure_ascii=False, separators=(",", ":")) if mapping else ""


def _annotate_source_provenance(chunk: str, parsed: dict[str, Any], chunk_index: int) -> None:
    """用原始 chunk 回链来源，不增加模型输出 token。"""
    lines = chunk.splitlines()
    pages = list(dict.fromkeys(int(value) for value in _PAGE_MARKER_RE.findall(chunk)))
    source_chunk = chunk_index + 1
    pages_text = ",".join(str(page) for page in pages)

    for table in parsed.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table["_source_chunk"] = source_chunk
        table["_source_pages"] = pages_text
        for point in table.get("points") or []:
            if not isinstance(point, dict):
                continue
            point["_source_chunk"] = source_chunk
            page, row_index, row_text = _find_source_row_detail(
                lines,
                identifier=str(point.get("point_id") or ""),
                record=point,
                monitor_date=str(table.get("monitor_date") or ""),
            )
            point["_source_page"] = page
            point["_source_row_text"] = row_text
            row_cells = _split_source_cells(row_text)
            header_cells = _nearest_source_header(lines, row_index, len(row_cells))
            wide_mapping = _wide_date_group_columns(
                lines,
                row_index,
                str(table.get("monitor_date") or ""),
                row_text,
            )
            point["_source_field_map"] = _source_field_map(
                point,
                row_text,
                header_cells=header_cells,
                preferred_mapping=wide_mapping,
            )
        for point in table.get("deep_points") or []:
            if not isinstance(point, dict):
                continue
            point["_source_chunk"] = source_chunk
            depth = point.get("depth")
            page, row_index, row_text = _find_source_row_detail(
                lines,
                identifier=str(depth) if depth is not None else "",
                record=point,
                monitor_date=str(table.get("monitor_date") or ""),
            )
            point["_source_page"] = page
            point["_source_row_text"] = row_text
            row_cells = _split_source_cells(row_text)
            header_cells = _nearest_source_header(lines, row_index, len(row_cells))
            wide_mapping = _wide_date_group_columns(
                lines,
                row_index,
                str(table.get("monitor_date") or ""),
                row_text,
            )
            point["_source_field_map"] = _source_field_map(
                point,
                row_text,
                header_cells=header_cells,
                preferred_mapping=wide_mapping,
            )


def _chunk_result_incomplete_reason(chunk: str, parsed: dict[str, Any]) -> str:
    """检查结构化表段是否被模型截断或省略。非结构化前言不做表数推断。"""
    if not _STRUCTURED_TABLE_START_RE.search(chunk):
        return ""

    tables = parsed.get("tables") or []
    if not isinstance(tables, list):
        return "tables 不是数组"

    _PERIOD_DATE_KW = ("监测时间", "时间段", "监测期")
    dates: set[str] = set()
    for _line in chunk.splitlines():
        line_dates = re.findall(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", _line)
        if len(line_dates) >= 2 and any(kw in _line for kw in _PERIOD_DATE_KW):
            continue
        dates.update(line_dates)
    if dates and len(tables) < len(dates):
        return f"原文含 {len(dates)} 个监测日期，但只返回 {len(tables)} 张表"

    for index, table in enumerate(tables, start=1):
        if not isinstance(table, dict):
            return f"第 {index} 张表不是对象"
        try:
            declared = int(float(table.get("point_count") or 0))
        except (TypeError, ValueError):
            declared = 0
        actual = max(
            len(table.get("points") or []),
            len(table.get("deep_points") or []),
        )
        if declared >= 5 and actual < declared * 0.8:
            return f"第 {index} 张表声明 {declared} 个测点，但只返回 {actual} 个"
    return ""


def parse_report_with_llm(raw_text: str) -> MonitoringReport:
    chunks = _split_chunks(raw_text)
    structured_mode = any(_STRUCTURED_TABLE_START_RE.search(chunk) for chunk in chunks)
    max_parallel = max(1, min(
        len(chunks),
        int(getattr(cfg, "LLM_PARSE_MAX_PARALLEL", 1) or 1),
    ))
    logger.info("文本分为 %d 个片段发送给 LLM，并发数=%d", len(chunks), max_parallel)
    all_tables, first = [], {}
    success_count = 0
    parse_failures = 0

    results: list[tuple[int, dict[str, Any] | None]] = []
    if max_parallel == 1:
        for i, chunk in enumerate(chunks):
            results.append((i, _parse_chunk_with_llm(i, len(chunks), chunk)))
    else:
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            future_to_index = {
                executor.submit(_parse_chunk_with_llm, i, len(chunks), chunk): i
                for i, chunk in enumerate(chunks)
            }
            for future in as_completed(future_to_index):
                i = future_to_index[future]
                try:
                    results.append((i, future.result()))
                except Exception as exc:  # 防御性兜底：单个 chunk 不应拖垮全局
                    logger.exception("第%d段LLM解析任务异常: %s", i + 1, exc)
                    results.append((i, None))

    for i, parsed in sorted(results, key=lambda item: item[0]):
        if parsed is None:
            parse_failures += 1
            continue
        success_count += 1
        if not first:
            first = parsed
        else:
            for key in ("thresholds", "summary_items", "project_name", "monitoring_company", "report_number", "monitoring_period", "monitoring_date", "conclusion", "interval_days"):
                existing = first.get(key, [])
                new_items = parsed.get(key, [])
                if key in ("thresholds", "summary_items"):
                    if key == "thresholds":
                        first[key] = _merge_records(existing, new_items, ("item_name",))
                    else:
                        first[key] = _merge_records(existing, new_items, ("monitoring_item",))
                elif new_items and not existing:
                    first[key] = new_items
        parsed_tables = parsed.get("tables", [])
        if structured_mode and not _STRUCTURED_TABLE_START_RE.search(chunks[i]):
            if parsed_tables:
                logger.info("第%d段为结构化文档前言，忽略其中 %d 张汇总伪表", i + 1, len(parsed_tables))
        else:
            all_tables.extend(parsed_tables)
    if success_count == 0 or not first:
        raise RuntimeError("LLM 结构化解析失败：所有文本片段调用均未成功，请检查模型服务连接或稍后重试。")
    first["tables"] = all_tables
    report = _build_report(first)
    report.raw_text = raw_text
    report.extraction_diagnostics["llm_chunk_count"] = len(chunks)
    report.extraction_diagnostics["llm_chunk_success_count"] = success_count
    report.extraction_diagnostics["llm_chunk_parse_failures"] = parse_failures
    logger.info(
        "解析完成: %s, %d张表, %d阈值, %d汇总",
        report.project_name, len(report.tables),
        len(report.thresholds), len(report.summary_items),
    )
    return report


def verify_report_with_llm(
    report_md: str,
    raw_text: str,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> str:
    preview_chars = getattr(cfg, "FINAL_REVIEW_PREVIEW_CHARS", 3000)
    text_preview = raw_text[:preview_chars]
    # 截断 report_md 防止 token 溢出
    max_report_chars = getattr(cfg, "FINAL_REVIEW_MAX_REPORT_CHARS", 8000)
    report_preview = report_md[:max_report_chars]
    if len(report_md) > max_report_chars:
        report_preview += f"\n\n... (报告共 {len(report_md)} 字符，已截取前 {max_report_chars} 字符)"
    msg = (
        "以下是监测报告自动检查结果和原始文本。请审核是否有遗漏或误判。"
        "注意正负号代表方向不代表大小。\n\n"
        f"## 检查报告\n{report_preview}\n\n"
        f"## 原始文本(前{preview_chars}字)\n```\n{text_preview}\n```\n\n请给出审核意见。"
    )
    timeout_sec = getattr(cfg, "FINAL_REVIEW_TIMEOUT_SEC", getattr(cfg, "LLM_TIMEOUT_NORMAL", 90))
    max_retries = getattr(cfg, "FINAL_REVIEW_MAX_RETRIES", 0)
    backoff_sec = getattr(cfg, "FINAL_REVIEW_RETRY_BACKOFF_SEC", 2)

    for attempt in range(1 + max_retries):
        if progress_callback:
            progress_callback(f"提交最终审核请求（第 {attempt + 1}/{max_retries + 1} 次）")
        result = call_chat_completion(
            [
                {"role": "system", "content": "你是建筑工程监测领域资深专家。正负号代表方向不代表大小。"},
                {"role": "user", "content": msg},
            ],
            timeout=timeout_sec,
            max_tokens=4000,
            max_retries=0,
            temperature=0.3,
        )
        if result is not None:
            return result
        logger.error("AI审核调用失败: 第 %d 次请求未成功", attempt + 1)
        if attempt < max_retries:
            if progress_callback:
                progress_callback("最终审核失败，准备重试。")
            time.sleep(backoff_sec * (2 ** attempt))

    return "AI审核调用失败: 最终审核请求未成功返回结果。"
