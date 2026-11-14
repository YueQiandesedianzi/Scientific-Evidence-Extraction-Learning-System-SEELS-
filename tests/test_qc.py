"""Each of the seven QC checks must fire on injected problems."""
from seels.qc.checks import run_qc
from seels.util import new_id


def _codes(db, pid):
    return [f["check_code"] for f in db.list_qc_flags(paper_id=pid, resolved=False)]


def test_c1_missing_and_unlocatable_evidence(synth_ingested):
    db, pid = synth_ingested
    m = db.list_measurements(paper_id=pid, limit=1)[0]
    db.conn().execute("DELETE FROM measurement_evidence WHERE measurement_id=?",
                      (m["measurement_id"],))
    db.conn().commit()
    ev = db.list_evidence(paper_id=pid)[0]
    db.conn().execute("UPDATE evidence SET quote_text='zzz not in paper zzz' WHERE evidence_id=?",
                      (ev["evidence_id"],))
    db.conn().commit()
    res = run_qc(db, pid)
    assert res["by_check"]["evidence_integrity"] >= 1
    assert "EVIDENCE_INTEGRITY" in _codes(db, pid)


def test_c2_bad_standardization_detected(synth_ingested):
    db, pid = synth_ingested
    m = db.list_measurements(paper_id=pid, limit=1000)
    target = next(x for x in m if x["value_std"] is not None)
    db.update_measurement(target["measurement_id"], value_std=target["value_std"] * 1000)
    res = run_qc(db, pid)
    assert res["by_check"]["value_unit"] >= 1
    msgs = " ".join(f["message"] for f in db.list_qc_flags(paper_id=pid)
                    if f["check_code"] == "VALUE_UNIT")
    assert "differs from recomputed" in msgs


def test_c3_duplicates_flagged(synth_ingested):
    db, pid = synth_ingested
    m = db.get_measurement(db.list_measurements(paper_id=pid, limit=1)[0]["measurement_id"])
    ev = m["evidence"][0]["evidence_id"]
    dup_id = new_id("M")
    db.insert_measurement({"measurement_id": dup_id, "paper_id": pid,
                           "sample_id": m["sample_id"], "quantity": m["quantity"],
                           "value_raw": m["value_raw"], "unit_raw": m["unit_raw"],
                           "value_std": m["value_std"], "unit_std": m["unit_std"],
                           "source_type": m["source_type"],
                           "extraction_method": m["extraction_method"]},
                          [(ev, "primary")])
    res = run_qc(db, pid)
    assert res["by_check"]["duplicates"] >= 1


def test_c4_conditions_flag(synth_ingested):
    db, pid = synth_ingested
    mid = new_id("M")
    db.insert_measurement({"measurement_id": mid, "paper_id": pid,
                           "quantity": "electrical_conductivity", "value_raw": "1",
                           "unit_raw": "S/cm", "value_std": 100.0, "unit_std": "S/m",
                           "conditions": {}}, [])
    res = run_qc(db, pid)
    msgs = [f["message"] for f in db.list_qc_flags(paper_id=pid)
            if f["check_code"] == "CONDITIONS"]
    assert any("expects condition" in m for m in msgs)


def test_c5_cross_source_mismatch(synth_ingested):
    db, pid = synth_ingested
    samples = db.list_samples(pid)
    sid = samples[0]["sample_id"]
    ev = db.list_evidence(paper_id=pid)[0]["evidence_id"]
    a, b = new_id("M"), new_id("M")
    db.insert_measurement({"measurement_id": a, "paper_id": pid, "sample_id": sid,
                           "quantity": "band_gap", "value_raw": "1.0", "unit_raw": "eV",
                           "value_std": 1.602e-19, "unit_std": "J", "source_type": "table",
                           "extraction_method": "rules-v1"}, [(ev, "primary")])
    db.insert_measurement({"measurement_id": b, "paper_id": pid, "sample_id": sid,
                           "quantity": "band_gap", "value_raw": "2.0", "unit_raw": "eV",
                           "value_std": 3.204e-19, "unit_std": "J", "source_type": "text",
                           "extraction_method": "rules-v1"}, [(ev, "primary")])
    res = run_qc(db, pid)
    assert res["by_check"]["cross_source"] >= 1
    msgs = [f["message"] for f in db.list_qc_flags(paper_id=pid)
            if f["check_code"] == "CROSS_SOURCE"]
    assert any("inconsistent" in m for m in msgs)


def test_c6_curve_flags(synth_ingested):
    db, pid = synth_ingested
    cid = new_id("C")
    db.insert_curve({"curve_id": cid, "paper_id": pid, "page": 2,
                     "figure_ref": "Figure 9", "image_path": "nonexistent.png",
                     "method": "manual"})
    db.replace_curve_points(cid, [{"x_raw": 1, "y_raw": 2, "source": "manual_click"},
                                  {"x_raw": 2, "y_raw": 4, "source": "manual_click"}])
    res = run_qc(db, pid)
    msgs = [f["message"] for f in db.list_qc_flags(paper_id=pid)
            if f["check_code"] == "CURVE_REDRAW"]
    assert any("without calibration" in m for m in msgs)
    empty = new_id("C")
    db.insert_curve({"curve_id": empty, "paper_id": pid, "page": 2,
                     "figure_ref": "Figure 10", "image_path": "x.png"})
    run_qc(db, pid)
    msgs = [f["message"] for f in db.list_qc_flags(paper_id=pid)
            if f["check_code"] == "CURVE_REDRAW"]
    assert any("no curve points" in m for m in msgs)


def test_c7_llm_and_ocr_routed_to_human(synth_ingested, scan_pdf):
    db, pid = synth_ingested
    ev = db.list_evidence(paper_id=pid)[0]["evidence_id"]
    mid = new_id("M")
    db.insert_measurement({"measurement_id": mid, "paper_id": pid, "quantity": "band_gap",
                           "value_raw": "1.5", "unit_raw": "eV", "value_std": 2.4e-19,
                           "unit_std": "J", "extraction_method": "llm:openai-compat",
                           "status": "candidate"}, [(ev, "primary")])
    res = run_qc(db, pid)
    m = db.get_measurement(mid)
    assert m["status"] == "needs_human_review"
    assert res["measurements_needing_human_review"] >= 1
    flags = [f for f in db.list_qc_flags(paper_id=pid) if f["check_code"] == "HIGH_RISK_HUMAN"]
    assert any("LLM" in f["message"] for f in flags)

    # scanned-paper evidence routes to human review as well
    from seels.parsing.pipeline import ingest
    r = ingest(db, scan_pdf)
    spid = r["paper"]["paper_id"]
    sid = new_id("M")
    eid = new_id("EV")
    paper = db.get_paper(spid)
    db.insert_evidence({"evidence_id": eid, "paper_id": spid, "page": 1,
                        "bbox": [1, 2, 3, 4], "quote_text": "ocr text",
                        "element_kind": "text", "source_type": "ocr",
                        "pdf_sha256": paper["sha256"], "pdf_path": paper["source_path"]})
    db.insert_measurement({"measurement_id": sid, "paper_id": spid, "quantity": "Tc",
                           "value_raw": "9", "unit_raw": "K", "value_std": 9.0,
                           "unit_std": "K", "extraction_method": "rules-v1",
                           "status": "candidate"}, [(eid, "primary")])
    run_qc(db, spid)
    assert db.get_measurement(sid)["status"] == "needs_human_review"


def test_qc_preserves_human_decisions(synth_ingested):
    db, pid = synth_ingested
    m = db.list_measurements(paper_id=pid, limit=1)[0]
    db.set_measurement_status(m["measurement_id"], "accepted")
    run_qc(db, pid)
    assert db.get_measurement(m["measurement_id"])["status"] == "accepted"
