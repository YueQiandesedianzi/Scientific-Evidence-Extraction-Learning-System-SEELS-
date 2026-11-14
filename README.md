# SEELS — Scientific Evidence Extraction & Learning System

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#快速开始)
[![Tests](https://img.shields.io/badge/pytest-41_passed-brightgreen.svg)](#测试与评估)
[![Version](https://img.shields.io/badge/version-0.1.0_MVP-orange.svg)](#已知限制与路线图)

> 从学术论文 PDF 中抽取**可追溯、可验证、可复现**的「样品 – 条件 – 性能」数据：
> 每一个数值都绑定原文页码、区域坐标与逐字引文；LLM 只能提供候选，**只有人工复核能让数据入库生效**。
> 系统目标不是无人值守，而是人机协同的论文伴读与可信数据集构建。

**四条证据纪律（贯穿全部模块）**

| 纪律 | 落地机制 |
|---|---|
| 无证据不入库 | 每条测量强制关联 ≥1 条证据（PDF sha256 + 页码 + bbox + 原文引用 + 来源类型 + 抽取方法 + 审核状态）；无证据链接的行在写入层直接丢弃 |
| 不静默补全 | 字段不确定 → `null + 原因`；LLM 引文落不回原文 → 整条拒绝并留档；缺失全部变成可见 QC 标记 |
| LLM 置信度 ≠ 正确性 | 置信度仅存档展示；accepted / human_verified 只能由人工复核动作触发 |
| 原始 PDF 只读 | 解析前后 sha256 比对；一切派生物写入 `data/work/` |

## 架构总览

![SEELS 架构](docs/assets/architecture.png)

解析（双引擎）→ 抽取（规则 + LLM 适配器，强制引文落地）→ 曲线（校准/点取/回绘验证）→ SQLite 存储 → 七项 QC → 复核学习 Web UI → CSV/Excel/ML 数据集导出。详见 [docs/02-architecture.md](docs/02-architecture.md)。

## 功能特性

- **高保真解析** — pdfplumber（主）+ pypdfium2（备/渲染）双引擎；原生/扫描自动判别；异常页自动回退并记录差异日志；词/表格单元格/图注/图片全部带 bbox；断点恢复
- **结构化抽取** — 确定性 rules-v1（表格结构 + 保守文本模式）；`llm:openai-compat` 与 `llm:langextract` 适配器（引文强制回原文定位）；单位解析/标准化与原始值分离；歧义符号（σ/ρ/κ…）标记待人工消歧而非猜测
- **曲线抽取** — 图区自动检测；轴校准（线性/log10）；图上点取数字化；WebPlotDigitizer CSV 导入；曲线重绘 + 原图叠加验证（重投影残差 ≤3px）；auto/LLM 来源永不自动通过
- **质量控制** — 证据完整性、数值单位重算、重复检测、条件关联、跨源一致性、曲线回绘、高风险转人工，共 7 项确定性检查
- **复核与学习** — 本地 Web UI：左侧 PDF 页面渲染 + 证据高亮 + 曲线三联图，右侧结构化数据 / AI 解释 / 六维学习卡 / 异常 / 审核操作（接受·修改·驳回·存疑·返回原始证据），全程 append-only 审计
- **导出** — 9 张审计表 CSV、Excel 工作簿、ML 就绪数据集（入库策略写入 manifest，绝不静默过滤）、金标准误差对照报告

## 快速开始

<details open>
<summary><b>Windows</b></summary>

```powershell
cd SEELS
python -m venv --system-site-packages .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt

# 全流程演示（单篇论文：解析→抽取→图区→QC→学习卡→导出）
.venv\Scripts\python -m seels demo data\papers\arxiv_2611.17283.pdf

# 启动复核界面 → 浏览器打开 http://127.0.0.1:8765
.venv\Scripts\python -m seels serve
```

</details>

<details>
<summary><b>Linux / macOS</b></summary>

```bash
cd SEELS
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

python -m seels demo data/papers/arxiv_2611.17283.pdf
python -m seels serve        # http://127.0.0.1:8765
```

</details>

> 测试语料（12 篇 arXiv 开放获取论文）不随仓库分发，运行 `python scripts/fetch_arxiv.py` 重新下载（需外网）；
> 随后 `python scripts/corpus_report.py` 可复现全语料流水线（约 4 分钟）。

## 常用命令

```text
python -m seels init                     # 建目录与数据库 schema
python -m seels ingest a.pdf b.pdf       # 解析入库（只读原件；默认断点恢复，--force 全量）
python -m seels extract all              # 结构化抽取（--extractor rules-v1 | llm:openai-compat | llm:langextract）
python -m seels figures all              # 图区检测 → 曲线记录
python -m seels qc all                   # 七项质量检查
python -m seels cards all                # 生成六维学习卡
python -m seels export --fmt both        # CSV + Excel + ML 数据集
python -m seels gold-compare REF --gold data/gold/xxx.json   # 金标准误差对照
python -m seels papers                   # 库存清单
python -m seels serve                    # 复核与学习 Web UI
```

**LLM 抽取器（可选）**：规则抽取器零外部依赖即可工作；接入 LLM 可提升召回，证据纪律不变——

```powershell
# 任意 OpenAI 兼容端点（OpenAI / DeepSeek / vLLM / Ollama / LM Studio…）
python -m seels extract P-xxxx --extractor llm:openai-compat --cfg-json "{\"endpoint\":\"https://api.deepseek.com/chat/completions\",\"api_key\":\"sk-...\",\"model\":\"deepseek-chat\"}"

# google/langextract（需 pip install langextract；云 key 或本机 Ollama）
python -m seels extract P-xxxx --extractor llm:langextract --cfg-json "{\"model_id\":\"gemma2:2b\",\"model_url\":\"http://localhost:11434\"}"
```

## 数据模型（一图流）

    Paper ─┬─ Sample ─── Measurement ──(N:M)── Evidence   ← 页码+bbox+逐字引文+PDF哈希
           ├─ Curve ──── CurvePoint                        ← 校准/点取/WPD导入/回绘验证
           ├─ Review（append-only 审计：接受/修改/驳回/存疑）
           ├─ LearningCard（六维学习卡，事实字段全部引用证据ID）
           ├─ QcFlag（7 项检查 + EX_NULL_FIELD 缺失清单）
           └─ ExtractionRun / ParseLog（可复现运行记录）

字段级溯源采用统一 `FieldVal` 契约：**value 与 null_reason 互斥；有值必有证据，无值必有原因**。完整 DDL 与状态机见 [docs/03-schema.md](docs/03-schema.md)。

## 测试与评估

```powershell
python -m pytest tests -q     # 41 passed
```

| 评估层 | 结果 |
|---|---|
| 合成论文 · 精确金标准（构造即真值，pytest 守护） | **8/8 匹配，相对误差 0.000**；篡改值→正确报 MISMATCH，虚构条目→正确报 MISSING |
| 真实论文 · 部分金标准（arXiv 2611.17283，76 页） | **4/8 匹配（误差全 0.000）**，4 项如实报缺失/近失，召回 50%；**0 编造数值** |
| 12 篇 arXiv 语料（4 个子领域，280 页）端到端 | 解析 0 失败页；187 测量 / 415 证据 / 72 图区 / 187 学习卡；QC 未决标记 238 条全部可见 |
| 冷启动全流水线耗时 | 243.7 s（含全部解析与图区渲染） |

细节与复现命令：[docs/04-evaluation.md](docs/04-evaluation.md)。

## 项目结构

    seels/            核心包：parsing · extraction · curves · qc · learning · export · ui · db · cli
    tests/            pytest 41 项（金标准、LLM 拒绝未落地引文、曲线真值恢复、审核状态机…）
    scripts/          fetch_arxiv.py（语料下载）· corpus_report.py（全语料流水线）· make_arch_diagram.py
    data/papers/      原始 PDF（只读，不分发）+ manifest.json（来源 URL 与 sha256）
    data/gold/        金标准：合成精确版 · 真实论文部分版[PROVISIONAL] · 人工标注模板
    data/work/        派生物：seels.sqlite3 · parse.json · renders · curves · exports（可全量再生）
    docs/             可行性报告 · 架构 · Schema · 评估 · 限制与路线图

## 文档索引

| 文档 | 内容 |
|---|---|
| [01 技术可行性报告](docs/01-feasibility-report.md) | 8 个开源项目逐项核实（维护状态/许可证/部署/API/定位能力/限制）与选型决策 |
| [02 系统架构](docs/02-architecture.md) | 架构图、模块边界、坐标约定、幂等与断点恢复、LLM 证据纪律 |
| [03 数据 Schema](docs/03-schema.md) | 全部表结构、FieldVal 契约、状态机、导出契约 |
| [04 评估报告](docs/04-evaluation.md) | 金标准误差对照、12 篇语料端到端结果、复现命令 |
| [05 已知限制与路线图](docs/05-limitations-roadmap.md) | 18 项如实限制 + P1/P2/P3 计划 + 系统边界（明确不做什么） |

<details>
<summary><b>验收标准对照（点击展开）</b></summary>

| 验收项 | 实现与验证 |
|---|---|
| 正式入库数值均具备来源证据 | 写入层强制（无证据不入库）+ QC C1 复核 + `tests/test_extraction` |
| 数据库记录 ↔ PDF 位置跳转 | UI 每条证据「定位到原文」→ 页面渲染 + 红框闪烁；证据含页码/bbox/引文/PDF 哈希 |
| 曲线查看、校准与复核 | UI 曲线面板 region/overlay/redraw 三联 + 校准拾取 + WPD 导入 + 人工核验；`tests/test_curves` |
| 错误与缺失不静默补全 | null+reason · uncertain→EX_NULL_FIELD · LLM 未落地引文拒绝留档；测试覆盖 |
| 人工复核结果持久保存 | reviews 表 append-only（旧值→新值/复核人/时间/备注）；`tests/test_server` |
| ≥10 篇不同类型论文测试 | 12 篇 arXiv + 合成原生 + 模拟扫描 fixture；docs/04 §4 |
| 金标准与自动抽取误差对照 | `data/gold/*` + `gold-compare` 命令（CSV/JSON 报告）；docs/04 §2–3 |
| 可导出 ML 数据集 | `dataset_measurements.csv` + `dataset_manifest.json`（入库策略显式声明，163 行 100% 带证据） |

</details>

## 许可

- 本仓库代码：**MIT**（见 [LICENSE](LICENSE)）
- 运行依赖均为宽松许可：pdfplumber / pdfminer.six（MIT）· pypdfium2（BSD/Apache-2.0）· matplotlib · pandas / numpy / openpyxl / Pillow
- WebPlotDigitizer（AGPL-3.0）仅作为**外部自托管人工工具**经 CSV 数据交换集成，不链接其代码；如需内嵌请自行评估 AGPL 义务
- 可选二期组件（docling MIT · MinerU Apache-2.0+附加条款 · langextract Apache-2.0 · docling-graph MIT）未进入 MVP 依赖，接入前按可行性报告 §3–4 复核
- 测试论文来自 arXiv 开放获取，版权归原作者；本仓库仅保留来源清单与哈希

## 已知限制与路线图

要点：OCR 未捆绑（扫描页如实标记 `needs_ocr`，不静默降级）· rules-v1 对符号密集论文召回有限（LLM 适配器为既定提升路径）· 真实论文金标准为 AI 辅助临时标注（PROVISIONAL，待专家复核）· 曲线点与测量行尚未正式关联。完整 18 项限制与 P1/P2/P3 计划见 [docs/05-limitations-roadmap.md](docs/05-limitations-roadmap.md)。

---

开发环境：Windows + Python 3.12。全新环境仅需 `pip install -r requirements.txt -r requirements-dev.txt`；复核 UI 无外网/CDN 依赖，可完全离线使用。
