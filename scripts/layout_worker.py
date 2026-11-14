"""Standalone docling layout worker.

Runs under the dedicated ASCII-path layout venv (see requirements-layout.txt
and docs/06 plan P1). Isolation reasons:
  1. docling-parse's native layer fails to load its bundled resources from
     non-ASCII install paths (observed on E:\\<CJK>\\... with glyphs .dat files),
     so this worker lives in C:\\Users\\<user>\\.seels\\layout-venv;
  2. the PDF under analysis may also sit on a non-ASCII path, so it is copied
     to an ASCII temp file for the conversion (originals stay read-only);
  3. SEELS core venv stays free of torch/docling (subprocess + JSON contract).

Output: SEELS layout sidecar JSON (schema seels.layout/1.0), bboxes converted
to top_left_pts. Written by Python (unicode-safe) directly to --out.

Usage:
  <layout-venv python> layout_worker.py --pdf P --out O [--page-range a-b] [--ocr]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--page-range", default=None, help="inclusive 1-based, e.g. 3-12")
    ap.add_argument("--ocr", action="store_true", help="enable OCR pipeline (scanned pages)")
    ap.add_argument("--no-tables", action="store_true",
                    help="skip TableFormer table-structure analysis (much faster; "
                         "figure regions do not need it)")
    ap.add_argument("--charts", action="store_true",
                    help="enable docling chart extraction (chart2csv via granite_vision; "
                         "downloads VLM weights on first use, slow on CPU)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    import docling
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    src = os.path.abspath(a.pdf)
    if not os.path.exists(src):
        print("WORKER_ERROR pdf not found: " + src)
        return 2
    digest = hashlib.sha256(open(src, "rb").read()).hexdigest()

    # cache: reuse a sidecar that already matches this exact PDF
    if os.path.exists(a.out) and not a.page_range:
        try:
            prev = json.load(open(a.out, encoding="utf-8"))
            if prev.get("pdf_sha256") == digest and prev.get("ocr") == bool(a.ocr):
                print("LAYOUT_CACHED pages=%d out=%s" % (len(prev.get("pages") or []), a.out))
                return 0
        except Exception:
            pass  # corrupt cache -> regenerate

    tmpdir = tempfile.mkdtemp(prefix="seels_layout_")
    tmp_pdf = os.path.join(tmpdir, digest[:16] + ".pdf")
    shutil.copy2(src, tmp_pdf)   # ASCII-safe working copy; original untouched

    opts = PdfPipelineOptions()
    opts.do_ocr = bool(a.ocr)
    opts.do_table_structure = not a.no_tables
    if a.charts:
        # chart extraction requires picture classification (docling enables it
        # automatically, but be explicit); weights: granite_vision preset
        try:
            opts.do_chart_extraction = True
        except Exception as e:
            print("WORKER_WARN chart option: %r" % (e,))
        try:
            opts.do_picture_classification = True
        except Exception:
            pass
    conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

    kwargs = {}
    if a.page_range:
        lo, hi = (int(x) for x in a.page_range.split("-", 1))
        kwargs["page_range"] = (lo, hi)

    t0 = time.time()
    try:
        res = conv.convert(tmp_pdf, raises_on_error=True, **kwargs)
    finally:
        pass
    doc = res.document
    seconds = round(time.time() - t0, 1)

    pages_meta = {}
    try:
        items = doc.pages.items() if hasattr(doc.pages, "items") else enumerate(doc.pages, 1)
        for page_no, page in items:
            pages_meta[int(page_no)] = {"width_pts": float(page.size.width),
                                        "height_pts": float(page.size.height)}
    except Exception as e:
        print("WORKER_WARN pages meta: %r" % (e,))

    blocks_by_page: dict = {}

    def add(kind, prov_list, meta=None):
        for prov in prov_list or []:
            try:
                pno = int(prov.page_no)
                bb = prov.bbox
                h = pages_meta.get(pno, {}).get("height_pts")
                try:
                    if h:
                        bb = bb.to_top_left_origin(h)
                except Exception:
                    pass
                rec = {"type": kind, "page_no": pno,
                       "bbox": [round(float(bb.l), 1), round(float(bb.t), 1),
                                round(float(bb.r), 1), round(float(bb.b), 1)]}
                if meta:
                    rec.update(meta)
                blocks_by_page.setdefault(pno, []).append(rec)
            except Exception as e:
                print("WORKER_WARN block %s: %r" % (kind, e))

    n_fig = n_tab = n_chart = 0
    for pic in (getattr(doc, "pictures", None) or []):
        cap = None
        try:
            cap = (pic.caption_text(doc) or "")[:300] or None
        except Exception:
            pass
        label = None
        try:
            label = str(pic.label) if getattr(pic, "label", None) else None
        except Exception:
            pass
        meta = {"caption": cap, "label": label}
        chart = getattr(pic, "chart", None)
        if chart is not None:
            try:
                dumped = chart.model_dump(mode="json") if hasattr(chart, "model_dump")                     else json.loads(json.dumps(chart, default=str))
                meta["chart"] = dumped
                n_chart += 1
            except Exception as e:
                meta["chart_error"] = repr(e)[:200]
        try:
            cls = getattr(pic, "classification", None)
            if cls is not None:
                meta["classification"] = str(getattr(cls, "classname", cls))
        except Exception:
            pass
        add("figure", getattr(pic, "prov", None), meta)
        n_fig += 1
    for tb in (getattr(doc, "tables", None) or []):
        cap = None
        try:
            cap = (tb.caption_text(doc) or "")[:300] or None
        except Exception:
            pass
        extra = {"caption": cap}
        data = getattr(tb, "data", None)
        if data is not None:
            extra["n_rows"] = getattr(data, "num_rows", None)
            extra["n_cols"] = getattr(data, "num_cols", None)
        add("table", getattr(tb, "prov", None), extra)
        n_tab += 1

    diag = {
        "n_pages_doc": len(getattr(doc, "pages", {}) or {}),
        "n_texts": len(getattr(doc, "texts", None) or []),
        "n_pictures": len(getattr(doc, "pictures", None) or []),
        "n_tables": len(getattr(doc, "tables", None) or []),
    }
    print("WORKER_DIAG " + json.dumps(diag))

    out = {
        "schema": "seels.layout/1.0",
        "engine": "docling",
        "engine_version": getattr(docling, "__version__", "unknown"),
        "pdf_sha256": digest,
        "pdf_path": src,
        "ocr": bool(a.ocr),
        "page_range": a.page_range,
        "seconds": seconds,
        "diag": diag,
        "pages": [{"page_no": pno, **pages_meta.get(pno, {}),
                   "blocks": blocks_by_page.get(pno, [])}
                  for pno in sorted(set(list(pages_meta) + list(blocks_by_page)))],
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("LAYOUT_OK pages=%d figures=%d tables=%d charts=%d seconds=%.1f out=%s"
          % (len(out["pages"]), n_fig, n_tab, n_chart, seconds, a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
