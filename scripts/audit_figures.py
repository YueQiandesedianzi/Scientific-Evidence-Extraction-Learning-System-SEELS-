
import sys, json, statistics
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from seels.db import open_db
from seels.parsing.pipeline import load_parse

db = open_db()
curves = db.list_curves()
n = len(curves)
heur = [c for c in curves if "region_heuristic" in (c.get("notes") or "")]
print("TOTAL_CURVES:", n)
print("HEURISTIC_FALLBACK:", len(heur), "(%.0f%%)" % (100 * len(heur) / max(1, n)))
areas = []
for c in curves:
    bb = c.get("region_bbox")
    if bb:
        areas.append(round((bb[2] - bb[0]) * (bb[3] - bb[1])))
if areas:
    print("REGION_AREA pts2: median", statistics.median(areas), "min", min(areas), "max", max(areas))
fixed_box = sum(1 for c in curves if "region_heuristic" in (c.get("notes") or "")
                and c.get("region_bbox") and abs((c["region_bbox"][3] - c["region_bbox"][1]) - 280) < 30)
print("BLIND_280PT_BOXES:", fixed_box)
print()
print("%-20s %5s %6s %8s %8s" % ("paper", "pages", "figcap", "img_pages", "emb_imgs"))
tot_caps = tot_imgs = tot_imgpages = 0
for p in db.list_papers():
    doc = load_parse(p["paper_id"])
    if not doc:
        continue
    figcaps = sum(1 for pg in doc["pages"] for c in (pg.get("captions") or []) if c["kind"] == "figure")
    imgpages = sum(1 for pg in doc["pages"] if pg.get("images"))
    imgs = sum(len(pg.get("images") or []) for pg in doc["pages"])
    tot_caps += figcaps; tot_imgs += imgs; tot_imgpages += imgpages
    print("%-20s %5d %6d %8d %8d" % (p["paper_id"], doc["n_pages"], figcaps, imgpages, imgs))
print("TOTALS figcaps=%d embedded_imgs=%d pages_with_imgs=%d" % (tot_caps, tot_imgs, tot_imgpages))
# subfigure caption check (Figure 3a/3b style refs)
import re
sub = 0
for p in db.list_papers():
    doc = load_parse(p["paper_id"])
    if not doc: continue
    for pg in doc["pages"]:
        for c in pg.get("captions") or []:
            if c["kind"] == "figure" and re.search(r"\d+[a-z]\b", c["ref"] or ""):
                sub += 1
print("SUBFIGURE_LETTER_REFS:", sub)
