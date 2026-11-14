"""CSV exports: full audit tables + an ML-ready dataset with manifest."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .. import config
from ..util import utc_now
from .common import collect_rows

TABLES = ("papers", "samples", "measurements", "evidence", "curves",
          "curve_points", "reviews", "qc_flags", "learning_cards")

DATASET_COLUMNS = ("measurement_id", "paper_id", "paper_sha256", "sample_name", "quantity",
                   "value_raw", "unit_raw", "value_std", "unit_std", "conditions_json",
                   "test_method", "source_type", "extraction_method", "status",
                   "n_evidence", "evidence_ids", "evidence_pages", "first_quote")


def _out_dir(out_dir=None, paper_id=None) -> Path:
    d = Path(out_dir) if out_dir else (config.EXPORT_DIR / (paper_id or "all"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_csv(path: Path, rows: list, cols=None) -> Path:
    if cols is None:
        cols = list(rows[0].keys()) if rows else []
        seen = set(cols)
        for r in rows[1:]:
            for k in r:
                if k not in seen:
                    cols.append(k)
                    seen.add(k)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=cols or ["_empty"], extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)
    return path


def export_csv(db, out_dir=None, paper_id=None) -> list:
    data = collect_rows(db, paper_id=paper_id)
    d = _out_dir(out_dir, paper_id)
    return [_write_csv(d / (t + ".csv"), data[t]) for t in TABLES]


def export_dataset_csv(db, out_dir=None, paper_id=None) -> Path:
    """ML-ready dataset: rows that a model may train on.

    Inclusion policy (documented in the manifest, never silent):
    - value_std present (usable numeric), AND
    - status not in (rejected,), AND
    - n_evidence >= 1.
    Rows needing human review are KEPT but carry status so downstream users can
    filter; 'accepted' marks human-verified rows.
    """
    data = collect_rows(db, paper_id=paper_id)
    rows = []
    excluded = {"no_value_std": 0, "rejected": 0, "no_evidence": 0}
    for m in data["measurements"]:
        if m["status"] == "rejected":
            excluded["rejected"] += 1
            continue
        if m["value_std"] is None:
            excluded["no_value_std"] += 1
            continue
        if not m["n_evidence"]:
            excluded["no_evidence"] += 1
            continue
        rows.append({k: m.get(k) for k in DATASET_COLUMNS})
    d = _out_dir(out_dir, paper_id)
    p = _write_csv(d / "dataset_measurements.csv", rows, cols=list(DATASET_COLUMNS))
    manifest = {
        "generated_at": utc_now(),
        "scope": {"paper_id": paper_id or "all"},
        "n_rows": len(rows),
        "columns": list(DATASET_COLUMNS),
        "column_notes": {
            "value_std": "standardized value (SI-ish, see unit_std); raw value kept in value_raw/unit_raw",
            "evidence_ids": "semicolon-separated ids into evidence.csv (page, bbox, quote)",
            "first_quote": "verbatim source text of the primary evidence",
            "status": "candidate | accepted | rejected | uncertain | needs_human_review",
        },
        "inclusion_policy": "value_std not null AND status != rejected AND n_evidence >= 1",
        "excluded": excluded,
        "provenance": "every row is traceable to PDF page + bbox + quote via evidence.csv",
        "warning": "LLM confidence is never part of this dataset; correctness is "
                   "established only by human review (status=accepted) or deterministic QC.",
    }
    (d / "dataset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
