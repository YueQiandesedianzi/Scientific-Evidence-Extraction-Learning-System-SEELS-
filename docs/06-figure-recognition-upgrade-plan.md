# 图区识别升级方案（版面分析引擎接入）

状态：**方案待决策**（2026-11-14 制定；本文档只定方案，不含实施）。
触发原因：人工使用复核 UI 后确认图区识别质量不可接受；本方案先给出问题审计数据，再给出完整升级路线。

---

## 1. 问题诊断（实证审计，scripts/audit_figures.py，2026-11-14）

当前图区检测机制（seels/curves/figures.py）：图注（"Figure N"）定位 → 在其上方寻找**内嵌位图** bbox 并集 → 无位图时回退为"图注上方 280pt 盲盒"。对 12 篇语料 73 个图区的审计结果：

| 指标 | 数值 | 含义 |
|---|---|---|
| 启发式回退区域 | **28/73（38%）** | 找不到内嵌位图，区域不可信 |
| 纯盲盒区域（280pt 固定高） | **19/73（26%）** | 完全靠猜 |
| 区域面积中位数 / 最大 | 100,174 / 234,061 pt² | 最大 ≈ 0.56 页——多子图/跨栏误并 |
| 矢量图重灾论文 | P-aa760664b1f01846：3 个图注、**0 张内嵌位图** | arXiv/LaTeX 论文图多为**矢量绘图**，pdfminer 的 page.images 只列**光栅 XObject**，矢量图完全不可见 |
| 子图（Figure 3a/3b 型） | 7 处引用未处理 | 一个区域框住多个子图，无法分别校准 |
| 表格假阳性（旁证） | 单篇 36 页论文报 104 个"表" | 同一根因：无版面模型，纯几何启发式 |

**结论**：根因是缺少版面分析模型，几何启发式在矢量图、多子图、多栏版面下系统性失效。用户判断正确：需要 MinerU / docling 级别的深度版面引擎。

## 2. 选型对比（针对"图区/表格区域识别"子任务；完整核实见 docs/01）

| 维度 | docling 2.150.0 | MinerU 4.2.2 | 现状启发式 |
|---|---|---|---|
| 版面模型 | Docling-Heron（Apache-2.0，171.7MB）输出 PictureItem/TableItem 等块级 bbox | PP-DocLayoutV2（0.21GB，basic 档起）输出 block 级 [0,1] 归一化 bbox | 无 |
| 矢量图可见性 | ✅（版面模型基于渲染/结构，不依赖光栅 XObject） | ✅ | ❌（核心缺陷） |
| 输出坐标 | prov.bbox{l,t,r,b} + coord_origin，自带 to_top_left_origin() 换算 | [0,1] 归一化 × docvortex_layout.width_pt/height_pt；page_idx 0 基 | pt（top-left） |
| 表格结构 | TableFormer（212.8MB）单元格级 bbox+span，可治假阳性 | HTML table_body + bbox（VLM 档有表头丢失 open issue） | pdfplumber 几何找表（假阳性高） |
| 图表数值化 | ✅ chart2csv（granite_vision VLM，权重另需 1–3GB，可选） | chart block（数值化深度未能核实） | ❌ |
| 扫描 OCR | ✅ RapidOCR/EasyOCR 等（CPU 可） | ✅ PP-OCRv6（CPU 可，basic 档 0.89GB） | ❌（仅标记 needs_ocr） |
| Windows/CPU 部署 | pip 安装，torch CPU；权重首跑自动下载（可 artifacts_path 离线） | pip 安装；Windows 走 onnxruntime-DirectML；CPU 分档（≥12 核可到 Standard） | 无依赖 |
| 许可证 | 代码 MIT；权重 Apache-2.0/CDLA-P2 | Apache-2.0+附加条款（内部研究免费；MAU>1亿或月收入>$2000万需商业许可；在线服务需署名） | — |
| API 稳定性 | 每日发版，需 pin；Python API 一等公民 | 4.x 两次破坏性迁移，需 pin + schema 校验 | — |
| 集成成本 | 低（DocumentConverter→JSON，纯 Python 进程内） | 中（CLI/SDK 双入口，middle_json 二次解析） | — |

**推荐组合**：
- **主版面引擎：docling**（原生 PDF 图区/表格区域；MIT、进程内 Python API、坐标换算自带、TableFormer 顺带治表格假阳性；CPU 可跑）。
- **扫描件引擎：MinerU basic 档**（补上 MVP 最大缺口 OCR；同时作为版面备选引擎做交叉验证）。内部研究用途不触发其许可附加条款，合规结论记录在案。
- 现有启发式**降级为最后回退**，产出必须带 fallback 标记 + QC info（不静默）。
- 若决策倾向"只用 MinerU"：技术上可行（其 block bbox 同样满足需求），代价是表格结构弱于 TableFormer、许可附加条款、Windows onnx 路径额外验证工作量；方案接口（§3）对两者中立，切换成本低。

## 3. 目标架构

新增**版面层（LayoutProvider）**，与现有解析层并列、互不侵入：

    PDF（只读）
      ├── parsing/（不变）→ parse.json：词级证据、表格单元格 bbox（证据粒度主力）
      └── layout/（新增）→ layout.<engine>.json：块级版面（figure/table/caption/equation/text + score）
                    │
                    ▼
        curves/figures.py 融合器：
          区域 = layout figure block bbox（首选）
               ↔ caption 文本对齐（列重叠 + 垂直距离）
               → 子图切分（block 内聚类 / caption 3a,3b 标记）
               → 无 layout 时退回启发式（打 fallback 标记 + QC info）
                    ▼
        curves 表：region_bbox + region_source(layout:docling|layout:mineru|heuristic_fallback)
                   + region_score + layout_sha（可复现）

**LayoutProvider 接口（新文件 seels/layout/base.py）**：

    class LayoutProvider:
        name: str            # "docling" | "mineru"
        version: str         # pin 的确切版本
        def analyze(pdf_path, pages=None) -> LayoutDoc: ...
        def cache_path(paper_sha) -> Path:   # sidecar: data/work/layout/<sha16>.<engine>.json
        def available() -> bool:             # 依赖/权重是否就绪，未就绪时明确报错而非静默跳过

    LayoutDoc = {"schema": "seels.layout/1.0", "engine": ..., "engine_version": ...,
                 "pdf_sha256": ..., "pages": [{"page_no": 1基, "width_pts", "height_ptse",
                   "blocks": [{"type": "figure|table|caption|equation|text|chart",
                               "bbox": [x0,y0,x1,y1] top_left_pts, "score": 0-1,
                               "text": 可选(caption/equation), "meta": {...}}]}],
                 "created_at": ...}

**坐标换算（适配层职责，写入测试）**：
- docling：prov.bbox 为 {l,t,r,b} + coord_origin（PDF 路径通常 BOTTOMLEFT）→ 用 BoundingBox.to_top_left_origin() 或 y0'=H-b、y1'=H-t 换算；页尺寸取 DoclingDocument.pages[].size。
- MinerU：content_list/middle_json 的 bbox 为 [0,1] 归一化 → ×(width_pt,height_pt)；page_idx 0 基 → +1；处理 image_rotations。

**表格路径升级（可选开关，A/B 验证后决定默认值）**：TableFormer 表结构 vs pdfplumber find_tables；证据单元格 bbox 谁稳用谁；104 假阳性论文作为 A/B 基准（目标 ≤20）。

**扫描 PDF 路径（MinerU 阶段）**：needs_ocr 页 → MinerU OCR 词/块 + bbox → parse.json 增补 ocr 层（engine 字段区分）→ 证据 source_type=ocr → 既有 C7 检查自动将其路由 needs_human_review（政策已就绪，零新增规则）。

**曲线数据纪律不变**：区域再准，曲线数值仍须校准+数字化+回绘验证+人工核验；chart2csv 类自动数值一律 source=auto_trace，永不自动 verified。

## 4. 分阶段实施计划

| 阶段 | 内容 | 产出/验收 | 工作量 |
|---|---|---|---|
| **P0 审计** | 已完成（本文 §1，scripts/audit_figures.py 可重跑） | 基线数字入档 | ✅ |
| **P1 环境+POC** | venv 安装 docling==2.150.0（torch CPU，权重 ~0.5GB，HF 下载失败走 HF_ENDPOINT=hf-mirror.com）；对 3 篇代表论文（矢量图重灾 P-aa760664、图多 P-a160c171、76 页大文档 P-455fe3）跑版面 | figure block 对人工核对页的召回 ≥90%；CPU ≤5s/页；76 页文档内存峰值记录（对照 issue #2779 风险）；产出 POC 报告 | 1 天 |
| **P2a 快速缓解（不依赖引擎）** | UI 增加**区域手工编辑**：在页面渲染图上拖拽框选/微调 region → 重裁剪；region_source 徽标显示来源（layout/heuristic_fallback） | 用户可立即修正任何坏区域；截图更新 | 0.5 天 |
| **P2b LayoutProvider + docling 适配器** | seels/layout/{base,docling_provider}.py；sidecar 缓存（按 pdf sha + engine 版本键控）；figures.py 融合器改造；caption↔block 对齐；子图切分（caption 字母标记 + block 聚类）；CLI：python -m seels layout REF [--engine docling] | 新 fixture 测试（mock provider）；73 个历史区域重生成对比报告；heuristic 回退占比 38%→<10% 且全部显式标记 | 2–3 天 |
| **P3 图区金标准与回归** | 人工标注 20 个图区 bbox（含 7 处子图），IoU 评估脚本进 gold.py 家族 | **IoU ≥0.8**（20 图金标准）；表格 A/B：104 假阳性论文 ≤20；41+新增测试全绿；金标准 8/8 不回归 | 1 天 |
| **P4 MinerU 扫描件引擎** | mineru==4.2.2 basic 档（权重 ~0.9GB）；needs_ocr 页 → OCR 词级证据入 parse.json（engine=mineru-ocr，source_type=ocr）→ C7 高风险通道；许可合规结论记录 | 模拟扫描 fixture 端到端出词级证据；真实扫描件（如可得）抽验；性能记录 | 2–3 天 |
| **P5（可选，另行决策）** | docling chart2csv（granite_vision 权重 1–3GB）→ 曲线 auto_trace 来源接入 | 自动数值仅进未验证通道；与人工数字化做一致性抽检 | 评估后定 |
| **P6 收尾** | README/docs/05 更新；配置项 SEELS_LAYOUT_ENGINE=docling|mineru|heuristic；打 tag v0.2.0 | 文档与验收表刷新 | 0.5 天 |

依赖关系：P1 → P2b → P3；P2a 独立可先行；P4 独立（仅依赖决策）；P5 依赖 P2b/P3 完成。

## 5. 资源与风险

**资源**：磁盘 +3–5GB（torch CPU 轮子 + docling 权重 0.5GB；P4 再 +0.9GB）；内存峰值 4–8GB；无 GPU 需求；模型下载经 HF（国内镜像 hf-mirror.com 兜底，离线场景 artifacts_path / MINERU_MODEL_SOURCE=local 预置）。

**风险与缓解**：
1. docling 大文档内存问题（issue #2779/#2209）→ 分批转换 + document_timeout(90–120s) + 失败页回退启发式并记录；76 页论文在 P1 实测。
2. 版本漂移（docling 每日发版 / MinerU 4.x 破坏性迁移史）→ 精确 pin（docling==2.150.0、mineru==4.2.2）+ layout.json 记录 engine_version + fixtures 回归；升级须重跑 P3 金标准。
3. 坐标系错翻 → 换算函数单元测试（合成 PDF 已知真值，沿用 test_curves 方法学）。
4. MinerU 许可附加条款 → 内部研究用途不触发；若未来对外服务，按 docs/01 §1.2 履行署名/商业许可评估。
5. torch CPU 安装体积/网络 → 国内 pip 镜像 + 一次性安装进独立 venv extra（requirements-layout.txt，不进核心依赖；核心 MVP 保持零重型依赖）。
6. 版面引擎自身错误 → region_source+score 入库、UI 徽标可视化、P2a 手工修正兜底、QC 对 fallback 区域出 info 标记——**引擎结果同样不享受"自动可信"待遇**。

## 6. 实施记录（2026-11-14 决策后当日完成 P1+P2a+P2b）

**用户决策**：A) docling 主版面 + MinerU 扫描 OCR；授权下载；chart2csv（P5）纳入本期；P2a 立即实施。

**关键工程发现（Windows 特有）**：docling-parse 7.26.0 的原生层无法从**非 ASCII 安装路径**加载自带资源（glyphs/*.dat 打开失败，报 "filename does not exists"），且 PDF 路径含中文同样有风险；另有子进程 stdin 继承导致的启动挂起。解决方案（已实现并验证）：
1. 版面引擎装入**纯 ASCII 路径独立 venv**（C:\Users\<user>\.seels\layout-venv，SEELS_LAYOUT_PYTHON 可覆写），核心 venv 保持零 torch——比原计划更干净的隔离；
2. scripts/layout_worker.py 子进程契约：PDF 复制到 ASCII 临时文件解析（原件只读不动）、JSON sidecar（schema seels.layout/1.0，sha256 键控缓存、含 diag 计数）、stdin=DEVNULL；
3. --no-tables 跳过 TableFormer（图区用途不需要，速度 ~2×）。

**P1 POC 实测（CPU，docling 2.150.0，HerON 版面）**：

| 论文 | 页 | docling 图区块 | 图注数 | 引擎秒 | 秒/页 | 旧启发式区域 IoU≥0.5 | 旧区域 IoU 中位 |
|---|---|---|---|---|---|---|---|
| P-aa760664（矢量图重灾） | 7 | **3** | 3 | 22.9 | 3.3 | 1/3 | 0.414 |
| P-a160c171 | 11 | **6** | 6 | 29.0 | 2.6 | 3/6 | 0.531 |
| P-455fe33f | 76 | **35**（另 8 表格块） | 8 | 108.7 | 1.4 | 3/9 | 0.000 |

结论：docling 图区召回与图注数精确一致（3/3、6/6），76 页大文档无内存问题；旧启发式区域与版面真值 IoU 中位数 0~0.53，证实 §1 诊断。验收线"figure block 召回 ≥90%、CPU ≤5s/页"达标。

**P2a（已交付）**：UI「调整区域」拖拽框选 + region_source 徽标 + 校准/点位自动重置 + modify 审计留痕 + manual 区域防覆盖（tests 46 项全绿，含 4 项 hermetic 布局融合测试）。

**P2b（已交付）**：seels/layout（base+docling_provider）、figures.py 版面融合（caption↔block 匹配、未匹配大块→Unnamed 区域、稳定曲线 ID、陈旧区域安全清理）、CLI --no-layout、/api/figures 透传。sidecar 缓存跨 POC/生产共享。

**全语料融合结果（scripts/audit_figures.py，2026-11-15）**：164 个区域中 **149 个（91%）来自 layout:docling**，启发式回退 12 个（7%，全部显式标记）+ legacy 1 个（带人工数据的演示曲线，受保护保留）；带图注名区域 71 个，另新增 93 个 Unnamed 版面块候选（供人工筛选）。矢量图重灾论文 P-aa760664 从"3 个全盲盒"变为 3/3 版面区域。**验收线"回退占比 <10% 且全部显式标记"达成**（38% → 7%）。

**P3 工具链（已交付，待人工标注）**：scripts/make_region_worksheet.py 已生成 24 份标注工作表（红色 pt 标尺；蓝框=现有 SEELS 区域，绿框=docling 版面块，橙虚线=图注）+ data/gold/regions_gold_TEMPLATE.json 模板；人工填写 gold_bbox 后运行 python -m seels gold-regions --gold <文件> 即得逐项与汇总 IoU 报告（验收线 IoU≥0.8）。评估逻辑由 tests/test_gold_regions.py 守护（PASS/FAIL/MISSING/SKIPPED 判定）。

**P4 MinerU 扫描 OCR（已实现）**：mineru==4.2.2 安装于 D:\seels-tools\mineru-venv（ASCII 路径，模型经 HF_HOME / MODELSCOPE_CACHE 重定向 D 盘，basic 档权重 ~0.9GB 首次运行下载）。组件：scripts/ocr_worker.py（子进程调 mineru-kit parse → middle_json → span bbox 按 token 比例拆分为 top_left_pts 词框，防御性递归解析 schema）+ seels/parsing/ocr_provider.py（sha256 键控 sidecar 缓存、页级合并、sha 不符拒用）+ parsing.pipeline.augment_ocr（needs_ocr → ocr_done，ocr_meta 留痕，parse.json 原子更新）+ CLI：python -m seels ocr REF [--force]。证据纪律：OCR 页证据 source_type=ocr → QC C7 自动路由 needs_human_review（tests/test_ocr.py 2 项密闭测试守护全链路；真实引擎 e2e 见 scripts/demo_ocr.py，结果跑完后补录于此）。

**P5 chart2csv（已实现通道，本机硬件受阻——如实记录）**：docling 图表数值化依赖 granite-vision-4.1-4b 权重 ≈4.96GB；实测 C 盘仅剩 3.8GB 下载失败（HF_HOME 可迁 D 盘解决磁盘），但机器 15.7GB 内存当前仅 ~2.1GB 空闲，加载期已出现 67MB 内存分配失败，4B VLM 的 CPU 推理在本机不可行。--charts 参数、provider 开关、独立 sidecar（-charts 后缀）均已实现并入库，满足任一条件即可启用：(a) 重启释放内存 + HF_HOME 指向 D 盘（预计仍缓慢：CPU 上每图分钟级）；(b) 远程 VLM 后端（LM Studio / vLLM API）；(c) GPU 机器。期间曲线数值仍走人工校准/点取/WPD 导入闭环。

**待办**：P3 人工标注 24 图 → gold-regions 正式验收；P4 真实 e2e 结果补录；P5 待硬件/远程后端条件满足后启用。

## 7. 决策点（原文存档；四项决策已全部确认，见 §6 首行）

1. **引擎组合**：A) docling 主版面 + MinerU 扫描 OCR（推荐）；B) 仅 MinerU（版面+OCR 一体，接受表格结构与许可条款差异）；C) P1 双 POC 后凭数据定。
2. **磁盘/下载授权**：允许安装 torch CPU + 模型权重（P1 约 3GB；选 A 全量约 4–5GB）？
3. **P5 chart2csv（自动图表数值化）**是否纳入本期（权重再 +1–3GB，且产出永远走未验证通道）？
4. P2a（UI 手工框选修正）是否立即先行实施（半天工作量，不依赖上述决策）？
