"""Figure-region detection + curve record creation + digitizing operations.

Region sources, in priority order:
  1. layout:docling   - deep layout blocks (seels.layout, subprocess worker),
                        matched to "Figure N" captions by column overlap +
                        vertical proximity; unmatched large blocks become
                        "Unnamed p<n>#<i>" regions;
  2. embedded_images  - geometric union of raster images above the caption;
  3. heuristic_fallback - blind box above the caption (flagged in notes + QC).
Region images are cropped from a page render (derived files only; originals
untouched). Curve ids are deterministic (hash of paper|page|ref) so ids are
stable across engines; manual/layout regions are never silently overwritten
(manual regions are fully protected), and stale untouched legacy regions are
pruned on re-runs.
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import config
from ..parsing.pipeline import load_parse, render_page_image
from ..util import jdump, sha256_bytes, utc_now
from .calibrate import build_calibration
from .digitize import (enforce_source_policy, parse_wpd_csv, points_from_pixels,
                       wpd_points_to_records)
from .redraw import overlay_curve, redraw_curve

FIGURE_DPI = 200


def _region_for_caption(page: dict, cap: dict):
    cb = cap["bbox"]
    W, H = page["width_pts"], page["height_pts"]
    cands = []
    for im in page.get("images") or []:
        bb = im.get("bbox")
        if not bb:
            continue
        if bb[3] <= cb[1] + 60 and bb[3] >= cb[1] - 0.9 * H:
            ox = min(bb[2], cb[2] + 90) - max(bb[0], cb[0] - 90)
            if ox > 10:
                cands.append(bb)
    note = None
    if cands:
        x0 = min(b[0] for b in cands)
        y0 = min(b[1] for b in cands)
        x1 = max(b[2] for b in cands)
        y1 = max(b[3] for b in cands)
        region = [x0, y0 - 4, x1, min(H, cb[3] + 2)]
    else:
        region = [max(0, cb[0] - 10), max(20.0, cb[1] - 280),
                  min(W, cb[2] + 10), min(H, cb[3] + 2)]
        note = "region_heuristic_no_embedded_images"
    region = [max(0.0, region[0]), max(0.0, region[1]),
              min(W, region[2]), min(H, region[3])]
    if region[2] - region[0] < 40 or region[3] - region[1] < 40:
        return None, None
    return [round(v, 2) for v in region], note


def _crop_region(paper: dict, page_no: int, region, img_path: Path, dpi: int) -> None:
    from PIL import Image
    page_img = render_page_image(paper, page_no, dpi=dpi)
    im = Image.open(page_img)
    s = dpi / 72.0
    crop = im.crop((int(region[0] * s), int(region[1] * s),
                    int(region[2] * s), int(region[3] * s)))
    img_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(img_path, format="PNG")


def _best_block_for_caption(cap: dict, blocks: list, used: set):
    """Index of the layout figure block that best matches a caption, else None."""
    cb = cap["bbox"]
    best, best_score = None, 0.0
    for i, b in enumerate(blocks):
        if i in used:
            continue
        bb = b.get("bbox")
        if not bb or len(bb) != 4:
            continue
        vgap = cb[1] - bb[3]                 # caption top minus block bottom
        if vgap < -30 or vgap > 250:
            continue
        ox = min(bb[2], cb[2] + 60) - max(bb[0], cb[0] - 60)
        if ox < 20:
            continue
        area = max(1.0, (bb[2] - bb[0]) * (bb[3] - bb[1]))
        score = (ox / max(1.0, cb[2] - cb[0])
                 + min(area, 60000.0) / 100000.0
                 - abs(vgap) / 500.0)
        if score > best_score:
            best, best_score = i, score
    return best


def _prune_stale(db, paper_id: str, keep: set) -> int:
    """Delete untouched legacy regions (old id scheme / superseded heuristics).

    Never deletes regions that are manual/layout-sourced, carry points or a
    calibration, or have any human review attached.
    """
    removed = 0
    for cv in db.list_curves(paper_id):
        cid = cv["curve_id"]
        if cid in keep:
            continue
        src = cv.get("region_source") or ""
        if src == "manual" or src.startswith("layout"):
            continue
        if db.list_curve_points(cid) or cv.get("calibration"):
            continue
        if db.list_reviews(target_type="curve", target_id=cid):
            continue
        img = cv.get("image_path")
        db.conn().execute("DELETE FROM curves WHERE curve_id=?", (cid,))
        removed += 1
        if img:
            try:
                base = Path(img)
                for p in (base, base.with_name(base.stem + "_overlay.png"),
                          base.with_name(base.stem + "_redraw.png")):
                    if p.exists():
                        p.unlink()
            except OSError:
                pass
    db.conn().commit()
    return removed


def extract_figures(db, paper_id: str, dpi: int = FIGURE_DPI,
                    use_layout: bool = True) -> list:
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError(paper_id)
    parse_doc = load_parse(paper_id)
    if not parse_doc:
        raise RuntimeError("paper %s not parsed yet" % paper_id)

    layout_doc = None
    if use_layout:
        try:
            from ..layout.docling_provider import DoclingLayoutProvider
            prov = DoclingLayoutProvider()
            if prov.available():
                layout_doc = prov.analyze(paper)
                db.log_parse(paper_id, None, "layout:docling", "info", "layout_ready",
                             "version=%s seconds=%s sha_ok=%s" % (
                                 layout_doc.get("engine_version"),
                                 layout_doc.get("seconds"),
                                 layout_doc.get("pdf_sha256") == paper["sha256"]))
            else:
                db.log_parse(paper_id, None, "layout", "info", "layout_unavailable",
                             "layout venv/worker not found; using geometric heuristics "
                             "(see requirements-layout.txt / SEELS_LAYOUT_PYTHON)")
        except Exception as ex:
            layout_doc = None
            db.log_parse(paper_id, None, "layout:docling", "warning", "layout_failed",
                         repr(ex)[:500])

    fig_blocks: dict = {}
    if layout_doc and layout_doc.get("pdf_sha256") == paper["sha256"]:
        for pg in layout_doc.get("pages") or []:
            fig_blocks[pg["page_no"]] = [b for b in pg.get("blocks") or []
                                         if b.get("type") == "figure"]

    out_dir = config.CURVE_DIR / paper_id
    out_dir.mkdir(parents=True, exist_ok=True)
    created: list = []

    def upsert(curve_id, page_no, ref, region, source, notes_text):
        existing = db.get_curve(curve_id)
        prot = (existing or {}).get("region_source") or ""
        if existing and prot == "manual":
            created.append(curve_id)        # human decisions are never overwritten
            return
        img_path = out_dir / (curve_id + ".png")
        if (not img_path.exists()) or (existing or {}).get("region_bbox") != region:
            _crop_region(paper, page_no, region, img_path, dpi)
        rec = {
            "curve_id": curve_id, "paper_id": paper_id, "page": page_no,
            "figure_ref": ref, "image_path": str(img_path),
            "image_sha256": sha256_bytes(img_path.read_bytes()),
            "region_bbox": region, "region_source": source,
            "method": "manual", "series_name": ref,
            "verification_status": (existing or {}).get("verification_status", "unverified"),
            "notes": notes_text[:500],
            "created_at": (existing or {}).get("created_at") or utc_now(),
        }
        if existing:
            db.update_curve(curve_id, page=page_no, figure_ref=ref,
                            image_path=rec["image_path"], image_sha256=rec["image_sha256"],
                            region_bbox=region, region_source=source, notes=rec["notes"])
        else:
            db.insert_curve(rec)
        created.append(curve_id)

    def clamp(region, W, H):
        return [round(max(0.0, region[0]), 2), round(max(0.0, region[1]), 2),
                round(min(W, region[2]), 2), round(min(H, region[3]), 2)]

    for page in parse_doc.get("pages") or []:
        pno = page["page_no"]
        W, H = page["width_pts"], page["height_pts"]
        blocks = fig_blocks.get(pno, [])
        used: set = set()
        for cap in page.get("captions") or []:
            if cap["kind"] != "figure":
                continue
            bi = _best_block_for_caption(cap, blocks, used) if blocks else None
            if bi is not None:
                used.add(bi)
                region = clamp(blocks[bi]["bbox"], W, H)
                source = "layout:docling"
                note = None
            else:
                region, note = _region_for_caption(page, cap)
                source = "heuristic_fallback" if note else "embedded_images"
            if region is None or region[2] - region[0] < 20 or region[3] - region[1] < 20:
                db.log_parse(paper_id, pno, "curves", "warning", "figure_region_too_small",
                             "cannot derive region for %s" % cap.get("ref"))
                continue
            cid = "C-" + sha256_bytes(
                ("%s|%d|%s" % (paper_id, pno, cap["ref"])).encode("utf-8"))[:12]
            upsert(cid, pno, cap["ref"], region, source,
                   "; ".join(x for x in [note, cap["text"][:200]] if x))
        for i, b in enumerate(blocks):
            if i in used:
                continue
            bb = b.get("bbox") or [0, 0, 0, 0]
            if (bb[2] - bb[0]) * (bb[3] - bb[1]) < 5000:
                continue                    # skip tiny picture blocks (logos, icons)
            ref = "Unnamed p%d#%d" % (pno, i)
            region = clamp(bb, W, H)
            if region[2] - region[0] < 20 or region[3] - region[1] < 20:
                continue
            cid = "C-" + sha256_bytes(
                ("%s|%d|%s" % (paper_id, pno, ref)).encode("utf-8"))[:12]
            extra = (" label=%s" % b["label"]) if b.get("label") else ""
            upsert(cid, pno, ref, region, "layout:docling",
                   "layout block without matched caption;" + extra)
    n_pruned = _prune_stale(db, paper_id, set(created))
    if n_pruned:
        db.log_parse(paper_id, None, "curves", "info", "pruned_stale_regions",
                     "removed %d untouched legacy figure regions" % n_pruned)
    return created


def update_region(db, curve_id: str, bbox_pts, reviewer: str = "anonymous") -> dict:
    """Manually adjust a figure region (UI drag-select).

    Consequences (all recorded, nothing silent):
    - region image is re-cropped from the page render (derived file only);
    - stale overlay/redraw outputs are deleted;
    - calibration + digitized points are CLEARED (pixel coordinate system changed);
    - verification_status drops to unverified; region_source becomes 'manual';
    - a 'modify' review entry stores old -> new for audit;
    - manual regions are protected from being overwritten by extract_figures().
    """
    curve = db.get_curve(curve_id)
    if not curve:
        raise KeyError(curve_id)
    paper = db.get_paper(curve["paper_id"])
    if not paper:
        raise KeyError(curve["paper_id"])
    parse_doc = load_parse(curve["paper_id"]) or {}
    W = H = None
    for pg in parse_doc.get("pages") or []:
        if pg["page_no"] == curve.get("page"):
            W, H = pg["width_pts"], pg["height_pts"]
            break
    if not W or not H:
        W, H = 612.0, 792.0
    x0 = max(0.0, min(float(bbox_pts[0]), W - 10))
    y0 = max(0.0, min(float(bbox_pts[1]), H - 10))
    x1 = min(float(bbox_pts[2]), W)
    y1 = min(float(bbox_pts[3]), H)
    if x1 - x0 < 20 or y1 - y0 < 20:
        raise ValueError("region too small (minimum 20 x 20 pt)")
    bbox = [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]

    from PIL import Image
    page_img = render_page_image(paper, curve.get("page") or 1, dpi=FIGURE_DPI)
    im = Image.open(page_img)
    s = FIGURE_DPI / 72.0
    crop = im.crop((int(bbox[0] * s), int(bbox[1] * s), int(bbox[2] * s), int(bbox[3] * s)))
    img_path = Path(curve["image_path"])
    img_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = img_path.with_suffix(".tmp.png")
    crop.save(tmp, format="PNG")
    os.replace(tmp, img_path)
    for suffix in ("_overlay.png", "_redraw.png"):
        stale = img_path.with_name(img_path.stem + suffix)
        if stale.exists():
            stale.unlink()

    old = {"region_bbox": curve.get("region_bbox"),
           "had_calibration": bool(curve.get("calibration")),
           "n_points": len(db.list_curve_points(curve_id)),
           "verification_status": curve.get("verification_status")}
    db.replace_curve_points(curve_id, [])
    db.update_curve(curve_id,
                    region_bbox=bbox,
                    image_sha256=sha256_bytes(img_path.read_bytes()),
                    calibration=None,
                    verification_status="unverified",
                    region_source="manual",
                    notes=(((curve.get("notes") or "") + "; manual_region@" + utc_now()))[:500])
    db.add_review(curve["paper_id"], "curve", curve_id, "modify", old,
                  {"region_bbox": bbox},
                  "region manually adjusted (calibration/points reset)", reviewer)
    return db.get_curve(curve_id)


def set_calibration(db, curve_id: str, spec: dict) -> dict:
    curve = db.get_curve(curve_id)
    if not curve:
        raise KeyError(curve_id)
    from PIL import Image
    size = Image.open(curve["image_path"]).size
    cal = build_calibration(spec, image_size=size)
    db.update_curve(curve_id, calibration=cal)
    # re-derive data values for points that have pixel coordinates
    pts = db.list_curve_points(curve_id)
    if pts:
        new_pts = []
        for p in pts:
            if p["pixel_x"] is not None:
                recs = points_from_pixels(cal, [(p["pixel_x"], p["pixel_y"])],
                                          x_unit=cal["axes"]["x"].get("unit"),
                                          y_unit=cal["axes"]["y"].get("unit"),
                                          source=p["source"])
                new_pts.append(recs[0])
            else:
                new_pts.append(p)
        db.replace_curve_points(curve_id, new_pts)
    return db.get_curve(curve_id)


def add_manual_points(db, curve_id: str, pts_px: list) -> dict:
    """pts_px: [[x,y],...] in image pixels. Requires calibration."""
    curve = db.get_curve(curve_id)
    if not curve or not curve.get("calibration"):
        raise ValueError("calibrate the curve before digitizing points")
    cal = curve["calibration"]
    recs = points_from_pixels(cal, pts_px, x_unit=cal["axes"]["x"].get("unit"),
                              y_unit=cal["axes"]["y"].get("unit"), source="manual_click")
    old = [dict(p) for p in db.list_curve_points(curve_id)]
    for p in old:
        p.pop("point_id", None)
    db.replace_curve_points(curve_id, old + recs)
    db.update_curve(curve_id, method="manual")
    overlay = overlay_curve(db, curve_id)
    redraw_curve(db, curve_id)
    return overlay


def import_wpd_csv(db, curve_id: str, csv_text: str, series: str = None) -> dict:
    parsed = parse_wpd_csv(csv_text)
    if not parsed:
        raise ValueError("no data found in WebPlotDigitizer CSV")
    name = series or next(iter(parsed))
    if name not in parsed:
        raise ValueError("series %r not in CSV (have: %s)" % (name, sorted(parsed)))
    curve = db.get_curve(curve_id)
    cal = curve.get("calibration") if curve else None
    x_unit = cal["axes"]["x"].get("unit") if cal else None
    y_unit = cal["axes"]["y"].get("unit") if cal else None
    recs = wpd_points_to_records(parsed[name], x_unit=x_unit, y_unit=y_unit)
    db.replace_curve_points(curve_id, recs)
    db.update_curve(curve_id, method="wpd_import", series_name=name)
    overlay = overlay_curve(db, curve_id)
    redraw_curve(db, curve_id)
    return {"series": name, "n_points": len(recs), **overlay}


def import_auto_points(db, curve_id: str, pts: list, source: str = "auto_trace") -> dict:
    """Import points from an automatic digitizer or an LLM. Source policy:
    these are NEVER treated as verified data until a human reviews them."""
    if source not in ("auto_trace", "llm_guess"):
        raise ValueError("import_auto_points is only for auto/LLM sources")
    recs = [{"x_raw": x, "y_raw": y, "x_std": None, "y_std": None,
             "pixel_x": None, "pixel_y": None, "source": source} for x, y in pts]
    db.replace_curve_points(curve_id, recs)
    db.update_curve(curve_id, method=source, verification_status="unverified")
    enforce_source_policy(db, curve_id, {source})
    redraw_curve(db, curve_id)
    return {"n_points": len(recs), "verification_status": "unverified"}
