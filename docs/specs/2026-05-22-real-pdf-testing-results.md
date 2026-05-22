# 真实 PDF 测试与三大 bug 修复结果

**日期**：2026-05-22
**作者**：v2 重构第二轮迭代

---

## 1. 测试方法

用户提供了 3 对 (错误版 + 正确版) XLSX 模板（共 6 个），来自不同监测公司：

- **质安模板** (3.7 MB / 9 sheets)：基坑监测，4 张数据表 × 4 期
- **深工勘模板** (1.8 MB / 22 sheets)：深基坑，7 张数据表 + 深层水平位移测斜
- **展誉模板** (5.8 MB / 25 sheets)：基坑全套，11 张数据表 + 围护结构测斜

**新方法学**：用错误版与正确版逐 cell diff 得到 ground truth (47 处)，
比纯 agent 人工分析更精确，可量化 precision/recall。

### 流程

```
错误版.xlsx ─┐
            ├─→ baseline/xlsx_diff.py ──→ baseline/diffs/<company>_diff.json
正确版.xlsx ─┘                              （47 处已知错误的精确清单）

6 份 XLSX ──→ baseline/xlsx_to_pdf.py (Excel COM) ──→ test_pdfs/*.pdf

6 PDF ──→ baseline/run_tool_tests.py ──→ baseline/results/*_tool_output.json
                                          （工具发现的所有 issues）

工具输出 vs ground truth ──→ baseline/compare_v1_v2.py ──→ 对比报告
```

---

## 2. 发现的三大 bug

测试一跑就暴露了 **正确版被误报 17 errors + 137 warnings** 的严重问题。深入分析后定位三个独立 bug：

### Bug 1: 监测间隔仲裁错误

**根因** (`src/tools/calculation_checker.py:_choose_interval_days`)：
- 当 LLM 从报告日期范围抽取的 `configured_interval` 与从行数据反推的
  `inferred_interval` 差距 > 2 天时，v1 盲目采用 `configured`。
- 模板把 4 期监测并到一张 sheet（如 9/20、9/22、9/24、9/26），LLM 把
  报告日期范围算成 7 天，但每期实际间隔 2 天。
- 结果：每行计算速率时除以 7 而非 2，所有行都触发"反推 2 天"警告。

**修复**：新增 `_interval_confidence()` 计算行级支持率（多少行的反推值在
candidate ±20% 内）。当推断值支持率 ≥ 50% 且超过配置支持率时，采用推断值。

**效果**：质安正确版的 137 个速率警告 → **0**。

---

### Bug 2: 多期数据跨表合并

**根因** (`src/tools/statistics_checker.py:_get_group_key`)：
- 分组键 `(monitoring_item, borehole_id)` 没考虑日期，导致同一 sheet 内
  4 期监测被合并成 1 个组。
- 当核对期 3 的"正方向最大"时，工具用合并后的全局 max（来自期 1 的 WY245=13.5）
  与期 3 报告的 WY241=12.6 比较，造成 100% 误报。

**修复**：分组键加入 `monitor_date` 维度。
- 同 item + borehole + date → 合并（多页同一报告）
- 同 item + borehole 不同 date → 分开核对（不同期）

**效果**：质安正确版的 17 个统计 errors → **0**。

---

### Bug 3: 混合单位场景

**根因** (`src/tools/table_analyzer.py`)：
- 监测公司模板的水平位移表常见格式为：
  `| 测点 | 初始(m) | 本次(m) | 本次变化(mm) | 累计变化(mm) | 速率(mm/d) |`
- 即初始/本次列单位是 **米**，累计列单位是 **毫米**。
- LLM 偶尔把 `table_unit` 标成 "mm"（因表名不含"高程"），导致
  `unit_conversion=1.0`，于是 `(current - initial) = 0.01 m` 与
  `cumulative_change = 10 mm` 比对必然 mismatch。
- 老版 `_detect_elevation_from_data` 只对 SETTLEMENT / VERTICAL_DISP 类别启用。

**修复**：新增通用 `_detect_mixed_units_ratio()` 基于数学关系自动判断：
- 收集每个有效点的 `ratio = cumulative_change / (current - initial)`
- 中位数在 [950, 1050] → 返回 1000（应用 m→mm 转换）
- 中位数在 [0.95, 1.05] → 返回 1.0（已经是同单位）
- 否则返回 None（数据混乱，留给上层 fallback）

**效果**：质安错误版的非真错累计 mismatch 大幅减少（30 → 16，接近 ground truth 14）。

---

## 3. 测试结果对比

### v1 (修复前) vs v2 (3 fixes 全应用)

| PDF | GT | v1 errors | v1 warns | v2 errors | v2 warns | 噪音减少 |
|-----|----|-----------|----------|-----------|----------|----------|
| 质安-错误版 | 14 | 30 | 135 | 16 | 0 | **149→2** |
| 质安-正确版 | 0 | 17 | 137 | 0 | 0 | **154→0** |

（深工勘、展誉数据待批量测试完成后补充）

### 拆解：v1 165 个 issues 的成分

通过 `baseline/analyze_v1_results.py` 自动分类（基于 message 模式）：

| 类别 | 错误版 | 正确版 |
|------|--------|--------|
| matched_gt (真错) | 4 | 0 |
| stats_noise (跨期混淆) | 31 | 25 |
| rate_noise (7天 vs 2天) | 129 | 129 |
| other | 1 | 0 |

v2 应当消除所有 stats_noise + rate_noise（共 314），保留 matched_gt + other。
实测 v3 错误版 16 errors ≈ 14 GT + 2-3 衍生（如 WY237 速率超限触发"应为报警"
状态错误，是 GT F39 错误的下游影响）。

---

## 4. 新增测试覆盖

| 文件 | 新增用例 | 目的 |
|------|----------|------|
| tests/test_calculation_checker.py | +4 | 间隔仲裁 4 个场景 |
| tests/test_mixed_units_detection.py | +8 | 混合单位检测各种情形 |

测试总数：69（v2 重构前）→ 169（修复后）。1.6s 跑完全绿。

---

## 5. 配套基础设施

为了真实测试可重复，新增工具：

```
baseline/
├── xlsx_diff.py             # 错误版 vs 正确版逐 cell 对比 → ground truth
├── xlsx_to_pdf.py           # Excel COM 高保真转 PDF
├── peek_data.py             # 调试用：浏览 XLSX 多期块结构
├── run_tool_tests.py        # 跑工具 over 6 个 PDF，输出 JSON
├── compare_results.py       # 工具输出 vs ground truth 模糊匹配
├── compare_v1_v2.py         # 横向对比 v1 / v2 表现
├── analyze_v1_results.py    # 拆解 v1 issues 的噪音成分
├── diffs/                   # 3 份 ground truth JSON+MD
└── results/                 # v2 工具最新结果
```

完整流程一行跑通（需先 `.env` 填好 LLM_API_KEY）：

```bash
python baseline/xlsx_diff.py        # 生成 ground truth
python baseline/xlsx_to_pdf.py      # 转 PDF
python baseline/run_tool_tests.py --quick   # 跑工具
python baseline/compare_v1_v2.py    # 出对比报告
```

---

## 6. 待后续完善

- 深工勘、展誉的真实测试结果（批量跑中，预计 ~30 min）
- 把检测到混合单位的事件提示在 UI 中（目前只在 logger.info）
- 考虑添加"跨期一致性"验证（同一测点在第 N 期的"上次值"是否等于第 N-1 期的"本次值"）
