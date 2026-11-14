"""Six-dimension learning cards (六维学习卡) + AI explanation.

Cards are generated from *stored, evidenced data only* (rule templates).
An LLM may later enrich ai_explanation (ai_source='llm:<model>'), but card
facts always cite evidence ids; the UI shows provenance for every dimension.

Dimensions:
  1 research_question      - what the paper is about (title/context)
  2 sample_and_composition - which sample the number belongs to
  3 conditions_and_method  - under which conditions / how measured
  4 key_result             - the quantitative result itself (raw + std)
  5 reliability_and_limits - evidence count, QC flags, review status, nulls
  6 transferable_insight   - methodological takeaway / comparison hint
"""
from __future__ import annotations

from .util import new_id, utc_now

DIMS = ("research_question", "sample_and_composition", "conditions_and_method",
        "key_result", "reliability_and_limits", "transferable_insight")

DIM_LABELS = {
    "research_question": "研究问题",
    "sample_and_composition": "样品与组成",
    "conditions_and_method": "条件与方法",
    "key_result": "关键结果",
    "reliability_and_limits": "可靠性与局限",
    "transferable_insight": "可迁移洞见",
}


def _dim(content: str, evidence_ids=None, null_reason: str = None) -> dict:
    return {"content": content, "evidence_ids": list(evidence_ids or []),
            "null_reason": null_reason}


def build_card_for_measurement(db, measurement_id: str) -> dict:
    m = db.get_measurement(measurement_id)
    if not m:
        raise KeyError(measurement_id)
    paper = db.get_paper(m["paper_id"]) or {}
    evs = m.get("evidence") or []
    ev_ids = [e["evidence_id"] for e in evs]
    dims = {}

    dims["research_question"] = _dim(
        "论文《%s》（%d 页，%s PDF）。" % (paper.get("title") or "标题未知",
                                        paper.get("n_pages") or -1,
                                        paper.get("pdf_kind") or "?"))
    sample = db.get_sample(m["sample_id"]) if m.get("sample_id") else None
    if sample:
        ident = (sample.get("fields") or {}).get("identity") or {}
        formula = (ident.get("formula") or {}).get("value")
        dims["sample_and_composition"] = _dim(
            "样品：%s%s" % (sample.get("name"), ("（%s）" % formula) if formula else ""),
            (ident.get("name") or {}).get("evidence_ids") or ev_ids)
    else:
        dims["sample_and_composition"] = _dim(
            "", None, null_reason="sample_not_resolved_for_this_measurement")

    cond_parts = []
    for k, v in (m.get("conditions") or {}).items():
        if isinstance(v, dict) and v.get("value") is not None:
            cond_parts.append("%s=%s %s" % (k, v.get("value"), v.get("unit") or ""))
        elif isinstance(v, dict):
            cond_parts.append("%s=%s(null:%s)" % (k, v.get("raw"), v.get("null_reason")))
    dims["conditions_and_method"] = _dim(
        "条件：%s；方法：%s；来源类型：%s" % ("; ".join(cond_parts) or "未见记录",
                                          m.get("test_method") or "未见记录",
                                          m.get("source_type")), ev_ids)

    std_txt = ""
    if m.get("value_std") is not None:
        std_txt = "（标准化：%g %s，%s）" % (m["value_std"], m.get("unit_std") or "",
                                        m.get("std_method") or "")
    else:
        std_txt = "（标准化失败：%s）" % (m.get("std_null_reason") or "n/a")
    dims["key_result"] = _dim(
        "%s = %s %s%s" % (m.get("quantity") or "quantity?", m.get("value_raw"),
                          m.get("unit_raw") or "", std_txt), ev_ids)

    flags = db.list_qc_flags(target_type="measurement", target_id=measurement_id,
                             resolved=False)
    dims["reliability_and_limits"] = _dim(
        "证据 %d 条；抽取方法：%s；审核状态：%s；未决 QC 标记 %d 个%s" % (
            len(evs), m.get("extraction_method"), m.get("status"), len(flags),
            ("：" + "; ".join(f["check_code"] + ":" + f["message"][:60] for f in flags[:4]))
            if flags else ""),
        ev_ids + [e["evidence_id"] for e in evs])

    dims["transferable_insight"] = _dim(
        "对比同论文/同数据库中相同 quantity 的其他样品时，注意测试条件是否一致；"
        "该数据来自 %s，可直接跳转原文位置核对。" % (m.get("source_type") or "?"), ev_ids)

    ai_explanation = (
        "该记录从论文第 %s 页的%s中提取：%s = %s %s。%s" % (
            evs[0]["page"] if evs else "?",
            {"table": "表格单元格", "text": "正文语句", "curve": "曲线数字化"}.get(m.get("source_type"), "来源"),
            m.get("quantity") or "指标", m.get("value_raw"), m.get("unit_raw") or "",
            "所有字段均可点击证据回跳原文核对；未通过核验的字段以 null+原因呈现。"))
    card = {
        "card_id": new_id("LC"), "paper_id": m["paper_id"],
        "target_type": "measurement", "target_id": measurement_id,
        "dimensions": dims, "ai_explanation": ai_explanation,
        "ai_source": "rule_template", "created_at": utc_now(),
    }
    db.upsert_learning_card(card)
    return card


def generate_cards_for_paper(db, paper_id: str, limit: int = 200) -> int:
    ms = db.list_measurements(paper_id=paper_id, limit=limit)
    n = 0
    for m in ms:
        build_card_for_measurement(db, m["measurement_id"])
        n += 1
    return n
