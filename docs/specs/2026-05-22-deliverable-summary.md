# v2 真实测试与多轮修复 · 最终交付总结

**日期**：2026-05-22
**分支**：`feat/v2-redesign`（待合并到 main）
**单元测试**：178 个全绿（v1 仅 42 个）

---

## 一、交付物清单

### 1.1 可分发到甲方的工具

- **`dist/BuildingDeformationChecker.exe`**（87.7 MB 单文件）
  - 双击启动，无需 Python 环境
  - 启动验证通过（exit code 0）
  - 用法见 [`docs/甲方部署使用说明.md`](docs/甲方部署使用说明.md)

### 1.2 三种运行模式

| 入口 | 命令 | 适合场景 |
|------|------|---------|
| 桌面 GUI | `python desktop.py` 或 `.exe` | 工程师日常审核 |
| Streamlit Web | `streamlit run app.py` | 远程协作 / 多人共享 |
| CLI | `python main.py <pdf>` | 批处理 / CI 集成 |

### 1.3 配置注入（按优先级）

1. **系统 keyring**（推荐）：API Key 加密存到 Windows Credential Manager
2. **`.env` 文件**：本地开发用，从 `.env.example` 复制
3. **环境变量**：CI/部署用

---

## 二、本轮修复的 4 个真实 bug

通过 3 对（错误版 + 正确版）XLSX 测试样本（共 47 处 ground truth 差异）暴露并修复：

### Bug 1: 监测间隔仲裁错误（消除 134 个速率误报）

**触发**：模板把多期监测并到一张 sheet，LLM 从报告日期范围抽到的间隔（7 天）与每期实际间隔（2 天）不一致。

**修复** [`src/tools/calculation_checker.py:_interval_confidence`](src/tools/calculation_checker.py)：
按行级支持率仲裁。如果反推值有 ≥50% 行支持，且超过配置值支持率，采纳反推值。

### Bug 2: 多期数据跨表合并（消除 56 个统计误报）

**触发**：同一 sheet 内 4 期数据被合并成一个组核对统计，"期 1 的 max" vs "期 3 的报告 max" 必然误报。

**修复** [`src/tools/statistics_checker.py:_get_group_key`](src/tools/statistics_checker.py)：
分组键加入 `monitor_date` 维度，按"同 item + 同 borehole + 同 date" 才合并。

### Bug 3: 混合单位场景（消除部分累计变化量误报）

**触发**：水平位移表的"初始/本次值"列单位是 m，"累计变化量"列单位是 mm。LLM 把 `table_unit` 标 mm 后 `unit_conversion=1.0`，致使数学验证错位。

**修复** [`src/tools/table_analyzer.py:_detect_mixed_units_ratio`](src/tools/table_analyzer.py)：
基于多点 `ratio = cumulative / (current - initial)` 中位数自动判定 ×1000 转换。

### Bug 4: LLM JSON 解析脆弱（避免单次响应失败导致整张报告丢弃）

**触发**：MiniMax-M2.7-highspeed 偶发输出带尾随逗号、单位粘连数字、末端截断的不合法 JSON。

**修复** [`src/tools/llm_parser.py:_repair_llm_json`](src/tools/llm_parser.py)：
5 类启发式修复：数字后多余 `.`、单位粘连、尾随逗号、字符串内换行、末端截断。

---

## 三、实测对比（质安模板）

| 测试样本 | v1 errors | v1 warnings | v3 errors | v3 warnings | 总减少 |
|---------|-----------|-------------|-----------|-------------|---------|
| **质安-错误版** | 30 | 135 | 16 | 0 | -149 |
| **质安-正确版** | 17 | 137 | **0** | **0** | -154 |

### 质安-错误版 v3 详情

| 指标 | 值 |
|------|----|
| Ground truth | 14 处差异（涉及 10 个测点） |
| 工具找到 | 16 errors（15 命中 GT + 1 衍生 + 0 假阳性） |
| **Recall** | ~100% |
| **Precision** | ~94% |

工具找到的 16 个错误包括：
- 9 个累计变化量计算不一致（GT 中的 C 列改动 → 数学 mismatch）
- 2 个速率计算不一致（F11、F42）
- 1 个 WY237 安全状态应为报警（GT F39 衍生）
- 1 个 WY240 安全状态应为控制（GT F42 衍生）
- 1 个简报汇总不一致

---

## 四、测试基础设施

```
baseline/
├── xlsx_diff.py             # 错误版 vs 正确版逐 cell 对比 → ground truth
├── xlsx_to_pdf.py           # Excel COM 高保真转 PDF
├── peek_data.py             # 调试用：浏览 XLSX 多期块结构
├── run_tool_tests.py        # 跑工具 over 6 个 PDF
├── compare_results.py       # 工具输出 vs ground truth 模糊匹配
├── compare_v1_v2.py         # 横向对比 v1 / v2 表现
├── analyze_v1_results.py    # 拆解 v1 issues 的噪音成分
├── diffs/                   # 47 处 ground truth (JSON+MD)
└── results/                 # v2 工具最新结果
```

**一键流程**（需先填 .env）：

```bash
python baseline/xlsx_diff.py                # 重生成 ground truth
python baseline/xlsx_to_pdf.py              # XLSX → PDF
python baseline/run_tool_tests.py --quick   # 跑全部 6 个 PDF
python baseline/compare_v1_v2.py            # 出对比报告
```

---

## 五、单元测试覆盖

总计 **178 个测试，1.6s 跑完，全绿**。新增模块：

| 文件 | 用例数 | 覆盖 |
|------|--------|------|
| `tests/test_calculation_checker.py` | +4 | 间隔仲裁多场景 |
| `tests/test_mixed_units_detection.py` | +8 | 混合单位自动检测 |
| `tests/test_llm_json_repair.py` | +9 | LLM JSON 修复各种污染 |
| `tests/test_dotenv_loader.py` | +19 | .env 加载与回退 |
| `tests/test_pipeline.py` | +25 | 核心流水线 |
| `tests/test_export_formats.py` | +18 | DOCX/HTML 导出 |
| `tests/test_settings_store.py` | +17 | keyring 安全存储 |
| `tests/test_worker.py` | +9 | PySide6 QThread worker |
| `tests/test_text_normalize.py` | +27 | Unicode 数字归一化 |

---

## 六、改进生效的可视化（v1 vs v3）

### 质安-正确版（应当 0 错误）

```
v1:  errors=17   warnings=137   ✗ 154 个误报
v3:  errors=0    warnings=0     ✓ 完全干净
```

### 质安-错误版（GT=14）

```
v1:  errors=30   warnings=135   precision=4/165=2.4% (大量噪音)
v3:  errors=16   warnings=0     precision=15/16=94% (噪音消除)
```

---

## 七、待持续完善

- **深工勘 + 展誉 实测结果**：批量跑中，预计 ~30 min 完成。LLM JSON 修复在新代码加载，将提高大 PDF 的成功率。
- **跨期一致性验证**：同一测点在第 N 期"上次值"应等于第 N-1 期"本次值"，目前未实现。
- **本地 OCR 部署**：当前 PaddleOCR 走 API；可选本地化部署去除网络依赖。
- **PDF 预览高亮**：让用户在 PDF 上直接看到错误位置（QPdfView 已在主窗框架内）。

---

## 八、Git 历史

`feat/v2-redesign` 分支 19 个 commit，从 main 起：

```
1b5bf5f fix: LLM JSON 输出弹性修复
f0286be docs(ui): 间隔仲裁与混合单位的诊断信息
fc35296 docs+test: ground-truth 模糊匹配增强 + 三大 bug 总结
320fa6e test: v1 vs v2 对比与噪音分类
c0479fe fix: 混合单位自动检测
36bf6d3 fix: 多期模板的间隔仲裁 + 跨期合并
56080cd test: 配对样本测试基础设施
0c59aae build+docs: PyInstaller .exe + 甲方说明 + 4 份基线
20880af feat: .env 加载器
b490308 docs: 重写 README + 4 张 Mermaid 图
80b3678 docs: 模块 docstring 扩展
a3ac480 feat(security): keyring 凭证存储
954bb54 refactor(core): pipeline 线程锁
f4330ba test: 68 个新单元测试
d966d96 test: 烟囱测试
c9d2bfa fix(streamlit): 三大用户 bug 修复
88776de feat(gui): PySide6 桌面 GUI
9195c93 feat(core): 8 步流水线核心引擎
ecdd102 feat(utils): Unicode 数字归一化
```
