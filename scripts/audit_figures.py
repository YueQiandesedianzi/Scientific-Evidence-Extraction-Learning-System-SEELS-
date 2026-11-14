"""Audit figure-region quality: source distribution + fallback ratios.

Re-runnable anytime:  python scripts/audit_figures.py
Reports per region_source (layout:docling / embedded_images /
heuristic_fallback / manual) so the docs/06 acceptance metric
"heuristic fallback < 10%" can be verified objectively.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from seels.db import open_db
from seels.parsing.pipeline import load_parse


def main():
    db = open_db()
    curves = db.list_curves()
    src = Counter((c.get("region_source") or "legacy_null") for c in curves)
    print("TOTAL_CURVES:", len(curves))
    for k, v in src.most_common():
        print("  %-22s %3d  (%.0f%%)" % (k, v, 100 * v / max(1, len(curves))))
    heur = src.get("heuristic_fallback", 0) + src.get("legacy_null", 0)
    print("HEURISTIC_OR_LEGACY: %d (%.0f%%)" % (heur, 100 * heur / max(1, len(curves))))
    named = sum(1 for c in curves if not (c.get("figure_ref") or "").startswith("Unnamed"))
    print("CAPTION_NAMED:", named, "| UNNAMED_BLOCKS:", len(curves) - named)
    # layout coverage per paper
    print()
    print("%-20s %6s %8s %8s" % ("paper", "curves", "layout", "heur/null"))
    for p in db.list_papers():
        cs = db.list_curves(p["paper_id"])
        lay = sum(1 for c in cs if (c.get("region_source") or "").startswith("layout"))
        heu = sum(1 for c in cs if (c.get("region_source") or "") in
                  ("heuristic_fallback", "embedded_images", ""))
        print("%-20s %6d %8d %8d" % (p["paper_id"], len(cs), lay, heu))


if __name__ == "__main__":
    main()
