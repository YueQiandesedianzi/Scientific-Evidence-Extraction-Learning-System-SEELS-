"""P4 end-to-end demo: real MinerU OCR on a simulated scanned paper.

Self-contained: builds the synthetic paper (tests/conftest._build_synth),
renders page 1 into an image-only PDF (simulated scan), ingests it, runs the
REAL MinerU OCR provider (first run downloads ~0.9GB basic-tier models to
D:\seels-tools via HF_HOME/MODELSCOPE_CACHE), then extraction + QC to prove:
  - needs_ocr page becomes ocr_done with word boxes
  - extracted values carry source_type='ocr' evidence
  - QC C7 routes them to needs_human_review (never auto-accepted)

Everything is written to data/work/_ocrtest (scratch DB, dev data untouched).
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SEELS_DATA_DIR"] = str(ROOT / "data" / "work" / "_ocrtest")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8")


def main():
    from conftest import _build_synth
    import pypdfium2 as pdfium

    d = Path(os.environ["SEELS_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    synth = d / "synth.pdf"
    if not synth.exists():
        _build_synth(synth)
    doc = pdfium.PdfDocument(str(synth))
    img = doc[0].render(scale=2.0).to_pil().convert("RGB")
    doc.close()
    scan = d / "scanned.pdf"
    img.save(scan, format="PDF", resolution=144.0)
    print("scan built:", scan)

    from seels.db import open_db
    from seels.parsing.pipeline import ingest, load_parse, augment_ocr
    from seels.extraction.pipeline import run_extraction
    from seels.qc.checks import run_qc

    db = open_db()
    r = ingest(db, str(scan))
    pid = r["paper"]["paper_id"]
    print("INGESTED", pid, r["parse"]["classification"])
    res = augment_ocr(db, pid)          # real MinerU provider
    print("OCR:", res)
    doc2 = load_parse(pid)
    p1 = doc2["pages"][0]
    print("page1:", p1["status"], "| chars:", p1["char_count"],
          "| words:", len(p1["words"]), "| ocr_meta:", p1.get("ocr_meta"))
    print("text sample:", (p1.get("text") or "")[:300])
    ex = run_extraction(db, pid, "rules-v1")
    print("EXTRACT:", ex["stats"])
    qc = run_qc(db, pid)
    print("QC:", qc)
    for m in db.list_measurements(paper_id=pid, limit=20):
        full = db.get_measurement(m["measurement_id"])
        ev = (full.get("evidence") or [{}])[0]
        print("  M:", full["quantity"], "|", full["value_raw"], full["unit_raw"],
              "| src:", ev.get("source_type"), "| status:", full["status"],
              "| bbox:", ev.get("bbox"), "| quote:", (ev.get("quote_text") or "")[:50])
    print("OCR_E2E_DONE")


if __name__ == "__main__":
    main()
