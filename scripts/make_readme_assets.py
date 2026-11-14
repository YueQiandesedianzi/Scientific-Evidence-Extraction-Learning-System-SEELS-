"""Generate README assets: curve-workflow triptych + real UI screenshots.

- Seeds a DEMO curve (synthetic plot with exact ground truth, clearly labeled)
  into the working DB so the curve panel shows region/overlay/redraw.
- Composes docs/assets/curve-workflow.png from the real module outputs.
- Boots the review UI in-process and drives it with headless Chromium,
  capturing console errors (UI health check) and 4 screenshots.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw

from seels import config
from seels.curves import figures as cfig
from seels.db import open_db
from seels.parsing.pipeline import render_page_image
from seels.util import sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
ASSETS.mkdir(parents=True, exist_ok=True)

RICH_PAPER = "P-455fe33f2b5fab9a"     # SiC-BN paper (180 measurements, evidence on p28)
DEMO_CURVE = "C-demo00000001"
PORT = 8899


def seed_demo_curve(db):
    """Synthetic plot with renderer-exact ground truth -> calibrated curve."""
    outdir = config.CURVE_DIR / RICH_PAPER
    outdir.mkdir(parents=True, exist_ok=True)
    img_path = outdir / (DEMO_CURVE + ".png")

    fig, ax = plt.subplots(figsize=(4.6, 3.2), dpi=150)
    xs = [i * 0.25 for i in range(41)]
    ys = [2 * x + 1 for x in xs]
    ax.plot(xs, ys, "b-", lw=1.4)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 25)
    ax.set_xlabel("x (nm)")
    ax.set_ylabel("T (K)")
    ax.set_title("Demo curve (synthetic)", fontsize=9)
    fig.savefig(img_path, bbox_inches="tight")
    fig.canvas.draw()
    tr = ax.transData
    p00 = tr.transform((0, 0))
    p1021 = tr.transform((10, 21))
    demo_pts = [tr.transform((x, 2 * x + 1)) for x in (1, 3, 5, 7, 9)]
    plt.close(fig)
    H = Image.open(img_path).height
    flip = lambda p: [float(p[0]), float(H - p[1])]
    c00, c1021 = flip(p00), flip(p1021)
    cpts = [flip(p) for p in demo_pts]

    if not db.get_curve(DEMO_CURVE):
        db.insert_curve({
            "curve_id": DEMO_CURVE, "paper_id": RICH_PAPER, "page": 28,
            "figure_ref": "Demo (synthetic)", "image_path": str(img_path),
            "image_sha256": sha256_bytes(img_path.read_bytes()),
            "region_bbox": None, "method": "manual", "series_name": "demo",
            "verification_status": "unverified",
            "notes": "README demo: synthetic curve with exact ground truth"})
    spec = {"axes": {
        "x": {"scale": "linear", "unit": "nm",
              "points": [{"px": c00[0], "py": c00[1], "value": 0},
                         {"px": c1021[0], "py": c1021[1], "value": 10}]},
        "y": {"scale": "linear", "unit": "K",
              "points": [{"px": c00[0], "py": c00[1], "value": 0},
                         {"px": c1021[0], "py": c1021[1], "value": 21}]}}}
    cfig.set_calibration(db, DEMO_CURVE, spec)
    db.replace_curve_points(DEMO_CURVE, [])
    cfig.add_manual_points(db, DEMO_CURVE, cpts)
    return img_path


def make_triptych(img_path: Path):
    base = img_path.parent / DEMO_CURVE
    region = Image.open(img_path).convert("RGB")
    overlay = Image.open(base.with_name(base.name + "_overlay.png")).convert("RGB")
    redraw = Image.open(base.with_name(base.name + "_redraw.png")).convert("RGB")
    h = 340
    def fit(im):
        r = h / im.height
        return im.resize((int(im.width * r), h))
    panels = [fit(region), fit(overlay), fit(redraw)]
    titles = ["1. 原文图区 (region)", "2. 叠加验证 (overlay)", "3. 曲线重绘 (redraw)"]
    gap, pad = 14, 34
    W = sum(p.width for p in panels) + gap * 2 + pad * 2
    canvas = Image.new("RGB", (W, h + pad + 26), "#ffffff")
    d = ImageDraw.Draw(canvas)
    x = pad
    for p, t in zip(panels, titles):
        canvas.paste(p, (x, pad))
        d.rectangle([x, pad, x + p.width - 1, pad + p.height - 1], outline="#c9d2de")
        d.text((x + 8, 8), t, fill="#1f2937")
        x += p.width + gap
    out = ASSETS / "curve-workflow.png"
    canvas.save(out)
    print("saved:", out, canvas.size)


def warm_and_screenshot():
    db = open_db()
    # warm page-image cache for the page we will show
    paper = db.get_paper(RICH_PAPER)
    render_page_image(paper, 28, dpi=140)
    db.close()

    from http.server import ThreadingHTTPServer
    from seels.ui import server as srv
    srv._DB = open_db()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.8)

    from playwright.sync_api import sync_playwright
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        pg = browser.new_page(viewport={"width": 1680, "height": 1020},
                              device_scale_factor=1.25)
        pg.on("console", lambda m: errors.append("console:" + m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))
        base = "http://127.0.0.1:%d" % PORT
        pg.goto(base + "/", wait_until="networkidle")
        pg.wait_for_function("document.querySelectorAll('#paperSel option').length > 0",
                             timeout=30000)
        pg.select_option("#paperSel", RICH_PAPER)
        pg.wait_for_function(
            "state.paperInfo && state.paperInfo.paper && state.paperInfo.paper.paper_id === '" + RICH_PAPER + "'",
            timeout=30000)
        pg.wait_for_selector("#mTable tbody tr", state="attached", timeout=30000)
        pg.fill("#pgNum", "28")
        pg.dispatch_event("#pgNum", "change")
        try:
            pg.wait_for_selector("#evOverlay rect", state="attached", timeout=20000)
        except Exception as e:
            errors.append("overlay-wait: " + str(e)[:120])
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(ASSETS / "ui-main.png"))
        print("shot ui-main")
        pg.click("#mTable tbody tr")
        pg.wait_for_timeout(1800)
        pg.screenshot(path=str(ASSETS / "ui-detail.png"))
        print("shot ui-detail")
        pg.click("button[data-tab='card']")
        pg.wait_for_timeout(700)
        pg.screenshot(path=str(ASSETS / "ui-card.png"))
        print("shot ui-card")
        pg.select_option("#curveSel", DEMO_CURVE)
        pg.wait_for_timeout(1500)
        pg.eval_on_selector(".curve-section", "el => el.scrollIntoView({block:'start'})")
        pg.wait_for_timeout(800)
        pg.locator(".curve-section").screenshot(path=str(ASSETS / "ui-curves.png"))
        print("shot ui-curves")
        browser.close()
    httpd.shutdown()
    httpd.server_close()
    print("CONSOLE_ERRORS(%d):" % len(errors))
    for e in errors[:15]:
        print("  ", e[:220])


if __name__ == "__main__":
    db = open_db()
    p = seed_demo_curve(db)
    db.close()
    make_triptych(p)
    warm_and_screenshot()
    print("ASSETS_DONE")
