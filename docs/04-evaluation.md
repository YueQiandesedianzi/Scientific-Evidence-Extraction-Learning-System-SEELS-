# 评估报告：金标准对照与语料库端到端结果

日期：2026-11-14。全部数字来自可复现运行（命令附后），非估计值。

## 1. 评估设计

| 层 | 材料 | 金标准性质 |
|---|---|---|
| 单元/集成 | 合成论文 fixture（tests/conftest.py 用 fpdf2+matplotlib 确定性生成：正文事实句、带框线数据表、图+图注） | **构造即金标准**：每个数值在生成代码里写死，精确到小数位（data/gold/synthetic_paper_gold.json，8 项） |
| 真实论文 | arXiv 2611.17283《SiC-BN composites with extreme thermal and environmental stability》76 页 | **部分金标准 8 项**（data/gold/arxiv_2611.17283_partial_gold.json）：由本会话代理逐句核读解析文本后转录，**文件内已如实标注 PROVISIONAL、需领域专家复核**；故意混入 rules-v1 预期找不到的条目以暴露真实召回 |
| 语料库 | 12 篇 arXiv 论文（cond-mat.mtrl-sci ×6、physics.app-ph ×2、cond-mat.supr-con ×2、cond-mat.mes-hall ×2），280 页 | 无金标准；验证流水线鲁棒性、产量与 QC 行为 |

## 2. 合成论文（精确金标准）

- 金标准 8 项（2 项正文 Tc + 6 项表格：Tc/电导率/电阻率 × Sample A/B）：**8/8 MATCHED，相对误差全部 0.000**（tests/test_extraction.py::test_gold_standard_full_match，pytest 守护）。
- 篡改检测：把金标准值改为 120 → 正确报 VALUE_MISMATCH；追加不存在的 coercivity 条目 → 正确报 MISSING（test_gold_compare_detects_tampering）。
- 区间防截断："efficiency of 12-15 %" 未被截断为 12，而是生成 null+reason=range_not_single_value 的 EX_NULL_FIELD 标记（test_uncertain_fields_are_null_with_reason）。
- 条件与样品关联：表格测量全部带上图注条件 temperature=300 K（含证据 ID）；Sample A/B 正确注册并关联（test_conditions_and_samples_attached）。
- 标准化：0.0083 Ohm*cm → 8.3e-5 Ohm*m，方法字符串留痕；原始值/单位不变（test_standardization_fields）。

## 3. 真实论文部分金标准（arXiv 2611.17283）

命令：python -m seels gold-compare P-455fe33f2b5fab9a --gold data/gold/arxiv_2611.17283_partial_gold.json

| # | 金标准条目（原文照录） | 结果 | 相对误差 |
|---|---|---|---|
| 0 | thermal_conductivity 11.57 W/mK（p28 正文） | MATCHED | 0.000 |
| 1 | 1850 kg/m3（密度 ρ，p28 正文） | MATCHED | 0.000 |
| 2 | 8.9 mm（高斯光斑 σ，p28 正文） | MATCHED | 0.000 |
| 3 | -1.051549（p40 表格，界面能，单位未在表中印刷） | MATCHED | 0.000 |
| 4 | heat_flux 1.21 MW/m2（"Gaussian flux of 1.21 MW/m2"） | MISSING（预期内：量名不在模式表） | — |
| 5 | specific_heat 750 J/Kg-K（"cp =750"） | MISSING（预期内：符号 cp 未覆盖） | — |
| 6 | thermal_conductivity 11.65 W/mK（"kSiC-BN =11.65"，模型输入） | VALUE_MISMATCH* | — |
| 7 | temperature 1504 C（"T₁ =1504 °C"，下标符号） | MISSING（预期内） | — |

召回 4/8 = 50%；**匹配项误差全部为 0.000；未发现任何编造数值**。

*第 6 项说明：抽取器实际未提取 11.65（符号句式 kSiC-BN = 值 不在模式内），对照器的"近失诊断"（阈值 0.5）把它配到了邻近的 11.57——两者其实是不同物理量（复材实测值 vs 模型输入值）。这如实暴露了两点：rules-v1 的召回缺口，以及近失诊断仅供参考、必须人工判读。

符号歧义处理验证：σ=8.9 mm 与 ρ=1850 kg/m3 在库中的 quantity 为 symbol_sigma_ambiguous / symbol_rho_ambiguous 并带 CONDITIONS(info) 标记——系统不猜"σ 是电导率还是粗糙度"，交人工判定（该论文中 σ 实为高斯光斑宽度、ρ 实为密度，证明歧义标记是正确行为）。

## 4. 12 篇语料库端到端（ingest→extract→figures→qc→cards→export）

命令：python scripts/corpus_report.py（报告 data/work/corpus_report.json）

| arXiv | 页 | 类型 | 表 | 测量 | 证据 | 图区 | 未决QC(info/warn) | 秒 |
|---|---|---|---|---|---|---|---|---|
| 2611.16125 | 11 | native | 9 | 2 | 10 | 6 | 9/0 | 1.2 |
| 2611.16252 | 7 | native | 10 | 1 | 1 | 6 | 9/0 | 1.2 |
| 2611.16841 | 21 | native | 0 | 0 | 0 | 6 | 6/0 | 2.0 |
| 2611.16912 | 21 | native | 9 | 1 | 3 | 5 | 9/0 | 3.2 |
| 2611.17001 | 7 | native | 6 | 3 | 8 | 3 | 7/0 | 1.2 |
| 2611.17096 | 7 | native | 4 | 0 | 0 | 3 | 3/0 | 1.6 |
| 2611.17196 | 14 | native | 4 | 0 | 4 | 10 | 10/0 | 2.6 |
| 2611.17217 | 36 | native | 104 | 0 | 6 | 6 | 8/0 | 2.7 |
| 2611.17222 | 32 | native | 12 | 0 | 5 | 7 | 9/0 | 2.4 |
| 2611.17283 | 76 | native | 23 | 180 | 378 | 8 | 151/1 | 1.4 |
| 2611.17310 | 14 | native | 0 | 0 | 0 | 7 | 7/0 | 3.1 |
| 2611.17335 | 34 | native | 8 | 0 | 0 | 5 | 9/0 | 1.8 |
| **合计** | **280** | 12×native | 189 | **187** | **415** | **72** | 238 | **25.8（缓存热）** |

观察与解读（如实）：
1. **解析层稳健**：280 页 0 失败页、0 回退页（arXiv 均为原生 PDF）；首轮冷解析共 ~169s，最慢单篇 92.8s（36 页含 104 个表状结构——pdfplumber 表检测在公式/对齐线密集页较慢且有假阳性）。
2. **抽取产量与论文类型强相关**：数据表密集型材料论文（17283）产出 180 条测量；理论/模拟类论文（16841、17310 等）几乎无可规则抽取的"样品-数值"结构 → 0 条。**0 是诚实结果**：系统没有为凑数而放宽证据纪律；未决 QC 标记（EX_NULL_FIELD 等）如实记录了"看到了数字但无法安全归属"的情形。
3. **QC 行为**：238 条未决标记中 237 info / 1 warning；warning 为跨源一致性提示。high_risk 在最终规则下为 0（此前版本的表头误报已由"表单元格证据按解析表结构校验"修复）。
4. **图区提取**：72 个候选曲线/图区（图注+内嵌图 bbox 启发式），全部 unverified 状态等待人工校准/数字化——符合"曲线数据必须人工校准备案"的设计。
5. 学习卡 187 张（每条测量一张，模板生成，事实字段引用证据 ID）。

## 5. 自动化测试

pytest：**41 项全部通过**（tests/：units 7、db 4、parsing 5、extraction 8、curves 5、qc 8、export 2、server 3；另 conftest 生成原生/模拟扫描两种 fixture）。覆盖：坐标换算标定、原生/扫描判别、双引擎回退与差异日志、断点恢复、原始 PDF 哈希不变、金标准全匹配、篡改检测、LLM 未落地 quote 拒绝入库、曲线校准真值恢复（(5,11)→误差<0.05/0.15）、叠加残差、auto/LLM 曲线永不自动 verified、七项 QC 逐项触发、导出策略、审核状态机与 append-only。

## 6. 复现命令

    python -m venv --system-site-packages .venv
    .venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
    python scripts\fetch_arxiv.py            # 下载 12 篇测试语料（需外网）
    python scripts\corpus_report.py          # 全语料流水线 + corpus_report.json
    python -m seels gold-compare P-455fe33f2b5fab9a --gold data\gold\arxiv_2611.17283_partial_gold.json
    python -m pytest tests -q                # 41 passed
    python -m seels serve                    # 复核 UI http://127.0.0.1:8765
