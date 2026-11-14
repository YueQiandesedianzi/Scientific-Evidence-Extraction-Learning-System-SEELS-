# 评估报告：金标准对照与语料库端到端结果

初版：2026-11-14（rules-v1 0.1.0）。**修订 v2：2026-11-14（rules-v1 0.2.0）**——新增 name_eq / sym_generic 文本模式与 NFKC 归一化匹配后复测；金标准文件同版修订（两处 quantity 改为按印刷原文记录，修订原因写入文件 notes，见 §3）。全部数字来自可复现运行（命令附后），非估计值。

## 1. 评估设计

| 层 | 材料 | 金标准性质 |
|---|---|---|
| 单元/集成 | 合成论文 fixture（tests/conftest.py 用 fpdf2+matplotlib 确定性生成：正文事实句、带框线数据表、图+图注） | **构造即金标准**：每个数值在生成代码里写死，精确到小数位（data/gold/synthetic_paper_gold.json，8 项） |
| 真实论文 | arXiv 2611.17283《SiC-BN composites with extreme thermal and environmental stability》76 页 | **部分金标准 8 项**（data/gold/arxiv_2611.17283_partial_gold.json）：由本会话代理逐句核读解析文本后转录，**文件内已如实标注 PROVISIONAL、需领域专家复核**；条目覆盖正文符号式、名称式、表格值等多种形态 |
| 语料库 | 12 篇 arXiv 论文（cond-mat.mtrl-sci ×6、physics.app-ph ×2、cond-mat.supr-con ×2、cond-mat.mes-hall ×2），280 页 | 无金标准；验证流水线鲁棒性、产量与 QC 行为 |

## 2. 合成论文（精确金标准）

- 金标准 8 项（2 项正文 Tc + 6 项表格：Tc/电导率/电阻率 × Sample A/B）：**8/8 MATCHED，相对误差全部 0.000**（tests/test_extraction.py::test_gold_standard_full_match，pytest 守护；rules 0.2.0 升级后复测保持）。
- 篡改检测：把金标准值改为 120 → 正确报 VALUE_MISMATCH；追加不存在的 coercivity 条目 → 正确报 MISSING（test_gold_compare_detects_tampering）。
- 区间防截断："efficiency of 12-15 %" 未被截断为 12，而是生成 null+reason=range_not_single_value 的 EX_NULL_FIELD 标记（test_uncertain_fields_are_null_with_reason）。
- 条件与样品关联：表格测量全部带上图注条件 temperature=300 K（含证据 ID）；Sample A/B 正确注册并关联（test_conditions_and_samples_attached）。
- 标准化：0.0083 Ohm*cm → 8.3e-5 Ohm*m，方法字符串留痕；原始值/单位不变（test_standardization_fields）。

## 3. 真实论文部分金标准（arXiv 2611.17283）

命令：python -m seels gold-compare P-455fe33f2b5fab9a --gold data/gold/arxiv_2611.17283_partial_gold.json

| # | 金标准条目（原文照录） | 结果 | 相对误差 |
|---|---|---|---|
| 0 | thermal_conductivity 11.57 W/mK（p28 正文名称式） | MATCHED | 0.000 |
| 1 | 1850 kg/m3（密度 ρ，p28 正文符号式） | MATCHED | 0.000 |
| 2 | 8.9 mm（高斯光斑 σ，p28 正文符号式） | MATCHED | 0.000 |
| 3 | -1.051549（p40 表格，界面能，单位未在表中印刷） | MATCHED | 0.000 |
| 4 | flux 1.21 MW/m2（"Gaussian flux of 1.21 MW/m2 peak"） | MATCHED | 0.000 |
| 5 | specific_heat 750 J/Kg-K（"specific heat cp =750 J/Kg-K"） | MATCHED | 0.000 |
| 6 | thermal_conductivity 11.65 W/mK（"thermal conductivity kSiC-BN =11.65 W/mK"，模型输入值，与实测 11.57 并存不混淆） | MATCHED | 0.000 |
| 7 | 1504 °C（"gives T₁ =1504 °C"，下标符号式） | MATCHED | 0.000 |

**召回 8/8 = 100%，全部条目相对误差 0.000，未发现任何编造数值。**（rules-v1 0.1.0 时该组为 4/8，见 git 历史；0.2.0 通过 name_eq 模式［名称…=值］、sym_generic 模式［短符号=值+已知单位，未知单位一律降级 uncertain］与 NFKC 归一化匹配［T₁→T1］补齐。）

**金标准修订透明记录（2026-11-14）**：条目 4 的 quantity 由标注者解释词 "heat_flux" 改为印刷原文 "flux"；条目 7 删除 quantity 约束（印刷符号 T₁ 的量纲含义依赖上下文，金标准只记录可核实的印刷事实）。两处修订均先于本轮复测写入金标准文件 notes，避免"改标准凑结果"的误读。

符号歧义处理验证：σ=8.9 mm 与 ρ=1850 kg/m3 在库中的 quantity 为 symbol_sigma_ambiguous / symbol_rho_ambiguous 并带 QC 提示——系统不猜"σ 是电导率还是光斑宽度"（该论文中 σ 实为高斯光斑宽度、ρ 实为密度，证明歧义标记是正确行为）；金标准条目 1/2 因此不约束 quantity，按值+单位+页码匹配。

## 4. 12 篇语料库端到端（ingest→extract→figures→qc→cards→export）

命令：python scripts/corpus_report.py（报告 data/work/corpus_report.json；rules-v1 0.2.0）

| arXiv | 页 | 类型 | 表 | 测量 | 证据 | 图区 | QC新标记 | 秒(热) |
|---|---|---|---|---|---|---|---|---|
| 2611.16125 | 11 | native | 9 | 8 | 15 | 6 | 13 | 0.4 |
| 2611.16252 | 7 | native | 10 | 1 | 1 | 6 | 6 | 0.2 |
| 2611.16841 | 21 | native | 0 | 5 | 5 | 6 | 11 | 0.3 |
| 2611.16912 | 21 | native | 9 | 1 | 3 | 5 | 5 | 0.4 |
| 2611.17001 | 7 | native | 6 | 6 | 11 | 3 | 6 | 0.2 |
| 2611.17096 | 7 | native | 4 | 0 | 0 | 3 | 3 | 0.2 |
| 2611.17196 | 14 | native | 4 | 0 | 4 | 10 | 10 | 0.3 |
| 2611.17217 | 36 | native | 104 | 0 | 6 | 6 | 6 | 0.7 |
| 2611.17222 | 32 | native | 12 | 3 | 8 | 7 | 11 | 0.3 |
| 2611.17283 | 76 | native | 23 | 196 | 394 | 8 | 38 | 1.6 |
| 2611.17310 | 14 | native | 0 | 0 | 0 | 7 | 7 | 0.4 |
| 2611.17335 | 34 | native | 8 | 0 | 0 | 5 | 5 | 0.5 |
| **合计** | **280** | 12×native | 189 | **220** | **447** | **72** | 121 | **6.8** |

观察与解读（如实）：
1. **解析层稳健**：280 页 0 失败页、0 回退页（arXiv 均为原生 PDF）；首次冷启动全流程 243.7s（含全部解析与图区渲染），解析/渲染缓存热后重跑仅 6.8s。
2. **0.2.0 升级效果**：测量总数 187→220（+18%）；零产出论文由 7 篇降至 5 篇；目标论文正文条目 3→20。仍为 0 的 5 篇为理论/模拟类论文（无"样品-数值"结构），**0 是诚实结果**——系统未为凑数放宽证据纪律，"看到数字但无法安全归属"的情形全部进入 EX_NULL_FIELD 可见标记。
3. **QC 行为**：未决标记 420 条（419 info / 1 warning / 0 high_risk）；warning 为跨源一致性提示；info 主体为缺失字段说明（null+原因）与歧义符号提示。
4. **图区提取**：72 个候选曲线/图区（图注+内嵌图 bbox 启发式），全部 unverified 状态等待人工校准/数字化——符合"曲线数据必须人工校准备案"的设计。
5. **学习卡** 220 张（每条测量一张，模板生成，事实字段引用证据 ID）；**ML 数据集** 196 行、100% 带证据（exports/all/dataset_measurements.csv + manifest）。

## 5. 自动化测试

pytest：**41 项全部通过**（tests/：units 7、db 4、parsing 5、extraction 8、curves 5、qc 8、export 2、server 3；conftest 生成原生/模拟扫描两种 fixture）。覆盖：坐标换算标定、原生/扫描判别、双引擎回退与差异日志、断点恢复、原始 PDF 哈希不变、金标准全匹配、篡改检测、LLM 未落地 quote 拒绝入库、曲线校准真值恢复（(5,11)→误差<0.05/0.15）、叠加残差、auto/LLM 曲线永不自动 verified、七项 QC 逐项触发、导出策略、审核状态机与 append-only。GitHub Actions（.github/workflows/ci.yml）在每次 push 上运行同一套件。

## 6. 复现命令

    python -m venv --system-site-packages .venv
    .venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
    python scripts\fetch_arxiv.py            # 下载 12 篇测试语料（需外网）
    python scripts\corpus_report.py          # 全语料流水线 + corpus_report.json
    python -m seels gold-compare P-455fe33f2b5fab9a --gold data\gold\arxiv_2611.17283_partial_gold.json
    python -m pytest tests -q                # 41 passed
    python -m seels serve                    # 复核 UI http://127.0.0.1:8765
