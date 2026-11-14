"""Shared layout contracts."""
from __future__ import annotations

from pathlib import Path

from .. import config

LAYOUT_DIR = config.WORK_DIR / "layout"


class LayoutError(Exception):
    pass


def sidecar_path(pdf_sha256: str, engine: str) -> Path:
    LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    return LAYOUT_DIR / ("P-%s.%s.json" % (pdf_sha256[:16], engine))
