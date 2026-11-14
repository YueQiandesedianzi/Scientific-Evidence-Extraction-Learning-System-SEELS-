"""Core data model (dataclasses). SQLite rows are dict-based; these classes
document the canonical shape shared by pipeline, API and UI.

Provenance rules:
- Evidence carries: pdf path + sha256, page, bbox (top_left_pts), quote text,
  element kind/ref (e.g. Table#2[r3,c1], Figure 4a), source type, extraction
  method, review status.
- A Measurement links to >=1 Evidence via measurement_evidence (roles:
  primary/supporting/cross_check/contradicting).
- Raw value and raw unit are always preserved; standardized value/unit are
  separate nullable fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Evidence:
    evidence_id: str
    paper_id: str
    page: int                       # 1-based
    bbox: Optional[list]            # [x0,y0,x1,y1] top-left pts
    coord_system: str = "top_left_pts"
    quote_text: str = ""
    element_kind: str = "text"      # text|table_cell|table|figure|caption|curve
    element_ref: str = ""           # human-readable locator, e.g. Table#2[r3,c1]
    source_type: str = "native_text"  # native_text|table|figure|caption|ocr|curve
    extraction_method: str = ""     # rules-v1|langextract|openai-compat|manual
    review_status: str = "unreviewed"  # unreviewed|accepted|rejected|uncertain
    pdf_sha256: str = ""
    pdf_path: str = ""
    created_at: str = ""


@dataclass
class Sample:
    sample_id: str
    paper_id: str
    name: Optional[str] = None
    # fields_json shape:
    # {"identity": {"name": FieldVal, "formula": FieldVal, "phase": FieldVal, ...},
    #  "composition": [{"component": FieldVal, "amount": FieldVal, "basis": FieldVal}]}
    # FieldVal = {"value": any|None, "unit": str|None, "raw": str|None,
    #             "evidence_ids": [..], "null_reason": str|None}
    fields: dict = field(default_factory=dict)
    notes: str = ""
    created_at: str = ""


@dataclass
class Measurement:
    measurement_id: str
    paper_id: str
    sample_id: Optional[str] = None
    run_id: Optional[str] = None
    quantity: str = ""
    value_raw: Optional[str] = None
    unit_raw: Optional[str] = None
    value_std: Optional[float] = None
    unit_std: Optional[str] = None
    std_method: Optional[str] = None      # e.g. "unit_table:MPa->Pa x1e6" | None + reason in std_null_reason
    std_null_reason: Optional[str] = None
    conditions: dict = field(default_factory=dict)  # {"temperature": FieldVal, ...}
    test_method: Optional[str] = None
    source_type: str = "text"             # text|table|curve
    extraction_method: str = ""
    status: str = "candidate"             # candidate|accepted|rejected|uncertain|needs_human_review
    created_at: str = ""


@dataclass
class Curve:
    curve_id: str
    paper_id: str
    page: Optional[int] = None
    figure_ref: str = ""                  # e.g. "Figure 3a"
    image_path: str = ""                  # figure region image (png)
    image_sha256: str = ""
    region_bbox: Optional[list] = None    # region on the PDF page (top-left pts)
    calibration: Optional[dict] = None    # see curves.calibrate
    method: str = "manual"                # manual|wpd_import|auto_trace|llm_guess
    series_name: str = ""
    verification_status: str = "unverified"  # unverified|redraw_ok|human_verified
    notes: str = ""
    created_at: str = ""


@dataclass
class Review:
    review_id: Optional[int]
    target_type: str                      # measurement|sample|curve|evidence|learning_card
    target_id: str
    action: str                           # accept|modify|reject|flag_uncertain|reopen|back_to_evidence
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    comment: str = ""
    reviewer: str = "anonymous"
    created_at: str = ""


LEARNING_CARD_DIMENSIONS = [
    "research_question",     # 1 research question / motivation
    "sample_and_composition",# 2 sample identity & composition
    "conditions_and_method", # 3 test conditions & method
    "key_result",            # 4 key quantitative result
    "reliability_and_limits",# 5 reliability, anomalies, limits
    "transferable_insight",  # 6 transferable method / insight
]


@dataclass
class LearningCard:
    card_id: str
    target_type: str                      # measurement (primary)
    target_id: str
    dimensions: dict = field(default_factory=dict)  # dim -> {"content": str, "evidence_ids": [...]}
    ai_explanation: str = ""
    ai_source: str = "rule_template"      # rule_template|llm:<model>|human
    created_at: str = ""
    updated_at: str = ""


@dataclass
class QcFlag:
    flag_id: Optional[int]
    target_type: str
    target_id: str
    check_code: str
    severity: str                         # info|warning|high_risk
    message: str = ""
    details: Optional[dict] = None
    resolved: bool = False
    created_at: str = ""


@dataclass
class Paper:
    paper_id: str
    source_path: str
    sha256: str
    title: Optional[str] = None
    n_pages: Optional[int] = None
    pdf_kind: str = "unknown"             # native|scanned|mixed|unknown
    parse_status: str = "pending"         # pending|parsed|partial|failed
    parse_json_path: Optional[str] = None
    meta: dict = field(default_factory=dict)
    ingested_at: str = ""
