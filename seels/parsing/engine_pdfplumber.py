"""Primary parse engine: pdfplumber (pdfminer.six backend, MIT license).

Produces words/tables/images/captions with top-left-pts coordinates.
Read-only: opens the PDF and never writes to it.
"""
from __future__ import annotations

from .base import EngineError, ParseEngine, find_captions_in_lines
from ..util import garbled_ratio


class PdfplumberEngine(ParseEngine):
    name = "pdfplumber"

    def open(self, path: str):
        import pdfplumber
        try:
            return pdfplumber.open(path)
        except Exception as e:
            raise EngineError("pdfplumber cannot open %s: %r" % (path, e))

    def n_pages(self, doc) -> int:
        return len(doc.pages)

    def metadata(self, doc) -> dict:
        md = getattr(doc, "metadata", None) or {}
        return {k: (v[:500] if isinstance(v, str) else v)
                for k, v in md.items() if k in ("Title", "Author", "Subject", "Creator", "Producer")}

    def parse_page(self, doc, index: int) -> dict:
        page = doc.pages[index]
        warnings: list = []
        words = []
        try:
            for wd in page.extract_words(use_text_flow=False, keep_blank_chars=False):
                words.append({"text": wd["text"], "x0": round(wd["x0"], 2),
                              "top": round(wd["top"], 2), "x1": round(wd["x1"], 2),
                              "bottom": round(wd["bottom"], 2)})
        except Exception as e:
            warnings.append("word extraction failed: %r" % (e,))
        try:
            text = page.extract_text(x_tolerance=2, y_tolerance=5) or ""
        except Exception as e:
            text = " ".join(w["text"] for w in words)
            warnings.append("extract_text failed: %r" % (e,))
        tables = []
        try:
            for ti, t in enumerate(page.find_tables()):
                bb = t.bbox  # (x0, top, x1, bottom)
                rows = t.extract()
                cells = []
                try:
                    for r, row in enumerate(t.rows):
                        for c, cb in enumerate(row.cells):
                            if cb is None:
                                continue
                            txt = None
                            if r < len(rows) and c < len(rows[r]):
                                txt = rows[r][c]
                            cells.append({"r": r, "c": c, "text": txt,
                                          "bbox": [round(cb[0], 2), round(cb[1], 2),
                                                   round(cb[2], 2), round(cb[3], 2)]})
                except Exception as e:
                    warnings.append("cell geometry failed for table %d: %r" % (ti, e))
                tables.append({
                    "table_index": ti,
                    "bbox": [round(bb[0], 2), round(bb[1], 2), round(bb[2], 2), round(bb[3], 2)],
                    "n_rows": len(rows),
                    "n_cols": max((len(r) for r in rows), default=0),
                    "rows": rows,
                    "cells": cells,
                })
        except Exception as e:
            warnings.append("table detection failed: %r" % (e,))
        images = []
        try:
            for im in page.images:
                src = im.get("srcsize") or [None, None]
                images.append({
                    "bbox": [round(im["x0"], 2), round(im["top"], 2),
                             round(im["x1"], 2), round(im["bottom"], 2)],
                    "pixel_width": src[0], "pixel_height": src[1],
                })
        except Exception as e:
            warnings.append("image listing failed: %r" % (e,))
        captions = []
        try:
            lines = page.extract_text_lines()
            captions = find_captions_in_lines(
                [{"text": l["text"], "x0": l["x0"], "top": l["top"],
                  "x1": l["x1"], "bottom": l["bottom"]} for l in lines])
        except Exception as e:
            captions = find_captions_in_lines(
                [{"text": l["text"], "x0": l["x0"], "top": l["top"],
                  "x1": l["x1"], "bottom": l["bottom"]}
                 for l in _lines_from_words(words)])
            warnings.append("extract_text_lines failed, captions from words: %r" % (e,))
        return {
            "index": index, "page_no": index + 1,
            "width_pts": round(page.width, 2), "height_pts": round(page.height, 2),
            "engine": self.name, "status": "ok",
            "char_count": len(text.strip()),
            "garbled": round(garbled_ratio(text), 3),
            "words": words, "tables": tables, "images": images, "captions": captions,
            "text": text, "warnings": warnings, "fallback": None,
        }


def _lines_from_words(words: list) -> list:
    from .base import group_words_into_lines
    return group_words_into_lines(words)
