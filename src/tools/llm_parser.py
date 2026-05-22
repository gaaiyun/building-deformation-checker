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

## 关于初始值
- 有些表没有"初始值"列，只有"本次变化量"和"累计变化量"，此时 initial_value 设 null
- 累计变化量是从项目首测以来的总变化，不一定能通过表中两列算出

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
    repaired = re.sub(r'(-?\d+\.\d+)\.(?=[,\s\]}])', r'\1', repaired)
    repaired = re.sub(r'(-?\d+\.?\d*)(mm/?d?|kN|KN|kn|cm|m)(?=[,\s\]}])', r'\1', repaired)

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

    # 如果错误位置在文本末端附近，尝试截断到上一个合法 '}'
    if exc.pos and exc.pos > len(repaired) * 0.5:
        # 找最后一个完整对象的结束位置（在 exc.pos 之前）
        search_text = repaired[:exc.pos + 1] if exc.pos < len(repaired) else repaired
        last_close = max(search_text.rfind('}'), search_text.rfind(']'))
        if last_close > 0:
            # 截断到最后一个 close
            truncated = repaired[:last_close + 1]
            # 计算未闭合的括号
            opens_curly = truncated.count('{') - truncated.count('}')
            opens_square = truncated.count('[') - truncated.count(']')
            # 补齐 ] 然后 }（嵌套顺序：先关数组再关对象，因为 tables 通常是数组）
            # 实际策略：从右往左扫描，看 unmatched 是 [ 还是 {，按相反顺序补
            if opens_square > 0 or opens_curly > 0:
                # 简单粗暴：智能补全 - 检查嵌套结构
                stack = []
                in_string = False
                escape = False
                for ch in truncated:
                    if escape:
                        escape = False
                        continue
                    if ch == '\\' and in_string:
                        escape = True
                        continue
                    if ch == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch in '{[':
                        stack.append(ch)
                    elif ch == '}':
                        if stack and stack[-1] == '{':
                            stack.pop()
                    elif ch == ']':
                        if stack and stack[-1] == '[':
                            stack.pop()
                # 按 stack 反序补齐
                closers = []
                for opener in reversed(stack):
                    closers.append('}' if opener == '{' else ']')
                truncated += ''.join(closers)
            return truncated

    return repaired if repaired != text else None


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
            monitoring_item=_text(tb.get("monitoring_item")),
            category=_cat(tb.get("category", "")),
            monitor_date=_text(tb.get("monitor_date")),
            monitor_count=_text(tb.get("monitor_count")),
            point_count=tb.get("point_count", 0),
            equipment_type=_text(tb.get("equipment_type")),
            equipment_model=_text(tb.get("equipment_model")),
            borehole_id=_sid(tb.get("borehole_id")),
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


def _split_chunks(text: str, max_chars: int | None = None) -> list[str]:
    """
    Multi-strategy text splitting:
    1. Try page markers (--- 第 N 页)
    2. Try table boundaries (【xxx】监测数据)
    3. Fallback: character-count split with overlap
    """
    max_chars = max_chars or getattr(cfg, "LLM_PARSE_CHUNK_CHARS", 18000)

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


def parse_report_with_llm(raw_text: str) -> MonitoringReport:
    chunks = _split_chunks(raw_text)
    logger.info("文本分为 %d 个片段发送给 LLM", len(chunks))
    all_tables, first = [], {}
    success_count = 0
    parse_failures = 0
    for i, chunk in enumerate(chunks):
        logger.info("正在处理第 %d/%d 段 (%d字符)...", i + 1, len(chunks), len(chunk))
        msg = (
            f"以下是监测报告第{i + 1}/{len(chunks)}段，请提取所有监测数据表格。"
            f"无表格则tables返回空列表。\n\n```\n{chunk}\n```"
        )
        raw = call_chat_completion(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": msg},
            ],
            timeout=getattr(cfg, "LLM_PARSE_TIMEOUT_SEC", 300),
            max_tokens=getattr(cfg, "LLM_PARSE_MAX_TOKENS", 24000),
            max_retries=getattr(cfg, "LLM_MAX_RETRIES", 2),
            temperature=0.1,
        )
        if raw is None:
            logger.error("第%d段LLM调用失败: 所有重试均未成功", i + 1)
            parse_failures += 1
            continue
        try:
            parsed = _extract_json_from_response(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("第%d段JSON解析失败: %s", i + 1, e)
            # 调试 dump：把失败的原始 LLM 输出写到磁盘以便排查
            try:
                from pathlib import Path
                debug_dir = Path("output") / "llm_debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                from time import strftime
                ts = strftime("%Y%m%d_%H%M%S")
                dump_path = debug_dir / f"chunk{i + 1}_failed_{ts}.txt"
                dump_path.write_text(
                    f"# LLM 调用失败的原始响应\n"
                    f"# 错误: {e}\n"
                    f"# Chunk index: {i + 1}/{len(chunks)}\n"
                    f"# 长度: {len(raw)} 字符\n\n"
                    f"{raw}",
                    encoding="utf-8",
                )
                logger.info("已保存失败的 LLM 响应到: %s", dump_path)
            except Exception as dump_exc:
                logger.warning("无法保存调试 dump: %s", dump_exc)
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
        all_tables.extend(parsed.get("tables", []))
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
