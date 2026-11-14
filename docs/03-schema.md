# SEELS 数据 Schema

存储：SQLite（WAL），DDL 见 seels/db.py（本文与代码同步维护）。
坐标约定：top_left_pts（左上原点，PDF 点，y 向下）。时间：UTC ISO-8601。

## 1. 实体关系

    Paper 1─N Sample 1─N Measurement N─M Evidence   （measurement_evidence，含 role）
    Paper 1─N Evidence（每条证据冗余记录 pdf_sha256 + pdf_path，自包含可校验）
    Paper 1─N Curve 1─N CurvePoint
    Review（append-only）→ 任意 target（measurement/sample/curve/evidence/learning_card）
    LearningCard 1─1 Measurement（target_type+target_id 唯一）
    QcFlag → 任意 target；ExtractionRun 1─N Measurement；ParseLog → Paper(+page)

**一条 Measurement 允许多条 Evidence**（role: primary 主证据 / supporting 辅助(表头、图注) / cross_check 交叉 / contradicting 矛盾）。样品字段级证据经 sample_evidence(sample_id, evidence_id, field_path) 关联。

## 2. 核心表（摘要）

    papers(paper_id PK, source_path, sha256 UNIQUE, title, authors, doi, arxiv_id,
           n_pages, pdf_kind[native|scanned|mixed|unknown],
           parse_status[pending|parsed|partial|failed], parse_json_path, meta_json, ingested_at)

    evidence(evidence_id PK, paper_id FK, page INT NOT NULL,        -- 1-based
             bbox_json, coord_system DEFAULT 'top_left_pts',
             quote_text,                                            -- 原文逐字引用
             element_kind[text|table_cell|table|figure|caption|curve],
             element_ref,                                           -- 人类可读定位，如 Table#2 p5 [r3,c1]
             source_type[native_text|table|figure|caption|ocr|curve],
             extraction_method,                                     -- rules-v1 / llm:openai-compat / manual...
             review_status[unreviewed|accepted|rejected|uncertain],
             pdf_sha256 NOT NULL, pdf_path NOT NULL, created_at)

    measurements(measurement_id PK, paper_id FK, sample_id FK NULL, run_id FK NULL,
                 quantity, value_raw, unit_raw,                     -- 原始值/原始单位永远保留
                 value_std, unit_std, std_method, std_null_reason,  -- 标准化独立字段（可空+原因）
                 conditions_json, test_method,
                 source_type[text|table|curve], extraction_method,
                 status[candidate|accepted|rejected|uncertain|needs_human_review], created_at)

    measurement_evidence(measurement_id FK, evidence_id FK, role, PK(measurement_id,evidence_id,role))

    samples(sample_id PK, paper_id FK, name, fields_json, notes, created_at)
    sample_evidence(sample_id FK, evidence_id FK, field_path, PK(sample_id,evidence_id,field_path))

    curves(curve_id PK, paper_id FK, page, figure_ref, image_path, image_sha256,
           region_bbox_json, calibration_json,
           method[manual|wpd_import|auto_trace|llm_guess], series_name,
           verification_status[unverified|redraw_ok|human_verified], notes, created_at)

    curve_points(point_id PK, curve_id FK, x_raw, y_raw,            -- 数据坐标（原始）
                 x_std, y_std,                                      -- 标准化（可空）
                 pixel_x, pixel_y,                                  -- 图像像素（点取来源必有）
                 source[manual_click|wpd_import|auto_trace|llm_guess])

    reviews(review_id PK AUTOINC, paper_id, target_type, target_id,
            action[accept|modify|reject|flag_uncertain|reopen|back_to_evidence],
            old_value_json, new_value_json, comment, reviewer, created_at)   -- 只增不改不删

    learning_cards(card_id PK, paper_id, target_type, target_id,
                   dimensions_json,        -- 六维，见 §4
                   ai_explanation, ai_source[rule_template|llm:<model>|human],
                   created_at, updated_at, UNIQUE(target_type,target_id))

    qc_flags(flag_id PK AUTOINC, paper_id, target_type, target_id, check_code,
             severity[info|warning|high_risk], message, details_json,
             resolved, resolved_by, resolved_at, created_at)

    extraction_runs(run_id PK, paper_id FK, extractor, extractor_version,
                    config_json, status[running|done|failed], stats_json,
                    started_at, finished_at)

    parse_logs(log_id PK AUTOINC, paper_id FK, page, engine, level[info|warning|error],
               code, message, details_json, created_at)

## 3. FieldVal 契约（字段级溯源）

样品字段与测量条件使用统一的 FieldVal 形状（存于 fields_json / conditions_json）：

    {"value": <any|null>,          # 解析后的值；缺失时 null
     "unit":  <str|null>,          # 原始单位
     "raw":   <str|null>,          # 原文片段（逐字）
     "evidence_ids": ["EV-...", ..],  # ≥1（value 非空时强制）
     "null_reason": <str|null>}    # value 为 null 时必填（如 no_formula_pattern_detected）

规则：**value 与 null_reason 互斥**；有值必有证据；无值必有原因。抽取器内部先以 evidence_idx（整数索引）传递，pipeline._materialize 统一替换为真实证据 ID（_resolve_idx）。

## 4. 六维学习卡（dimensions_json）

六个固定维度（learning.DIMENSIONS），每维 {"content": str, "evidence_ids": [...], "null_reason": str|null}：

1. research_question 研究问题（论文标题/类型/页数）
2. sample_and_composition 样品与组成（未解析出样品时 null_reason=sample_not_resolved_for_this_measurement）
3. conditions_and_method 条件与方法（含"未见记录"如实标注）
4. key_result 关键结果（原始值+标准化值+方法/失败原因）
5. reliability_and_limits 可靠性与局限（证据条数、抽取方法、审核状态、未决 QC 标记）
6. transferable_insight 可迁移洞见（对比注意事项 + 回跳原文指引）

## 5. 状态机

    measurement.status:
      candidate ──(人工 accept)──▶ accepted ──(reopen)──▶ candidate
      candidate ──(人工 modify+重算)──▶ accepted（旧值保存在 reviews.old_value_json）
      candidate ──(人工 reject)──▶ rejected
      candidate ──(人工 flag_uncertain)──▶ uncertain
      candidate ──(QC C7 高风险)──▶ needs_human_review ──(人工处置)──▶ accepted/rejected/...
      * QC 永不改动 accepted/rejected（人工决定优先）

    curve.verification_status:
      unverified ──(校准+点数≥2+残差≤3px+非auto源)──▶ redraw_ok ──(人工 accept)──▶ human_verified
      auto_trace/llm_guess 来源：强制 unverified + high_risk flag，人工 accept 才可 human_verified

    evidence.review_status: 随所属测量的 accept/reject 联动，亦可单独复核。

## 6. parse.json（解析产物，data/work/papers/<paper_id>/parse.json）

    {"schema_version": "1.0", "paper_id", "pdf": {path, sha256, bytes, path_rel},
     "classification": {"kind": "native|scanned|mixed", "scanned_pages": [], "failed_pages": []},
     "n_pages", "pages": [
        {"index", "page_no", "width_pts", "height_pts", "engine",
         "status": "ok|fallback|needs_ocr|failed", "char_count", "garbled",
         "words": [{"text","x0","top","x1","bottom"}],
         "tables": [{"table_index","bbox","n_rows","n_cols","rows","cells":[{"r","c","text","bbox"}]}],
         "images": [{"bbox","pixel_width","pixel_height"}],
         "captions": [{"kind":"figure|table","ref":"Figure 3","text","bbox"}],
         "text", "warnings", "fallback": {另一引擎摘要 + reason}}],
     "stats", "created_at", "updated_at"}

## 7. 导出契约

- 审计导出（9 表 CSV / Excel 工作簿）：全字段 + 证据链接列（evidence_ids/pages/kinds/first_quote）。
- ML 数据集 dataset_measurements.csv：入库策略 value_std 非空 ∧ status≠rejected ∧ n_evidence≥1；列含溯源字段；manifest.json 记录列说明、策略与被排除计数——**策略显式声明，绝不静默过滤**。
