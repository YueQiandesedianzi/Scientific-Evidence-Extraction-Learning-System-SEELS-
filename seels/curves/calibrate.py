"""Axis calibration: pixel <-> data-value mapping.

Calibration spec (stored as curves.calibration_json):
{
  "axes": {
    "x": {"scale": "linear"|"log10", "unit": str|None,
          "coef": [a, b],          # (log10-)value = a*px + b
          "points": [{"px": .., "py": .., "value": ..}, ...]},   # >=2, user-given
    "y": {...}
  },
  "image_size": [W, H]   # pixels of the figure-region image
}
Least-squares over the user points; log axes require positive values.
"""
from __future__ import annotations

import math


class CalibrationError(Exception):
    pass


def _fit_axis(points: list, scale: str, coord: str = "px") -> list:
    if len(points) < 2:
        raise CalibrationError("an axis needs >= 2 calibration points")
    xs = [float(p[coord]) for p in points]
    ys = []
    for p in points:
        v = float(p["value"])
        if scale == "log10":
            if v <= 0:
                raise CalibrationError("log axis requires positive calibration values")
            ys.append(math.log10(v))
        elif scale == "linear":
            ys.append(v)
        else:
            raise CalibrationError("unknown scale %r (linear|log10)" % scale)
    if max(xs) - min(xs) < 1.0:
        raise CalibrationError("calibration pixel positions too close together")
    if max(ys) - min(ys) < 1e-12:
        raise CalibrationError("calibration values are identical")
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    a = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    b = my - a * mx
    return [a, b]


def build_calibration(spec: dict, image_size=None) -> dict:
    axes_in = spec.get("axes") or {}
    if "x" not in axes_in or "y" not in axes_in:
        raise CalibrationError("calibration needs both x and y axes")
    axes = {}
    for ax in ("x", "y"):
        a = axes_in[ax]
        scale = a.get("scale", "linear")
        pts = a.get("points") or []
        coef = _fit_axis(pts, scale, coord=("px" if ax == "x" else "py"))
        axes[ax] = {"scale": scale, "unit": a.get("unit"), "coef": coef,
                    "points": [{"px": float(p["px"]), "py": float(p["py"]),
                                "value": float(p["value"])} for p in pts]}
    cal = {"axes": axes}
    if image_size:
        cal["image_size"] = list(image_size)
    return cal


def pixel_to_data(cal: dict, x_px: float, y_px: float):
    ax, ay = cal["axes"]["x"], cal["axes"]["y"]
    xv = ax["coef"][0] * float(x_px) + ax["coef"][1]
    yv = ay["coef"][0] * float(y_px) + ay["coef"][1]
    if ax["scale"] == "log10":
        xv = 10 ** xv
    if ay["scale"] == "log10":
        yv = 10 ** yv
    return xv, yv


def data_to_pixel(cal: dict, x_val: float, y_val: float):
    ax, ay = cal["axes"]["x"], cal["axes"]["y"]
    xv = math.log10(x_val) if ax["scale"] == "log10" else x_val
    yv = math.log10(y_val) if ay["scale"] == "log10" else y_val
    if ax["scale"] == "log10" and x_val <= 0:
        raise CalibrationError("log10 x requires positive value")
    if ay["scale"] == "log10" and y_val <= 0:
        raise CalibrationError("log10 y requires positive value")
    return ((xv - ax["coef"][1]) / ax["coef"][0],
            (yv - ay["coef"][1]) / ay["coef"][0])
