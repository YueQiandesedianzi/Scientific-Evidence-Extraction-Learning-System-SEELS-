"""Standalone MinerU OCR worker (runs under the ASCII-path mineru venv).

Mirrors the layout_worker contract (docs/06 P4):
  - PDF copied to an ASCII temp path (original stays read-only & untouched);
  - drives the documented 'mineru-kit parse' CLI (middle_json output) as a
    nested subprocess with stdin=DEVNULL (no pipe inheritance hangs);
  - transforms blocks/spans (normalized [0,1] bboxes) into SEELS word boxes
    {text,x0,top,x1,bottom} in top_left_pts, proportional per-token split;
  - writes JSON sidecar (schema seels.ocr/1.0) keyed by pdf sha256;
  - --dump-raw keeps the untouched middle_json for schema inspection.

Usage:
  <mineru-venv python> ocr_worker.py --pdf P --out O [--pages 3,5] [--tier basic]
                                     [--ocr-mode ocr] [--dump-raw]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _find_middle_json(root: Path):
    cands = sorted(root.rglob("*middle*json*")) + sorted(root.rglob("*.json"))
    for c in cands:
        if c.name.endswith(".json"):
            try:
                d = json.loads(c.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(d, dict) and ("pages" in d or "pdf_info" in d):
                return c, d
    return None, None


def _extract_units_from_middle(mj):
    """Schema-aware walk of MinerU middle_json (verified 2026-11-15, mineru 4.2.2):
    pages[] -> {page_idx (0-based), blocks[]}; block: {type, bbox [0,1] normalized
    top-left, content: [{type:'text', content:'...'} | nested lists]}.
    """
    units = []

    def gather_texts(node, acc):
        if isinstance(node, dict):
            c = node.get("content")
            if isinstance(c, str):
                if c.strip():
                    acc.append(c.strip())
            elif c is not None:
                gather_texts(c, acc)
            for k, v in node.items():
                if k == "content":
                    continue
                if isinstance(v, (list, dict)):
                    gather_texts(v, acc)
        elif isinstance(node, list):
            for v in node:
                gather_texts(v, acc)

    for page in mj.get("pages") or []:
        pidx = page.get("page_idx")
        for blk in page.get("blocks") or []:
            bbox = blk.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            acc = []
            gather_texts(blk, acc)
            text = " ".join(acc)
            if text.strip():
                units.append((text.strip(), [float(v) for v in bbox], pidx))
    return units


def _harvest_text_units(node, page_idx=None, out=None):
    """Defensive recursive harvest of (text, bbox4, page_idx) from middle json."""
    if out is None:
        out = []
    if isinstance(node, dict):
        pidx = node.get("page_idx", page_idx)
        text = node.get("text") or node.get("content")
        bbox = node.get("bbox")
        if isinstance(text, str) and text.strip() and isinstance(bbox, (list, tuple)) \
                and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            out.append((text.strip(), [float(v) for v in bbox], pidx))
        for k, v in node.items():
            if k in ("text", "content", "bbox"):
                continue
            _harvest_text_units(v, pidx, out)
    elif isinstance(node, list):
        for v in node:
            _harvest_text_units(v, page_idx, out)
    return out


def _token_boxes(text, bbox, W, H):
    """Split a text unit's bbox across whitespace tokens proportionally."""
    tokens = [(m.group(0), m.start()) for m in re.finditer(r"\S+", text)]
    if not tokens:
        return []
    total_chars = sum(len(t) for t, _ in tokens) or 1
    x0, y0, x1, y1 = bbox[0] * W, bbox[1] * H, bbox[2] * W, bbox[3] * H
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    width = x1 - x0
    out = []
    acc = 0
    for i, (tok, _) in enumerate(tokens):
        wx0 = x0 + width * (acc / total_chars)
        acc += len(tok)
        wx1 = x0 + width * (acc / total_chars) if i < len(tokens) - 1 else x1
        out.append({"text": tok, "x0": round(wx0, 2), "top": round(y0, 2),
                    "x1": round(max(wx1, wx0 + 0.5), 2), "bottom": round(y1, 2)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default=None, help="comma list of 1-based pages")
    ap.add_argument("--tier", default="basic")
    ap.add_argument("--ocr-mode", default="ocr")
    ap.add_argument("--dump-raw", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    src = os.path.abspath(a.pdf)
    if not os.path.exists(src):
        print("WORKER_ERROR pdf not found: " + src)
        return 2
    digest = hashlib.sha256(open(src, "rb").read()).hexdigest()

    tmpdir = Path(tempfile.mkdtemp(prefix="seels_ocr_"))
    tmp_pdf = tmpdir / (digest[:16] + ".pdf")
    shutil.copy2(src, tmp_pdf)
    outdir = tmpdir / "out"
    outdir.mkdir()

    kit = Path(sys.executable).parent / "mineru-kit.exe"
    if not kit.exists():
        kit = Path(sys.executable).parent / "mineru-kit"
    # mineru-kit parse takes INPUTS as positional args (verified via --help)
    cmd = [str(kit), "parse", str(tmp_pdf), "--output", str(outdir),
           "--format", "middle_json", "--tier", a.tier, "--ocr-mode", a.ocr_mode]
    if a.pages:
        cmd += ["--pages", a.pages]
    t0 = time.time()
    env = dict(os.environ)
    env.setdefault("HF_HOME", r"D:\seels-tools\hf-cache")
    env.setdefault("MODELSCOPE_CACHE", r"D:\seels-tools\modelscope-cache")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, stdin=subprocess.DEVNULL,
                       timeout=7000)
    if p.returncode != 0 and a.pages:
        print("WORKER_WARN paged run failed (rc=%s), retrying whole doc" % p.returncode)
        cmd = [c for c in cmd if c != "--pages" and c != a.pages]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, stdin=subprocess.DEVNULL,
                           timeout=7000)
    if p.returncode != 0:
        print("WORKER_ERROR mineru-kit failed rc=%s\n%s\n%s"
              % (p.returncode, (p.stdout or "")[-600:], (p.stderr or "")[-900:]))
        return 3

    mj_path, mj = _find_middle_json(outdir)
    if mj is None:
        print("WORKER_ERROR no middle_json found under %s" % outdir)
        return 4
    if a.dump_raw:
        shutil.copy2(mj_path, Path(a.out).with_name(Path(a.out).stem + ".middle_raw.json"))

    # page sizes via pypdfium2 (a mineru dependency) - robust fallback
    sizes = {}
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(tmp_pdf))
        for i in range(len(doc)):
            wpt, hpt = doc[i].get_size()
            sizes[i + 1] = (float(wpt), float(hpt))
        doc.close()
    except Exception as ex:
        print("WORKER_WARN pdfium sizes: %r" % (ex,))
    layout_ext = ((mj.get("extensions") or {}).get("docvortex_layout") or {}).get("pages") or []
    for lp in layout_ext:
        try:
            pno = int(lp.get("page_idx", 0)) + 1
            if lp.get("width_pt") and lp.get("height_pt"):
                sizes[pno] = (float(lp["width_pt"]), float(lp["height_pt"]))
        except Exception:
            pass

    want_pages = None
    if a.pages:
        want_pages = {int(x) for x in a.pages.split(",") if x.strip().isdigit()}

    units = _extract_units_from_middle(mj)
    if not units:
        # generic fallback for schema drift
        units = _harvest_text_units(mj.get("pages") if isinstance(mj.get("pages"), list) else mj)
    by_page = {}
    for text, bbox, pidx in units:
        if pidx is None:
            continue
        pno = int(pidx) + 1                    # middle json page_idx is 0-based
        if want_pages and pno not in want_pages:
            continue
        W, H = sizes.get(pno, (612.0, 792.0))
        by_page.setdefault(pno, []).extend(_token_boxes(text, bbox, W, H))

    pages_out = []
    for pno in sorted(by_page):
        W, H = sizes.get(pno, (612.0, 792.0))
        words = sorted(by_page[pno], key=lambda wd: (wd["top"], wd["x0"]))
        pages_out.append({"page_no": pno, "width_pts": round(W, 2), "height_pts": round(H, 2),
                          "words": words,
                          "text": " ".join(wd["text"] for wd in words)})
    out = {
        "schema": "seels.ocr/1.0", "engine": "mineru", "tier": a.tier,
        "ocr_mode": a.ocr_mode, "pdf_sha256": digest, "pdf_path": src,
        "requested_pages": sorted(want_pages) if want_pages else None,
        "seconds": round(time.time() - t0, 1),
        "pages": pages_out,
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("OCR_OK pages=%d words=%d seconds=%.1f out=%s"
          % (len(pages_out), sum(len(p["words"]) for p in pages_out),
             out["seconds"], a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
