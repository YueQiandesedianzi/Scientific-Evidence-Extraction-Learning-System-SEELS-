"""Render the SEELS architecture diagram to docs/assets/architecture.png."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(13.6, 9.2), dpi=150)
ax.set_xlim(0, 136)
ax.set_ylim(0, 96)
ax.axis("off")


def box(x, y, w, h, title, body, fc, ec, tfs=10.5, bfs=8.4):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.5",
                                fc=fc, ec=ec, lw=1.4, zorder=2))
    ax.text(x + w / 2, y + h - 1.6, title, ha="center", va="top",
            fontsize=tfs, fontweight="bold", color="#111827", zorder=3)
    if body:
        ax.text(x + w / 2, y + (h - 3.2) / 2 + 0.4, body, ha="center", va="center",
                fontsize=bfs, color="#1f2937", zorder=3, linespacing=1.5)


def arrow(x1, y1, x2, y2, label="", color="#374151", rad=0.0, lx=0, ly=0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                                 mutation_scale=15, color=color, lw=1.5,
                                 connectionstyle="arc3,rad=%s" % rad, zorder=1))
    if label:
        ax.text((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label, fontsize=8.2,
                color=color, ha="center", zorder=3,
                bbox=dict(fc="white", ec="none", alpha=0.85, pad=1.2))


BLUE = ("#e8f0fe", "#3b6fd4")
GREEN = ("#e7f6ec", "#2e9e5b")
ORANGE = ("#fdf1dd", "#d98e04")
PURPLE = ("#f1e9fb", "#7c4dcc")
GRAY = ("#eef1f5", "#5b6b80")
RED = ("#fdeaea", "#c24545")

# ---- main pipeline column ----
box(4, 84, 58, 9.5, "输入层：原始 PDF（只读）",
    "sha256 完整性校验（解析前后比对） · 原始文件绝不修改/覆盖 · arXiv 测试语料 ×12", *BLUE)
box(4, 68.5, 58, 12.5, "解析层 seels.parsing（双引擎）",
    "主引擎 pdfplumber (MIT)：词/表格单元格/图片/图注 + bbox\n"
    "第二引擎 pypdfium2 (BSD)：异常页回退 + 交叉校验 + 页面渲染\n"
    "原生/扫描判别 · 差异与错误日志 parse_logs · 断点恢复(resume)\n"
    "输出 parse.json（完整结构化：words/tables/images/captions/text）", *GREEN)
box(4, 51, 58, 14.5, "抽取层 seels.extraction",
    "rules-v1 确定性抽取（表格结构 + 保守文本模式）\n"
    "llm:openai-compat / llm:langextract 适配器（quote 强制回原文定位）\n"
    "定位失败 → 拒绝入库并记录原因 · 缺证据 → null + reason\n"
    "单位解析/标准化（值与单位分离，raw 永远保留）\n"
    "样品身份/组成 · 条件 · 方法 · 性能结果 → 证据强关联", *ORANGE)
box(4, 33.5, 58, 14.5, "曲线层 seels.curves",
    "图区检测（图注+内嵌图 bbox）→ 区域 PNG（页码/DPI/裁剪框留痕）\n"
    "轴校准（线性/log10，≥2 点最小二乘）· 图上点取数字化\n"
    "WebPlotDigitizer CSV 导入（外部人工工具，数据交换集成）\n"
    "曲线重绘 + 原图叠加验证（重投影残差 ≤3px → redraw_ok）\n"
    "auto/LLM 来源点：永不自动 verified + high_risk 标记", *PURPLE)
box(4, 17, 58, 13.5, "存储层 seels.db（SQLite, WAL）",
    "papers · samples · measurements · evidence（页码+bbox+引文+PDF哈希）\n"
    "measurement_evidence（一条测量对应多条证据） · curves · curve_points\n"
    "reviews（append-only 审计） · learning_cards（六维） · qc_flags\n"
    "extraction_runs · parse_logs（可复现/可追溯/断点恢复）", *GRAY)
box(4, 4, 58, 10, "质量控制层 seels.qc（7 项确定性检查）",
    "C1 证据完整性 · C2 数值与单位 · C3 重复检测 · C4 条件关联\n"
    "C5 跨源一致性 · C6 曲线回绘 · C7 高风险→needs_human_review\n"
    "LLM 自评置信度 ≠ 正确性证明（仅人工复核可 accept）", *RED)

# ---- right column ----
box(70, 55, 62, 25.5, "复核与学习 Web UI（seels.ui，stdlib HTTP + 原生 JS，离线）",
    "左侧：PDF 页面渲染（pypdfium2 缓存 PNG）+ 证据区域 SVG 高亮\n"
    "（点击证据框 → 反查关联测量）+ 曲线面板（region/overlay/redraw）\n"
    "右侧：结构化数据 · AI 解释 · 六维学习卡 · 异常(QC) · 审核记录\n"
    "操作：接受 / 修改(重算标准化) / 驳回 / 标记存疑 / 返回原始证据\n"
    "每次操作 append 到 reviews（旧值→新值全留痕）· 曲线人工核验\n"
    "曲线工具：轴校准拾取 · 点取数字化 · WPD CSV 导入", *BLUE, tfs=10)
box(70, 40, 62, 12, "导出层 seels.export",
    "9 张审计表 CSV（utf-8-sig）· Excel 工作簿（含 README 表）\n"
    "ML 就绪数据集 dataset_measurements.csv + manifest.json\n"
    "（入库策略显式声明：value_std 非空 ∧ 非 rejected ∧ 证据≥1）\n"
    "金标准对照报告 gold_compare.csv/json", *GREEN, tfs=10)
box(70, 24, 62, 13, "CLI 与可复现性（python -m seels …）",
    "init / ingest / extract / figures / qc / cards / export / serve\n"
    "gold-compare / demo · pytest 41 项自动化测试\n"
    "合成论文＝构造即金标准（确定性 fixture）· scripts/fetch_arxiv.py", *GRAY, tfs=10)
box(70, 4, 62, 17, "二期可选增强（接口已预留，MVP 不依赖）",
    "docling：TableFormer 表格 / chart2csv 图表数值 / OCR（MIT 代码）\n"
    "MinerU：扫描 PDF OCR + block 级 bbox（Apache2+附加条款，需合规审查）\n"
    "WebPlotDigitizer 自托管（AGPL 隔离，人工数字化）\n"
    "docling-graph：证据知识图谱（MIT，项目年轻）\n"
    "不采用：ChartRecover（无许可/权重缺失/停更） · 复杂 Agent 框架", *ORANGE, tfs=10)

# ---- arrows ----
arrow(33, 84, 33, 81.3)                                   # pdf -> parsing
arrow(33, 68.5, 33, 65.8, "parse.json")                   # parsing -> extraction
arrow(33, 51, 33, 48.3, "测量+样品+证据")                   # extraction -> curves? no: to storage below
arrow(62, 42, 66, 42)
arrow(33, 33.5, 33, 30.8, "曲线+点+验证状态")               # curves -> storage
arrow(33, 17, 33, 14.3)                                   # storage <-> qc (double)
arrow(30, 14.3, 30, 16.9, color="#c24545")
arrow(66, 24, 62, 24, color="#3b6fd4", label="")           # storage -> ui
arrow(62, 62, 70, 62, color="#3b6fd4")
ax.text(65.4, 63.2, "读", fontsize=8, color="#3b6fd4", ha="center")
arrow(70, 58, 62, 27, color="#3b6fd4", rad=-0.25)
ax.text(67.5, 45, "审核写入(reviews/状态)", fontsize=8, color="#3b6fd4", ha="left")
arrow(62, 20.5, 70, 44, color="#2e9e5b", rad=-0.2)
ax.text(64.5, 33, "导出", fontsize=8, color="#2e9e5b", ha="center")
arrow(96, 40, 96, 37.4, color="#2e9e5b")
arrow(96, 24, 96, 21.4, color="#5b6b80")
arrow(62, 55, 70, 20, color="#d98e04", rad=0.3)
ax.text(72, 30, "抽取触发", fontsize=8, color="#d98e04", ha="left")

ax.text(68, 92.5, "SEELS · Scientific Evidence Extraction & Learning System（MVP 架构）",
        fontsize=13, fontweight="bold", ha="center", color="#111827")
ax.text(68, 89.3, "原则：可追溯 · 可验证 · 可复现 · 促进人工学习（非无人值守）；证据纪律：无证据不入库，缺信息 null+原因，LLM 置信度不作数",
        fontsize=8.6, ha="center", color="#4b5563")

fig.savefig(OUT / "architecture.png", bbox_inches="tight", facecolor="white")
print("saved:", OUT / "architecture.png")
