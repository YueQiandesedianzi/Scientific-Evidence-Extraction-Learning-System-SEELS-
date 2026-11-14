from seels.db import open_db
from seels.util import new_id


def _mk(db):
    pid = "P-t1"
    db.upsert_paper({"paper_id": pid, "source_path": "x.pdf", "sha256": "ab" * 32})
    return pid


def test_measurement_evidence_roundtrip(db):
    pid = _mk(db)
    ev = new_id("EV")
    db.insert_evidence({"evidence_id": ev, "paper_id": pid, "page": 3,
                        "bbox": [1, 2, 3, 4], "quote_text": "Tc = 93 K",
                        "element_kind": "text", "pdf_sha256": "ab" * 32,
                        "pdf_path": "x.pdf"})
    mid = new_id("M")
    db.insert_measurement({"measurement_id": mid, "paper_id": pid, "quantity": "Tc",
                           "value_raw": "93", "unit_raw": "K", "value_std": 93.0,
                           "unit_std": "K"}, [(ev, "primary")])
    m = db.get_measurement(mid)
    assert m["evidence"][0]["quote_text"] == "Tc = 93 K"
    assert m["evidence"][0]["role"] == "primary"
    assert db.paper_summary(pid)["n_measurements"] == 1


def test_reviews_append_only(db):
    pid = _mk(db)
    mid = new_id("M")
    db.insert_measurement({"measurement_id": mid, "paper_id": pid})
    r1 = db.add_review(pid, "measurement", mid, "accept", reviewer="a")
    r2 = db.add_review(pid, "measurement", mid, "reopen", reviewer="b")
    revs = db.list_reviews(target_id=mid)
    assert len(revs) == 2 and revs[0]["review_id"] == r2 and revs[1]["review_id"] == r1


def test_candidate_only_deletion(db):
    pid = _mk(db)
    run_id = db.start_run(pid, "rules-v1", "0")
    m1, m2 = new_id("M"), new_id("M")
    db.insert_measurement({"measurement_id": m1, "paper_id": pid, "run_id": run_id,
                           "value_raw": "1", "status": "candidate"})
    db.insert_measurement({"measurement_id": m2, "paper_id": pid, "run_id": run_id,
                           "value_raw": "2", "status": "candidate"})
    db.set_measurement_status(m2, "accepted")     # human decision
    removed = db.delete_candidate_run_data(pid, "rules-v1")
    assert removed == 1
    assert db.get_measurement(m1) is None
    assert db.get_measurement(m2) is not None     # reviewed rows survive


def test_qc_flag_lifecycle(db):
    pid = _mk(db)
    fid = db.insert_qc_flag({"paper_id": pid, "target_type": "measurement",
                             "target_id": "M-x", "check_code": "T", "severity": "info",
                             "message": "m"})
    assert db.paper_summary(pid)["n_open_flags"] == 1
    db.resolve_flag(fid, "tester")
    assert db.paper_summary(pid)["n_open_flags"] == 0
    assert db.list_qc_flags(paper_id=pid, resolved=True)[0]["resolved_by"] == "tester"
