# 建筑变形监测报告检查智能体

自动检查建筑变形监测报告中的计算结果、统计数据和逻辑关系。

## 功能

- **PDF数据提取**: 支持文字版PDF(pdfplumber)和扫描件(PaddleOCR)
- **AI语义理解**: 通过LLM理解不同公司的表格格式，自动标准化
- **计算验证**: 逐条验证累计变化量、变化速率
- **统计验证**: 验证最大值/最小值/最大速率统计
  - 方向性检查：当所有值单方向时，另一方向应为"-"
  - 跨表引用检查：每张表统计值需对应本表数据
- **逻辑检查**: 安全状态判定、汇总表与分表一致性
- **报告生成**: 生成Markdown格式检查报告
- **Streamlit界面**: Web可视化交互

## 支持的监测项

| 监测项 | 常见别名 |
|--------|---------|
| 水平位移 | 支护结构顶部水平位移、基坑顶位移、坡顶水平位移 |
| 竖向位移 | 支护结构顶部竖向位移、基坑顶沉降 |
| 沉降 | 周边地面沉降、道路沉降、管线沉降 |
| 水位 | 地下水位、水位监测 |
| 锚索拉力 | 锚索应力、支撑轴力 |
| 深层水平位移 | 支护桩测斜、测斜 |

## 安装

```bash
pip install -r requirements.txt
```

## 使用

### Streamlit Web界面

```bash
streamlit run app.py
```

### 命令行

```bash
python main.py "监测报告.pdf"
python main.py "扫描件.pdf" --ocr
python main.py "报告.pdf" --no-ai-review
```

## 核心计算公式

- **本次变化** = 本次测值 − 上次测值
- **累计变化** = 本次测值 − 初始测值
- **变化速率** = 本次变化 / 时间(天)

> 注意：正负号代表**方向**，不代表大小。

## 检查规则

1. **累计变化量** = 本次测值 - 初始测值（高程类有精度容差）
2. **变化速率** = 本次变化量 / 监测间隔天数（自动推断间隔）
3. **正方向最大统计**: 所有累计值中正值的最大值；若无正值应为"-"
4. **负方向最大统计**: 所有累计值中负值绝对值最大的；若无负值应为"-"
5. **最大速率统计**: 所有速率中绝对值最大的
6. **跨表引用**: 每张表的统计值必须引用本表中的测点
7. **安全状态**: 根据报警/控制阈值判断
8. **汇总一致性**: 简报汇总表与详细数据表一致

## 项目结构

```
├── app.py                    # Streamlit Web UI
├── main.py                   # CLI入口
├── requirements.txt
├── src/
│   ├── config.py             # 配置(LLM/OCR/容差)
│   ├── models/
│   │   └── data_models.py    # 数据模型
│   └── tools/
│       ├── pdf_extractor.py  # PDF提取
│       ├── llm_parser.py     # LLM结构化解析
│       ├── calculation_checker.py  # 计算验证
│       ├── statistics_checker.py   # 统计验证
│       ├── logic_checker.py        # 逻辑检查
│       └── report_generator.py     # 报告生成
└── output/                   # 检查报告输出
```

## 配置

设置环境变量或修改 `src/config.py`:

| 变量 | 说明 | 默认值 |
|------|------|--------|
| LLM_API_KEY | DashScope API Key | (内置) |
| LLM_BASE_URL | API基础URL | https://coding.dashscope.aliyuncs.com/v1 |
| LLM_MODEL | 模型名称 | qwen3.5-plus |
