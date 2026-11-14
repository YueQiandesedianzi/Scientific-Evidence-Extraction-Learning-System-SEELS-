"""MinerU OCR provider (docs/06 P4): subprocess worker under an ASCII-path venv.

Same isolation contract as the docling layout provider:
 - engine lives outside the core venv (SEELS_MINERU_PYTHON, default
   D:\\seels-tools\\mineru-venv);
 - sha256-keyed JSON sidecar cache under WORK_DIR/ocr/, pages merged across runs;
 - sidecar sha mismatch => refuse (never serve stale OCR for a different PDF).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .. import config

DEFAULT_MINERU_PY = r"D:\seels-tools\mineru-venv\Scripts\python.exe"


def ocr_dir() -> Path:
    """Resolved lazily so tests can redirect config.WORK_DIR at runtime."""
    d = config.WORK_DIR / "ocr"
    d.mkdir(parents=True, exist_ok=True)
    return d


class OcrError(Exception):
    pass


class MinerUOcrProvider:
    name = "mineru-ocr"

    def __init__(self, python_exe: str = None, worker: str = None,
                 tier: str = "basic", timeout: int = 7200):
        self.python_exe = python_exe or os.environ.get("SEELS_MINERU_PYTHON",
                                                       DEFAULT_MINERU_PY)
        self.worker = worker or str(Path(config.BASE_DIR) / "scripts" / "ocr_worker.py")
        self.tier = tier
        self.timeout = timeout

    def available(self) -> bool:
        return Path(self.python_exe).exists() and Path(self.worker).exists()

    def sidecar(self, sha256: str) -> Path:
        return ocr_dir() / ("P-%s.mineru.json" % sha256[:16])

    def ocr_pages(self, paper: dict, pages, force: bool = False) -> dict:
        sc = self.sidecar(paper["sha256"])
        cached = {}
        if sc.exists():
            try:
                prev = json.loads(sc.read_text(encoding="utf-8"))
                if prev.get("pdf_sha256") == paper["sha256"]:
                    cached = {p["page_no"]: p for p in prev.get("pages") or []}
            except Exception:
                cached = {}
        need = [p for p in pages if force or p not in cached]
        if need:
            if not self.available():
                raise OcrError("mineru venv/worker not available: %s | %s "
                               "(see requirements-layout.txt / docs 06 P4)"
                               % (self.python_exe, self.worker))
            env = dict(os.environ)
            env.setdefault("HF_HOME", r"D:\seels-tools\hf-cache")
            env.setdefault("MODELSCOPE_CACHE", r"D:\seels-tools\modelscope-cache")
            env["PYTHONIOENCODING"] = "utf-8"
            cmd = [self.python_exe, self.worker,
                   "--pdf", paper["source_path"], "--out", str(sc) + ".run",
                   "--pages", ",".join(str(p) for p in sorted(need)),
                   "--tier", self.tier]
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=self.timeout,
                               stdin=subprocess.DEVNULL)
            run_out = Path(str(sc) + ".run")
            if p.returncode != 0 or not run_out.exists():
                raise OcrError("ocr worker failed (rc=%s): %s" % (
                    p.returncode, ((p.stdout or "") + "\n" + (p.stderr or ""))[-800:]))
            fresh = json.loads(run_out.read_text(encoding="utf-8"))
            run_out.unlink(missing_ok=True)
            if fresh.get("pdf_sha256") != paper["sha256"]:
                raise OcrError("ocr sidecar sha mismatch - refusing stale output")
            for pg in fresh.get("pages") or []:
                cached[pg["page_no"]] = pg
            merged = {"schema": "seels.ocr/1.0", "engine": "mineru", "tier": self.tier,
                      "pdf_sha256": paper["sha256"], "pdf_path": paper["source_path"],
                      "pages": [cached[k] for k in sorted(cached)]}
            sc.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        return {"schema": "seels.ocr/1.0", "engine": "mineru",
                "pdf_sha256": paper["sha256"],
                "pages": [cached[k] for k in sorted(cached) if k in set(pages)]}
