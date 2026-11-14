# SEELS 技术可行性调研报告

调研日期：2026-11-13（Asia/Shanghai）。
调研方式：对每个项目实际抓取并核读 GitHub 仓库页 / README / Releases / 源码文件 / 官方文档站 / PyPI JSON API / HuggingFace API；**不以项目名称推断功能**；无法核实之处显式标注"未能核实"。三个独立调研任务并行完成，本文汇总全部关键结论并给出选型决策。

---

## 1. 逐项核实结果

### 1.1 docling-project/docling

- **定位**：多格式文档解析框架（PDF/Office/HTML/图片/音视频 → DoclingDocument 统一表示），LF AI & Data 基金会项目，IBM Research 发起。
- **版本/维护**：v2.150.0（2026-11-10 发布）；调研当日（11-13）仍有提交（fix(docx) #5322）；一周内连发 5 版；star ≈ 70,544；**非常活跃**。
- **许可证**：代码 **MIT**；模型权重单独许可：TableFormer（CDLA-Permissive-2.0 / Apache-2.0）、版面模型 Docling-Heron（Apache-2.0，171.7MB）、图片分类器 DocumentFigureClassifier-v2.5（MIT）、GraniteDocling-258M（Apache-2.0）。
- **部署要求**：Python ≥3.10,<4.0；torch 系依赖（环境 2–4GB，估算）；PDF 标准管线需权重 ≈0.4–0.6GB（Heron 171.7MB + TableFormer accurate 212.8MB / fast 145.5MB）；VLM 路径另加 ~1–3GB；**GPU 非强制**；支持 artifacts_path 离线气隙部署；官方建议生产设 document_timeout 90–120s；issue 区有高内存报告（#2779 转换耗尽内存、#2209 累积 13GB）。
- **API/批处理**：CLI（docling convert，--to md/json/html/doctags/chunks...，--ocr-engine，--show-layout，--vlm-model）；Python DocumentConverter.convert / convert_all（page_range、progress_callback）；REST 服务 docling-serve v1.38.2（POST /v1/convert/source|file、异步任务+轮询/WebSocket、X-Api-Key、Redis 队列）；分布式 Docling Jobkit（Ray/Kubeflow、S3/GDrive 连接器）；MCP server。
- **输入/输出**：输入极广（原生 PDF、扫描件 OCR、图片、Office、EPUB、HTML、音视频、邮件等）；输出 md/json/yaml/html/doctags/chunks/latex 等；**JSON（DoclingDocument）为无损序列化，Markdown/HTML 导出丢坐标**；表格可导出 DataFrame/CSV/HTML。
- **原文定位**：**强**。每个 DocItem 带 prov: ProvenanceItem{page_no, bbox{l,t,r,b,coord_origin=TOPLEFT|BOTTOMLEFT}, charspan(0 基字符区间)}；表格 TableCell 级 bbox（含 row/col span、header 标志）；DocItem.get_image(doc) 按 bbox 裁剪页面图；ConversionResult.confidence 提供版面/OCR 质量分级（table_score 官方标注未实现）。
- **表格/曲线**：表格 TableFormer（默认 ACCURATE + do_cell_matching）结构化；**图表数值提取：有**——chart extraction enrichment（granite_vision_v4，本地 Transformers 或 LM Studio/vLLM 后端），输出 chart2csv（默认）/chart2summary/chart2code，结构化类型 PictureLineChartData（轴标签+每线 (x,y) 点列）/Bar/StackedBar/Pie/Scatter/Tabular；公式 do_formula_enrichment→LaTeX；OCR 引擎 RapidOCR/EasyOCR/tesserocr/ocrmac/Nemotron（auto 顺序 ocrmac→Nemotron→RapidOCR(onnx)→EasyOCR；Tesseract 不被 auto 选中）。
- **已知限制**：PDF 文本样式（粗体/下划线）不可得（仅声明式后端支持）；内存问题（#2779/#2209）；RapidOCR 在只读文件系统有问题（Databricks 场景，官方 docstring）；Python 3.13+旧 numpy 冲突（#283）；headless libGL.so.1（#2466）；特定 PDF 输出不可读（#960）；版本迭代极快需 pin；VLM 上手门槛（#2102/#2186/#3910）。
- **适配层要点**：以 JSON 导出为唯一事实源；prov 的 coord_origin（PDF 路径通常 BOTTOMLEFT）需按 page.size 换算统一坐标系；表格证据用 TableCell.bbox；图表数值需将 chart2csv 结果绑定 PictureItem.prov；OCR 显式配置勿用 auto；权重离线预置；钉住版本。

### 1.2 opendatalab/MinerU

- **定位**：复杂文档（PDF/Office 等）→ LLM-ready Markdown/JSON（仓库描述原文）。
- **版本/维护**：4.2.2（**2026-11-13 当天发布**）；star ≈ 83,623；open issues 10 / closed 2,422（处理极快）；**非常活跃**。
- **许可证**：**自定义 MinerU Open Source License = Apache-2.0 + 附加条款**：(a) 合并口径 MAU>1 亿或月收入>2000 万美元须另取商业许可；(b) 基于 MinerU 对外提供在线服务须显著署名；(c) 违反自动终止。旧包 magic-pdf（1.x CLI）为 AGPL-3.0，2025-05-24 停更，已被 mineru 包取代。模型权重：MinerU-4_models_torch 标 apache-2.0；onnx 仓库与 GGUF 未标注（未能核实）。
- **部署要求**：Python ≥3.11,<3.15（推荐 3.12）；分档 flash（原生文本零模型下载）/ basic（小模型包 ≈0.86–0.89GB：PP-OCRv6、PP-DocLayoutV2 0.21GB、PP-FormulaNet plus-M 0.62GB）/ standard、advanced（VLM MinerU2.5-Pro-1.2B：safetensors ≈2.33GB 或 GGUF Q8 ≈1.24GB）；**GPU 非强制**（CPU 分档：≥12 核可到 Standard；ONNX GPU 需 ≥6GB 显存；NVIDIA 8GB VRAM 仅是起点）；高吞吐建议 ≥16GB 内存；WSL 需 libgl1；遥测默认开启可关（mineru telemetry disable）。
- **API/批处理**：两套 CLI——mineru-kit parse（无状态批量：多文件/目录、--format markdown|middle_json|zip、--tier、--pages、--ocr-mode，明确的成功/跳过/失败语义与退出码）、mineru（文档库/Agent 阅读：parse/read/search/find/watch，稳定 locator doc:{id}/tier/page/block）；Python SDK（mineru.parser.parse → ParseResult.markdown/structured_content/to_json/save）；REST V1 POST /v1/tasks（1–100 文件 manifest、上传或 URL、sha256 校验、轮询、取消、ZIP 下载）；4.0 与 4.1 均有破坏性迁移。
- **输入/输出**：PDF（含扫描 OCR）、图片、Office 全家桶、RTF/ODF/EPUB/OFD/HTML/MHTML/CSV；输出 Markdown/HTML/LaTeX/DOCX/EPUB/PDF/Structured Content JSON/Content List V1(扁平)|V2(按页)/Middle JSON(schema docvortex.middle 2.0)/ZIP。
- **原文定位**：**block 级**——Content List V1 每项强制 page_idx（0 基）+bbox；Middle JSON 顶层 block 必须携带 [0,1] 归一化 bbox，配合 extensions.docvortex_layout 的 width_pt/height_pt/image_rotations 可还原 PDF 点坐标；**无字符级偏移**（InlineSpan 明确不带字体/bbox/score）。
- **表格/曲线**：表格 table block→安全 HTML table_body + caption/footnote + bbox + 截图，4.2 改进跨页合并（standard/advanced 档存在表头前合并信息行丢失的 open issue，basic 档正常）；公式 LaTeX（行间 equation + 行内 equation_inline）；存在 chart block 输出 "Chart HTML/文本"（--disable-image-analysis 可关），**数据点级还原深度未能核实**。
- **已知限制**：许可附加条款；API 快速演进（勿依赖旧 _backend/pdf_info 字段，校验 schema 标识）；mineru parse 默认仅前 10 页（完整导出用 mineru-kit parse -o，4.2.2 特别澄清）；海光 K100AI OOM、OCR ▱→□ 误识、Excel 列错位等 open issues；非 NVIDIA Docker 部署"待更新"。
- **适配层要点**：批量离线走 mineru-kit parse --format zip；归一化 bbox×页尺寸还原点坐标；页码 0 基/1 基统一；句/词级引用需二级对齐（用 pypdfium2/pdfplumber 在 block 内反查词框）；pin 小版本。

### 1.3 google/langextract

- **定位**：**LLM 驱动的结构化抽取 Python 库**（用户 prompt+few-shot → 结构化抽取，带精确源文本 grounding 与交互可视化）；不是解析器/OCR；README 声明"非 Google 官方支持产品"（健康应用另受 HAIDF 条款）。
- **版本/维护**：v1.7.2（2026-11-07 发布）；最近提交 2026-11-10；star ≈ 39,310；首发 0.1.0 于 2025-07-22，27 个 release，1.x 稳定线；Zenodo DOI 10.5281/zenodo.17015089；CI+tox 测试矩阵；**活跃**。
- **许可证**：**Apache-2.0**；无自有模型权重（纯客户端库）。
- **部署要求**：Python ≥3.10（CI 3.10/3.11）；依赖 google-genai≥1.39、pandas、pydantic、aiohttp、ml-collections 等（轻量，百 MB 量级——估算，未能核实精确值）；**必须外接 LLM 后端**：Gemini（LANGEXTRACT_API_KEY/Vertex）、OpenAI（OPENAI_API_KEY）——云端无需 GPU；或 Ollama 本地（模型 GB 量级由 Ollama 管理，GPU 可选）；提供 Dockerfile。
- **API/批处理**：**仅 Python API**（无 CLI、无内置 REST；HF Spaces 仅 demo）：lx.extract(text_or_documents, prompt_description, examples, model_id, extraction_passes, max_workers, max_char_buffer, output_schema, language_model_params...)；长文自动 chunking+并行+多 pass；Vertex AI Batch / OpenAI Batch（threshold/poll_interval，低于阈值自动回退实时）；provider 插件系统（内置 gemini/gemini_batch/openai/openai_batch/ollama + 社区注册表）；OpenAI 兼容端点经 ModelConfig(provider="openai", provider_kwargs={"base_url":...})。
- **输入/输出**：输入纯文本/文本 URL(fetch_urls)/Document 迭代对象；**不原生支持 PDF/图片**；输出 AnnotatedDocument（内存）、JSONL（lx.io.save_annotated_documents）、自包含交互 HTML（lx.visualize）；Extraction 字段：extraction_class、extraction_text、attributes、**char_interval(start_pos,end_pos)**、alignment_status、extraction_index、group_index；无表格结构化能力。
- **原文定位**：**字符级偏移是核心卖点**（"Maps every extraction to its exact location in the source text"）；LLM 抽取到示例文本时自动检测 char_interval=None（官方建议过滤）；**无页码/bbox**——回溯 PDF 位置必须由外层建立字符偏移→(page,bbox) 索引。
- **已知限制**：质量取决于 LLM/prompt/示例；示例不规范触发 Prompt alignment 警告；Gemini 模型退役周期需跟进；云速率限制（生产建议付费层）；Ollama 不支持 output_schema（退化 JSON mode+few-shot）；RegexTokenizer 长文本性能被点名（open issue）；1.6 默认值变更曾影响下游 wrapper。
- **适配层要点**：自建 PDF→带页标记文本流 + 偏移→页/bbox 映射；分块边界需 overlap 或按段对齐并跨块去重；丢弃 char_interval=None；只有 extraction_text（逐字）可作证据、attributes（推断）需另标注；用 output_schema 固化证据字段（Ollama 路线需额外校验层）；大规模走 Batch API；pin 版本。

### 1.4 tijoseymathew/langextract-docling

- **定位**：langextract 的轻量 wrapper，用 docling 补原生 PDF 处理；每条抽取可回溯到源 PDF 词级/表格单元格级位置（README 原文）。**即 langextract×docling 桥接层**。
- **版本/维护**：v1.6.1（2026-09-04）；仓库创建 2025-09-28；4 个 release；**star 17、fork 2、open issues 0——个人小体量项目**；半活跃（跟进上游 docling 2.125.0 破坏性变更并重建 fixtures，工程规范：CI trusted publishing、byte-for-byte 复制上游测试套件、确定性合成语料；但单人低频，巴士因子风险）。贡献者名单因 GitHub API 限流未能核实。
- **许可证**：**MIT**。
- **部署要求**：Python ≥3.10；**硬 pin langextract==1.6.0**（上游已 1.7.2——依赖冲突风险）+ docling≥2.125.0 + docling-core[chunking] + pypdfium2（字符几何回读）；间接需 docling 模型权重（GB 量级，未能核实具体值）与 langextract 的 LLM 后端；GPU 非强制。
- **API/批处理**：仅 Python API：lx.extract(本地 PDF 路径|PDF URL|纯文本, ...)；lx.visualize_pdf(result)（自包含高亮 HTML：页面 base64 内嵌、播放/步进/点击跳转/缩放）；provenance_to_dict(result) sidecar JSON；include_provenance=False 可只做 PDF→markdown；无 CLI/REST；多 PDF 自行循环；1.1.0 破坏性变更（fetch_urls 默认 False、默认模型 gemini-3.5-flash）。
- **输入/输出**：PDF/URL/纯文本（非 PDF 输入绕过 docling 无 provenance）；输出 AnnotatedDocument + **动态附加属性** provenance（docling 项级 SpanProvenance：locations[].page_no+bbox+坐标原点、charspan、doc_item_ref 如 #/texts/12）与 sub_provenance（pypdfium2 词级收窄：每行一框、表格到单元格、列表到 bullet；sub.exact 标记框内是否恰为被抽文本）+文档级 provenance_map；**动态属性不进 JSONL，必须 sidecar 持久化**。
- **原文定位**：页码✅ bbox✅ 字符级✅（继承 char_interval + charspan）；扫描件/图片/整表回退时 exact=False（块级降级）；未对齐抽取 provenance=None。**能回溯 PDF 页码+区域+原文引用，粒度到词/单元格**。
- **表格/曲线**：表格单元格级定位（非数据化）；docling 未放置单元格时回退整表框；markdown 序列化改写单元格（含竖线）时无 sub_provenance（官方建议按 sub_provenance or provenance 读取）；**无图表/曲线能力**。
- **适配层要点/结论**：它本身就是"页码+bbox+引用"的参考实现——**宜作参考架构评估（MIT 可 vendor 其 SpanProvenance+字符几何方案），不宜作生产依赖**（硬 pin、社区极小、单人维护）；集成需解决版本冲突（fork 升级或隔离 venv）、坐标原点统一、sidecar 落盘、扫描件降级提示。

### 1.5 docling-project/docling-graph

- **定位**：文档→**校验过的可查询知识图谱**管线（仓库描述原文 "Transform unstructured documents into validated, rich and queryable knowledge graphs"）：docling 转换 + Pydantic 模板 + LLM（LiteLLM 路由 OpenAI/Gemini/watsonx/Mistral/Bedrock 或本地 vLLM/Ollama）或本地 VLM（默认 numind/NuExtract-2.0-2B）抽取实体关系 → NetworkX MultiDiGraph → 导出 CSV/Cypher（Neo4j 直导脚本）/JSON/HTML 可视化/Markdown 报告。**非单纯图数据库导出工具**。
- **版本/维护**：v1.9.2（2026-11-08）；仓库创建 2025-11-17（**项目约 1 年**）；star 945、fork 104、issue 历史总量仅 7；7–11 月约 4 个月功能版本空窗（其间 dependabot 维护）；官方 API 稳定性政策：run_pipeline/PipelineConfig/异常体系 Stable，仅 custom stage API 标 Experimental；**活跃但年轻、社区极小、实战反馈少**。
- **许可证**：**MIT**；依赖模型各自许可（NuExtract-2.0-2B/8B MIT、Granite Apache-2.0、docling 权重见 1.1）。
- **部署要求**：Python ≥3.10,<4.0；docling 全套（torch）+litellm+networkx+pydantic v2+typer；**远程 LLM 路径无需 GPU**，官方最低 4 核/8GB RAM/5GB 磁盘，推荐 8+ 核/16GB+/20GB；本地 VLM 必须 NVIDIA GPU（NuExtract-2B 4GB VRAM、8B 8GB；本地 LLM 7–8B 16GB VRAM，CUDA 11.8/12.1）；官方性能示例：10 页发票 LLM 本地 ~30s/远程 ~45s/VLM ~90s。
- **API/批处理**：CLI docling-graph init/convert（--template、--processing-mode、--extraction-contract、--provenance off|standard|detailed）/inspect/merge（确定性图融合，无 LLM）/template from-docs|from-ontology(OWL/RDFS/SKOS/LinkML/JSON Schema)|lint|evaluate；Python run_pipeline(PipelineConfig)→PipelineContext{knowledge_graph, extracted_models}（默认纯内存）；批处理为官方 loop+tqdm/并行指南（无内置队列/REST；转换可外包 docling-serve）。
- **输入/输出**：输入 docling 全格式（含扫描 OCR）+ DocLang(.dclg/.dclx)；输出 nodes.csv/edges.csv/graph.cypher/graph.json/graph.html(Cytoscape)/report.md/metadata.json/**provenance.json**/docling document.json+dclg。
- **原文定位**：**显式设计目标；确定性字符串扫描 grounding（零 LLM 成本）；"Fail-empty, never fail-wrong"**。节点 __provenance__{document_id, match(verbatim 逐字命中/observed 近似+approximate:true), chunks(≤8), pages[], refs(Docling JSON 指针 #/texts/57、#/tables/1)}；detailed 模式加 spans（chunk 内字符偏移，展示≤4）；provenance.json 完整台账含 chunk 全文+text_hash+**item_geometry[{ref,page_no,bbox[x0,y0,x1,y1],page_width/height,dclg_location}]**；定位阶梯 verbatim→observed→document scope（仅 direct 契约兜底）→unresolved，从不伪造位置；合并/去重时 provenance union 不丢失。
- **表格/曲线**：表格依赖底层 docling（LLM 后端对复杂表弱，官方决策矩阵建议表密集→VLM 后端）；**无图表数值提取**（需前置 docling chart enrichment）。
- **已知限制**：非拉丁文字实体被规范化成空串导致错误合并——v1.9.2（2026-11-08）刚修复（#139），**修复前后 node ID 不兼容需重建图**，#143 仍跟踪边界情况；纯文本丢视觉信息；依赖 docling OCR 质量（差扫描件/手写弱）；大文档分块可能丢跨页上下文；direct 契约无法逐字命中则退化 document scope（无页码）；identity 字段必须"可从原文逐字复制"（官方 best practice 反复强调）；无 REST/队列。
- **适配层要点**：模板 identity 字段短、标量、可逐字复制（禁止模型编造 ID）；provenance="detailed"；match!=verbatim 证据降级人工确认；bbox 两跳换算（provenance.json item_geometry 或 refs→docling JSON prov，注意 l/t/r/b+coord_origin 与页高翻转）；图入库 Neo4j 后需 ETL 展开 __provenance__ JSON 字符串；版本钉死+固定样本回归（node ID 规则变过）。

### 1.6 automeris-io/WebPlotDigitizer

- **定位**：**浏览器应用**（非库）：CV 辅助从图表图像提取数值（README 原文 "computer vision assisted software ... extract numerical data from images of data visualizations"）；支持 2D XY（含日期/log 轴）、柱状/直方、极坐标、三元图、地图/显微测距、圆形记录仪（v4.7）。
- **版本/维护**：正式 release v4.7（2024-02-13，无二进制附件）；master 已 v5.3（2025-05-27 "initial commit to opensource v5"，未打 tag）；version_5_4 分支 **2026-07-27** 仍在更新（新增 MATLAB/Excel/Python 导出、压缩 XLSX）；star ≈3,188；104 open issues；README 称无官方 roadmap、贡献需先沟通；**半活跃**（开源仓库低频+托管产品持续演进）。
- **许可证**：代码 **AGPL-3.0**；README 明示云端 "AI Assist" 等**闭源**（Automeris LLC）；托管版需注册（automeris.io）；旧免费入口 app.automeris.io 现状未能核实；desktop/ 为自标 experimental 的 Electron 壳（license ISC 仅壳元数据），无预编译包。
- **部署要求**：Node.js 构建（官方 Dockerfile node:23.10-alpine + python3/py3-jinja2/py3-babel 模板渲染）；依赖 pdfjs-dist ^4.2.67 等；**无模型权重、无 GPU**；源码 1.69MB+node_modules 数百 MB；docker compose up --build 或 npm install && npm run build && npm start → localhost:8080。
- **API/批处理**：**无 CLI、无 Python API、无官方 REST**；官方工作流为浏览器内逐图交互；File→Save Project 导出 .tar（点+标定+原图）或 .json（参数+数据），Load Project 复用标定实现同版式半自动批量；源码内有 wpd.dataExport.generateCSV/download.js 等可控制台脚本化（非官方接口，升级有破坏风险）。
- **输入/输出**：输入 canvas 支持图像（JPEG/PNG/BMP/GIF）+ **多页 PDF（pdfjs-dist）** + 剪贴板粘贴；**TIFF 不支持**（docs 原文）；输出 CSV（View Data 复制/下载；列头=数据集名+轴变量名；数字格式 Fixed/Precision/Exponential；日期格式可配）、项目 .tar/.json；v5.4 分支加 XLSX/MATLAB/Python 导出；柱状图导出为类别+数值。
- **原文定位**：内部每点以像素坐标存储（axes.pixelToData 换算）；**导出 CSV 只含数据值，不含像素坐标/页码**；像素坐标两条路：标定选 "Align to Image Pixels" 或解析项目文件；PDF 页码需外部记录；**回溯 PDF 页+区域须自建映射**。
- **已知限制**：TIFF 不支持；分数（1/2）不按数值算；^指数不识别（须写 1.45e-10）；日期须按规定格式；AGPL 对商业/SaaS 集成有传染约束；无官方 roadmap。
- **适配层要点**：SEELS 渲染页图（记录页码/DPI/裁剪框）→人工在自托管 WPD 数字化→回收 CSV（或项目 JSON 取像素坐标）→SEELS 内换算 pixel↔data、回绘+叠加验证；区域 bbox=像素×72/DPI+裁剪偏移；原文引用从 PDF 文本层取图注；勿依赖闭源 AI Assist；iframe 嵌入需评估 AGPL。

### 1.7 materialsCnicCas/ChartRecover

- **定位**：深度学习科研图表数据恢复框架（源码核实）：vendored MMDetection v3.3.0/MMCV v2.1.0 上实现 Co-DINO 检测柱体/散点及 18 类图表元素（坐标轴、刻度、标签、图例等），TrOCR 识别轴/图例文字，刻度线-刻度值对齐（匈牙利匹配+线性回归，支持 linear 与 linear-x-log-y，按 R² 择优）换算真实数值。配套论文：Communications Engineering 2026-05-20，DOI 10.1038/s44172-026-00691-8（中科院计算机网络信息中心研发，官方新闻确认；论文 Code availability 是否指向本仓库未能核实）。
- **版本/维护**：**无 release/tag；全部 6 次提交集中 2025-09-04~09-15，此后（含论文正式发表后）无更新；star 3、fork 0、issue 0——一次性论文代码发布，停止维护**。
- **许可证**：**仓库无 LICENSE 文件**（GitHub API license: null；全量源码包扫描亦无 COPYING）；vendored mmdetection 连上游 Apache-2.0 声明也未保留；权重未发布故无权重许可可言。**法律状态不明，集成前须取得书面授权**。
- **部署要求**：Python 3.8.20 锁死（README："Other versions may not be compatible"）+torch 2.1.0+cu121+mmcv 2.1.0+transformers 4.46.3+numpy 1.24.1 等；**GPU 必须**（device='cuda:0' 硬编码；训练按 README 需 4 GPU）；CUDA 全家桶磁盘 ~6–8GB 量级；显存需求未能核实；**模型权重未随仓库发布**（脚本硬编码 ./mmdetection/my_checkpoint/... 路径但目录不存在），**训练数据集未发布**（data_root 指向作者本地路径）；OCR 模型目录未提供（源码注释示来自 microsoft/trocr-large-printed，GB 量级）。
- **API/批处理**：无 CLI 打包（python chart_data_extract_bar.py / _scatter.py 直跑；README 明言 "You need to modify the path(s) in the file"）；无 setup/pyproject；支持目录遍历批量推理（png/jpg/jpeg/gif）。
- **输入/输出**：输入仅垂直柱状/水平柱状/散点图**图片**（**不支持折线图/表格，不读 PDF**）；输出每图一目录：bar_boxes.json（柱体像素 bbox+置信度）、bounding_boxes.json（18 类元素 bbox）、axis/legend_label_texts.json（TrOCR 文本）、visual_data.json（数据元素像素坐标）、**reality_data.json（恢复的真实数值，柱状为 {x:OCR 类别, y:数值}）**、编号标注图、原图副本；无 CSV/Excel。
- **原文定位**：输出自带每个数据元素**像素级 bbox**+原图（数值↔像素区域 provenance 为三工具中最强）；无页码概念（单图输入）；回溯 PDF 需自建裁剪映射（pdf_rect = bbox_px × 72/DPI + crop_offset）。
- **已知限制**：**开箱不可用**（权重/数据缺失，须自训或联系作者）；无许可；路径硬编码须改代码；版本锁死；无 CPU 回退；仅柱状+散点；x 类别依赖 OCR 质量；论文声称的基准数值未能核实。
- **结论**：**MVP 不采用**（实验性论文代码：不可运行+法律状态不明+停止维护——符合"不得将实验性工具视为成熟生产组件"要求）。列为远期可选：取得 CNIC 团队权重与书面授权后，封装为子进程服务（去硬编码、device 参数化），其像素 bbox 输出可直接进 SEELS 证据模型；折线/表格仍需他途。

### 1.8 dilawar/PlotDigitizer

- **定位**：Python3 CLI，批量数字化**单轨迹**曲线图（README 原文：适合大量相似图如 EEG/ECG）；另有作者手工网页版 Plot Extractor（tools.dilawars.me，在线可用性未能核实）。
- **版本/维护**：PyPI 最新 0.3.0（2024-07-14）；GitHub v0.3.1 tag（2024-08-10，pre-release，**未上传 PyPI**——pip 装到的是旧版）；最近提交 2026-04-05（仅类型注解/文档，含 "Replace WebPlotDigitizer with Plot Extractor"）；star 175、fork 26、11 open issues 多零回复（#24 ImportError、#23 多曲线请求）；有 CI+tests；**半活跃偏低（个人维护，功能停滞）**。
- **许可证**：**声明冲突**——仓库 LICENSE 为 GPL-3.0 全文（GitHub API 识别 GPL-3.0），pyproject/PyPI 声明 LGPL-3.0-or-later；作者未澄清（真实意图未能核实）；**保守按 GPL-3.0 对待**。
- **部署要求**：Python ≥3.9,≤4；opencv-python ^4.10/numpy ^2.0/matplotlib ^3.9.1（仓库版另用 typer/loguru，与 PyPI 0.3.0 依赖不一致）；**无模型权重、无 GPU、纯 CPU**；交互标定需 GUI（cv2 窗口），给全 -l 可完全无头。
- **API/批处理**：CLI 两入口：plotdigitizer 与 plotdigitizer-locate（弹窗点选查像素坐标）；用法 plotdigitizer 图.png -p 0,0 -p 10,0 -p 0,1 -l 22,295 -l 142,295 -l 22,215 [-o out.csv] [--plot] [--remove-grid] [--invert-image]；-p 数据坐标（≥3 点，无 (0,0) 时须 4 点）、-l 对应像素（原点左下角）；**-l 给全即无交互可脚本循环（README 明确卖点）**；无稳定 Python API、无 REST。
- **输入/输出**：输入图片（cv2.imread 常见格式；**不读 PDF**；README 原文 "Only b/w images are supported"——彩色转灰度；建议预裁上/右轴与图例）；输出 CSV **实为每行 "%g %g" 空格分隔两列**（源码核实，非逗号分隔）；--plot 重绘 PNG；无 JSON。
- **原文定位**：输出仅数据坐标，**不带像素坐标/页码/bbox**（内部有像素信息但未导出，需 patch 源码旁路输出）；回溯 PDF 需自建映射。
- **表格/曲线**：表格无；曲线**仅单条轨迹、仅线性轴**（image.py 源码注释原文 "Currently only linear maps and only 2D"；log 轴 #18、多曲线 #23 均未实现）；自动前景/背景统计+按颜色提取轨迹+中位数拟合；--remove-grid 形态学去网格。
- **已知限制**（README+issues 实证）：图例未去除会破坏其下方检测（README 实例自曝 "it has screwed up the detection below it"）；右轴附近误差；标定点选误差传播；#24 安装后 ImportError（anaconda）、#17 部分图片无法工作、#13 暗背景（0.3.1 --invert-image 缓解）、#9 透明图、#7/#11 Windows 控制台 UnicodeEncodeError、#4 非均匀颜色轨迹不支持。
- **适配层结论**：可选备用件（**子进程 CLI 调用不链接**，规避 GPL 传染顾虑；按 GPL 保留声明）：仅路由"单曲线、线性轴、浅色背景"图；需自建 PDF→裁剪前处理（记录页码/裁剪框/DPI）、轴检测脚本替代人工 locate、patch 导出像素坐标、解析空格分隔输出；pin GitHub main（勿依赖 PyPI 0.3.0）；**MVP 不依赖**。

---

## 2. 横向对比矩阵

| 维度 | docling | MinerU | langextract | langextract-docling | docling-graph | WebPlotDigitizer | ChartRecover | PlotDigitizer |
|---|---|---|---|---|---|---|---|---|
| 形态 | 库+CLI+REST | CLI+SDK+REST | Python 库 | wrapper 库 | CLI+管线 | 浏览器应用 | 推理脚本 | Python CLI |
| 许可证 | MIT(代码) | Apache2+附加条款 | Apache-2.0 | MIT | MIT | AGPL-3.0 | **无** | GPL/LGPL 冲突 |
| 维护 | 非常活跃 | 非常活跃 | 活跃 | 个人半活跃 | 活跃但年轻 | 半活跃 | **停止** | 半活跃偏低 |
| 权重/GPU | 0.4–0.6GB / CPU 可 | 0.9–3.6GB / CPU 分档 | 无(外接 LLM) | 间接 docling | docling+VLM 需 GPU | 无 | **GPU 必须+权重缺失** | 无 |
| 页码+bbox | ✅ item+单元格级 | ✅ block 级(归一化) | ❌(仅字符偏移) | ✅ 词/单元格级 | ✅(provenance.json/docling 两跳) | 像素(项目文件) | 像素 bbox(图片内) | ❌ |
| 字符级定位 | ✅ charspan | ❌ | ✅ char_interval | ✅ | ✅ spans(detailed) | — | — | — |
| 表格结构化 | ✅ TableCell bbox | ✅ HTML | ❌ | 单元格级定位 | 依赖 docling | ❌ | ❌ | ❌ |
| 图表数值 | ✅ chart2csv(VLM) | 部分(深度未核实) | ❌ | ❌ | ❌ | ✅ 人工+CV 半自动 | 柱/散点(不可运行) | 单轨迹线性 |
| 扫描 OCR | ✅ 多引擎 | ✅ | ❌ | 经 docling(块级降级) | 经 docling | 图像皆可 | 图像 | 图像 |
| star | ~70.5k | ~83.6k | ~39.3k | 17 | 945 | ~3.2k | 3 | 175 |

## 3. SEELS MVP 选型决策

**核心原则：MVP 依赖必须全部可离线运行、许可证宽松、当天可验证；重组件（docling/MinerU/LLM）以可选增强引擎或适配器形式预留接口，不进第一版关键路径；实验性项目（ChartRecover、langextract-docling）不作为生产组件。**

| 决策点 | 选择 | 理由 |
|---|---|---|
| 主解析引擎 | **pdfplumber（pdfminer.six，MIT）** | 词/表格单元格/图片 bbox 直达证据粒度；纯 Python 轻量；实测 11 页论文 1.4s，9 表 6 图注全部带坐标 |
| 第二引擎+渲染器 | **pypdfium2（BSD/Apache-2.0）** | 与主引擎异构（PDFium 内核），适合异常页交叉校验与回退；页面渲染供复核 UI 与曲线模块；坐标系已实测标定（charbox 左下原点→top-left 换算，写入测试） |
| 扫描 PDF | 判别+标记 needs_ocr；OCR 引擎接口预留不捆绑 | docling（RapidOCR/EasyOCR）或 MinerU（basic 档）为二期 OCR/版面增强候选（ParseEngine 抽象已留接口）；MVP 对缺文本层如实上报，不静默 OCR |
| 结构化抽取 | **自研 rules-v1（确定性表格结构+正则）为主**；llm:openai-compat 与 llm:langextract 适配器已实现（需 key 激活） | rules-v1 零外部依赖、可复现、天然带证据；LLM 路径强制 quote 回原文定位（定位失败=拒绝入库并记录），LLM 置信度永不作为正确性证明——与 langextract char_interval 设计同构；langextract 核实结论（纯文本、无 PDF 能力、需外接 LLM）确认 grounding 层必须自建 |
| PDF+LLM 桥接 | langextract-docling 作**参考架构**（MIT 可借鉴 SpanProvenance+字符几何方案），不作生产依赖 | 个人项目、硬 pin langextract==1.6.0、社区无兜底 |
| 表格/图表增强（二期可选） | docling（TableFormer、chart2csv、OCR）或 MinerU（basic/standard） | 均非常活跃、带 provenance；接入点在 ParseEngine 抽象与 curve source=auto_trace（永不自动 verified）策略内；注意 MinerU 许可附加条款、docling 版本 pin |
| 知识图谱（远期可选） | docling-graph | provenance 哲学（fail-empty never fail-wrong、verbatim/observed 分级）与 SEELS 一致；但项目年轻、无曲线能力；MVP 不引入 Agent/图框架（符合"第一版不引入不必要复杂 Agent 框架"） |
| 曲线数字化 | **自建闭环：轴校准(线性/log10)+图上点取+WPD CSV 导入+回绘+叠加验证**；WebPlotDigitizer 为外部人工工具（自托管，AGPL 隔离）；PlotDigitizer 可选子进程备用；ChartRecover 不采用 | WPD 无编程接口、CSV 不带像素/页码→由 SEELS 记录页码/DPI/裁剪框并自建换算验证闭环；ChartRecover 权重缺失+无许可+停更（实验性）；auto/LLM 来源点永远 unverified+high_risk 直至人工复核 |

## 4. 风险登记

1. **pdfplumber 表格检测假阳性**（无线表格/公式对齐误判为表）：QC 重复/一致性检查+人工复核兜底；二期可换 docling TableFormer（单元格级 provenance 更强）。
2. **rules-v1 召回有限**（符号变体、复杂句式、下标）：金标准对照如实暴露（见 docs/04-evaluation.md）；LLM 适配器为既定提升路径，证据纪律不变。
3. **MinerU 许可附加条款**：仅在二期评估引入时按 MAU/营收门槛与署名义务复核。
4. **WebPlotDigitizer AGPL**：以独立自托管服务+人工使用+CSV 数据交换集成，不链接分发其代码；内嵌须评估 AGPL 义务。
5. **上游快速演进**（docling 几乎每日发版、MinerU 4.x 两次破坏性迁移、docling-graph node ID 规则变更）：任何二期接入必须 pin 版本+fixtures 回归。
6. **坐标系统一**：全系统 top_left_pts 约定，引擎适配器负责换算（合成 PDF 实证标定，test_parsing/test_curves 守护）。
7. **PlotDigitizer 许可声明冲突**：如启用，子进程调用+保留声明+向作者确认许可意图。

*主要证据来源：github.com/{docling-project/docling, docling-project/docling-graph, opendatalab/MinerU, google/langextract, tijoseymathew/langextract-docling, automeris-io/WebPlotDigitizer, materialsCnicCas/ChartRecover, dilawar/PlotDigitizer}（仓库页/README/Releases/源码/CHANGELOG/issues）、docling-project.github.io、opendatalab.github.io/MinerU、automeris.io/docs、PyPI JSON API、HuggingFace API（经 hf-mirror）、api.semanticscholar.org；采样 2026-11-13。*
