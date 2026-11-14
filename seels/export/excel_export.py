"""Excel export: one workbook with all tables + a README sheet."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import config
from ..util import utc_now
from .common import collect_rows

README_ROWS = [
    ["SEELS export", utc_now()],
    ["", ""],
    ["Sheets", "papers / samples / measurements / evidence / curves / curve_points / reviews / qc_flags / learning_cards"],
    ["Provenance", "every measurement links >=1 evidence row (page, bbox, quote, pdf sha256)"],
    ["Raw vs standardized", "value_raw+unit_raw preserve the printed original; value_std+unit_std are conversions (std_method explains, std_null_reason explains nulls)"],
    ["Status values", "candidate | accepted | rejected | uncertain | needs_human_review"],
    ["Policy", "LLM confidence is never treated as correctness; only human review accepts data"],
]


def export_excel(db, out_dir=None, paper_id=None) -> Path:
    data = collect_rows(db, paper_id=paper_id)
    d = Path(out_dir) if out_dir else (config.EXPORT_DIR / (paper_id or "all"))
    d.mkdir(parents=True, exist_ok=True)
    path = d / ("seels_export_%s.xlsx" % (paper_id or "all"))
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame(README_ROWS, columns=["key", "value"]).to_excel(
            xw, sheet_name="README", index=False)
        for name, rows in data.items():
            df = pd.DataFrame(rows) if rows else pd.DataFrame([{"_empty": True}])
            df.to_excel(xw, sheet_name=name[:31], index=False)
    return path
