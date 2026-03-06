# 建筑变形监测报告检查智能体

> 基于 LLM + 规则引擎的建筑变形监测报告自动审核系统，支持多种 PDF 格式，自动提取、理解、验证监测数据。

---

## 目录

- [项目概述](#项目概述)
- [系统架构](#系统架构)
- [完整处理流程](#完整处理流程)
- [安装与配置](#安装与配置)
- [使用方式](#使用方式)
- [项目结构](#项目结构)
- [各模块详细说明](#各模块详细说明)
  - [1. PDF 数据提取 (pdf_extractor.py)](#1-pdf-数据提取-pdf_extractorpy)
  - [2. LLM 结构化解析 (llm_parser.py)](#2-llm-结构化解析-llm_parserpy)
  - [3. 动态验证配置 (table_analyzer.py)](#3-动态验证配置-table_analyzerpy)
  - [3.5 表格分析计划 (ReAct)](#35-表格分析计划-table_analyzerpy--generate_analysis_plan)
  - [4. 计算验证 (calculation_checker.py)](#4-计算验证-calculation_checkerpy)
  - [5. 统计验证 (statistics_checker.py)](#5-统计验证-statistics_checkerpy)
  - [6. 逻辑检查 (logic_checker.py)](#6-逻辑检查-logic_checkerpy)
  - [7. AI 自验证 (self_verifier.py)](#7-ai-自验证-self_verifierpy)
  - [8. 报告生成 (report_generator.py)](#8-报告生成-report_generatorpy)
- [数据模型](#数据模型)
- [核心计算公式与正负号规则](#核心计算公式与正负号规则)
- [支持的监测项](#支持的监测项)
- [检查规则详解](#检查规则详解)
- [关键技术决策与注意事项](#关键技术决策与注意事项)
- [已知问题与踩坑记录](#已知问题与踩坑记录)
- [待改进方向](#待改进方向)
- [测试记录](#测试记录)
- [许可证](#许可证)

---

## 项目概述

本系统旨在自动化检查建筑变形监测报告中的：

- **计算结果**：累计变化量、变化速率是否与原始测值一致
- **统计结果**：最大/最小值、最大速率统计是否正确
- **逻辑关系**：安全状态判定、汇总表与分表数据一致性
- **数据完整性**：测点数量、编号一致性

**核心挑战**：不同监测公司出具的报告格式差异极大（表头命名、列排布、数据单位、初始值含义等都不同），无法用硬编码规则覆盖。本系统采用 **LLM 语义理解 + 动态规则配置 + 双重验证** 架构来解决这一问题。

---

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户输入 (PDF 文件)                      │
└────────────────────────┬────────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│  Step 1: PDF 数据提取                                    │
│  ┌──────────────┐   智能切换   ┌──────────────────┐     │
│  │  pdfplumber   │ ──────────→ │  PaddleOCR API   │     │
│  │  (文字版PDF)  │  质量不佳时  │  (扫描件/图片PDF) │     │
│  └──────────────┘             └──────────────────┘     │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│  Step 2: LLM 结构化解析                                  │
│  - 多策略文本分块 (页标记 → 表边界 → 字符数)               │
│  - 分块发送 LLM, 提取为标准化 JSON                        │
│  - 合并元数据 + 所有表格                                  │
│  - 动态生成 TableVerificationConfig                      │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│  Step 2.5: 表格分析计划 (ReAct)                           │
│  - Thought: 检测字段、识别数据类型                         │
│  - Observation: 数据样本、单位/基准分析                    │
│  - Action: 制定验证规则、容差、严重级别                    │
│  - 透明展示 AI 理解过程，供用户审查                        │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│  Step 3-5: 规则引擎验证                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ 计算验证  │  │ 统计验证  │  │ 逻辑检查  │              │
│  │ 累计变化  │  │ 最大/最小 │  │ 安全状态  │              │
│  │ 变化速率  │  │ 跨表引用  │  │ 汇总一致  │              │
│  │ 深层位移  │  │ 方向检查  │  │ 语义匹配  │              │
│  └──────────┘  └──────────┘  └──────────┘              │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│  Step 6: AI 自验证 (Two-LLM Pattern)                     │
│  - 对检出的 error 级别问题进行二次确认                      │
│  - confirm / downgrade / dismiss                        │
└────────────────────────┬───────────────────────────────┘
                         ▼
┌────────────────────────────────────────────────────────┐
│  Step 7: 报告生成 (Markdown / Word / HTML)                │
└────────────────────────────────────────────────────────┘
```

---

## 完整处理流程

| 步骤 | 名称 | 工具/模块 | 耗时 | 说明 |
|------|------|-----------|------|------|
| 1 | PDF 提取 | pdfplumber / PaddleOCR | 5-30s | 智能选择提取方式 |
| 2 | LLM 结构化解析 | DashScope qwen3.5-plus | 60-180s | 将文本转为标准化 JSON |
| 2b | 动态配置增强 | LLM (可选) | 0-60s | 对异常表格请求 LLM 确认配置 |
| 2.5 | 表格分析计划 | Python (ReAct) | <1s | 逐表分析字段/单位/验证策略，透明展示 |
| 3 | 计算验证 | 规则引擎 | <1s | 验证累计变化量、变化速率 |
| 4 | 统计验证 | 规则引擎 | <1s | 验证最大/最小值统计 |
| 5 | 逻辑检查 | LLM + 规则 | 30-60s | 语义匹配 + 安全状态 + 汇总一致性 |
| 6 | AI 自验证 | LLM | 30-120s | 对 error 级别做二次确认 |
| 7 | 报告生成 | Python | <1s | 生成 Markdown/Word/HTML |

**总耗时**：约 3-8 分钟（取决于 PDF 大小和 LLM 响应速度）

---

## 安装与配置

### 环境要求

- Python 3.10+
- 网络连接（需要访问 DashScope LLM API 和 PaddleOCR API）

### 安装依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 内容：

```
openai>=1.0.0        # LLM 调用（OpenAI兼容协议）
pdfplumber>=0.11.0   # 文字版 PDF 解析
requests>=2.31.0     # PaddleOCR API 调用
streamlit>=1.30.0    # Web 界面
python-docx>=1.0.0   # Word 文档导出
markdown>=3.5.0      # Markdown 转 HTML
```

### 配置

所有配置集中在 `src/config.py`：

| 配置项 | 说明 | 默认值 | 环境变量 |
|--------|------|--------|----------|
| `LLM_API_KEY` | DashScope API 密钥 | 内置 | `LLM_API_KEY` |
| `LLM_BASE_URL` | LLM API 基础 URL | `https://coding.dashscope.aliyuncs.com/v1` | `LLM_BASE_URL` |
| `LLM_MODEL` | 模型名称 | `qwen3.5-plus` | `LLM_MODEL` |
| `PADDLE_OCR_URL` | PaddleOCR 版式分析 API 地址 | 内置 | - |
| `PADDLE_OCR_TOKEN` | PaddleOCR 认证 Token | 内置 | - |
| `FLOAT_TOLERANCE` | 浮点数容差 (mm) | `0.15` | - |
| `RATE_TOLERANCE` | 速率容差 (mm/d) | `0.05` | - |

> **注意**：`FLOAT_TOLERANCE` 和 `RATE_TOLERANCE` 是全局默认值。实际验证时，`table_analyzer.py` 会根据每张表的数据特征动态调整容差，见 [动态验证配置](#3-动态验证配置-table_analyzerpy)。

---

## 使用方式

### Streamlit Web 界面（推荐）

```bash
streamlit run app.py
```

界面功能：
- **侧边栏**：选择 PDF 提取方式（智能切换/仅pdfplumber/强制PaddleOCR）、AI 自验证开关、AI 最终审核开关
- **进度显示**：`st.status` 8 步实时显示进度，`st.progress` 进度条
- **结果展示**：指标卡片（数据表数、错误数、警告数、提示数、耗时）+ 7 个选项卡（检查报告/分析计划/计算验证/统计验证/逻辑检查/AI审核/运行日志）
- **导出功能**：Markdown / Word (docx) / HTML（可打印为PDF）三种格式
- **运行日志**：实时捕获所有模块的日志输出，通过自定义 `StreamlitLogHandler` 实现

### 命令行

```bash
# 基本用法
python main.py "监测报告.pdf"

# 强制使用 PaddleOCR（适用于扫描件）
python main.py "扫描件.pdf" --ocr

# 跳过 AI 最终审核（加速）
python main.py "报告.pdf" --no-ai-review

# 跳过 AI 自验证
python main.py "报告.pdf" --no-self-verify

# 指定输出路径
python main.py "报告.pdf" -o output/my_report.md
```

---

## 项目结构

```
建筑变形监测Agent/
├── app.py                           # Streamlit Web UI (483行)
│                                    #   - StreamlitLogHandler 日志捕获
│                                    #   - 8步进度展示(含ReAct分析计划)
│                                    #   - 多格式导出 (MD/DOCX/HTML)
│                                    #   - _render_analysis_plan ReAct展示
│                                    #   - _render_issues 按表分组展示
│                                    #   - _generate_docx Word导出
│                                    #   - _generate_html 可打印HTML
│
├── main.py                          # CLI 入口 (178行)
│                                    #   - argparse 命令行参数
│                                    #   - 8步流水线
│
├── requirements.txt                 # Python 依赖
├── README.md                        # 本文档
├── .gitignore                       # Git 忽略规则
│
├── src/
│   ├── config.py                    # 全局配置 (23行)
│   │                                #   - LLM API 配置
│   │                                #   - PaddleOCR API 配置
│   │                                #   - 数值精度常量
│   │
│   ├── models/
│   │   └── data_models.py           # 数据模型定义 (165行)
│   │                                #   - SafetyStatus / MonitoringCategory 枚举
│   │                                #   - ThresholdConfig 报警/控制值
│   │                                #   - TableVerificationConfig 动态验证配置
│   │                                #   - MeasurementPoint 普通测点
│   │                                #   - DeepDisplacementPoint 深层位移测点
│   │                                #   - StatisticsSummary 表底统计
│   │                                #   - MonitoringTable 监测数据表
│   │                                #   - ReportSummaryItem 简报汇总项
│   │                                #   - MonitoringReport 完整报告
│   │                                #   - CheckIssue 检查问题
│   │
│   └── tools/
│       ├── pdf_extractor.py         # PDF 提取 (197行)
│       ├── llm_parser.py            # LLM 结构化解析 (343行)
│       ├── table_analyzer.py        # 动态验证配置 + ReAct分析计划
│       ├── calculation_checker.py   # 计算验证 (291行)
│       ├── statistics_checker.py    # 统计验证 (260行)
│       ├── logic_checker.py         # 逻辑检查 (326行)
│       ├── self_verifier.py         # AI 自验证 (142行)
│       └── report_generator.py      # 报告生成 (153行)
│
└── output/                          # 检查报告输出目录（git忽略）
```

---

## 各模块详细说明

### 1. PDF 数据提取 (pdf_extractor.py)

#### 技术方案

提供两种提取引擎，支持智能切换：

| 引擎 | 适用场景 | 技术 | 优点 | 缺点 |
|------|---------|------|------|------|
| **pdfplumber** | 文字版 PDF | 直接读取 PDF 文本层 | 速度快(5-10s)、精度高 | 对扫描件无效 |
| **PaddleOCR** | 扫描件/图片 PDF | API 版式分析 + OCR | 支持扫描件、表格识别强 | 速度慢(20-30s)、需网络 |

#### 智能切换机制

`_assess_pdfplumber_quality()` 通过两个维度评估 pdfplumber 提取质量：

1. **平均每页字符数**：低于 `MIN_CHARS_PER_PAGE`（50字符）则判定为扫描件
2. **关键标志词检测**：检查是否包含 "监测"、"测点"、"变化"、"累计"、"速率" 中至少 2 个

切换逻辑：
```
if 强制OCR → 直接用PaddleOCR
else:
    text = pdfplumber提取
    if 质量不佳 and 允许自动切换:
        ocr_text = PaddleOCR提取
        if OCR结果更长 → 用OCR版本
        else → 保留pdfplumber版本（OCR也没更好）
    else → 用pdfplumber结果
```

#### PaddleOCR API 调用细节

- **端点**：`PADDLE_OCR_URL`（PaddingPaddle AI Studio 部署）
- **认证**：Bearer Token
- **输入**：PDF 文件 Base64 编码
- **输出**：每页的 Markdown 文本 + 表格结构
- **超时**：300 秒
- **参数**：`fileType=0`（PDF）、关闭方向分类和图表识别以加速

#### 注意事项

- PaddleOCR API 是外部服务，需确保 Token 有效且服务可用
- 某些"盖章扫描件"实际上嵌入了文字层（OCR层），pdfplumber 可以提取到文字但质量可能不如 PaddleOCR
- `extract_tables_with_pdfplumber()` 提取结构化表格数据，但当前主流程只用了 `extract_text_with_pdfplumber()`（文本模式），结构化表格交给 LLM 理解

---

### 2. LLM 结构化解析 (llm_parser.py)

#### 核心思路

将 PDF 提取的非结构化文本发送给 LLM（qwen3.5-plus），由 LLM 理解表格含义后输出标准化 JSON。

#### 多策略文本分块 (`_split_chunks`)

大型 PDF 可能超过 LLM 上下文窗口，需要分块处理：

| 策略 | 触发条件 | 分块方式 | 优先级 |
|------|---------|---------|--------|
| 按页标记分割 | 文本包含 `--- 第 N 页` | 按页标记拆分，合并到 ≤28000 字符 | 最高 |
| 按表边界分割 | 文本包含 `【xxx】监测` | 按表标题拆分，合并到 ≤28000 字符 | 中 |
| 字符数回退分割 | 以上策略都不适用 | 每 28000 字符切割，500 字符重叠 | 最低 |

**设计决策**：`max_chars=28000` 而非更大，是因为 LLM 需要输出同等长度的 JSON 结构化数据，总 token 数（输入+输出）需控制在模型限制内。

#### SYSTEM_PROMPT 设计

提示词包含以下关键指令：

1. **提取规则**：必须提取每一张监测数据表格，数值原样提取
2. **语义理解**：通过语义识别监测项类型（不同公司表述差异大）
3. **初始值说明**：有些表没有初始值列，此时 `initial_value` 设 `null`
4. **正负号规则**：正负号代表方向不代表大小（这是最核心的领域知识）
5. **间隔天数**：如果报告有日期范围，计算间隔天数
6. **table_unit 字段**：LLM 需识别每张表的数据单位（mm/m/kN）
7. **initial_value_reliable 字段**：LLM 需判断初始值是否可用于计算累计变化
8. **JSON 结构模板**：严格定义输出格式

#### 多块合并策略

```
第1块: 提取元数据（project_name, thresholds, summary_items）+ tables
第2..N块: 只提取 tables（如果有新的 thresholds/summary 且第1块没有，才合并）
最终: 所有块的 tables 合并为一个列表
```

#### JSON 解析容错 (`_extract_json_from_response`)

LLM 输出可能包含：
- Markdown 代码块包裹（` ```json ... ``` `）
- `<thinking>` 标签（思考过程）
- 前缀/后缀无关文字

处理流程：
1. 去除 Markdown 代码块标记
2. 去除 `<thinking>` 标签内容
3. 找到第一个 `{` 和最后一个 `}` 之间的内容
4. `json.loads()` 解析

#### 数值解析辅助函数

- `_sf(v)`: 安全浮点数转换，处理 `None`、空字符串、`"--"`、`"-"`、`"N/A"` 等
- `_sid(v)`: 安全字符串 ID 转换，将 `"None"` / `"null"` 字符串转为空字符串
- `_cat(s)`: 类别字符串到 `MonitoringCategory` 枚举的映射

#### 注意事项

- LLM 调用超时设为 300 秒（大文档解析可能需要较长时间）
- `max_tokens=32000` 确保有足够空间输出大型 JSON
- `temperature=0.1` 低温度确保输出稳定性
- 某个分块 LLM 调用失败不会中断整个流程（`continue`）

---

### 3. 动态验证配置 (table_analyzer.py)

#### 核心理念

**不硬编码容差/严重级别**，而是根据每张表的实际数据特征动态决定验证参数。

这是整个系统最关键的设计之一，解决了"一套规则无法适配所有表格"的问题。

#### `build_verification_config()` 逻辑

根据 LLM 识别的 `table_unit` 和表格类别，生成 `TableVerificationConfig`：

| 数据类型 | unit | unit_conversion | cumulative_tolerance | severity | initial_value_reliable |
|---------|------|-----------------|---------------------|----------|----------------------|
| 普通位移 (mm) | mm | 1.0 | 0.15 | error | true |
| 高程数据 (m) | m | 1000.0 | 1.0 | warning | false |
| 水位数据 | mm | 1.0 | 10.0 | warning | false |
| 锚索拉力 (kN) | kN | 1.0 | 0.15 | error | true |

#### 关于高程数据的特殊处理

**这是最容易产生误报的场景**，需要特别理解：

```
初始高程 = -2.70184 m
本次高程 = -2.70242 m
报告中的累计变化 = 31.21 mm

计算: (-2.70242 - (-2.70184)) × 1000 = -0.58 mm
但报告说累计变化是 31.21 mm！

原因: 报告中的"初始高程"是本期的参考值，不是项目建设初期的首次测量值。
31.21 mm 是从项目首测以来的总累计变化，但表中没有真正的初始值。
```

因此高程数据的累计变化量不一致只能标记为 `warning`，不能判定为 `error`。

#### 启发式检测 (`_detect_elevation_from_data`)

如果 LLM 没有正确识别 `table_unit`，用启发式方法检测高程数据：

- 初始值绝对值 < 100（看起来像高程，单位 m）
- 累计变化绝对值 > 1（看起来像 mm 级别）
- 两者量级不匹配 → 判断为高程数据

#### LLM 配置增强 (`enrich_configs_with_llm`)

对于启发式判断可能不准确的表格（计算值与报告值相差 > 100 倍），额外请求 LLM 分析：

- 只在发现异常时才调用 LLM，减少 API 调用次数
- 失败不影响主流程（`non-fatal`）

---

### 3.5 表格分析计划 (table_analyzer.py — `generate_analysis_plan`)

#### 核心理念

在验证之前，先让系统"说出"它对每张表的理解，采用 **ReAct 模式**（Thought → Observation → Action）透明展示推理过程。

这一步不调用 LLM，纯 Python 分析，基于已构建的 `TableVerificationConfig` 和表格数据特征。

#### 输出结构

对每张表生成一个分析计划 dict，包含：

| 字段 | 说明 | 示例 |
|------|------|------|
| `table_name` | 监测项 + 孔位 | "深层水平位移观测 (C1)" |
| `fields_detected` | 哪些数据列有值 | `{"initial_value": true, "change_rate": true}` |
| `data_sample` | 前2个测点的原始数据 | "S1: 初始=-2.70184, 本次=-2.70242, 累计=31.21" |
| `unit` / `conversion_note` | 单位及转换说明 | "m → mm (×1000转换)" |
| `initial_reliable` / `reliability_reason` | 初始值可靠性分析 | "高程数据: 初始高程可能非项目首测基准" |
| `interval_days` / `interval_source` | 监测间隔及来源 | "9天 (从数据反推)" |
| `verification_methods` | 将执行的验证规则列表 | 累计变化量验证、变化速率验证、统计验证 |
| `special_notes` | 特殊处理说明 | "高程数据累计变化不一致仅标warning" |

#### 路由逻辑

根据表格类型决定适用的验证规则：

```
if 锚索/支撑 → 锚索累计变化验证 + 统计验证
elif 深层位移 → 深层位移速率验证(abs比较) + 统计验证(豁免跨表引用)
else → 累计变化量验证 + 变化速率验证 + 统计验证
```

#### Streamlit 展示

在"分析计划"选项卡中，每张表以 `st.expander` 展示：
- **Thought** — 字段识别矩阵（✅/❌）
- **Observation** — 数据样本 + 单位与基准分析
- **Action** — 将执行的验证规则（含公式、容差、级别）
- **特殊说明** — 需要注意的异常处理（如有）

---

### 4. 计算验证 (calculation_checker.py)

#### 核心公式

```
本次变化 = 本次测值 − 上次测值
累计变化 = 本次测值 − 初始测值
变化速率 = 本次变化 / 时间间隔(天)
```

#### 累计变化量验证 (`check_cumulative_change`)

```python
expected = (pt.current_value - pt.initial_value) * cfg.unit_conversion
tol = cfg.cumulative_tolerance
if abs(pt.cumulative_change) > 10:
    tol = max(tol, abs(pt.cumulative_change) * 0.05)  # 大值时动态放大容差
```

- 使用 `TableVerificationConfig` 的 `unit_conversion`（高程数据需 ×1000）
- 容差动态调整：当累计变化量 > 10mm 时，容差按 5% 放大
- 严重级别由 `cfg.severity_for_cumulative` 决定

#### 变化速率验证 (`check_change_rate`)

监测间隔天数的推断策略（按优先级）：
1. `cfg.interval_days`（LLM 从报告日期范围推断）
2. `_infer_interval_days()`：从表中测点数据反推（取众数）
   - 公式：`interval = current_change / change_rate`
   - 过滤条件：`0.5 < |interval| < 365`
3. 如果都无法推断，记录 `info` 级别提示并跳过

**个别测点间隔不同的处理**：当某个测点反推出的间隔天数与多数测点不同（但本身是合理的整数天），降级为 `warning` 而非 `error`，并标注"可能该点上次监测时间不同"。

#### 深层水平位移速率验证 (`check_deep_displacement_rate`)

- 使用 `abs(expected_rate)` 和 `abs(dp.change_rate)` 比较（比较绝对值）
- 这是一个重要的 bugfix：之前直接比较带符号值导致 `_close_enough(0.030, -0.030, 0.05)` 产生大量误报

#### 锚索拉力验证 (`check_anchor_force`)

- 公式：`累计变化 = 本次内力 - 初始内力`
- 单位 kN，不需要单位转换
- 初始值可靠，严格按 error 级别

---

### 5. 统计验证 (statistics_checker.py)

#### 验证内容

每张表底部通常有统计摘要行，验证以下指标：

| 统计项 | 验证方式 | 方向性检查 |
|--------|---------|----------|
| 正方向最大 | 所有累计值中正值的最大 | 若无正值，应为 "-" |
| 负方向最大 | 所有累计值中负值绝对值最大（值最小的负数） | 若无负值，应为 "-" |
| 最大速率 | 所有速率中绝对值最大，保留原始正负号 | - |
| 最大内力 | 锚索/支撑力值的最大 | - |
| 最小内力 | 锚索/支撑力值的最小 | - |

#### 设计原则

1. **每张表独立检查**：`_get_table_own_data()` 只取当前表自身数据，不跨表聚合
2. **跨表引用检测**：如果统计引用的测点 ID 不在本表中，标记为错误
3. **深层位移表豁免跨表引用检查**：深层位移表的统计值经常引用全局最大孔位（如 `CX10`），这是行业惯例而非错误

#### 水位数据特殊容差

水位监测数据的容差放大 10 倍（`tol = FLOAT_TOLERANCE * 10`），因为水位变化受环境因素影响较大。

---

### 6. 逻辑检查 (logic_checker.py)

#### LLM 语义匹配 (`_build_semantic_maps`)

这是解决"不同公司表述不同"的核心方案。

**问题**：报告中的阈值名称、数据表名称、简报汇总名称可能完全不同：
- 阈值："坡顶水平位移及沉降" 
- 数据表："支护结构顶部水平位移" + "支护结构顶部竖向位移"
- 简报："水平位移" + "竖向位移"

**解决方案**：将三组名称发送给 LLM，请求建立对应关系。

LLM 返回 JSON：
```json
{
  "threshold_to_tables": {"坡顶水平位移及沉降": ["支护结构顶部水平位移", "支护结构顶部竖向位移"]},
  "summary_to_tables": {"水平位移": ["支护结构顶部水平位移"]}
}
```

**回退策略**：LLM 调用失败时，使用关键词组匹配（`_build_fallback_maps`），包含 7 组预定义关键词。

#### 安全状态判定 (`check_safety_status`)

```
if |累计变化| >= 控制值 → 应为"控制"
elif |累计变化| >= 报警值 → 应为"报警"  
elif |速率| >= 速率限值 → 应为"报警"
else → 应为"正常"

if 报告标"正常" but 应为"报警"/"控制" → error
if 报告标"报警" but 应为"正常" → warning（过严不算大错）
```

#### 汇总表一致性检查 (`check_summary_consistency`)

验证简报汇总表中的最大值/最小值是否与对应分表的数据一致：

- 锚索拉力：比较汇总中的力值与分表中的 `current_value` 最大/最小值
- 其他监测项：比较汇总中的正/负方向最大与分表中的 `cumulative_change` 最大/最小值
- 容差：一般用 `FLOAT_TOLERANCE`，锚索拉力用 `FLOAT_TOLERANCE * 2`

#### 测点数量检查 (`check_point_count`)

比较表头声明的测点数量与实际提取的行数，不一致则标记为 `warning`。

---

### 7. AI 自验证 (self_verifier.py)

#### 核心思路：Two-LLM Verification Pattern

第一个 LLM 提取数据 → 规则引擎检查 → **第二个 LLM 确认错误**

这大幅减少了由于数据提取不准确或规则过于严格导致的误报。

#### 工作流程

1. 筛选所有 `severity="error"` 的问题（最多 20 个）
2. 对每个错误，从原始文本中截取相关表格片段（~2000 字符上下文）
3. 组装 prompt，要求 LLM 逐一确认：
   - `confirm`：错误确实存在 → 保持 error
   - `downgrade`：不确定 → 降级为 warning
   - `dismiss`：是误报 → 降级为 info
4. 提供领域知识提示（正负号规则、高程精度、水位基准）

#### 关键设计

- `MAX_ERRORS_TO_VERIFY = 20`：限制最大验证数量，避免 token 过多
- 使用 `id()` 做对象身份匹配，确保修改的是原始 CheckIssue 对象
- 失败时 non-fatal，返回原始错误列表

---

### 8. 报告生成 (report_generator.py)

#### Markdown 报告结构

```markdown
# 建筑变形监测报告检查报告
- 生成时间、项目名称、监测单位、报告编号、监测日期
## 检查结果统计 (错误/警告/提示表格)
## 数据提取摘要 (阈值/汇总/数据表列表)
## 计算验证结果 (错误/警告/提示分组)
## 统计验证结果
## 逻辑检查结果
## AI 专家补充审核 (可选)
## 结论
```

#### 多格式导出 (app.py)

| 格式 | 实现 | 用途 |
|------|------|------|
| **Markdown** | 原生字符串 | 开发者查看、版本控制 |
| **Word (docx)** | `python-docx` 生成 | 正式文档提交 |
| **HTML** | `markdown` 库转换 + CSS | 浏览器打印为 PDF |

Word 导出包含：标题居中、基本信息段落、结果统计表格、红色错误/橙色警告详情、结论。

HTML 导出包含：微软雅黑字体、响应式布局、打印优化（`@media print`）。

---

## 数据模型

### 核心数据类 (data_models.py)

```
MonitoringReport（完整报告）
├── project_name, monitoring_company, report_number, ...
├── thresholds: List[ThresholdConfig]        # 报警/控制值配置
├── summary_items: List[ReportSummaryItem]   # 简报汇总表
├── tables: List[MonitoringTable]            # 监测数据表
├── threshold_map: dict                       # LLM语义匹配缓存
├── summary_map: dict                         # LLM语义匹配缓存
└── raw_text: str                             # 原始PDF文本

MonitoringTable（单张数据表）
├── monitoring_item, category, monitor_date, ...
├── borehole_id, borehole_depth              # 深层位移孔位信息
├── points: List[MeasurementPoint]           # 普通测点
├── deep_points: List[DeepDisplacementPoint] # 深层位移测点
├── statistics: StatisticsSummary            # 表底统计
└── verification_config: TableVerificationConfig  # 动态验证配置

MeasurementPoint（普通测点）
├── point_id, initial_value, previous_value, current_value
├── current_change, cumulative_change, change_rate
└── safety_status

DeepDisplacementPoint（深层位移测点）
├── depth
├── previous_cumulative, current_cumulative
└── change_rate

TableVerificationConfig（动态验证配置）
├── unit (mm/m/kN), unit_conversion (1.0/1000.0)
├── cumulative_tolerance, rate_tolerance
├── interval_days, direction_convention
├── initial_value_reliable (true/false)
└── severity_for_cumulative (error/warning)

CheckIssue（检查问题）
├── severity (error/warning/info)
├── table_name, point_id, field_name
├── expected_value, actual_value
└── message
```

---

## 核心计算公式与正负号规则

### 计算公式

| 指标 | 公式 | 说明 |
|------|------|------|
| 本次变化 | `本次测值 − 上次测值` | 两次监测之间的变化量 |
| 累计变化 | `本次测值 − 初始测值` | 从项目首测以来的总变化 |
| 变化速率 | `本次变化 / 时间间隔(天)` | 每天的变化量 |

### 正负号规则（极其重要）

> **正负号代表方向，不代表大小！**

在建筑变形监测中：
- **正值**：通常表示向外/向上/拉力增大
- **负值**：通常表示向内/向下/拉力减小

因此：
- "正方向最大" = 所有正值中数值最大的（如 +5.2 > +3.1）
- "负方向最大" = 所有负值中绝对值最大的（如 -8.3 的绝对值 > -2.1 的绝对值）
- "最大变化速率" = 所有速率中绝对值最大的，但保留原始正负号

### 单位转换

| 数据类型 | 测值单位 | 变化量单位 | 转换 |
|---------|---------|-----------|------|
| 水平位移 | mm | mm | ×1 |
| 高程/沉降 | m | mm | ×1000 |
| 锚索拉力 | kN | kN | ×1 |
| 水位 | m | mm 或 m | 视报告而定 |

---

## 支持的监测项

| 监测项 | MonitoringCategory | 常见别名 | 特殊处理 |
|--------|-------------------|---------|---------|
| 水平位移 | HORIZONTAL_DISP | 支护结构顶部水平位移、基坑顶位移、坡顶水平位移 | - |
| 竖向位移 | VERTICAL_DISP | 支护结构顶部竖向位移、基坑顶沉降 | 高程数据检测 |
| 沉降 | SETTLEMENT | 周边地面沉降、道路沉降、管线沉降 | 高程数据检测 |
| 水位 | WATER_LEVEL | 地下水位、水位监测 | 放宽容差、初始值不可靠 |
| 锚索拉力 | ANCHOR_FORCE | 锚索应力、预应力锚索 | kN 单位、初始值可靠 |
| 支撑轴力 | STRUT_FORCE | 内支撑、钢支撑 | 同锚索拉力 |
| 深层水平位移 | DEEP_HORIZONTAL | 支护桩测斜、测斜 | 特殊数据结构 |
| 裂缝 | CRACK | 裂缝监测 | 目前仅分类，未特殊处理 |

---

## 检查规则详解

### 计算验证规则

| # | 规则 | 公式 | 容差 | 严重级别 |
|---|------|------|------|---------|
| 1 | 累计变化量 | `(本次测值 - 初始测值) × unit_conversion` | 动态(0.15~1.0mm) | 动态(error/warning) |
| 2 | 变化速率 | `本次变化量 / 间隔天数` | 0.05 mm/d | error |
| 3 | 深层位移速率 | `(本次累计 - 上次累计) / 间隔天数` | 0.05 mm/d | error |
| 4 | 锚索累计变化 | `本次内力 - 初始内力` | 0.15 kN | error |

### 统计验证规则

| # | 规则 | 说明 |
|---|------|------|
| 5 | 正方向最大统计 | 应为所有正累计值中的最大值 |
| 6 | 负方向最大统计 | 应为所有负累计值中绝对值最大的 |
| 7 | 方向性检查 | 若所有值非正，正方向统计应为 "-" |
| 8 | 最大速率统计 | 应为所有速率绝对值最大的 |
| 9 | 跨表引用检测 | 统计引用的测点必须在本表中 |

### 逻辑检查规则

| # | 规则 | 说明 |
|---|------|------|
| 10 | 安全状态判定 | 根据报警值/控制值验证安全状态标记 |
| 11 | 汇总一致性 | 简报汇总表与分表数据应一致 |
| 12 | 测点数量 | 表头声明的数量与实际行数应一致 |

---

## 关键技术决策与注意事项

### 1. 为什么用 LLM 而不是正则提取表格？

- 不同公司的表格格式差异极大（列名、列顺序、数据排布都不同）
- 正则/硬编码需要为每家公司写一套规则，维护成本极高
- LLM 能通过语义理解自动适配不同格式

### 2. 为什么需要 Two-LLM Verification Pattern？

- 第一个 LLM 提取数据时可能出错（如把"备注"误认为"测值"）
- 规则引擎检查基于提取的数据，如果提取有误则会产生"真诚的误报"
- 第二个 LLM 结合原文上下文重新审视错误，能有效排除误报

### 3. 为什么累计变化量不能简单用 `本次 - 初始` 计算？

- **高程数据精度问题**：高程只有 5 位小数（精度 0.01mm），经过几十期累积，误差可能达到数 mm
- **初始值基准不同**：部分报告中的"初始值"是本期参考值，不是项目首次测量值
- **水位特殊性**：水位的初始基准可能因施工阶段而改变

### 4. 为什么深层位移速率比较用绝对值？

之前 `_close_enough(expected_rate, dp.change_rate, tol)` 直接比较带符号值：
- `expected = 0.030`（正向），`actual = -0.030`（报告用负号表示方向）
- `|0.030 - (-0.030)| = 0.060 > 0.05`，误判为错误

修复后：`_close_enough(abs(expected), abs(actual), tol)` 只比较大小，忽略方向。

### 5. 为什么跳过深层位移表的跨表引用检查？

深层位移通常有多个孔位（如 CX1~CX15），每个孔位一张表。表底统计的"最大"可能引用全局最大的孔位（如"CX10"），这是跨所有孔位的全局统计，不是本表的。

### 6. PaddleOCR 优先级

优先使用 PaddleOCR 而非其他 OCR 方案的原因：
- 支持版式分析（layout parsing），能识别表格结构
- 输出 Markdown 格式，保留表格结构信息
- 对中文识别效果好
- API 调用方式简单

### 7. LLM 调用的容错设计

- 所有 LLM 调用都有 try-except 包裹
- 超时设置：解析 300s、语义匹配 60s、自验证 120s
- 失败后有回退策略（如语义匹配回退到关键词匹配）
- 非核心的 LLM 调用（配置增强、自验证）失败不影响主流程

### 8. Streamlit 日志捕获

通过自定义 `StreamlitLogHandler` 类继承 `logging.Handler`，将所有模块的日志记录收集到内存列表 `log_records` 中，在"运行日志"选项卡中显示：

```python
class StreamlitLogHandler(logging.Handler):
    def emit(self, record):
        log_records.append(self.format(record))
```

---

## 已知问题与踩坑记录

### 1. LLM 输出 "None" 字符串

**现象**：LLM 有时将空值输出为字符串 `"None"` 而非 `null`

**影响**：`stats.max_rate_id = "None"` 被当作有效测点ID，导致跨表引用检查误报

**修复**：`_sid()` 函数将 `"None"` / `"null"` 转为空字符串

### 2. 深层位移速率大量误报（177 个）

**现象**：红土广场 PDF 检出 177 个深层位移速率错误

**原因**：直接比较带符号的 `expected_rate` 和 `dp.change_rate`，正负号方向不同导致差值翻倍

**修复**：比较绝对值 `_close_enough(abs(expected), abs(actual), tol)`

### 3. 深层位移统计跨表引用误报（82 个）

**现象**：红土广场 PDF 深层位移表的统计值引用了其他表的测点

**原因**：行业惯例，深层位移的"全局最大"引用跨孔位的数据

**修复**：`is_deep` 时跳过 `_check_cross_table_ref`

### 4. 大文档 LLM 超时

**现象**：恒大中心 138K 字符 PDF 在 LLM 解析阶段超时

**修复**：改进文本分块策略（3 种策略），增大单块上限到 28K 字符，增大 `max_tokens` 到 32K

### 5. config.py LLM_BASE_URL 环境变量名错误

**现象**：`LLM_BASE_URL` 的 `os.getenv` 第一个参数误写为 `"LLM_API_KEY"`，导致如果设置了 `LLM_API_KEY` 环境变量，`LLM_BASE_URL` 会被覆盖为 API Key 的值

**修复**：改为正确的 `os.getenv("LLM_BASE_URL", ...)`

### 6. PowerShell heredoc 语法不兼容

**现象**：Git commit 时使用 heredoc 语法（`<<'EOF'`）在 PowerShell 中报错

**修复**：使用单行 commit message 或 `-m` 参数

---

## 待改进方向

### 高优先级

1. **本地 OCR 支持**：当前 PaddleOCR 依赖远程 API，应支持本地部署的 PaddleOCR/PP-Structure，避免网络依赖和 Token 过期问题
2. **LLM 提取准确率提升**：对于复杂的合并单元格或多页表格，LLM 有时会遗漏数据行或错误对齐列。可考虑：
   - 先用 pdfplumber 提取结构化表格，再让 LLM 理解语义
   - 使用多轮对话让 LLM 自检提取结果
3. **批量处理能力**：支持一次上传多个 PDF，批量检查并汇总结果
4. **持久化存储**：当前检查结果只保存为文件，应支持数据库存储，方便历史对比
5. **单元测试覆盖**：当前缺少自动化测试，应为每个 checker 编写单元测试

### 中优先级

6. **增量验证**：对比同一项目的连续期报告，验证"上次测值"是否与前期报告的"本次测值"一致
7. **可配置规则引擎**：允许用户通过 YAML/JSON 自定义验证规则、容差、严重级别
8. **图表验证**：验证报告中的监测曲线图与数据表是否一致
9. **模板学习**：从已验证正确的报告中学习特定公司的格式，提高后续提取准确率
10. **多模型支持**：允许切换不同 LLM（如 GPT-4、Claude），或使用本地模型降低成本

### 低优先级

11. **PDF 批注输出**：直接在原 PDF 上标注发现的错误位置
12. **多语言支持**：目前仅支持中文报告
13. **Web API 化**：提供 REST API 接口，方便集成到其他系统
14. **权限管理**：多用户场景下的权限控制
15. **缓存机制**：对相同 PDF 的重复检查使用缓存，避免重复 LLM 调用

### 性能优化

16. **并行 LLM 调用**：多个分块可以并行发送给 LLM（当前是串行）
17. **流式输出**：LLM 解析过程中流式返回进度，提升用户体验
18. **内存优化**：大 PDF 的 raw_text 可以不全部保存在 MonitoringReport 中

---

## 测试记录

### 测试用 PDF

| PDF 文件 | 页数 | 特点 | 已测试 |
|---------|------|------|--------|
| 监测报告检查（测试）.pdf | 19 | 文字版，含人工植入的错误，有对比基准（docx） | ✅ |
| 恒大中心基坑支护工程地铁监测报告第209期 | ~60 | 大文档(138K字符)，需要分块处理 | ✅ |
| 红土创新广场项目基坑监测报告第133期 | ~50 | 大量深层位移表，曾触发大量误报 | ✅ |

### 最近一次测试结果（监测报告检查（测试）.pdf）

```
项目名称: 智能科技创新中心
数据表: 12 张
阈值: 11 项
汇总: 7 项

检查结果:
- 错误: 8 个
- 警告: 26 个
- 提示: 0 个

发现的错误:
1. [竖向位移] S7 负方向最大统计：所有累计值均为非负值，但报告有负方向最大
2. [周边地面沉降] D2 正方向最大统计：所有累计值均为非正值，但报告有正方向最大
3. [周边地面沉降] D5 最大速率统计：实际最大速率测点为 D5，但报告写 D2
4. [管线沉降] G2 正方向最大统计：同上
5. [管线沉降] G5 最大速率统计：同上
6-8. [深层位移] C1/C11/C5 负方向最大统计：方向性统计不一致
```

### OCR 功能测试

| 测试项 | 结果 | 说明 |
|--------|------|------|
| pdfplumber 提取文字版 PDF | ✅ 通过 | 9985 字符，提取完整 |
| PaddleOCR API 调用 | ✅ 通过 | 19 页，97531 字符 |
| 智能质量评估（文字版） | ✅ 通过 | 正确判定为高质量 |
| 智能切换（文字版不切换） | ✅ 通过 | 保持 pdfplumber 结果 |
| LLM 超时容错 | ✅ 通过 | 配置增强/自验证超时不影响主流程 |

---

## 技术栈

| 类别 | 技术 | 版本 | 用途 |
|------|------|------|------|
| LLM | DashScope (qwen3.5-plus) | OpenAI 兼容协议 | 语义理解、数据提取、语义匹配、自验证 |
| PDF 解析 | pdfplumber | ≥0.11.0 | 文字版 PDF 文本提取 |
| OCR | PaddleOCR (API) | 在线服务 | 扫描件版式分析 |
| Web 框架 | Streamlit | ≥1.30.0 | Web 可视化界面 |
| 文档生成 | python-docx | ≥1.0.0 | Word 文档导出 |
| Markdown | markdown | ≥3.5.0 | Markdown 转 HTML |
| HTTP 客户端 | requests | ≥2.31.0 | PaddleOCR API 调用 |
| LLM 客户端 | openai | ≥1.0.0 | DashScope API 调用 |

---

## 许可证

本项目为内部工具，仅供学习和使用。
