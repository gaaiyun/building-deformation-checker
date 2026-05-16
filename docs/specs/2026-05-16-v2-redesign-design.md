# 建筑变形监测 Agent v2 重构设计

**日期**: 2026-05-16
**目标**: 在不破坏现有 8 步核查流水线的前提下，彻底解决 Streamlit 的状态丢失问题，并新增稳定的 Windows 原生 GUI。

---

## 1. 现状诊断（来自 5 路并行调研）

### 1.1 现有架构评价
- **8 步流水线设计正确**（提取 → LLM 解析 → ReAct 分析计划 → 计算/统计/逻辑核查 → Two-LLM 自验证 → 报告生成）
- 与领域行业最佳实践对齐（GB 50497-2019 / JGJ 8-2016）
- 双 LLM 误报治理模式、动态容差、单位转换链等都设计合理

### 1.2 现存 Bug 根因
**Streamlit 3 个用户反馈 bug 全部来自同一架构缺陷**：

| Bug 现象 | 根因 |
|---------|------|
| 切换浏览器 tab 中断运行 | Streamlit WebSocket 重连触发 full rerun，`ScriptRunner` 线程被取消 |
| 下载 HTML 后无法继续 | 所有结果作为局部变量存在 `if st.button` 块内，rerun 即丢失 |
| 24 分钟长任务被杀 | 整个流水线在主脚本线程同步执行，rerun 必杀 |

**根本原因**：`app.py` 全文 1300 行**完全没有使用 `st.session_state`**。

### 1.3 PDF 技术栈评价
- 现有 pdfplumber + PaddleOCR-VL 选型基本正确，**不需要全推翻**
- 缺失：PyMuPDF 文本层快速路由（80% 页面应跳过 OCR）
- 缺失：Unicode 数字归一化（U+2212 minus、U+FF11 全角数字等是 #1 静默 bug 源）

---

## 2. v2 设计

### 2.1 架构总图

```
┌─────────────────────────────────────────────────────────┐
│  UI 层（多套，可选）                                       │
│  ┌──────────────────┐  ┌──────────────────┐              │
│  │ PySide6 桌面 GUI  │  │ Streamlit Web UI │              │
│  │ (主推，稳定)       │  │ (修复 session)   │              │
│  └────────┬─────────┘  └────────┬─────────┘              │
│           │ progress callback   │                         │
└───────────┼─────────────────────┼─────────────────────────┘
            ▼                     ▼
┌─────────────────────────────────────────────────────────┐
│  核心引擎（UI 无关，可被任意 UI 调用）                       │
│  ┌─────────────────────────────────────────────────────┐│
│  │ src/core/pipeline.py - 8 步流水线编排器              ││
│  │   - 接收 PDF 路径 + 配置 + 进度回调                   ││
│  │   - 返回 PipelineResult (含所有中间产物)              ││
│  └─────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │ src/utils/text_normalize.py - Unicode 归一化         ││
│  │   - U+2212 → -, 全角数字 → ASCII, 千分位 → 标准      ││
│  └─────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │ src/tools/pdf_extractor.py - 已有，加 PyMuPDF 路由   ││
│  │   - 新增 _detect_text_layer_quality() 启发式判断     ││
│  │   - 文本层质量足够 → 跳过 OCR                         ││
│  └─────────────────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────────────────┐│
│  │ src/tools/*.py - 已有计算/统计/逻辑/自验证（不动）     ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

### 2.2 模块拆分

| 模块 | 路径 | 职责 | 状态 |
|------|------|------|------|
| 核心流水线 | `src/core/pipeline.py` | 8 步编排，UI 无关 | **新增** |
| 流水线结果 | `src/core/pipeline_result.py` | 数据封装类 | **新增** |
| 文本归一化 | `src/utils/text_normalize.py` | Unicode 修复 | **新增** |
| 桌面 GUI | `gui_desktop/main_window.py` | PySide6 主窗 | **新增** |
| 桌面 Worker | `gui_desktop/worker.py` | QThread 后台任务 | **新增** |
| 桌面入口 | `desktop.py` | 启动脚本 | **新增** |
| Streamlit | `app.py` | 重构以使用 session_state | **改造** |
| PDF 提取 | `src/tools/pdf_extractor.py` | 加 PyMuPDF 路由 | **改造** |
| 其他工具 | `src/tools/*` | 不动 | 保留 |

### 2.3 关键修复点

#### A. Streamlit session_state 重构
- **机制**：用任务 UUID 跟踪运行；后台 `threading.Thread` 跑流水线；UI 用 `@st.fragment(run_every="1s")` 轮询；结果存 `st.session_state[uuid]`
- **效果**：tab 切换、下载、重连都不丢状态

#### B. PySide6 桌面 GUI
- **机制**：`QThread` worker 执行流水线，通过 `pyqtSignal(step, progress, message)` 上报；主线程更新 `QProgressBar` 和 `QTextEdit`
- **PDF 预览**：`QPdfView` + `QPdfDocument`（Qt 6.5+ 内置）
- **结果展示**：`QTabWidget` × 8 tab，分别对应 8 步结果
- **导出**：`QFileDialog.getSaveFileName` 原生对话框

#### C. Unicode 归一化
位置：在 `pdf_extractor` 输出 `clean_text` 之前调用
```python
def normalize_numeric_text(text: str) -> str:
    # U+2212 (math minus), U+FF0D (full-width), U+2010..2015 (dashes) → ASCII '-'
    # U+FF11..FF19 (full-width digits) → ASCII
    # U+FF0E (full-width period) → '.'
    # 千分位 1,234.56 保留（pandas/float 可处理）
    # 注意：不能误伤化学式 CO₂ 等下标
```

#### D. PyMuPDF 文本层路由
- 在 `pdf_extractor.extract_pdf()` 内：先用 PyMuPDF 检测每页 text-layer 字符数 + 表格 bbox
- 字符密度 > 阈值 且 没有 image-only 标志 → 用 pdfplumber 提取
- 否则 → 走 PaddleOCR 路径

---

## 3. 实现优先级

| 优先级 | 任务 | 难度 | 预估 | 是否需 API key |
|-------|------|------|------|---------------|
| P0 | 核心引擎 + Unicode 归一化 | 中 | 2h | 否 |
| P0 | Streamlit session_state 重构 | 中 | 2h | 否（本地测试） |
| P0 | PySide6 桌面 GUI（含 QThread） | 高 | 3-4h | 否 |
| P1 | PyMuPDF 文本层路由 | 低 | 1h | 否 |
| P2 | 端到端 LLM + OCR 测试 | - | 1h | **是** |
| P2 | PyInstaller 打包 .exe | 中 | 1-2h | 否 |

---

## 4. 兼容性保证

- 所有现有测试用例必须继续通过（`tests/` 目录的 8 个文件）
- 原 `main.py` CLI 入口保留并复用新的 `core/pipeline.py`
- 原 `app.py` 不删除，重构而非重写（保留所有 UI 逻辑，加 session_state 包装）
- 5 个样本 PDF 的 OCR 缓存继续可用，确保离线回归

---

## 5. 验收标准

1. **Streamlit bug 修复**:
   - 运行中切换浏览器 tab → 回来仍显示当前进度
   - 完成后下载 Markdown → 仍可下载 Word/HTML
   - 完成后上传新 PDF → 不需要刷新页面
2. **桌面版可用**:
   - `python desktop.py` 启动原生窗口
   - 可拖拽/选择 PDF
   - 实时显示 8 步进度条 + 日志
   - 24 分钟长任务不被任何 UI 操作打断
   - 可保存 MD/DOCX/HTML 三格式
3. **核心引擎**:
   - Unicode 归一化单元测试覆盖：U+2212、全角数字、混合符号
   - PyMuPDF 路由对鱼珠乐天 PDF 走文本层（不再调 OCR）
4. **回归测试**: 5 个样本 PDF 离线（用缓存 OCR）跑通

---

## 6. 风险与降级

| 风险 | 应对 |
|------|------|
| PySide6 在 Win11 + Python 3.13 兼容性 | 已确认支持（Qt 6.9）；若失败降级到 NiceGUI 3.0 |
| 用户未设置 API key | 桌面 GUI 启动时提示设置；缓存路径可离线测核查规则 |
| PyMuPDF 文本层判断误判 | 保留 `auto_fallback=True`，质量差自动回退 OCR |
| Streamlit fragment 轮询 1s 性能 | 调整为 2s；纯 UI 渲染开销忽略不计 |

---

## 7. 不做的事（YAGNI）

- 不替换 PaddleOCR（现有 API 调用已够用，本地化部署留待将来）
- 不引入 MinerU/TATR/marker（避免巨型依赖；现状 PDF 80% 准确率已经够）
- 不做多文件批处理（单文件场景为主）
- 不做数据库持久化（文件输出已满足）
- 不重写 LLM 客户端（已稳定）
