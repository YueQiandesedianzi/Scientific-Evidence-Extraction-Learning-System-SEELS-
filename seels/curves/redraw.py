"""Curve redraw + original-image overlay verification.

redraw_curve : clean XY replot of stored points (data space).
overlay_curve: reproject stored data points back to image pixels (via the
calibration) and scatter them over the original figure image; computes the
median reprojection residual in pixels. Only when a calibration exists,
points are consistent (median residual <= config.OVERLAY_MAX_MEDIAN_PX) and
the source policy allows it, verification_status becomes "redraw_ok".
LLM/auto sources can never reach a verified state without human review.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

from .. import config
from .calibrate import CalibrationError, data_to_pixel
from .digitize import AUTO_SOURCES


def _out_paths(curve: dict):
    base = Path(curve["image_path"]).parent / (Path(curve["curve_id"]).name)
    return base.with_name(base.name + "_redraw.png"), base.with_name(base.name + "_overlay.png")


def redraw_curve(db, curve_id: str) -> Path:
    curve = db.get_curve(curve_id)
    pts = db.list_curve_points(curve_id)
    if not pts:
        raise ValueError("curve %s has no points to redraw" % curve_id)
    xy = sorted(((p["x_raw"], p["y_raw"]) for p in pts
                 if p["x_raw"] is not None and p["y_raw"] is not None))
    redraw_p, _ = _out_paths(curve)
    fig, ax = plt.subplots(figsize=(5.2, 3.8), dpi=130)
    ax.plot([a for a, _ in xy], [b for _, b in xy], "-", color="#1f77b4", lw=1.2,
            label=curve.get("series_name") or "series")
    ax.scatter([a for a, _ in xy], [b for _, b in xy], s=8, color="#1f77b4")
    cal = curve.get("calibration") or {}
    ax.set_xlabel("x" + (" [%s]" % cal["axes"]["x"]["unit"] if cal.get("axes", {}).get("x", {}).get("unit") else ""))
    ax.set_ylabel("y" + (" [%s]" % cal["axes"]["y"]["unit"] if cal.get("axes", {}).get("y", {}).get("unit") else ""))
    if cal.get("axes", {}).get("x", {}).get("scale") == "log10":
        ax.set_xscale("log")
    if cal.get("axes", {}).get("y", {}).get("scale") == "log10":
        ax.set_yscale("log")
    ax.set_title("%s (digitized, %s)" % (curve.get("figure_ref") or curve_id,
                                         curve.get("verification_status")), fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    redraw_p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(redraw_p)
    plt.close(fig)
    return redraw_p


def overlay_curve(db, curve_id: str) -> dict:
    curve = db.get_curve(curve_id)
    pts = db.list_curve_points(curve_id)
    img_path = Path(curve["image_path"])
    if not img_path.exists():
        raise FileNotFoundError(curve["image_path"])
    img = Image.open(img_path).convert("RGB")
    _, overlay_p = _out_paths(curve)
    cal = curve.get("calibration")

    px_pts = []          # points drawn on overlay (image pixel coords)
    residuals = []
    for p in pts:
        px = py = None
        if cal and p["x_raw"] is not None and p["y_raw"] is not None:
            try:
                px, py = data_to_pixel(cal, p["x_raw"], p["y_raw"])
            except (CalibrationError, ZeroDivisionError):
                px = py = None
        if px is None and p["pixel_x"] is not None:
            px, py = p["pixel_x"], p["pixel_y"]
        if px is None:
            continue
        px_pts.append((px, py))
        if cal and p["pixel_x"] is not None and p["x_raw"] is not None:
            residuals.append(abs(px - p["pixel_x"]) + abs(py - p["pixel_y"]))

    fig = plt.figure(figsize=(img.width / 100.0, img.height / 100.0), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.imshow(img)
    if px_pts:
        ax.scatter([a for a, _ in px_pts], [b for _, b in px_pts], s=7,
                   color="#ff2d2d", alpha=0.75, linewidths=0)
    ax.set_axis_off()
    overlay_p.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(overlay_p)
    plt.close(fig)

    median_res = None
    if residuals:
        s = sorted(residuals)
        median_res = s[len(s) // 2]
    sources = {p["source"] for p in pts if p.get("source")}
    auto = bool(sources & set(AUTO_SOURCES))
    prev_status = curve["verification_status"]
    if prev_status == "human_verified":
        status = prev_status           # human decision always wins
    elif cal and len(pts) >= 2 and not auto and \
            (median_res is None or median_res <= config.OVERLAY_MAX_MEDIAN_PX):
        status = "redraw_ok"
    else:
        status = "unverified"
    if status != prev_status:
        db.update_curve(curve_id, verification_status=status)
    if median_res is not None and median_res > config.OVERLAY_MAX_MEDIAN_PX:
        db.insert_qc_flag({
            "paper_id": curve["paper_id"], "target_type": "curve", "target_id": curve_id,
            "check_code": "CURVE_REDRAW_RESIDUAL", "severity": "warning",
            "message": "median reprojection residual %.2f px > %.1f px"
                       % (median_res, config.OVERLAY_MAX_MEDIAN_PX)})
    return {"overlay_path": str(overlay_p), "median_residual_px": median_res,
            "n_points_drawn": len(px_pts), "verification_status": status}
