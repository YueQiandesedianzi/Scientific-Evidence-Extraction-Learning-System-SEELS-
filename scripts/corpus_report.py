"""Run the full SEELS pipeline over the whole arXiv test corpus and report.

Stages per paper: ingest(parse) -> extract(rules-v1) -> figures -> QC -> cards.
Writes data/work/corpus_report.json and prints a summary. Originals are never
modified.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from seels import config
from seels.curves.figures import extract_figures
from seels.db import open_db
from seels.extraction.pipeline import run_extraction
from seels.export import export_all
from seels.learning import generate_cards_for_paper
from seels.parsing.pipeline import ingest
from seels.qc.checks import run_qc


def main():
    db = open_db()
    config.ensure_dirs()
    papers = sorted(config.PAPERS_DIR.glob("arxiv_*.pdf"))
    manifest = {}
    mf = config.PAPERS_DIR / "manifest.json"
    if mf.exists():
        for rec in json.loads(mf.read_text(encoding="utf-8")):
            manifest[rec["file"]] = rec
    rows = []
    t_all = time.time()
    for pdf in papers:
        t0 = time.time()
        rec = manifest.get(pdf.name, {})
        meta = {"title": rec.get("title"), "arxiv_id": rec.get("arxiv_id")}
        r = ingest(db, str(pdf), meta=meta)
        pid = r["paper"]["paper_id"]
        ex = run_extraction(db, pid, "rules-v1")
        figs = extract_figures(db, pid)
        qc = run_qc(db, pid)
        cards = generate_cards_for_paper(db, pid, limit=200)
        s = db.paper_summary(pid)
        row = {
            "file": pdf.name, "paper_id": pid, "title": (rec.get("title") or "")[:60],
            "arxiv_id": rec.get("arxiv_id"),
            "pages": r["parse"]["n_pages"], "kind": r["parse"]["classification"]["kind"],
            "scanned_pages": len(r["parse"]["classification"]["scanned_pages"]),
            "fallback_pages": len(r["parse"]["stats"]["fallback_pages"]),
            "tables": r["parse"]["stats"]["total_tables"],
            "captions": r["parse"]["stats"]["total_captions"],
            "measurements": s["n_measurements"], "samples": s["n_samples"],
            "evidence": s["n_evidence"], "figures": len(figs),
            "extract_stats": ex["stats"], "qc": qc, "cards": cards,
            "status_counts": s["status_counts"], "seconds": round(time.time() - t0, 1),
        }
        rows.append(row)
        print("[%2d/%2d] %-14s pages=%-3d M=%-4d E=%-4d F=%-2d flags=%-3d %6.1fs" % (
            len(rows), len(papers), (rec.get("arxiv_id") or pdf.stem)[:14],
            row["pages"], row["measurements"], row["evidence"], row["figures"],
            qc["n_flags_new"], row["seconds"]), flush=True)
    files = export_all(db)
    total = round(time.time() - t_all, 1)
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "total_seconds": total,
           "n_papers": len(rows), "rows": rows, "exports": files,
           "totals": {
               "measurements": sum(r["measurements"] for r in rows),
               "evidence": sum(r["evidence"] for r in rows),
               "figures": sum(r["figures"] for r in rows),
               "cards": sum(r["cards"] for r in rows)}}
    (config.WORK_DIR / "corpus_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("TOTAL %ss | measurements=%d evidence=%d figures=%d cards=%d | exports=%d" % (
        total, out["totals"]["measurements"], out["totals"]["evidence"],
        out["totals"]["figures"], out["totals"]["cards"], len(files)))
    print("report:", config.WORK_DIR / "corpus_report.json")


if __name__ == "__main__":
    main()
