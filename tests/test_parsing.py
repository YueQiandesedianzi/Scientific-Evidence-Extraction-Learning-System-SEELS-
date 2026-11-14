import json

from seels.parsing import pipeline as pp
from seels.util import sha256_file


def test_native_paper_full_parse(db, synth_pdf):
    sha_before = sha256_file(synth_pdf)
    r = pp.ingest(db, synth_pdf)
    paper, parse = r["paper"], r["parse"]
    assert parse["classification"]["kind"] == "native"
    assert parse["n_pages"] == 2
    doc = json.loads(open(r["parse_json"], encoding="utf-8").read())
    p2 = doc["pages"][1]
    assert len(p2["tables"]) >= 1
    t = p2["tables"][0]
    assert t["n_rows"] >= 3 and any("Sample" in (c or "") for c in t["rows"][0])
    cells = t["cells"]
    assert all(c["bbox"] and len(c["bbox"]) == 4 for c in cells)
    refs = [c["ref"] for c in p2["captions"]]
    assert "Table 1" in refs and "Figure 1" in refs
    p1 = doc["pages"][0]
    assert any(w["text"] == "93" for w in p1["words"])
    # original PDF untouched (read-only policy)
    assert sha256_file(synth_pdf) == sha_before
    assert paper["parse_status"] == "parsed"


def test_scanned_pdf_flagged_needs_ocr(db, scan_pdf):
    r = pp.ingest(db, scan_pdf)
    parse = r["parse"]
    assert parse["classification"]["kind"] == "scanned"
    doc = json.loads(open(r["parse_json"], encoding="utf-8").read())
    assert doc["pages"][0]["status"] == "needs_ocr"
    assert doc["pages"][0]["char_count"] <= 30
    codes = [l["code"] for l in db.list_parse_logs(r["paper"]["paper_id"])]
    assert "ocr_required" in codes


def test_resume_skips_parsed_pages(db, synth_pdf):
    r1 = pp.ingest(db, synth_pdf)
    r2 = pp.ingest(db, synth_pdf)          # resume
    assert r2["parse"]["stats"] == r1["parse"]["stats"]
    logs = db.list_parse_logs(r1["paper"]["paper_id"])
    assert any(l["code"] == "resume" for l in logs)


def test_secondary_engine_fallback(db, synth_pdf, monkeypatch):
    from seels.parsing.engine_pdfplumber import PdfplumberEngine
    calls = {"n": 0}
    orig = PdfplumberEngine.parse_page

    def flaky(self, doc, index):
        if index == 0:
            calls["n"] += 1
            raise RuntimeError("simulated primary failure")
        return orig(self, doc, index)

    monkeypatch.setattr(PdfplumberEngine, "parse_page", flaky)
    r = pp.ingest(db, synth_pdf, force=True)
    doc = json.loads(open(r["parse_json"], encoding="utf-8").read())
    p1 = doc["pages"][0]
    assert p1["status"] == "fallback" and p1["engine"] == "pypdfium2"
    assert p1["char_count"] > 50 and len(p1["words"]) > 5
    logs = db.list_parse_logs(r["paper"]["paper_id"])
    assert any(l["code"] == "primary_page_failed" for l in logs)
    assert any(l["code"] == "fallback_used" for l in logs)


def test_page_image_render(db, synth_pdf):
    r = pp.ingest(db, synth_pdf)
    img = pp.render_page_image(r["paper"], 1, dpi=100)
    from PIL import Image
    im = Image.open(img)
    assert im.width > 700 and im.format == "PNG"
    # cached on second call
    img2 = pp.render_page_image(r["paper"], 1, dpi=100)
    assert img == img2
