"""Gold-standard comparison + evidence discipline + LLM grounding policy."""
import json

from seels.extraction.pipeline import run_extraction
from seels.gold import compare_gold, load_gold


def test_gold_standard_full_match(synth_ingested, gold_file):
    db, pid = synth_ingested
    gold = load_gold(gold_file)
    report = compare_gold(db, pid, gold)
    s = report["summary"]
    assert s["n_gold_items"] == 8
    assert s["n_matched"] == 8, [r for r in report["rows"] if r["status"] != "MATCHED"]
    assert s["n_missing"] == 0 and s["n_value_mismatch"] == 0
    assert s["recall"] == 1.0


def test_gold_compare_detects_tampering(synth_ingested, gold_file, tmp_path):
    db, pid = synth_ingested
    gold = load_gold(gold_file)
    gold["items"][0]["value"] = 120.0         # wrong value (outside tol) -> mismatch
    gold["items"].append({"quantity": "coercivity", "value": 500, "unit": "Oe", "page": 1})
    p = tmp_path / "tampered_gold.json"
    p.write_text(json.dumps(gold), encoding="utf-8")
    report = compare_gold(db, pid, load_gold(str(p)))
    st = {r["gold_index"]: r["status"] for r in report["rows"]}
    assert st[0] == "VALUE_MISMATCH"
    assert st[len(gold["items"]) - 1] == "MISSING"


def test_every_measurement_has_complete_evidence(synth_ingested):
    db, pid = synth_ingested
    paper = db.get_paper(pid)
    ms = db.list_measurements(paper_id=pid, limit=1000)
    assert len(ms) >= 8
    for m in ms:
        full = db.get_measurement(m["measurement_id"])
        assert full["evidence"], full["measurement_id"]
        for ev in full["evidence"]:
            assert ev["paper_id"] == pid
            assert ev["page"] >= 1
            assert ev["quote_text"]
            assert ev["bbox"] and len(ev["bbox"]) == 4
            assert ev["pdf_sha256"] == paper["sha256"]


def test_uncertain_fields_are_null_with_reason(synth_ingested, gold_file):
    db, pid = synth_ingested
    gold = json.loads(open(gold_file, encoding="utf-8").read())
    flags = [f for f in db.list_qc_flags(paper_id=pid)
             if f["check_code"] == "EX_NULL_FIELD"]
    assert flags, "expected EX_NULL_FIELD flags for the 12-15% range"
    msgs = json.dumps([f["details"] for f in flags], ensure_ascii=False)
    for exp in gold["expected_uncertain"]:
        assert exp["reason"] in msgs
        assert exp["quote_contains"] in msgs
    # and the range value was NOT stored as a measurement
    for m in db.list_measurements(paper_id=pid, limit=1000):
        assert m["value_raw"] not in ("12", "15")


def test_conditions_and_samples_attached(synth_ingested):
    db, pid = synth_ingested
    ms = db.list_measurements(paper_id=pid, limit=1000)
    tab = [db.get_measurement(m["measurement_id"]) for m in ms
           if m["source_type"] == "table"]
    assert tab
    for m in tab:
        cond = m["conditions"] or {}
        assert "temperature" in cond, "caption condition 'at 300 K' missing"
        assert cond["temperature"]["value"] == 300
        assert cond["temperature"]["evidence_ids"], "condition must cite evidence"
    samples = db.list_samples(pid)
    names = {s["name"] for s in samples}
    assert {"Sample A", "Sample B"} <= names
    for s in samples:
        ident = s["fields"]["identity"]["name"]
        assert ident["value"] and ident["evidence_ids"]


def test_standardization_fields(synth_ingested):
    db, pid = synth_ingested
    found = None
    for m in db.list_measurements(paper_id=pid, limit=1000):
        if m["unit_raw"] == "Ohm*cm" and m["value_raw"] == "0.0083":
            found = db.get_measurement(m["measurement_id"])
            break
    assert found
    assert abs(found["value_std"] - 8.3e-5) < 1e-15
    assert found["unit_std"] == "Ohm*m" and found["std_method"]
    assert found["value_raw"] == "0.0083" and found["unit_raw"] == "Ohm*cm"


def test_llm_items_without_grounded_quote_are_never_stored(db, synth_pdf):
    from seels.parsing.pipeline import ingest
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    calls = []

    def fake_caller(model, text):
        calls.append(text)
        return json.dumps({"items": [
            {"quote": "Tc = 93 K", "page": 1, "quantity": "Tc",
             "value_raw": "93", "unit_raw": "K", "confidence": 0.99},
            {"quote": "Tc = 500 K was fabricated", "page": 1, "quantity": "Tc",
             "value_raw": "500", "unit_raw": "K", "confidence": 0.98},
            {"quote": "Sample A (La0.7Sr0.3MnO3) shows Tc = 93 K", "page": 1,
             "quantity": "unparsable", "value_raw": "not-a-number", "unit_raw": None,
             "confidence": 0.7},
        ]})

    res = run_extraction(db, pid, "llm:openai-compat",
                         {"api_key": "test", "caller": fake_caller})
    assert calls, "LLM caller was not invoked"
    ms = db.list_measurements(paper_id=pid, limit=1000)
    llm_ms = [m for m in ms if m["extraction_method"] == "llm:openai-compat"]
    assert len(llm_ms) == 1, llm_ms          # only the grounded item survived
    assert llm_ms[0]["value_raw"] == "93"
    assert llm_ms[0]["status"] == "candidate"   # confidence 0.99 does NOT accept
    run = db.get_run(res["run_id"])
    assert run["stats"]["n_rejected"] >= 1
    reasons = {x["reject_reason"] for x in run["stats"]["rejected"]}
    assert "quote_not_grounded" in reasons
    # unparseable grounded item became a visible null-field flag, not a value
    flags = [f for f in db.list_qc_flags(paper_id=pid) if f["check_code"] == "EX_NULL_FIELD"]
    assert flags


def test_rerun_replaces_candidates_keeps_reviewed(synth_ingested):
    db, pid = synth_ingested
    ms1 = db.list_measurements(paper_id=pid, limit=1000)
    keep = ms1[0]["measurement_id"]
    db.set_measurement_status(keep, "accepted")     # simulate human review
    run_extraction(db, pid, "rules-v1")             # re-run same extractor
    ms2 = db.list_measurements(paper_id=pid, limit=1000)
    ids2 = {m["measurement_id"] for m in ms2}
    assert keep in ids2                              # reviewed row survived
    assert db.get_measurement(keep)["status"] == "accepted"
