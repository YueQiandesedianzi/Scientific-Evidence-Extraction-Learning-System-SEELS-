"""docling layout provider: subprocess worker under an ASCII-path venv.

Why subprocess (details in scripts/layout_worker.py):
  1. docling-parse's native layer cannot load its bundled glyph/cmap resources
     from non-ASCII install paths (reproduced on E:\\<CJK>\\...), so the engine
     lives in an ASCII-path venv (default C:\\Users\\<user>\\.seels\\layout-venv,
     override with SEELS_LAYOUT_PYTHON);
  2. PDFs on non-ASCII paths are copied to an ASCII temp file by the worker
     (originals remain read-only and untouched);
  3. the SEELS core venv stays free of torch/docling; the contract is a JSON
     sidecar (schema seels.layout/1.0) cached by PDF sha256 -> reproducible.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .. import config
from .base import LayoutError, sidecar_path

DEFAULT_LAYOUT_PY = r"C:\Users\HP\.seels\layout-venv\Scripts\python.exe"


class DoclingLayoutProvider:
    name = "docling"

    def __init__(self, python_exe: str = None, worker: str = None, ocr: bool = False,
                 tables: bool = False, charts: bool = False, timeout: int = 7200):
        self.python_exe = python_exe or os.environ.get("SEELS_LAYOUT_PYTHON",
                                                       DEFAULT_LAYOUT_PY)
        self.worker = worker or str(Path(config.BASE_DIR) / "scripts" / "layout_worker.py")
        self.ocr = ocr
        self.tables = tables      # TableFormer is slow on CPU; figure regions don't need it
        self.charts = charts      # granite_vision chart2csv; heavy on CPU, opt-in
        self.timeout = timeout

    def available(self) -> bool:
        return Path(self.python_exe).exists() and Path(self.worker).exists()

    def analyze(self, paper: dict, force: bool = False) -> dict:
        engine = (self.name + ("-ocr" if self.ocr else "")
                  + ("-tab" if self.tables else "") + ("-charts" if self.charts else ""))
        out = sidecar_path(paper["sha256"], engine)
        if out.exists() and not force:
            try:
                data = json.loads(out.read_text(encoding="utf-8"))
                if data.get("pdf_sha256") == paper["sha256"]:
                    return data
            except Exception:
                pass  # corrupt/stale sidecar -> regenerate below
        if not self.available():
            raise LayoutError("layout venv/worker not available: %s | %s"
                              % (self.python_exe, self.worker))
        env = dict(os.environ)
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        env["PYTHONIOENCODING"] = "utf-8"
        cmd = [self.python_exe, self.worker,
               "--pdf", paper["source_path"], "--out", str(out)]
        if self.ocr:
            cmd.append("--ocr")
        if not self.tables:
            cmd.append("--no-tables")
        if self.charts:
            cmd.append("--charts")
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=self.timeout,
                           stdin=subprocess.DEVNULL)
        if p.returncode != 0 or not out.exists():
            raise LayoutError("layout worker failed (rc=%s): %s" % (
                p.returncode, ((p.stdout or "") + "\n" + (p.stderr or ""))[-800:]))
        data = json.loads(out.read_text(encoding="utf-8"))
        if data.get("pdf_sha256") != paper["sha256"]:
            raise LayoutError("layout sidecar sha mismatch - refusing stale output")
        return data
