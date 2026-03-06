# 建筑变形监测报告检查智能体

自动检查建筑变形监测报告中的计算结果、统计数据和逻辑关系。

## 功能

- **PDF数据提取**: 支持文字版PDF(pdfplumber)和扫描件(PaddleOCR)
- **AI语义理解**: 通过LLM理解不同公司的表格格式，自动标准化
- **计算验证**: 逐条验证累计变化量、变化速率
- **统计验证**: 验证最大值/最小值/最大速率统计
- **逻辑检查**: 安全状态判定、汇总表与分表一致性
- **报告生成**: 生成Markdown格式检查报告

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
