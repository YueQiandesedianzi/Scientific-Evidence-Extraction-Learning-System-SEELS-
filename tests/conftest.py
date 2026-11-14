"""Shared fixtures: a deterministic synthetic paper with exact ground truth,
a simulated scanned PDF, and an in-memory DB."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _build_synth(path: Path) -> None:
    from fpdf import FPDF
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(3.6, 2.2), dpi=150)
    t = list(range(50, 301, 10))
    rho = [0.0083 + 0.00004 * (x - 50) for x in t]
    ax.plot(t, rho, "b-")
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("rho (Ohm cm)")
    figpng = path.with_name("fig1.png")
    fig.savefig(figpng, bbox_inches="tight")
    plt.close(fig)

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("helvetica", "B", 15)
    pdf.text(20, 20, "Synthetic Test Paper for SEELS")
    pdf.set_font("helvetica", "", 10.5)
    pdf.text(20, 34, "We studied two manganite samples prepared by solid-state reaction.")
    pdf.text(20, 42, "Sample A (La0.7Sr0.3MnO3) shows Tc = 93 K in the SQUID measurement.")
    pdf.text(20, 50, "The critical temperature of Sample B is 85 K.")
    pdf.text(20, 58, "The power conversion efficiency of 12-15 % was observed in devices.")

    pdf.add_page()
    pdf.set_font("helvetica", "", 10)
    headers = ["Sample", "Tc (K)", "conductivity (S/cm)", "resistivity (Ohm*cm)"]
    rows = [["Sample A", "93", "120", "0.0083"],
            ["Sample B", "85", "95", "0.0105"]]
    col_w = [34, 24, 44, 44]
    x0, y0, rh = 20, 30, 8
    for ri, row in enumerate([headers] + rows):
        for ci, cell in enumerate(row):
            pdf.set_xy(x0 + sum(col_w[:ci]), y0 + ri * rh)
            pdf.cell(col_w[ci], rh, cell, border=1)
    pdf.text(x0, y0 + 3 * rh + 5, "Table 1. Transport properties measured at 300 K.")
    pdf.image(str(figpng), x=20, y=75, w=90)
    pdf.text(20, 138, "Figure 1. Resistivity vs temperature for Sample A.")
    pdf.output(str(path))


@pytest.fixture(scope="session")
def synth_pdf(tmp_path_factory) -> str:
    d = tmp_path_factory.mktemp("synth")
    path = d / "synth_paper.pdf"
    _build_synth(path)
    return str(path)


@pytest.fixture(scope="session")
def scan_pdf(tmp_path_factory, synth_pdf) -> str:
    """Image-only PDF (simulated scan): render page 1, rebuild as image PDF."""
    import pypdfium2 as pdfium
    d = tmp_path_factory.mktemp("scan")
    out = d / "scanned.pdf"
    doc = pdfium.PdfDocument(synth_pdf)
    page = doc[0]
    img = page.render(scale=2.0).to_pil().convert("RGB")
    doc.close()
    img.save(out, format="PDF", resolution=144.0)
    return str(out)


@pytest.fixture()
def db():
    from seels.db import open_db
    d = open_db(":memory:")
    yield d
    d.close()


@pytest.fixture(scope="session")
def gold_file() -> str:
    return str(ROOT / "data" / "gold" / "synthetic_paper_gold.json")


@pytest.fixture()
def synth_ingested(db, synth_pdf):
    """DB with the synthetic paper parsed AND extracted (rules-v1)."""
    from seels.parsing.pipeline import ingest
    from seels.extraction.pipeline import run_extraction
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    run_extraction(db, pid, "rules-v1")
    return db, pid
