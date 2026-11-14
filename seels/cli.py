"""SEELS command line interface.

Usage:  python -m seels <command> [options]

Commands:
  init                          create data dirs + DB schema
  papers                        list ingested papers
  ingest PATH [PATH...]         parse PDFs into the store (read-only on sources)
  extract REF [--extractor E]   run structured extraction (REF = paper id/prefix/path, or 'all')
  figures REF                   detect figure regions -> curve records
  qc REF                        run the 7 quality checks
  cards REF                     generate six-dimension learning cards
  export [--paper REF] [--fmt csv|xlsx|both] [--out DIR]
  gold-compare REF --gold FILE [--out PREFIX]
                                compare human gold standard vs extracted values
  serve [--host H] [--port P]   start the review & learning web UI
  demo PATH                     ingest+extract+figures+qc+cards+export for one PDF
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config
from .db import open_db
from .util import sha256_file


def _resolve_papers(db, ref: str) -> list:
    if ref in ("all", "--all", "*"):
        return db.list_papers()
    p = Path(ref)
    if p.exists():
        found = db.find_paper_by_sha(sha256_file(p))
        return [found] if found else []
    exact = db.get_paper(ref)
    if exact:
        return [exact]
    return [x for x in db.list_papers() if x["paper_id"].startswith(ref)]


def _need_one(db, ref: str) -> dict:
    papers = _resolve_papers(db, ref)
    if not papers:
        raise SystemExit("no paper matches %r (ingest it first?)" % ref)
    return papers[0]


def cmd_init(args, db):
    config.ensure_dirs()
    print("initialized:", config.DATA_DIR)
    print("database:", db.path)


def cmd_papers(args, db):
    rows = db.list_papers()
    if not rows:
        print("(store is empty - use: python -m seels ingest <pdf>)")
        return
    for p in rows:
        s = db.paper_summary(p["paper_id"])
        print("%s  %-8s %-6s pages=%-4s M=%-4d E=%-4d C=%-2d flags=%-3d  %s" % (
            p["paper_id"], p["parse_status"], p["pdf_kind"], p["n_pages"],
            s["n_measurements"], s["n_evidence"], s["n_curves"], s["n_open_flags"],
            (p.get("title") or p["source_path"])[:70]))


def cmd_ingest(args, db):
    from .parsing.pipeline import ingest
    for path in args.paths:
        meta = None
        if args.meta:
            meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
        r = ingest(db, path, force=args.force, meta=meta)
        st = r["parse"]["stats"]
        print("ingested %s -> %s [%s, %d pages, tables=%d captions=%d fallback=%s]" % (
            Path(path).name, r["paper"]["paper_id"], r["parse"]["classification"]["kind"],
            r["parse"]["n_pages"], st["total_tables"], st["total_captions"],
            st["fallback_pages"]))


def cmd_extract(args, db):
    from .extraction.pipeline import run_extraction
    cfg = json.loads(args.cfg_json) if args.cfg_json else None
    for paper in _resolve_papers(db, args.ref):
        r = run_extraction(db, paper["paper_id"], args.extractor, cfg)
        print("%s %s -> %s" % (paper["paper_id"], r["extractor"],
                               json.dumps(r["stats"], ensure_ascii=False)[:220]))


def cmd_figures(args, db):
    from .curves.figures import extract_figures
    for paper in _resolve_papers(db, args.ref):
        ids = extract_figures(db, paper["paper_id"])
        print("%s -> %d figure regions" % (paper["paper_id"], len(ids)))


def cmd_qc(args, db):
    from .qc.checks import run_qc
    for paper in _resolve_papers(db, args.ref):
        r = run_qc(db, paper["paper_id"])
        print(json.dumps(r, ensure_ascii=False))


def cmd_cards(args, db):
    from .learning import generate_cards_for_paper
    for paper in _resolve_papers(db, args.ref):
        n = generate_cards_for_paper(db, paper["paper_id"], limit=args.limit)
        print("%s -> %d cards" % (paper["paper_id"], n))


def cmd_export(args, db):
    from .export import export_all
    pid = None
    if args.paper:
        pid = _need_one(db, args.paper)["paper_id"]
    files = export_all(db, out_dir=args.out, paper_id=pid, fmt=args.fmt)
    for f in files:
        print("exported:", f)


def cmd_gold_compare(args, db):
    from .gold import compare_gold, load_gold, write_compare_report
    paper = _need_one(db, args.ref)
    gold = load_gold(args.gold)
    report = compare_gold(db, paper["paper_id"], gold)
    out = args.out or (str(config.EXPORT_DIR / (paper["paper_id"] + "_gold")))
    paths = write_compare_report(report, out)
    s = report["summary"]
    print("gold-compare %s vs %s" % (paper["paper_id"], Path(args.gold).name))
    print("  items=%d matched=%d value_mismatch=%d missing=%d | recall=%.1f%%" % (
        s["n_gold_items"], s["n_matched"], s["n_value_mismatch"], s["n_missing"],
        100.0 * s["n_matched"] / max(1, s["n_gold_items"])))
    print("  extracted-not-in-gold (informational): %d" % s["n_extra_extracted"])
    for f in paths:
        print("  report:", f)


def cmd_serve(args, db):
    from .ui.server import main as serve_main
    serve_main(host=args.host, port=args.port)


def cmd_demo(args, db):
    from .parsing.pipeline import ingest
    from .extraction.pipeline import run_extraction
    from .curves.figures import extract_figures
    from .qc.checks import run_qc
    from .learning import generate_cards_for_paper
    from .export import export_all
    r = ingest(db, args.path)
    pid = r["paper"]["paper_id"]
    print("[1/5] ingested", pid, r["parse"]["classification"])
    print("[2/5] extract:", json.dumps(run_extraction(db, pid)["stats"], ensure_ascii=False)[:200])
    print("[3/5] figures:", len(extract_figures(db, pid)), "regions")
    print("[4/5] qc:", json.dumps(run_qc(db, pid), ensure_ascii=False)[:200])
    print("[5/5] cards:", generate_cards_for_paper(db, pid), "| exports:")
    for f in export_all(db, paper_id=pid):
        print("   ", f)
    print("review UI: python -m seels serve")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="seels", description="SEELS - scientific evidence "
                                 "extraction & learning system (MVP)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create dirs + schema").set_defaults(fn=cmd_init)
    sub.add_parser("papers", help="list papers").set_defaults(fn=cmd_papers)

    p = sub.add_parser("ingest", help="parse PDF(s)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--force", action="store_true", help="re-parse even if complete")
    p.add_argument("--meta", help="JSON file with title/authors/doi/arxiv_id")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("extract", help="run structured extraction")
    p.add_argument("ref", help="paper id / prefix / pdf path / 'all'")
    p.add_argument("--extractor", default="rules-v1",
                   help="rules-v1 | llm:openai-compat | llm:langextract")
    p.add_argument("--cfg-json", help='extractor config, e.g. {"api_key": "...", "model": "..."}')
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("figures", help="detect figure regions -> curve records")
    p.add_argument("ref")
    p.set_defaults(fn=cmd_figures)

    p = sub.add_parser("qc", help="run quality checks")
    p.add_argument("ref")
    p.set_defaults(fn=cmd_qc)

    p = sub.add_parser("cards", help="generate learning cards")
    p.add_argument("ref")
    p.add_argument("--limit", type=int, default=200)
    p.set_defaults(fn=cmd_cards)

    p = sub.add_parser("export", help="CSV/Excel export")
    p.add_argument("--paper", help="limit to one paper (default: all)")
    p.add_argument("--fmt", default="both", choices=("csv", "xlsx", "both"))
    p.add_argument("--out", help="output directory")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("gold-compare", help="compare gold standard vs extraction")
    p.add_argument("ref")
    p.add_argument("--gold", required=True)
    p.add_argument("--out", help="output prefix")
    p.set_defaults(fn=cmd_gold_compare)

    p = sub.add_parser("serve", help="start review web UI")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("demo", help="full pipeline on one PDF")
    p.add_argument("path")
    p.set_defaults(fn=cmd_demo)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config.ensure_dirs()
    db = open_db()
    try:
        args.fn(args, db)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
