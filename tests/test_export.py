import csv
import json
from pathlib import Path

from seels.export import export_all
from seels.export.csv_export import export_csv, export_dataset_csv


def test_csv_tables_and_dataset_policy(synth_ingested, tmp_path):
    db, pid = synth_ingested
    out = tmp_path / "exp"
    files = export_csv(db, out_dir=out, paper_id=pid)
    names = {Path(f).name for f in files}
    assert {"papers.csv", "samples.csv", "measurements.csv", "evidence.csv",
            "curves.csv", "curve_points.csv", "reviews.csv", "qc_flags.csv",
            "learning_cards.csv"} <= names
    with open(out / "measurements.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows and rows[0]["evidence_ids"] and rows[0]["first_quote"]

    # rejected rows and null-value rows are excluded from the ML dataset
    m0 = db.list_measurements(paper_id=pid, limit=1000)[0]
    db.set_measurement_status(m0["measurement_id"], "rejected")
    p = export_dataset_csv(db, out_dir=out, paper_id=pid)
    with open(p, encoding="utf-8-sig") as f:
        drows = list(csv.DictReader(f))
    assert all(r["measurement_id"] != m0["measurement_id"] for r in drows)
    assert all(r["value_std"] not in ("", "None") for r in drows)
    assert all(int(r["n_evidence"]) >= 1 for r in drows)
    manifest = json.loads((out / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["excluded"]["rejected"] >= 1
    assert manifest["inclusion_policy"]


def test_excel_export(synth_ingested, tmp_path):
    from openpyxl import load_workbook
    db, pid = synth_ingested
    files = export_all(db, out_dir=tmp_path / "x", paper_id=pid, fmt="xlsx")
    wb = load_workbook(files[0])
    assert "README" in wb.sheetnames and "measurements" in wb.sheetnames
    ws = wb["measurements"]
    head = [c.value for c in ws[1]]
    assert "value_raw" in head and "evidence_ids" in head
    assert ws.max_row > 1
