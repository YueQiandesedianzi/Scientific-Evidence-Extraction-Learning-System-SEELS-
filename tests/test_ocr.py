"""Hermetic OCR-path tests (fake provider; real MinerU runs are script-level).

Verifies the full P4 contract without the engine installed:
  needs_ocr page -> augment_ocr -> parse.json updated (status ocr_done)
  -> extraction produces measurements with source_type='ocr' evidence
  -> QC C7 routes them to needs_human_review.
"""
import pytest

from seels.extraction.pipeline import run_extraction
from seels.parsing.pipeline import augment_ocr, ingest, load_parse
from seels.qc.checks import run_qc


class FakeOcrProvider:
    tier = "basic"

    def __init__(self, words_by_page):
        self.words_by_page = words_by_page

    def available(self):
        return True

    def ocr_pages(self, paper, pages, force=False):
        return {"schema": "seels.ocr/1.0", "engine": "mineru",
                "pdf_sha256": paper["sha256"],
                "pages": [self.words_by_page[p] for p in pages
                          if p in self.words_by_page]}


def _words(pairs):
    ws = [{"text": t, "x0": a, "top": b, "x1": c, "bottom": d}
          for t, a, b, c, d in pairs]
    return ws, " ".join(w["text"] for w in ws)


def test_ocr_end_to_end_human_review(db, scan_pdf):
    r = ingest(db, scan_pdf)
    pid = r["paper"]["paper_id"]
    doc = load_parse(pid)
    assert doc["pages"][0]["status"] == "needs_ocr"

    # realistic OCR line: long enough to pass the 20-char page-text threshold
    ws, text = _words([
        ("The", 40, 150, 58, 162), ("sample", 62, 150, 96, 162),
        ("shows", 100, 150, 128, 162), ("Tc", 132, 150, 146, 162),
        ("=", 150, 150, 158, 162), ("93", 162, 150, 176, 162),
        ("K", 180, 150, 188, 162), ("in", 192, 150, 202, 162),
        ("the", 206, 150, 220, 162), ("SQUID", 224, 150, 252, 162),
        ("measurement.", 256, 150, 310, 162)])
    prov = FakeOcrProvider({1: {"page_no": 1, "words": ws, "text": text}})
    res = augment_ocr(db, pid, provider=prov)
    assert res["augmented"] == 1 and not res["remaining_needs_ocr"]

    doc = load_parse(pid)
    p1 = doc["pages"][0]
    assert p1["status"] == "ocr_done" and p1["engine"] == "mineru-ocr"
    assert p1["char_count"] > 0 and p1["ocr_meta"]["prev_status"] == "needs_ocr"
    assert db.get_paper(pid)["parse_status"] == "parsed"

    run_extraction(db, pid, "rules-v1")
    ms = db.list_measurements(paper_id=pid, limit=100)
    assert ms, "OCR text 'Tc = 93 K' should yield a measurement"
    m = db.get_measurement(ms[0]["measurement_id"])
    assert m["value_raw"] == "93" and m["unit_raw"] == "K"
    assert m["status"] == "candidate"                    # not auto-accepted
    assert all(ev["source_type"] == "ocr" for ev in m["evidence"])

    run_qc(db, pid)
    m2 = db.get_measurement(ms[0]["measurement_id"])
    assert m2["status"] == "needs_human_review"      # C7: OCR evidence -> human gate
    flags = [f for f in db.list_qc_flags(paper_id=pid)
             if f["check_code"] == "HIGH_RISK_HUMAN"]
    assert any("OCR" in f["message"] for f in flags)


def test_ocr_provider_unavailable_raises(db, scan_pdf):
    from seels.parsing.ocr_provider import MinerUOcrProvider, OcrError
    r = ingest(db, scan_pdf)
    pid = r["paper"]["paper_id"]

    class NoEngine(MinerUOcrProvider):
        def available(self):
            return False

    with pytest.raises(OcrError):
        augment_ocr(db, pid, provider=NoEngine(
            python_exe="Z:/nonexistent/python.exe",
            worker="Z:/nonexistent/worker.py"))
    doc = load_parse(pid)
    assert doc["pages"][0]["status"] == "needs_ocr"   # nothing silently changed
