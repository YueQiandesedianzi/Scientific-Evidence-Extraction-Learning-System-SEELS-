"""Seven deterministic QC checks.

C1 EVIDENCE_INTEGRITY   every stored value keeps complete, re-locatable evidence
C2 VALUE_UNIT           numbers finite; stored standardization recomputed & verified
C3 DUPLICATES           duplicate samples / duplicate measurements surfaced
C4 CONDITIONS           required test conditions attached; ambiguous quantities flagged
C5 CROSS_SOURCE         table vs text agreement per (sample, quantity)
C6 CURVE_REDRAW         calibration sanity + reprojection residual recomputation
C7 HIGH_RISK_HUMAN      OCR-page / LLM-only / auto-curve data routed to human review

Policy: an LLM's self-reported confidence NEVER marks data correct. QC only
adds flags and may move *candidate* rows to needs_human_review; accepted /
rejected statuses (human decisions) are never changed by QC.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .. import config
from ..extraction.schema import CONDITION_REQUIRED
from ..extraction.units import parse_value_unit, standardize
from ..parsing.pipeline import load_parse
from ..util import locate_quote_in_words, normalize_text

QC_CODES = ("EVIDENCE_INTEGRITY", "VALUE_UNIT", "DUPLICATES", "CONDITIONS",
            "CROSS_SOURCE", "CURVE_REDRAW", "HIGH_RISK_HUMAN")

import re as _re

_TABLE_REF_RE = _re.compile(r"Table#(\\d+)")


def _cell_quote_in_tables(ev: dict, pg: dict) -> bool:
    """True when the evidence quote matches a parsed cell of its table."""
    qn = normalize_text(ev.get("quote_text") or "")
    if not qn:
        return False
    tables = pg.get("tables") or []
    m = _TABLE_REF_RE.search(ev.get("element_ref") or "")
    if m:
        ti = int(m.group(1))
        tables = [t for t in tables if t.get("table_index") == ti] or tables
    for t in tables:
        for cell in t.get("cells") or []:
            if normalize_text(str(cell.get("text") or "")) == qn:
                return True
        for row in t.get("rows") or []:
            for c in row or []:
                if c is not None and normalize_text(str(c)) == qn:
                    return True
    return False

POSITIVE_QUANTITIES = ("electrical_conductivity", "resistivity", "density",
                       "thermal_conductivity", "mass", "length")


def _sig(v: float, n: int = 9) -> float:
    """Round to n significant figures (scale-safe dedup for tiny SI values)."""
    import math
    if v is None or v == 0:
        return 0.0
    d = n - int(math.floor(math.log10(abs(v)))) - 1
    return round(v, d)


def _flag(db, paper_id, ttype, tid, code, severity, message, details=None):
    db.insert_qc_flag({"paper_id": paper_id, "target_type": ttype, "target_id": tid,
                       "check_code": code, "severity": severity,
                       "message": message, "details": details})
    return 1


# ---------------------------------------------------------------- C1
def check_evidence_integrity(db, paper_id, parse_doc):
    n = 0
    paper = db.get_paper(paper_id)
    pages = {p["page_no"]: p for p in (parse_doc or {}).get("pages") or []}
    for m in db.list_measurements(paper_id=paper_id, limit=100000):
        full = db.get_measurement(m["measurement_id"])
        evs = full.get("evidence") or []
        if not evs:
            n += _flag(db, paper_id, "measurement", m["measurement_id"], "EVIDENCE_INTEGRITY",
                       "high_risk", "measurement has no evidence link")
            continue
        for ev in evs:
            if ev.get("pdf_sha256") != paper["sha256"]:
                n += _flag(db, paper_id, "evidence", ev["evidence_id"], "EVIDENCE_INTEGRITY",
                           "high_risk", "evidence pdf hash differs from paper hash")
            if not ev.get("quote_text"):
                n += _flag(db, paper_id, "evidence", ev["evidence_id"], "EVIDENCE_INTEGRITY",
                           "warning", "evidence has empty quote_text")
                continue
            pg = pages.get(ev.get("page"))
            if ev.get("element_kind") == "table_cell" and pg:
                if not _cell_quote_in_tables(ev, pg):
                    n += _flag(db, paper_id, "evidence", ev["evidence_id"],
                               "EVIDENCE_INTEGRITY", "high_risk",
                               "table-cell quote not found among parsed cells of %s"
                               % (ev.get("element_ref") or "page tables"),
                               {"quote": ev["quote_text"][:200]})
                continue
            if pg and pg.get("words") is not None:
                bb, _matched, score = locate_quote_in_words(
                    pg["words"], ev["quote_text"], min_score=config.QUOTE_MATCH_MIN_SCORE)
                if bb is None:
                    in_page_text = normalize_text(ev["quote_text"])[:120] in \
                        normalize_text(pg.get("text") or "")
                    if in_page_text:
                        n += _flag(db, paper_id, "evidence", ev["evidence_id"],
                                   "EVIDENCE_INTEGRITY", "info",
                                   "quote present in page text but not word-locatable "
                                   "(score %.2f); bbox kept as recorded by extractor" % score)
                    else:
                        n += _flag(db, paper_id, "evidence", ev["evidence_id"],
                                   "EVIDENCE_INTEGRITY", "high_risk",
                                   "quote cannot be re-located on page %s (best score %.2f)"
                                   % (ev.get("page"), score),
                                   {"quote": ev["quote_text"][:200]})
    return n


# ---------------------------------------------------------------- C2
def check_value_unit(db, paper_id, parse_doc):
    n = 0
    for m in db.list_measurements(paper_id=paper_id, limit=100000):
        mid = m["measurement_id"]
        if m.get("value_raw") is None:
            if m.get("status") != "uncertain":
                n += _flag(db, paper_id, "measurement", mid, "VALUE_UNIT", "info",
                           "no raw value recorded")
            continue
        parsed = parse_value_unit(
            (str(m["value_raw"]) + (" " + m["unit_raw"] if m.get("unit_raw") else "")).strip())
        if m.get("value_std") is None:
            if not m.get("std_null_reason"):
                n += _flag(db, paper_id, "measurement", mid, "VALUE_UNIT", "warning",
                           "value_std is null without std_null_reason")
        else:
            if parsed["value"] is None:
                n += _flag(db, paper_id, "measurement", mid, "VALUE_UNIT", "warning",
                           "value_std present but value_raw unparseable (%s)" % parsed["reason"])
            else:
                std = standardize(parsed["value"], m.get("unit_raw"))
                if std["value_std"] is None:
                    n += _flag(db, paper_id, "measurement", mid, "VALUE_UNIT", "warning",
                               "stored value_std not reproducible from raw (%s)" % std["reason"])
                elif abs(std["value_std"] - m["value_std"]) > \
                        max(1e-9, abs(std["value_std"]) * 1e-6):
                    n += _flag(db, paper_id, "measurement", mid, "VALUE_UNIT", "warning",
                               "stored value_std=%g differs from recomputed %g"
                               % (m["value_std"], std["value_std"]))
            if m.get("unit_std") and m["value_std"] is not None:
                q = (m.get("quantity") or "")
                if m["unit_std"] == "K" and m["value_std"] <= 0:
                    n += _flag(db, paper_id, "measurement", mid, "VALUE_UNIT", "warning",
                               "non-positive absolute temperature %g K" % m["value_std"])
                if q in POSITIVE_QUANTITIES and m["value_std"] < 0:
                    n += _flag(db, paper_id, "measurement", mid, "VALUE_UNIT", "warning",
                               "negative value for physically positive quantity %s" % q)
    return n


# ---------------------------------------------------------------- C3
def check_duplicates(db, paper_id, parse_doc):
    n = 0
    samples = db.list_samples(paper_id)
    by_name = defaultdict(list)
    for s in samples:
        by_name[normalize_text(s.get("name") or "").lower()].append(s["sample_id"])
    for name, ids in by_name.items():
        if len(ids) > 1:
            n += _flag(db, paper_id, "sample", ",".join(ids), "DUPLICATES", "info",
                       "duplicate sample name within paper: %r" % name)
    # cross-paper same-name samples (informational; may be legitimate)
    for s in samples:
        others = db.conn().execute(
            "SELECT paper_id, sample_id FROM samples WHERE name=? AND paper_id!=? LIMIT 5",
            (s.get("name"), paper_id)).fetchall()
        if others:
            n += _flag(db, paper_id, "sample", s["sample_id"], "DUPLICATES", "info",
                       "sample name also present in %d other paper(s)" % len(others))
    groups = defaultdict(list)
    for m in db.list_measurements(paper_id=paper_id, limit=100000):
        key = (m.get("sample_id"), normalize_text(m.get("quantity") or ""),
               normalize_text(str(m.get("value_raw") or "")), m.get("unit_raw"))
        groups[key].append(m["measurement_id"])
    for key, ids in groups.items():
        if len(ids) > 1 and key[2]:
            n += _flag(db, paper_id, "measurement", ",".join(ids), "DUPLICATES", "info",
                       "%d identical measurement rows (sample=%s qty=%s val=%s %s)"
                       % (len(ids), key[0], key[1], key[2], key[3]))
    return n


# ---------------------------------------------------------------- C4
def check_conditions(db, paper_id, parse_doc):
    n = 0
    for m in db.list_measurements(paper_id=paper_id, limit=100000):
        q = m.get("quantity") or ""
        if "_ambiguous" in q:
            n += _flag(db, paper_id, "measurement", m["measurement_id"], "CONDITIONS", "info",
                       "ambiguous symbol quantity %r: check context to disambiguate" % q)
        required = CONDITION_REQUIRED.get(q)
        if required:
            have = set((m.get("conditions") or {}).keys())
            if not have & set(required) and not (have - {"raw"}):
                n += _flag(db, paper_id, "measurement", m["measurement_id"], "CONDITIONS",
                           "warning",
                           "quantity %s expects condition(s) %s but none are linked"
                           % (q, required))
    return n


# ---------------------------------------------------------------- C5
def check_cross_source(db, paper_id, parse_doc, tol=0.05):
    n = 0
    groups = defaultdict(list)
    for m in db.list_measurements(paper_id=paper_id, limit=100000):
        if m.get("value_std") is None:
            continue
        groups[(m.get("sample_id"), normalize_text(m.get("quantity") or ""))].append(m)
    for (sid, q), ms in groups.items():
        if len(ms) < 2 or not q or sid is None:
            continue  # cross-source consistency requires a resolved sample
        vals = sorted({_sig(m["value_std"]) for m in ms})
        srcs = {m.get("source_type") for m in ms}
        if len(vals) == 1:
            if len(srcs) > 1:
                n += _flag(db, paper_id, "measurement",
                           ",".join(m["measurement_id"] for m in ms), "CROSS_SOURCE", "info",
                           "value %g %s confirmed by sources %s"
                           % (vals[0], ms[0].get("unit_std") or "", sorted(srcs)))
            continue
        lo, hi = vals[0], vals[-1]
        guard = max(abs(lo), abs(hi))
        if guard == 0:
            continue
        rel = (hi - lo) / guard          # scale-free: works for J, m, Pa, ...
        if rel > tol:
            n += _flag(db, paper_id, "measurement",
                       ",".join(m["measurement_id"] for m in ms), "CROSS_SOURCE", "warning",
                       "inconsistent values for %s sample=%s: %s (rel spread %.1f%%)"
                       % (q, sid, vals[:6], rel * 100),
                       {"ids": [m["measurement_id"] for m in ms]})
    return n


# ---------------------------------------------------------------- C6
def check_curve_redraw(db, paper_id, parse_doc):
    n = 0
    for cv in db.list_curves(paper_id):
        cid = cv["curve_id"]
        pts = db.list_curve_points(cid)
        if not pts:
            n += _flag(db, paper_id, "curve", cid, "CURVE_REDRAW", "info",
                       "figure region extracted but no curve points digitized yet")
            continue
        cal = cv.get("calibration")
        if cal is None and cv.get("method") != "wpd_import":
            n += _flag(db, paper_id, "curve", cid, "CURVE_REDRAW", "warning",
                       "points present without calibration")
        if cv.get("method") == "wpd_import" and cal is None:
            n += _flag(db, paper_id, "curve", cid, "CURVE_REDRAW", "info",
                       "WPD-imported values carry WebPlotDigitizer's own calibration; "
                       "add SEELS calibration to enable overlay verification")
        if cal:
            from ..curves.calibrate import CalibrationError, data_to_pixel
            res = []
            for p in pts:
                if p["pixel_x"] is None or p["x_raw"] is None:
                    continue
                try:
                    qx, qy = data_to_pixel(cal, p["x_raw"], p["y_raw"])
                    res.append(abs(qx - p["pixel_x"]) + abs(qy - p["pixel_y"]))
                except (CalibrationError, ZeroDivisionError):
                    pass
            if res:
                med = sorted(res)[len(res) // 2]
                if med > config.OVERLAY_MAX_MEDIAN_PX:
                    n += _flag(db, paper_id, "curve", cid, "CURVE_REDRAW", "warning",
                               "reprojection residual %.2f px exceeds %.1f px"
                               % (med, config.OVERLAY_MAX_MEDIAN_PX))
        if cv.get("verification_status") == "unverified":
            n += _flag(db, paper_id, "curve", cid, "CURVE_REDRAW", "info",
                       "curve not verified (needs redraw check / human review)")
    return n


# ---------------------------------------------------------------- C7
def check_high_risk_human(db, paper_id, parse_doc):
    n = 0
    pages = {p["page_no"]: p for p in (parse_doc or {}).get("pages") or []}
    ocr_pages = {pno for pno, p in pages.items() if p.get("status") == "needs_ocr"}
    for m in db.list_measurements(paper_id=paper_id, limit=100000):
        mid = m["measurement_id"]
        risky = None
        if (m.get("extraction_method") or "").startswith("llm"):
            if m.get("status") == "candidate":
                risky = "LLM-extracted value requires human review (LLM confidence is not proof)"
        full = db.get_measurement(mid)
        if any((e.get("page") in ocr_pages) or e.get("source_type") == "ocr"
               for e in full.get("evidence") or []):
            risky = "evidence located on an OCR/scanned page"
        if risky and m.get("status") == "candidate":
            db.set_measurement_status(mid, "needs_human_review")
            n += _flag(db, paper_id, "measurement", mid, "HIGH_RISK_HUMAN", "high_risk", risky)
    for cv in db.list_curves(paper_id):
        pts = db.list_curve_points(cv["curve_id"])
        if any((p.get("source") in ("llm_guess", "auto_trace")) for p in pts) \
                and cv.get("verification_status") != "human_verified":
            n += _flag(db, paper_id, "curve", cv["curve_id"], "HIGH_RISK_HUMAN", "high_risk",
                       "auto/LLM-derived curve points are not verified experimental data")
    return n


CHECKS = (check_evidence_integrity, check_value_unit, check_duplicates,
          check_conditions, check_cross_source, check_curve_redraw,
          check_high_risk_human)


def run_qc(db, paper_id: str, deep: bool = True) -> dict:
    """Run all checks for one paper. Refreshes unresolved QC-owned flags."""
    db.clear_unresolved_flags(paper_id, codes=QC_CODES)
    parse_doc = load_parse(paper_id) if deep else None
    by_code = Counter()
    total = 0
    for fn in CHECKS:
        c = fn(db, paper_id, parse_doc)
        by_code[fn.__name__.replace("check_", "")] = c
        total += c
    sev = Counter(f["severity"] for f in db.list_qc_flags(paper_id=paper_id, resolved=False))
    nhr = db.conn().execute(
        "SELECT COUNT(*) FROM measurements WHERE paper_id=? AND status='needs_human_review'",
        (paper_id,)).fetchone()[0]
    return {"paper_id": paper_id, "n_flags_new": total, "by_check": dict(by_code),
            "open_flags_by_severity": dict(sev), "measurements_needing_human_review": nhr}
