"""Ingest pipeline: classify native/scanned, dual-engine parse, diff-log, persist.

Guarantees:
- The source PDF is opened read-only and its sha256 is re-verified after
  parsing; SEELS never writes to it.
- Full structured JSON is saved to WORK_DIR/papers/<paper_id>/parse.json.
- Per-page anomalies trigger the secondary engine; both results and the diff
  are recorded (parse_logs in DB + "fallback" block in JSON).
- Resume: already-parsed pages are kept unless --force; failed pages are
  retried on the next run.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .. import config
from ..util import jdump, rel_to, sha256_file, utc_now
from .base import EngineError, page_needs_ocr
from .engine_pdfplumber import PdfplumberEngine
from .engine_pypdfium2 import PdfiumEngine

PRIMARY = PdfplumberEngine()
SECONDARY = PdfiumEngine()

PAGE_DONE = ("ok", "fallback", "needs_ocr")


def parse_json_path(paper_id: str) -> Path:
    return config.WORK_DIR / "papers" / paper_id / "parse.json"


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(jdump(obj), encoding="utf-8")
    os.replace(tmp, path)


def load_parse(paper_id: str) -> Optional[dict]:
    p = parse_json_path(paper_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _summarize(pp: dict) -> dict:
    """Compact summary of a page parse result (for the non-canonical engine)."""
    return {"engine": pp["engine"], "status": pp.get("status"),
            "char_count": pp.get("char_count"), "n_words": len(pp.get("words") or []),
            "garbled": pp.get("garbled"), "n_tables": len(pp.get("tables") or []),
            "warnings": pp.get("warnings") or []}


def _parse_page_dual(db, paper_id: str, pdoc, sdoc, index: int) -> dict:
    """Parse one page with the primary engine; fall back when anomalous."""
    pp = None
    err = None
    try:
        pp = PRIMARY.parse_page(pdoc, index)
    except Exception as e:
        err = e
    page_no = index + 1

    if pp is None:
        db.log_parse(paper_id, page_no, PRIMARY.name, "error", "primary_page_failed",
                     "primary engine failed on page: %r" % (err,))
        try:
            sp = SECONDARY.parse_page(sdoc, index)
            sp["status"] = "fallback"
            sp["fallback"] = {"engine": PRIMARY.name, "error": repr(err), "chosen": False}
            db.log_parse(paper_id, page_no, SECONDARY.name, "warning", "fallback_used",
                         "secondary engine used as canonical for this page")
            return sp
        except Exception as e2:
            db.log_parse(paper_id, page_no, SECONDARY.name, "error", "both_engines_failed",
                         "both engines failed: %r" % (e2,))
            return {"index": index, "page_no": page_no, "engine": "none", "status": "failed",
                    "char_count": 0, "words": [], "tables": [], "images": [],
                    "captions": [], "text": "", "garbled": 0.0,
                    "warnings": ["both engines failed: primary=%r secondary=%r" % (err, e2)],
                    "fallback": None}

    need_secondary = None
    if pp["garbled"] > config.GARBLED_RATIO_THRESHOLD:
        need_secondary = "primary text garbled (ratio=%.2f)" % pp["garbled"]
    elif pp["char_count"] <= config.SCANNED_PAGE_MAX_CHARS:
        need_secondary = "primary found almost no text (%d chars)" % pp["char_count"]
    if pp["warnings"]:
        # keep going but make anomalies visible in the diff log
        for wmsg in pp["warnings"]:
            db.log_parse(paper_id, page_no, PRIMARY.name, "warning", "primary_warning", wmsg)

    if need_secondary is None:
        return pp

    sp = None
    try:
        sp = SECONDARY.parse_page(sdoc, index)
    except Exception as e:
        db.log_parse(paper_id, page_no, SECONDARY.name, "error", "secondary_failed",
                     "secondary engine failed while cross-checking: %r" % (e,))

    if sp is not None and sp["char_count"] > max(2 * pp["char_count"], 30) and \
            sp["garbled"] < pp["garbled"] + 0.05:
        # secondary sees clearly more/better text -> prefer it
        sp["status"] = "fallback"
        sp["fallback"] = dict(_summarize(pp), chosen=False, reason=need_secondary)
        db.log_parse(paper_id, page_no, SECONDARY.name, "warning", "fallback_canonical",
                     "secondary engine canonical: %s; secondary chars=%d vs primary=%d"
                     % (need_secondary, sp["char_count"], pp["char_count"]),
                     {"primary": _summarize(pp), "secondary": _summarize(sp)})
        return sp

    # primary stays canonical; annotate with cross-check summary
    if sp is not None:
        pp["fallback"] = dict(_summarize(sp), chosen=False, reason=need_secondary)
    if page_needs_ocr(pp, config.SCANNED_PAGE_MIN_IMAGE_COVER) or \
            (sp is not None and page_needs_ocr(sp, config.SCANNED_PAGE_MIN_IMAGE_COVER)
             and sp["char_count"] > pp["char_count"]):
        pp["status"] = "needs_ocr"
        db.log_parse(paper_id, page_no, PRIMARY.name, "warning", "ocr_required",
                     "no usable text layer; page needs OCR (OCR engine not bundled in MVP)")
    else:
        db.log_parse(paper_id, page_no, PRIMARY.name, "info", "cross_check_done",
                     "low-text page cross-checked with secondary engine",
                     {"primary": _summarize(pp),
                      "secondary": _summarize(sp) if sp else None})
    return pp


def ingest(db, pdf_path: str, force: bool = False, meta: Optional[dict] = None) -> dict:
    """Parse one PDF into the store. Returns {"paper":..., "parse": summary}."""
    pdf_path = str(Path(pdf_path).resolve())
    if not Path(pdf_path).exists():
        raise FileNotFoundError(pdf_path)
    sha = sha256_file(pdf_path)
    paper_id = "P-" + sha[:16]
    size = Path(pdf_path).stat().st_size

    existing = db.get_paper(paper_id)
    parse_doc = None if force else load_parse(paper_id)
    paper = {
        "paper_id": paper_id, "source_path": pdf_path, "sha256": sha,
        "title": (meta or {}).get("title") or (existing or {}).get("title"),
        "authors": (meta or {}).get("authors") or (existing or {}).get("authors"),
        "doi": (meta or {}).get("doi") or (existing or {}).get("doi"),
        "arxiv_id": (meta or {}).get("arxiv_id") or (existing or {}).get("arxiv_id"),
        "ingested_at": (existing or {}).get("ingested_at") or utc_now(),
        "meta": dict((existing or {}).get("meta") or {}, **(meta or {})),
    }
    db.upsert_paper(paper)

    pdoc = sdoc = None
    primary_ok = True
    try:
        pdoc = PRIMARY.open(pdf_path)
    except EngineError as e:
        primary_ok = False
        db.log_parse(paper_id, None, PRIMARY.name, "error", "primary_open_failed", str(e))
    try:
        sdoc = SECONDARY.open(pdf_path)
    except EngineError as e:
        db.log_parse(paper_id, None, SECONDARY.name, "error", "secondary_open_failed", str(e))
        if not primary_ok:
            db.update_paper(paper_id, parse_status="failed")
            raise
    try:
        n_pages = PRIMARY.n_pages(pdoc) if primary_ok else SECONDARY.n_pages(sdoc)
        if not paper["title"]:
            try:
                md = PRIMARY.metadata(pdoc) if primary_ok else SECONDARY.metadata(sdoc)
                if md.get("Title"):
                    db.update_paper(paper_id, title=str(md["Title"])[:500])
            except Exception:
                pass

        old_pages = {}
        if parse_doc and len(parse_doc.get("pages") or []) == n_pages:
            for pg in parse_doc["pages"]:
                if pg.get("status") in PAGE_DONE:
                    old_pages[pg["index"]] = pg
            if old_pages and not force:
                db.log_parse(paper_id, None, "pipeline", "info", "resume",
                             "reusing %d already-parsed pages" % len(old_pages))

        pages = []
        for i in range(n_pages):
            if i in old_pages:
                pages.append(old_pages[i])
                continue
            if primary_ok:
                pages.append(_parse_page_dual(db, paper_id, pdoc, sdoc, i))
            else:
                try:
                    sp = SECONDARY.parse_page(sdoc, i)
                    sp["status"] = "fallback"
                    pages.append(sp)
                except Exception as e:
                    db.log_parse(paper_id, i + 1, SECONDARY.name, "error",
                                 "page_failed", repr(e))
                    pages.append({"index": i, "page_no": i + 1, "engine": "none",
                                  "status": "failed", "char_count": 0, "words": [],
                                  "tables": [], "images": [], "captions": [],
                                  "text": "", "garbled": 0.0,
                                  "warnings": [repr(e)], "fallback": None})

        scanned = [p["page_no"] for p in pages if p["status"] == "needs_ocr"]
        failed = [p["page_no"] for p in pages if p["status"] == "failed"]
        if len(scanned) == n_pages and n_pages > 0:
            kind = "scanned"
        elif not scanned:
            kind = "native"
        else:
            kind = "mixed"
        status = "parsed" if not failed else ("failed" if len(failed) == n_pages else "partial")

        parse_doc = {
            "schema_version": "1.0",
            "paper_id": paper_id,
            "pdf": {"path": pdf_path, "sha256": sha, "bytes": size,
                    "path_rel": rel_to(pdf_path, config.BASE_DIR)},
            "classification": {"kind": kind, "scanned_pages": scanned, "failed_pages": failed},
            "n_pages": n_pages,
            "pages": pages,
            "stats": {
                "total_chars": sum(p.get("char_count", 0) for p in pages),
                "total_words": sum(len(p.get("words") or []) for p in pages),
                "total_tables": sum(len(p.get("tables") or []) for p in pages),
                "total_captions": sum(len(p.get("captions") or []) for p in pages),
                "fallback_pages": [p["page_no"] for p in pages if p["status"] == "fallback"],
            },
            "created_at": (parse_doc or {}).get("created_at") or utc_now(),
            "updated_at": utc_now(),
        }
        _atomic_write_json(parse_json_path(paper_id), parse_doc)

        sha_after = sha256_file(pdf_path)
        if sha_after != sha:
            db.log_parse(paper_id, None, "pipeline", "error", "source_hash_changed",
                         "source PDF hash changed during parsing - read-only policy violated")
        db.update_paper(paper_id, n_pages=n_pages, pdf_kind=kind, parse_status=status,
                        parse_json_path=rel_to(parse_json_path(paper_id), config.BASE_DIR))
        db.log_parse(paper_id, None, "pipeline", "info", "parse_done",
                     "kind=%s pages=%d scanned=%d failed=%d" % (kind, n_pages, len(scanned), len(failed)))
        summary = {k: parse_doc[k] for k in ("schema_version", "paper_id", "classification",
                                             "n_pages", "stats", "created_at", "updated_at")}
        summary["pdf"] = parse_doc["pdf"]
        return {"paper": db.get_paper(paper_id), "parse": summary,
                "parse_json": str(parse_json_path(paper_id))}
    finally:
        if pdoc is not None:
            PRIMARY.close(pdoc)
        if sdoc is not None:
            SECONDARY.close(sdoc)


def augment_ocr(db, paper_id: str, provider=None, force: bool = False) -> dict:
    """Fill needs_ocr pages with MinerU OCR words (docs/06 P4).

    Updates parse.json in place (atomic): page.status needs_ocr -> ocr_done,
    words/text/char_count from OCR, ocr_meta recorded. Downstream evidence from
    these pages must carry source_type='ocr' (rules extractor does this), which
    QC check C7 routes to needs_human_review automatically.
    """
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(paper_id)
    parse_doc = load_parse(paper_id)
    if not parse_doc:
        raise RuntimeError("paper %s not parsed yet" % paper_id)
    todo = [p["page_no"] for p in parse_doc.get("pages") or []
            if p.get("status") == "needs_ocr" or (force and p.get("status") == "ocr_done")]
    if not todo:
        return {"augmented": 0, "pages": []}
    if provider is None:
        from .ocr_provider import MinerUOcrProvider
        provider = MinerUOcrProvider()
    result = provider.ocr_pages(paper, todo, force=force)
    by_no = {p["page_no"]: p for p in result.get("pages") or []}
    n = 0
    for pg in parse_doc.get("pages") or []:
        o = by_no.get(pg["page_no"])
        if not o:
            continue
        prev_status = pg.get("status")
        pg["words"] = o.get("words") or []
        pg["text"] = o.get("text") or ""
        pg["char_count"] = len(pg["text"].strip())
        pg["status"] = "ocr_done"
        pg["engine"] = "mineru-ocr"
        pg["ocr_meta"] = {"engine": "mineru", "tier": getattr(provider, "tier", "basic"),
                          "prev_status": prev_status,
                          "word_boxes": "proportional_token_split(span_bbox)"}
        pg.setdefault("warnings", []).append(
            "OCR text: word boxes are proportional splits of span bboxes (approximate)")
        n += 1
        db.log_parse(paper_id, pg["page_no"], "mineru-ocr", "info", "ocr_augmented",
                     "chars=%d words=%d" % (pg["char_count"], len(pg["words"])))
    parse_doc["updated_at"] = utc_now()
    _atomic_write_json(parse_json_path(paper_id), parse_doc)
    remaining = [p["page_no"] for p in parse_doc.get("pages") or []
                 if p.get("status") == "needs_ocr"]
    db.update_paper(paper_id, parse_status=("parsed" if not remaining else "partial"))
    return {"augmented": n, "pages": sorted(by_no), "remaining_needs_ocr": remaining}


def render_page_image(paper: dict, page_no: int, dpi: int = 144) -> Path:
    """Render one page to a cached PNG (never touches the source PDF)."""
    paper_id = paper["paper_id"]
    out = config.RENDER_DIR / paper_id / ("p%04d_d%d.png" % (page_no, dpi))
    if out.exists():
        return out
    src = paper["source_path"]
    if not Path(src).exists():
        raise FileNotFoundError("source PDF missing: %s (re-ingest the paper)" % src)
    doc = SECONDARY.open(src)
    try:
        img = SECONDARY.render_page(doc, page_no - 1, dpi=dpi)
    finally:
        SECONDARY.close(doc)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.png")
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out
