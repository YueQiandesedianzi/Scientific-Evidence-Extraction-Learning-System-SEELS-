"""P1 POC: docling layout via the ASCII-path layout venv (subprocess worker).

For representative papers: run scripts/layout_worker.py, then compare docling
figure blocks against our caption-derived curve regions (IoU), report timing.
Writes data/work/layout_poc.json and prints a summary table.

Env: SEELS_LAYOUT_PYTHON  (default C:\\Users\\HP\\.seels\\layout-venv\\Scripts\\python.exe)
     HF_ENDPOINT          (default https://hf-mirror.com for weight download)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from seels import config
from seels.db import open_db

LAYOUT_PY = os.environ.get(
    "SEELS_LAYOUT_PYTHON", r"C:\Users\HP\.seels\layout-venv\Scripts\python.exe")
WORKER = ROOT / "scripts" / "layout_worker.py"
DEFAULT_PAPERS = ["P-aa760664b1f01846", "P-a160c171e15d68f3", "P-455fe33f2b5fab9a"]


def iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def run_worker(pdf: str, out_json: Path) -> dict:
    env = dict(os.environ)
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env["PYTHONIOENCODING"] = "utf-8"
    t0 = time.time()
    p = subprocess.run([LAYOUT_PY, str(WORKER), "--pdf", pdf, "--out", str(out_json),
                        "--no-tables"],
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       timeout=3600, stdin=subprocess.DEVNULL)
    dt = time.time() - t0
    if p.returncode != 0:
        return {"error": (p.stdout or "")[-400:] + (p.stderr or "")[-1200:], "seconds": dt}
    return {"json": json.loads(Path(out_json).read_text(encoding="utf-8")),
            "stdout": (p.stdout or "").strip()[-200:], "seconds": dt}


def main():
    pids = sys.argv[1:] or DEFAULT_PAPERS
    db = open_db()
    outdir = config.WORK_DIR / "layout"
    outdir.mkdir(parents=True, exist_ok=True)
    report = {"engine": "docling (subprocess worker)", "papers": []}
    for pid in pids:
        paper = db.get_paper(pid)
        if not paper:
            print("skip (not ingested):", pid)
            continue
        sidecar = outdir / (pid + ".docling.json")
        r = run_worker(paper["source_path"], sidecar)
        if "error" in r:
            print("ERROR", pid, r["error"][-300:])
            report["papers"].append({"paper_id": pid, "error": r["error"][-600:],
                                     "seconds": round(r["seconds"], 1)})
            continue
        doc = r["json"]
        figs = [(pg["page_no"], b["bbox"]) for pg in doc["pages"]
                for b in pg["blocks"] if b["type"] == "figure"]
        tabs = [(pg["page_no"], b["bbox"]) for pg in doc["pages"]
                for b in pg["blocks"] if b["type"] == "table"]
        curves = db.list_curves(pid)
        matched, ious = 0, []
        for cv in curves:
            best = 0.0
            for pno, bb in figs:
                if pno == cv.get("page") and cv.get("region_bbox"):
                    best = max(best, iou(cv["region_bbox"], bb))
            if best >= 0.5:
                matched += 1
            ious.append(round(best, 3))
        row = {"paper_id": pid, "pages": paper.get("n_pages"),
               "worker_seconds": round(r["seconds"], 1),
               "engine_seconds": doc.get("seconds"),
               "docling_figures": len(figs), "docling_tables": len(tabs),
               "our_curve_regions": len(curves),
               "regions_matched_iou>=0.5": matched,
               "median_iou": sorted(ious)[len(ious) // 2] if ious else None,
               "sidecar": str(sidecar)}
        report["papers"].append(row)
        print(json.dumps(row, ensure_ascii=False))
    try:
        report["docling_version"] = doc.get("engine_version")
    except Exception:
        pass
    outp = config.WORK_DIR / "layout_poc.json"
    outp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("report:", outp)


if __name__ == "__main__":
    main()
