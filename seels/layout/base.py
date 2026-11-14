"""Shared layout contracts."""
from __future__ import annotations

from pathlib import Path

from .. import config

class LayoutError(Exception):
    pass


def layout_dir() -> Path:
    """Resolved lazily so tests can redirect config.WORK_DIR at runtime."""
    d = config.WORK_DIR / "layout"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sidecar_path(pdf_sha256: str, engine: str) -> Path:
    return layout_dir() / ("P-%s.%s.json" % (pdf_sha256[:16], engine))
