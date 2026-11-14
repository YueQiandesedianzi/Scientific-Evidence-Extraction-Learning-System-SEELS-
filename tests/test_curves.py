"""Curve calibration ground truth, WPD import, redraw/overlay, source policy."""
import numpy as np
import pytest
from PIL import Image


@pytest.fixture()
def synth_plot(tmp_path):
    """Plot with renderer-exact pixel ground truth."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(4, 3), dpi=150)
    xs = np.linspace(0, 10, 40)
    ax.plot(xs, 2 * xs + 1, "b-")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 25)
    png = tmp_path / "fig.png"
    fig.savefig(png)
    fig.canvas.draw()
    tr = ax.transData
    pts = {k: tr.transform(v) for k, v in
           {"o00": (0, 0), "o1021": (10, 21), "o511": (5, 11)}.items()}
    plt.close(fig)
    H = Image.open(png).height
    flip = lambda p: (float(p[0]), float(H - p[1]))
    return str(png), {k: flip(v) for k, v in pts.items()}


def _mk_curve(db, img, cid="C-test0001"):
    from seels.util import sha256_bytes
    db.upsert_paper({"paper_id": "P-c", "source_path": img, "sha256": "11" * 32})
    db.insert_curve({"curve_id": cid, "paper_id": "P-c", "page": 1,
                     "figure_ref": "Figure 1", "image_path": img,
                     "image_sha256": sha256_bytes(open(img, "rb").read()),
                     "region_bbox": [0, 0, 10, 10], "method": "manual",
                     "series_name": "test"})
    return cid


def test_calibration_recovers_ground_truth(db, synth_plot):
    img, pts = synth_plot
    from seels.curves.calibrate import build_calibration, pixel_to_data
    cal = build_calibration({"axes": {
        "x": {"scale": "linear", "unit": "nm",
              "points": [{"px": pts["o00"][0], "py": pts["o00"][1], "value": 0},
                         {"px": pts["o1021"][0], "py": pts["o1021"][1], "value": 10}]},
        "y": {"scale": "linear", "unit": "K",
              "points": [{"px": pts["o00"][0], "py": pts["o00"][1], "value": 0},
                         {"px": pts["o1021"][0], "py": pts["o1021"][1], "value": 21}]}}})
    xv, yv = pixel_to_data(cal, *pts["o511"])
    assert abs(xv - 5) < 0.05 and abs(yv - 11) < 0.15


def test_manual_points_units_and_overlay(db, synth_plot):
    from seels.curves import figures as F
    img, pts = synth_plot
    cid = _mk_curve(db, img)
    F.set_calibration(db, cid, {"axes": {
        "x": {"scale": "linear", "unit": "nm",
              "points": [{"px": pts["o00"][0], "py": pts["o00"][1], "value": 0},
                         {"px": pts["o1021"][0], "py": pts["o1021"][1], "value": 10}]},
        "y": {"scale": "linear", "unit": "K",
              "points": [{"px": pts["o00"][0], "py": pts["o00"][1], "value": 0},
                         {"px": pts["o1021"][0], "py": pts["o1021"][1], "value": 21}]}}})
    ov = F.add_manual_points(db, cid, [list(pts["o511"]), list(pts["o1021"])])
    assert ov["verification_status"] == "redraw_ok"
    assert ov["median_residual_px"] <= 1.0
    p = [x for x in db.list_curve_points(cid) if x["x_std"]][0]
    assert abs(p["x_std"] - 5e-9) < 1e-11      # 5 nm -> m
    assert abs(p["y_std"] - 11) < 0.15         # K stays K


def test_wpd_csv_import(db, synth_plot):
    from seels.curves import figures as F
    from seels.curves.digitize import parse_wpd_csv
    parsed = parse_wpd_csv("X,conductivity,seebeck\n0,100,5\n1,,6\n2,300,7\n")
    assert parsed["conductivity"] == [(0.0, 100.0), (2.0, 300.0)]
    assert parsed["seebeck"] == [(0.0, 5.0), (1.0, 6.0), (2.0, 7.0)]
    img, _ = synth_plot
    cid = _mk_curve(db, img)
    r = F.import_wpd_csv(db, cid, "X,Y\n0,1\n1,2\n2,4\n", series="Y")
    assert r["n_points"] == 3
    pts = db.list_curve_points(cid)
    assert [p["source"] for p in pts] == ["wpd_import"] * 3
    assert db.get_curve(cid)["method"] == "wpd_import"


def test_llm_and_auto_points_never_verified(db, synth_plot):
    from seels.curves import figures as F
    img, _ = synth_plot
    cid = _mk_curve(db, img)
    r = F.import_auto_points(db, cid, [(0, 1), (5, 11)], source="llm_guess")
    assert r["verification_status"] == "unverified"
    cv = db.get_curve(cid)
    assert cv["verification_status"] == "unverified" and cv["method"] == "llm_guess"
    flags = db.list_qc_flags(target_type="curve", target_id=cid)
    assert any(f["check_code"] == "CURVE_UNVERIFIED_AUTO" and f["severity"] == "high_risk"
               for f in flags)
    # a bad calibration source is also rejected
    with pytest.raises(Exception):
        F.import_auto_points(db, cid, [(0, 1)], source="manual")


def test_manual_region_update(db, synth_pdf):
    from pathlib import Path
    from seels.parsing.pipeline import ingest
    from seels.curves.figures import (add_manual_points, extract_figures,
                                      set_calibration, update_region)
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    cid = extract_figures(db, pid)[0]
    cv = db.get_curve(cid)
    set_calibration(db, cid, {"axes": {
        "x": {"scale": "linear",
              "points": [{"px": 0, "py": 0, "value": 0}, {"px": 100, "py": 0, "value": 1}]},
        "y": {"scale": "linear",
              "points": [{"px": 0, "py": 100, "value": 0}, {"px": 0, "py": 0, "value": 1}]}}})
    add_manual_points(db, cid, [[50, 50]])
    assert len(db.list_curve_points(cid)) >= 1
    b = cv["region_bbox"]
    cv2 = update_region(db, cid, [b[0] + 5, b[1] + 5, b[2] - 5, b[3] - 5], reviewer="tester")
    assert cv2["region_source"] == "manual"
    assert cv2["calibration"] is None
    assert db.list_curve_points(cid) == []
    assert cv2["verification_status"] == "unverified"
    assert Path(cv2["image_path"]).exists()
    revs = db.list_reviews(target_type="curve", target_id=cid)
    assert revs and revs[0]["action"] == "modify" and revs[0]["reviewer"] == "tester"
    assert revs[0]["old_value"]["had_calibration"] is True
    # manual region survives a figures re-run
    extract_figures(db, pid)
    cv3 = db.get_curve(cid)
    assert cv3["region_source"] == "manual" and cv3["region_bbox"] == cv2["region_bbox"]
    # tiny regions rejected
    with pytest.raises(ValueError):
        update_region(db, cid, [10, 10, 15, 15])


def test_figure_region_extraction(db, synth_pdf):
    from seels.parsing.pipeline import ingest
    from seels.curves.figures import extract_figures
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    # hermetic: skip the optional docling engine (tested via fake provider in
    # tests/test_layout.py; the real engine may be present on dev machines)
    ids = extract_figures(db, pid, use_layout=False)
    assert ids, "synthetic paper has Figure 1 - region must be found"
    cv = db.get_curve(ids[0])
    assert cv["figure_ref"] == "Figure 1" and cv["page"] == 2
    assert cv["region_source"] in ("embedded_images", "heuristic_fallback")
    from pathlib import Path
    img = Path(cv["image_path"])
    assert img.exists() and img.stat().st_size > 1000
    assert cv["verification_status"] == "unverified"   # nothing verified automatically
    # idempotent re-run
    ids2 = extract_figures(db, pid, use_layout=False)
    assert ids2 == ids
