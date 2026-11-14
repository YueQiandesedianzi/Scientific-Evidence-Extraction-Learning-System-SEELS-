"""Global configuration for SEELS. Paths can be overridden via env vars."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("SEELS_DATA_DIR", str(BASE_DIR / "data")))
PAPERS_DIR = DATA_DIR / "papers"      # original PDFs, read-only by policy
GOLD_DIR = DATA_DIR / "gold"          # human gold-standard annotations
WORK_DIR = DATA_DIR / "work"          # derived artifacts (parse json, renders, db)
RENDER_DIR = WORK_DIR / "renders"
CURVE_DIR = WORK_DIR / "curves"
EXPORT_DIR = WORK_DIR / "exports"
DB_PATH = Path(os.environ.get("SEELS_DB", str(WORK_DIR / "seels.sqlite3")))

SCHEMA_VERSION = "1.0.0"

# Canonical bbox convention: origin top-left of page, unit = PDF points (1/72 in),
# y grows downward. Engine adapters must convert to this convention.
COORD_SYSTEM = "top_left_pts"

# --- parsing thresholds ---
SCANNED_PAGE_MAX_CHARS = 30          # page with <= N text chars counts as image-only
SCANNED_PAGE_MIN_IMAGE_COVER = 0.4   # image area fraction to call a textless page scanned
GARBLED_RATIO_THRESHOLD = 0.30       # fraction of replacement/private chars before "garbled"

# --- extraction ---
QUOTE_MATCH_MIN_SCORE = 0.90         # min similarity when locating a quote in page text

# --- curves ---
OVERLAY_MAX_MEDIAN_PX = 3.0          # redraw-check threshold (pixels, image space)

# --- ui ---
UI_HOST = os.environ.get("SEELS_UI_HOST", "127.0.0.1")
UI_PORT = int(os.environ.get("SEELS_UI_PORT", "8765"))

def ensure_dirs() -> None:
    for d in (DATA_DIR, PAPERS_DIR, GOLD_DIR, WORK_DIR, RENDER_DIR, CURVE_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
