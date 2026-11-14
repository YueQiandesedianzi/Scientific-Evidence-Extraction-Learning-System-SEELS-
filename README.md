# SEELS — Scientific Evidence Extraction & Learning System (MVP)

面向学术论文 PDF 的**证据抽取与学习系统**：高保真解析 → 文本/表格/曲线数据抽取 → 数据与 PDF 原文证据强关联 → 人工复核与论文伴读 → 标准化数据库与 CSV/Excel 导出。

设计目标不是完全无人值守，而是**可追溯、可验证、可复现、促进人工学习**：

- **无证据不入库**：每条数值必须携带 PDF 哈希 + 页码 + 区域坐标 + 原文引用 + 来源类型 + 抽取方法 + 审核状态；一条测量可关联多条证据。
- **不静默补全**：字段不确定 → null + 原因；引文无法落回原文 → 拒绝入库并记录；错误与缺失全部变成可见的 QC 标记。
- **LLM 置信度 ≠ 正确性**：只有人工复核能把数据置为 accepted；auto/LLM 来源的曲线点永远 unverified。
- **原始 PDF 只读**：解析前后 sha256 比对，派生物全部进 data/work/。

## 快速开始（Windows）

    cd SEELS
    python -m venv --system-site-packages .venv
    .venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt

    # 1) 入库解析（可多篇；原始件只读）
    .venv\Scripts\python -m seels ingest data\papers\arxiv_2611.17283.pdf

    # 2) 结构化抽取（rules-v1 零外部依赖；LLM 适配器见下）
    .venv\Scripts\python -m seels extract all --extractor rules-v1

    # 3) 图区检测（曲线抽取入口）+ 质量检查 + 学习卡
    .venv\Scripts\python -m seels figures all
    .venv\Scripts\python -m seels qc all
    .venv\Scripts\python -m seels cards all

    # 4) 复核与学习界面（浏览器打开 http://127.0.0.1:8765）
    .venv\Scripts\python -m seels serve

    # 5) 导出（CSV / Excel / ML 数据集）
    .venv\Scripts\python -m seels export --fmt both

一键体验（单篇全流程）：

    .venv\Scripts\python -m seels demo data\papers\arxiv_2611.17283.pdf

测试语料与金标准复现（需外网下载 arXiv）：

    .venv\Scripts\python scripts\fetch_arxiv.py      # 12 篇语料 -> data/papers/
    .venv\Scripts\python scripts\corpus_report.py    # 全语料流水线报告
    .venv\Scripts\python -m pytest tests -q          # 41 项自动化测试

## CLI 一览

    init            创建目录与数据库 schema
    papers          列出已入库论文（解析状态/计数）
    ingest PDF...   解析入库（--force 全量重解析；默认断点恢复；--meta 附加元数据 JSON）
    extract REF     结构化抽取（REF = 论文 id/前缀/PDF 路径/all；--extractor 选择器；--cfg-json 配置）
    figures REF     图区检测 -> 曲线记录
    qc REF          七项质量检查
    cards REF       生成六维学习卡
    export          导出（--paper --fmt csv|xlsx|both --out）
    gold-compare REF --gold FILE   金标准 vs 抽取结果误差对照
    serve           复核与学习 Web UI（--host --port）
    demo PDF        单篇全流程演示

LLM 抽取器（可选）：llm:openai-compat 适配任意 OpenAI 兼容端点；llm:langextract 需先 pip install langextract 并配置后端（云 key 或本机 Ollama）。配置经 --cfg-json 传入（endpoint / api_key / model / model_id / model_url 等，见 seels/extraction/llm.py 文档串）。两者都被强制 quote 回原文定位，未落地条目一律拒绝并留档。

## 复核界面（python -m seels serve）

- **左侧**：PDF 页面渲染（dpi 缩放）+ 证据区域彩色高亮（文本/表单元格/图注分色；悬停显示引文与坐标；点击证据框反查关联测量）；下方曲线面板：原文图区 / 叠加验证 / 曲线重绘三联图，轴校准（图上拾取刻度点 + 填数值，线性/log10 + 单位）、点取数字化、WebPlotDigitizer CSV 导入、曲线接受/驳回/存疑。
- **右侧**：数据（样品 + 测量表，过滤/搜索）、详情/审核（全字段 + 逐条证据“定位到原文” + 接受/修改/驳回/标记存疑/返回原始证据；修改自动重算标准化并保留旧值）、六维学习卡、异常（QC 标记 + 缺失字段 null+原因清单）、审核记录（append-only 审计表）。
- 顶部：结构化抽取 / 提取图表区域 / 运行QC / 生成学习卡 / 导出CSV / 导出Excel；复核人姓名随每次审核操作持久保存。

## 项目结构

    seels/            核心包（parsing / extraction / curves / qc / learning / export / ui / db / cli）
    tests/            pytest 41 项（含金标准全匹配、LLM 拒绝未落地引文、曲线真值恢复等）
    scripts/          fetch_arxiv.py（语料下载） corpus_report.py（全语料流水线） make_arch_diagram.py
    data/papers/      原始 PDF（只读）+ manifest.json
    data/gold/        金标准（合成精确版 + 真实论文部分版[PROVISIONAL] + 人工标注模板）
    data/work/        派生物：seels.sqlite3、papers/<id>/parse.json、renders/、curves/、exports/
    docs/             01 可行性报告 · 02 架构 · 03 Schema · 04 评估 · 05 限制与路线图

## 文档索引

- 技术可行性报告（8 个开源项目逐项核实）：docs/01-feasibility-report.md
- 系统架构（含架构图 docs/assets/architecture.png）：docs/02-architecture.md
- 数据 Schema：docs/03-schema.md
- 评估报告（金标准误差对照 + 12 篇语料结果）：docs/04-evaluation.md
- 已知限制与后续开发计划：docs/05-limitations-roadmap.md

## 评估结果摘要

- 合成论文精确金标准：**8/8 全匹配，相对误差 0.000**（pytest 守护）。
- 真实论文（arXiv 2611.17283）部分金标准 8 项：**4 项匹配（误差全 0.000），4 项如实报缺失/近失**，召回 50%；全程 **0 编造数值**。
- 12 篇语料 280 页：解析 0 失败页；产出 187 测量 / 415 证据 / 72 图区 / 187 学习卡；QC 未决标记 238 条全部可见（237 info / 1 warning）。
- 详见 docs/04-evaluation.md。

## 验收标准对照

| 验收项 | 实现与验证 |
|---|---|
| 正式入库数值均具备来源证据 | 存储层强制：无证据链接的测量不写入（pipeline._materialize）；QC C1 复核；tests/test_extraction |
| 数据库记录到 PDF 位置跳转 | UI 详情页每条证据“定位到原文”→ 页面渲染+红框闪烁；证据含页码/bbox/引文/PDF 哈希 |
| 曲线查看、校准与复核 | UI 曲线面板 region/overlay/redraw + 校准拾取 + WPD 导入 + 人工核验；tests/test_curves |
| 错误与缺失不静默补全 | null+reason、uncertain→EX_NULL_FIELD 标记、LLM 未落地 quote 拒绝并留档；tests 覆盖 |
| 人工复核结果持久保存 | reviews 表 append-only（旧值→新值、复核人、时间、备注）；tests/test_server |
| 至少 10 篇不同类型论文测试 | 12 篇 arXiv（4 个子领域）+ 合成原生 + 模拟扫描 fixture；docs/04 第 4 节 |
| 金标准与自动抽取误差对照 | data/gold/* + python -m seels gold-compare（CSV/JSON 报告）；docs/04 第 2–3 节 |
| 可导出 ML 数据集 | exports/all/dataset_measurements.csv + dataset_manifest.json（入库策略显式声明） |

## 许可与依赖说明

- 核心运行依赖均为宽松许可：pdfplumber / pdfminer.six（MIT）、pypdfium2（BSD/Apache-2.0）、matplotlib、pandas/numpy/openpyxl/Pillow。
- WebPlotDigitizer（AGPL-3.0）仅作为**外部自托管人工工具**经 CSV 数据交换集成，不链接其代码；如需内嵌须自行评估 AGPL 义务。
- 可选二期组件（docling MIT / MinerU Apache-2.0+附加条款 / langextract Apache-2.0 / docling-graph MIT）未进入 MVP 依赖；接入前按 docs/01 第 3–4 节复核许可与版本策略。
- 测试论文来自 arXiv 开放获取（data/papers/manifest.json 记录来源 URL 与 sha256；版权归原作者）。

## 环境说明

开发环境：Windows + Python 3.12（venv --system-site-packages 复用宿主 numpy/pandas/Pillow/openpyxl）。全新环境直接 pip install -r requirements.txt -r requirements-dev.txt 即可；复核 UI 无外网/CDN 依赖，可完全离线使用。
