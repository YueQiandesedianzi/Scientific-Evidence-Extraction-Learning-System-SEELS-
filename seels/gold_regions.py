"""P3 evaluation: human gold figure regions vs SEELS regions (IoU).

Gold file: data/gold/regions_gold_*.json produced by
scripts/make_region_worksheet.py and filled by a human annotator
(items with gold_bbox=null are skipped and counted).

Reports, per item: best IoU of our DB region (and its source) plus best IoU of
raw layout blocks; summary pass rates at tolerance (default 0.8).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .util import utc_now


def iou(a, b) -> float:
    if not a or not b:
        return 0.0
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / ua if ua > 0 else 0.0


def compare_regions(db, gold: dict) -> dict:
    rows = []
    n_ok = n_skip = n_missing = 0
    ious_ours, ious_layout = [], []
    for it in gold.get("items") or []:
        g = it.get("gold_bbox")
        if not g:
            n_skip += 1
            rows.append({**{k: it.get(k) for k in ("id", "paper_id", "page", "figure_ref")},
                         "status": "SKIPPED"})
            continue
        tol = float(it.get("tolerance_iou", 0.8))
        cands = [c for c in db.list_curves(it["paper_id"]) if c.get("page") == it["page"]]
        best_ours, best_src = 0.0, None
        for c in cands:
            v = iou(c.get("region_bbox"), g)
            if v > best_ours:
                best_ours, best_src = v, c.get("region_source")
        from .layout.base import sidecar_path
        best_lay = 0.0
        sp = sidecar_path(it["paper_id"][2:] if it["paper_id"].startswith("P-") else "", "docling")
        # sidecar keyed by pdf sha: resolve via paper row
        paper = db.get_paper(it["paper_id"])
        if paper:
            sp = sidecar_path(paper["sha256"], "docling")
            if sp.exists():
                try:
                    lay = json.loads(sp.read_text(encoding="utf-8"))
                    for pg in lay.get("pages") or []:
                        if pg.get("page_no") == it["page"]:
                            for b in pg.get("blocks") or []:
                                if b.get("type") == "figure":
                                    best_lay = max(best_lay, iou(b.get("bbox"), g))
                except Exception:
                    pass
        status = "PASS" if best_ours >= tol else ("MISSING" if not cands else "FAIL")
        if status == "PASS":
            n_ok += 1
        elif status == "MISSING":
            n_missing += 1
        ious_ours.append(round(best_ours, 3))
        ious_layout.append(round(best_lay, 3))
        rows.append({**{k: it.get(k) for k in ("id", "paper_id", "page", "figure_ref")},
                     "status": status, "tol": tol,
                     "our_best_iou": round(best_ours, 3), "our_region_source": best_src,
                     "layout_best_iou": round(best_lay, 3),
                     "gold_bbox": g, "gold_note": it.get("gold_note", "")})
    n = len(ious_ours)
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    summary = {
        "generated_at": utc_now(), "annotator": gold.get("annotator"),
        "n_items": len(gold.get("items") or []), "n_evaluated": n,
        "n_skipped_null_gold": n_skip, "n_pass": n_ok, "n_missing_region": n_missing,
        "pass_rate": round(n_ok / n, 3) if n else None,
        "mean_iou_ours": mean(ious_ours), "mean_iou_layout_blocks": mean(ious_layout),
        "tolerance_default": 0.8,
    }
    return {"summary": summary, "rows": rows}


def write_region_report(report: dict, out_prefix: str):
    out_csv = Path(out_prefix + "_regions.csv")
    out_json = Path(out_prefix + "_regions.json")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = report["rows"]
    cols = sorted({k for r in rows for k in r}) if rows else ["_empty"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        wr.writerows(rows)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    return [str(out_csv), str(out_json)]
