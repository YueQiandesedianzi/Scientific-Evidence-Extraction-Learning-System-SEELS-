"""CSV / Excel export subsystem."""
from .csv_export import export_csv, export_dataset_csv
from .excel_export import export_excel

__all__ = ["export_csv", "export_dataset_csv", "export_excel", "export_all"]


def export_all(db, out_dir=None, paper_id=None, fmt="both"):
    from . import csv_export, excel_export
    made = []
    if fmt in ("csv", "both"):
        made += csv_export.export_csv(db, out_dir, paper_id=paper_id)
        made.append(csv_export.export_dataset_csv(db, out_dir, paper_id=paper_id))
    if fmt in ("xlsx", "both"):
        made.append(excel_export.export_excel(db, out_dir, paper_id=paper_id))
    return [str(p) for p in made]
