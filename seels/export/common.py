"""Shared row collectors for the exporters (flat, auditable rows)."""
from __future__ import annotations

from ..util import jdump


def collect_rows(db, paper_id=None):
    papers = db.list_papers()
    if paper_id:
        papers = [p for p in papers if p["paper_id"] == paper_id]
    pids = [p["paper_id"] for p in papers]
    pmap = {p["paper_id"]: p for p in papers}

    samples, measurements, evidence, curves, points = [], [], [], [], []
    reviews, flags, cards = [], [], []
    for pid in pids:
        for s in db.list_samples(pid):
            samples.append({"sample_id": s["sample_id"], "paper_id": pid,
                            "name": s.get("name"), "fields_json": jdump(s.get("fields")),
                            "notes": s.get("notes"), "created_at": s.get("created_at")})
        for m in db.list_measurements(paper_id=pid, limit=1000000):
            full = db.get_measurement(m["measurement_id"])
            evs = full.get("evidence") or []
            smp = db.get_sample(full["sample_id"]) if full.get("sample_id") else None
            measurements.append({
                "measurement_id": full["measurement_id"], "paper_id": pid,
                "paper_sha256": pmap[pid]["sha256"],
                "paper_title": pmap[pid].get("title") or "",
                "sample_id": full.get("sample_id"), "sample_name": (smp or {}).get("name"),
                "quantity": full.get("quantity"), "value_raw": full.get("value_raw"),
                "unit_raw": full.get("unit_raw"), "value_std": full.get("value_std"),
                "unit_std": full.get("unit_std"), "std_method": full.get("std_method"),
                "std_null_reason": full.get("std_null_reason"),
                "conditions_json": jdump(full.get("conditions")),
                "test_method": full.get("test_method"),
                "source_type": full.get("source_type"),
                "extraction_method": full.get("extraction_method"),
                "status": full.get("status"), "run_id": full.get("run_id"),
                "n_evidence": len(evs),
                "evidence_ids": ";".join(e["evidence_id"] for e in evs),
                "evidence_pages": ";".join(str(e["page"]) for e in evs),
                "evidence_kinds": ";".join(e.get("element_kind") or "" for e in evs),
                "first_quote": (evs[0]["quote_text"][:300] if evs else ""),
                "first_bbox_json": jdump(evs[0]["bbox"]) if evs else "",
                "created_at": full.get("created_at"),
            })
        for e in db.list_evidence(paper_id=pid):
            evidence.append({**{k: e.get(k) for k in
                               ("evidence_id", "paper_id", "page", "coord_system",
                                "quote_text", "element_kind", "element_ref", "source_type",
                                "extraction_method", "review_status", "pdf_sha256",
                                "pdf_path", "created_at")},
                             "bbox_json": jdump(e.get("bbox"))})
        for cv in db.list_curves(pid):
            n_pts = len(db.list_curve_points(cv["curve_id"]))
            curves.append({"curve_id": cv["curve_id"], "paper_id": pid, "page": cv.get("page"),
                           "figure_ref": cv.get("figure_ref"), "image_path": cv.get("image_path"),
                           "image_sha256": cv.get("image_sha256"),
                           "region_bbox_json": jdump(cv.get("region_bbox")),
                           "calibration_json": jdump(cv.get("calibration")),
                           "method": cv.get("method"), "series_name": cv.get("series_name"),
                           "verification_status": cv.get("verification_status"),
                           "n_points": n_pts, "notes": cv.get("notes"),
                           "created_at": cv.get("created_at")})
            for p in db.list_curve_points(cv["curve_id"]):
                points.append({k: p.get(k) for k in
                               ("point_id", "curve_id", "x_raw", "y_raw", "x_std", "y_std",
                                "pixel_x", "pixel_y", "source")})
        reviews += [{k: r.get(k) for k in
                     ("review_id", "paper_id", "target_type", "target_id", "action",
                      "comment", "reviewer", "created_at")}
                    | {"old_value_json": jdump(r.get("old_value")),
                       "new_value_json": jdump(r.get("new_value"))}
                    for r in db.list_reviews(paper_id=pid)]
        flags += [{k: f.get(k) for k in
                   ("flag_id", "paper_id", "target_type", "target_id", "check_code",
                    "severity", "message", "resolved", "resolved_by", "resolved_at",
                    "created_at")}
                  | {"details_json": jdump(f.get("details"))}
                  for f in db.list_qc_flags(paper_id=pid)]
        cards += [{"card_id": c["card_id"], "paper_id": c.get("paper_id"),
                   "target_type": c["target_type"], "target_id": c["target_id"],
                   "dimensions_json": jdump(c.get("dimensions")),
                   "ai_explanation": c.get("ai_explanation"), "ai_source": c.get("ai_source"),
                   "created_at": c.get("created_at"), "updated_at": c.get("updated_at")}
                  for c in db.list_learning_cards(pid)]
    paper_rows = [{k: p.get(k) for k in
                   ("paper_id", "source_path", "sha256", "title", "authors", "doi",
                    "arxiv_id", "n_pages", "pdf_kind", "parse_status", "parse_json_path",
                    "ingested_at")} | {"meta_json": jdump(p.get("meta"))} for p in papers]
    return {"papers": paper_rows, "samples": samples, "measurements": measurements,
            "evidence": evidence, "curves": curves, "curve_points": points,
            "reviews": reviews, "qc_flags": flags, "learning_cards": cards}
