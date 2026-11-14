"""P3 tooling: build a human-annotation worksheet for figure-region gold.

Renders sampled pages with:
  - blue box  = current SEELS region (curve record),
  - green boxes = docling layout figure blocks (when sidecar exists),
  - orange box = matched caption,
  - pt rulers along top/left edges so the annotator can read coordinates.

Writes PNGs to data/work/region_worksheet/ and a gold TEMPLATE json to
data/gold/regions_gold_TEMPLATE.json (annotator fills gold_bbox per item;
then run: python -m seels gold-regions --gold <filled file>).

Usage: python scripts/make_region_worksheet.py [N]   (default N=20, caption-named
regions first, spread across papers)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from seels import config
from seels.db import open_db
from seels.parsing.pipeline import load_parse, render_page_image

DPI = 100
S = DPI / 72.0


def sidecar_blocks(pid: str, page_no: int):
    from seels.layout.base import sidecar_path
    p = sidecar_path(pid_sha(pid), "docling")
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    for pg in data.get("pages") or []:
        if pg.get("page_no") == page_no:
            return [b["bbox"] for b in pg.get("blocks") or [] if b.get("type") == "figure"]
    return []


def pid_sha(pid: str) -> str:
    return pid[2:] if pid.startswith("P-") else pid


def main():
    n_target = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    db = open_db()
    out_dir = config.WORK_DIR / "region_worksheet"
    out_dir.mkdir(parents=True, exist_ok=True)
    curves = [c for c in db.list_curves() if not (c["figure_ref"] or "").startswith("Unnamed")]
    curves = [c for c in curves if c.get("region_bbox")]
    # spread across papers, caption-named first
    by_paper = {}
    for c in curves:
        by_paper.setdefault(c["paper_id"], []).append(c)
    picked = []
    while len(picked) < n_target and by_paper:
        for pid in list(by_paper):
            if by_paper[pid]:
                picked.append(by_paper[pid].pop(0))
            else:
                del by_paper[pid]
    items = []
    for i, cv in enumerate(picked, 1):
        pid, page_no = cv["paper_id"], cv["page"]
        paper = db.get_paper(pid)
        parse = load_parse(pid) or {}
        pg = next((p for p in parse.get("pages") or [] if p["page_no"] == page_no), None)
        if not pg or not paper:
            continue
        img = render_page_image(paper, page_no, dpi=DPI)
        fig_w, fig_h = pg["width_pts"] * S / DPI, pg["height_pts"] * S / DPI
        fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
        ax.imshow(plt.imread(str(img)))
        ax.set_xlim(0, pg["width_pts"])
        ax.set_ylim(pg["height_pts"], 0)
        blocks = sidecar_blocks(pid, page_no)
        for bb in blocks:
            ax.add_patch(Rectangle((bb[0], bb[1]), bb[2] - bb[0], bb[3] - bb[1],
                                   fill=False, edgecolor="#16a34a", lw=1.6, ls="-"))
        rb = cv["region_bbox"]
        ax.add_patch(Rectangle((rb[0], rb[1]), rb[2] - rb[0], rb[3] - rb[1],
                               fill=False, edgecolor="#2563eb", lw=2.0, ls="-"))
        for cap in pg.get("captions") or []:
            if cap["kind"] == "figure" and cap["ref"] == cv["figure_ref"]:
                cb = cap["bbox"]
                ax.add_patch(Rectangle((cb[0], cb[1]), cb[2] - cb[0], cb[3] - cb[1],
                                       fill=False, edgecolor="#d97706", lw=1.4, ls="--"))
        # pt rulers
        step = 50
        for x in range(0, int(pg["width_pts"]), step):
            ax.plot([x, x], [0, 6], color="red", lw=0.8)
            ax.text(x + 1, 10, str(x), fontsize=4, color="red")
        for y in range(0, int(pg["height_pts"]), step):
            ax.plot([0, 6], [y, y], color="red", lw=0.8)
            ax.text(7, y - 2, str(y), fontsize=4, color="red")
        ax.set_axis_off()
        wid = "RG-%03d" % i
        png = out_dir / (wid + "_" + pid + "_p" + str(page_no) + ".png")
        fig.savefig(png, bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        items.append({
            "id": wid, "paper_id": pid, "page": page_no,
            "figure_ref": cv["figure_ref"], "curve_id": cv["curve_id"],
            "worksheet_png": str(png),
            "our_region": rb, "our_region_source": cv.get("region_source"),
            "layout_blocks": blocks,
            "gold_bbox": None,
            "gold_note": "",
        })
        print(wid, pid, "p%d" % page_no, cv["figure_ref"], "->", png.name)
    tpl = {
        "_instructions": (
            "Fill gold_bbox=[x0,y0,x1,y1] in PDF points, top-left origin, using the red pt "
            "rulers on each worksheet PNG (or the UI page render). Judge the TRUE figure "
            "extent (plot area incl. axis labels, excl. caption text). Leave gold_bbox null "
            "to skip an item. Then: python -m seels gold-regions --gold <this file>."),
        "annotator": "human:<name>  (REQUIRED: region gold must be human-judged)",
        "items": items,
    }
    out = config.GOLD_DIR / "regions_gold_TEMPLATE.json"
    out.write_text(json.dumps(tpl, ensure_ascii=False, indent=2), encoding="utf-8")
    print("template:", out, "items:", len(items))


if __name__ == "__main__":
    main()
