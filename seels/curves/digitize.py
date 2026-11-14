"""Digitizing helpers: pixel->data points, WebPlotDigitizer CSV import,
unit standardization of axes, and storage policy enforcement."""
from __future__ import annotations

import csv
import io
import re

from ..extraction.units import standardize
from .calibrate import pixel_to_data

AUTO_SOURCES = ("llm_guess", "auto_trace")


def points_from_pixels(cal: dict, pts_px, x_unit: str = None, y_unit: str = None,
                       source: str = "manual_click") -> list:
    out = []
    xs = standardize(1.0, x_unit) if x_unit else None
    ys = standardize(1.0, y_unit) if y_unit else None
    for px, py in pts_px:
        xv, yv = pixel_to_data(cal, px, py)
        x_std = xv * xs["value_std"] if (xs and xs["value_std"] is not None) else None
        y_std = yv * ys["value_std"] if (ys and ys["value_std"] is not None) else None
        out.append({"x_raw": xv, "y_raw": yv, "x_std": x_std, "y_std": y_std,
                    "pixel_x": float(px), "pixel_y": float(py), "source": source})
    return out


_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eEdD][-+]?\d+)?$")


def _to_float(s: str):
    s = (s or "").strip()
    if not _NUM.match(s):
        return None
    return float(s.replace("d", "e").replace("D", "e"))


def parse_wpd_csv(text: str) -> dict:
    """Parse a WebPlotDigitizer CSV export.

    Handles the common layouts:
      - single series:  header X,Y (or no header)
      - multi series:   first column X, one column per dataset; blank cells
                        where a dataset has no point on that row.
    Returns {series_name: [(x, y), ...]} preserving row order.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    delim = ","
    first_line = next((l for l in text.split("\n") if l.strip()), "")
    for d in (",", ";", "\t"):
        if d in first_line:
            delim = d
            break
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        return {}
    header = None
    if any(_to_float(c) is None for c in rows[0][:2]):
        header = rows[0]
        rows = rows[1:]
    ncol = max(len(r) for r in rows)
    names = []
    for j in range(1, ncol):
        nm = ("Series%d" % j)
        if header and j < len(header) and header[j].strip():
            nm = header[j].strip()
        names.append(nm)
    series = {nm: [] for nm in names}
    last_x = None
    for r in rows:
        cells = list(r) + [""] * (ncol - len(r))
        xv = _to_float(cells[0])
        if xv is not None:
            last_x = xv
        for j, nm in enumerate(names, start=1):
            yv = _to_float(cells[j])
            if yv is not None and last_x is not None:
                series[nm].append((last_x, yv))
    return {k: v for k, v in series.items() if v}


def wpd_points_to_records(xy: list, source: str = "wpd_import",
                          x_unit: str = None, y_unit: str = None) -> list:
    xs = standardize(1.0, x_unit) if x_unit else None
    ys = standardize(1.0, y_unit) if y_unit else None
    out = []
    for xv, yv in xy:
        out.append({"x_raw": xv, "y_raw": yv,
                    "x_std": xv * xs["value_std"] if (xs and xs["value_std"] is not None) else None,
                    "y_std": yv * ys["value_std"] if (ys and ys["value_std"] is not None) else None,
                    "pixel_x": None, "pixel_y": None, "source": source})
    return out


def enforce_source_policy(db, curve_id: str, sources: set) -> None:
    """Auto/LLM-derived points never auto-verify: force unverified + high-risk flag."""
    curve = db.get_curve(curve_id)
    if not curve:
        return
    if sources & set(AUTO_SOURCES) and curve["verification_status"] != "human_verified":
        db.update_curve(curve_id, verification_status="unverified")
        db.insert_qc_flag({
            "paper_id": curve["paper_id"], "target_type": "curve", "target_id": curve_id,
            "check_code": "CURVE_UNVERIFIED_AUTO", "severity": "high_risk",
            "message": "curve contains %s points: not verified data until human review"
                       % ",".join(sorted(sources & set(AUTO_SOURCES)))})
