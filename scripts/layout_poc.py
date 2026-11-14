"""P1 POC: run docling layout analysis on representative papers and report.

Measures, per paper: wall time, pages/sec, figure/table block counts vs our
caption-based regions, and how many of our 73 curve regions would gain a
layout-derived bbox. Writes data/work/layout_poc.json + prints a summary.

Usage:  python scripts/layout_poc.py [paper_id ...]
Default papers: the vector-figure-heavy one, the image-rich one, the 76-page one.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from seels import config
from seels.db import open_db
from seels.parsing.pipeline import load_parse

DEFAULT_PAPERS = ["P-aa760664b1f01846", "P-a160c171e15d68f3", "P-455fe33f2b5fab9a"]


def convert(pdf_path: str):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    opts = PdfPipelineOptions()
    opts.do_ocr = False                 # native PDFs: text layer exists; keep POC fast
    opts.do_table_structure = True
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return conv.convert(pdf_path)


def blocks_of(doc):
    """Yield (kind, page_no, bbox_top_left[l,t,r,b]) for pictures and tables."""
    out = []
    pages = {p.page_no: p for p in doc.pages.values()} if hasattr(doc.pages, "values") else {}

    def page_h(page_no):
        p = pages.get(page_no)
        try:
            return float(p.size.height)
        except Exception:
            return None

    for pic in getattr(doc, "pictures", []) or []:
        for prov in getattr(pic, "prov", []) or []:
            bb = prov.bbox
            try:
                bb_tl = bb.to_top_left_origin(page_h(prov.page_no) or 792.0)
            except Exception:
                bb_tl = bb
            out.append(("figure", prov.page_no,
                        [round(bb_tl.l, 1), round(bb_tl.t, 1), round(bb_tl.r, 1), round(bb_tl.b, 1)]))
    for tb in getattr(doc, "tables", []) or []:
        for prov in getattr(tb, "prov", []) or []:
            bb = prov.bbox
            try:
                bb_tl = bb.to_top_left_origin(page_h(prov.page_no) or 792.0)
            except Exception:
                bb_tl = bb
            out.append(("table", prov.page_no,
                        [round(bb_tl.l, 1), round(bb_tl.t, 1), round(bb_tl.r, 1), round(bb_tl.b, 1)]))
    return out


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def main():
    pids = sys.argv[1:] or DEFAULT_PAPERS
    db = open_db()
    report = {"engine": "docling", "papers": []}
    t_all = time.time()
    for pid in pids:
        paper = db.get_paper(pid)
        if not paper:
            print("skip (not ingested):", pid)
            continue
        t0 = time.time()
        try:
            res = convert(paper["source_path"])
        except Exception as e:
            report["papers"].append({"paper_id": pid, "error": repr(e)[:300]})
            print("ERROR", pid, repr(e)[:200])
            continue
        dt = time.time() - t0
        doc = res.document
        blocks = blocks_of(doc)
        figs = [b for b in blocks if b[0] == "figure"]
        tabs = [b for b in blocks if b[0] == "table"]
        parse = load_parse(pid) or {}
        caps = [(pg["page_no"], c) for pg in parse.get("pages", [])
                for c in (pg.get("captions") or []) if c["kind"] == "figure"]
        curves = [c for c in db.list_curves(pid)]
        # match each existing curve region to best docling figure block (same page)
        matched, ious = 0, []
        for cv in curves:
            best = 0.0
            for kind, pno, bb in figs:
                if pno == cv.get("page") and cv.get("region_bbox"):
                    best = max(best, iou(cv["region_bbox"], bb))
            if best >= 0.5:
                matched += 1
            ious.append(round(best, 3))
        row = {"paper_id": pid, "pages": paper.get("n_pages"), "seconds": round(dt, 1),
               "sec_per_page": round(dt / max(1, paper.get("n_pages") or 1), 2),
               "docling_figures": len(figs), "docling_tables": len(tabs),
               "our_fig_captions": len(caps), "our_curve_regions": len(curves),
               "regions_with_layout_match_iou>=0.5": matched,
               "median_iou": sorted(ious)[len(ious) // 2] if ious else None}
        report["papers"].append(row)
        print(json.dumps(row, ensure_ascii=False))
    report["total_seconds"] = round(time.time() - t_all, 1)
    try:
        import docling
        report["docling_version"] = getattr(docling, "__version__", "2.150.0")
    except Exception:
        pass
    out = config.WORK_DIR / "layout_poc.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("report:", out)


if __name__ == "__main__":
    main()
