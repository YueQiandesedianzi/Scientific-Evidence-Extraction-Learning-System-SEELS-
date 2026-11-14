"""Shared data shapes and engine interface for the parsing subsystem.

Canonical coordinate system for the whole project: "top_left_pts"
  origin = top-left of page, unit = PDF points (1/72 inch), y grows downward.
  bbox = [x0, y0, x1, y1]

Engines must convert their native coordinates into this system.
Word dict:  {"text": str, "x0": f, "top": f, "x1": f, "bottom": f}
Table dict: {"table_index": int, "bbox": [..], "n_rows": int, "n_cols": int,
             "rows": [[str|None]], "cells": [{"r","c","text","bbox"}]}
Caption:    {"kind": "figure"|"table", "ref": "Figure 3", "text": str, "bbox": [..]}
"""
from __future__ import annotations

import re
from typing import Optional

CAPTION_RE = re.compile(
    r"^\s*(Fig(?:ure)?|Table|Tab)\.?\s*\.?\s*(\d+[a-zA-Z]?)\b", re.IGNORECASE)


class EngineError(Exception):
    """Raised when an engine cannot parse a page/document."""


class ParseEngine:
    """Interface implemented by both engines."""
    name = "abstract"

    def open(self, path: str):
        raise NotImplementedError

    def close(self, doc) -> None:
        try:
            doc.close()
        except Exception:
            pass

    def n_pages(self, doc) -> int:
        raise NotImplementedError

    def parse_page(self, doc, index: int) -> dict:
        raise NotImplementedError

    def metadata(self, doc) -> dict:
        return {}


def normalize_ref(kind: str, num: str) -> str:
    k = kind.lower()
    if k.startswith("fig"):
        return "Figure " + num
    return "Table " + num


def find_captions_in_lines(lines: list) -> list:
    """lines: [{"text","x0","top","x1","bottom"}] -> caption dicts."""
    caps = []
    for ln in lines:
        m = CAPTION_RE.match(ln.get("text", ""))
        if m:
            kind = "figure" if m.group(1).lower().startswith("fig") else "table"
            caps.append({
                "kind": kind,
                "ref": normalize_ref(m.group(1), m.group(2)),
                "text": ln["text"].strip(),
                "bbox": [round(ln["x0"], 2), round(ln["top"], 2),
                         round(ln["x1"], 2), round(ln["bottom"], 2)],
            })
    return caps


def group_words_into_lines(words: list, y_tol: float = 2.5) -> list:
    """Group word dicts into text lines (same baseline band), left-to-right."""
    lines: list = []
    for wd in sorted(words, key=lambda w: (w["top"], w["x0"])):
        placed = False
        for ln in lines:
            if abs(ln["top"] - wd["top"]) <= y_tol and abs(ln["bottom"] - wd["bottom"]) <= y_tol + 2:
                ln["words"].append(wd)
                ln["x0"] = min(ln["x0"], wd["x0"])
                ln["x1"] = max(ln["x1"], wd["x1"])
                placed = True
                break
        if not placed:
            lines.append({"top": wd["top"], "bottom": wd["bottom"],
                          "x0": wd["x0"], "x1": wd["x1"], "words": [wd]})
    out = []
    for ln in sorted(lines, key=lambda l: (l["top"], l["x0"])):
        ws = sorted(ln["words"], key=lambda w: w["x0"])
        out.append({"text": " ".join(w["text"] for w in ws),
                    "x0": ln["x0"], "top": ln["top"], "x1": ln["x1"], "bottom": ln["bottom"],
                    "words": ws})
    return out


def page_needs_ocr(page: dict, min_image_cover: float) -> bool:
    """A page is image-only ('scanned') when it has (almost) no text layer but
    large images covering most of the page area."""
    if page.get("char_count", 0) > 30:
        return False
    area = max(1.0, page.get("width_pts", 1) * page.get("height_pts", 1))
    cover = 0.0
    for im in page.get("images", []):
        bb = im.get("bbox") or [0, 0, 0, 0]
        cover += max(0.0, bb[2] - bb[0]) * max(0.0, bb[3] - bb[1])
    return (cover / area) >= min_image_cover
