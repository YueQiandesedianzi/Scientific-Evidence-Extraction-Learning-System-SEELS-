"""Human gold standard vs automatic extraction comparison.

Gold file (JSON):
{
  "annotator": "human:<name>" | "session-agent",   # who made the gold labels
  "paper_file": "arxiv_xxxx.pdf",                  # informational
  "notes": "...",
  "items": [
    {"quantity": "critical_temperature",   # canonical name or alias; optional
     "value": 93.0, "unit": "K",          # expected RAW value as printed
     "sample": "Sample A",                # optional normalized sample name
     "page": 1,                           # optional evidence page constraint
     "value_std": 93.0, "unit_std": "K",  # optional expected standardized pair
     "tolerance_rel": 0.02,               # optional, default 0.02
     "source": "table"|"text"|"curve",    # optional constraint
     "note": "human comment"}
  ]
}

Matching policy (strict, no fuzzy credit):
- value must match within tolerance_rel on the standardized value when both
  sides have one, else on raw numeric parsing of value/unit;
- quantity (when given) must match after alias canonicalization;
- sample/page/source constraints (when given) must hold;
- each gold item matches at most one extracted measurement (greedy, best
  relative error first).
Output: per-item CSV + JSON summary (matched / value_mismatch / missing) and
an informational count of extracted rows not covered by gold (gold may be a
partial annotation - extras are NOT counted as false positives).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .extraction.schema import QUANTITY_ALIASES
from .extraction.units import parse_value_unit, standardize
from .util import normalize_text, utc_now


def load_gold(path: str) -> dict:
    g = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(g.get("items"), list):
        raise ValueError("gold file needs an 'items' list")
    return g


def _canon(q):
    if not q:
        return None
    t = normalize_text(q).lower()
    return QUANTITY_ALIASES.get(t, t)


def _num(value, unit):
    parsed = parse_value_unit((str(value) + (" " + unit if unit else "")).strip())
    if parsed["value"] is None:
        return None, None
    std = standardize(parsed["value"], unit)
    return parsed["value"], std["value_std"]


def compare_gold(db, paper_id: str, gold: dict) -> dict:
    ms = [db.get_measurement(m["measurement_id"])
          for m in db.list_measurements(paper_id=paper_id, limit=1000000)]
    used = set()
    rows = []
    n_matched = n_mismatch = n_missing = 0
    for gi, item in enumerate(gold["items"]):
        tol = float(item.get("tolerance_rel", 0.02))
        g_raw, g_std = _num(item["value"], item.get("unit"))
        exp_std = item.get("value_std", g_std)
        best = None
        for m in ms:
            if m["measurement_id"] in used:
                continue
            if item.get("quantity") and _canon(m.get("quantity")) != _canon(item["quantity"]):
                continue
            if item.get("source") and m.get("source_type") != item["source"]:
                continue
            if item.get("page"):
                pages = {e["page"] for e in m.get("evidence") or []}
                if int(item["page"]) not in pages:
                    continue
            if item.get("sample"):
                smp = db.get_sample(m["sample_id"]) if m.get("sample_id") else None
                if not smp or normalize_text(smp.get("name") or "").lower() != \
                        normalize_text(item["sample"]).lower():
                    continue
            m_raw, m_std = _num(m.get("value_raw"), m.get("unit_raw"))
            cmp_a = (exp_std, m_std) if (exp_std is not None and m_std is not None) else (g_raw, m_raw)
            if cmp_a[0] is None or cmp_a[1] is None:
                continue
            denom = max(abs(cmp_a[0]), 1e-12)
            rel = abs(cmp_a[1] - cmp_a[0]) / denom
            if rel <= tol and (best is None or rel < best[0]):
                best = (rel, m)
        if best is None:
            # second pass: find near-miss on value for diagnostics (mismatch vs missing)
            near = None
            for m in ms:
                if m["measurement_id"] in used:
                    continue
                if item.get("quantity") and _canon(m.get("quantity")) != _canon(item["quantity"]):
                    continue
                m_raw, m_std = _num(m.get("value_raw"), m.get("unit_raw"))
                a = (exp_std if (exp_std is not None and m_std is not None) else g_raw)
                b = (m_std if (exp_std is not None and m_std is not None) else m_raw)
                if a is None or b is None:
                    continue
                rel = abs(b - a) / max(abs(a), 1e-12)
                if near is None or rel < near[0]:
                    near = (rel, m)
            if near and near[0] <= 0.5:
                status = "VALUE_MISMATCH"
                n_mismatch += 1
                m = near[1]
            else:
                status = "MISSING"
                n_missing += 1
                m = None
        else:
            status = "MATCHED"
            n_matched += 1
            m = best[1]
        if m is not None and status == "MATCHED":
            used.add(m["measurement_id"])
        rows.append({
            "gold_index": gi, "status": status,
            "gold_quantity": item.get("quantity"), "gold_value": item.get("value"),
            "gold_unit": item.get("unit"), "gold_sample": item.get("sample"),
            "gold_page": item.get("page"),
            "matched_measurement_id": (m or {}).get("measurement_id"),
            "matched_value_raw": (m or {}).get("value_raw"),
            "matched_unit_raw": (m or {}).get("unit_raw"),
            "matched_value_std": (m or {}).get("value_std"),
            "matched_status": (m or {}).get("status"),
            "matched_page": ((m or {}).get("evidence") or [{}])[0].get("page") if m else None,
            "rel_error": (best[0] if best is not None and status == "MATCHED" else None),
            "gold_note": item.get("note"),
        })
    summary = {
        "paper_id": paper_id, "generated_at": utc_now(),
        "annotator": gold.get("annotator"), "gold_file_notes": gold.get("notes"),
        "n_gold_items": len(gold["items"]), "n_matched": n_matched,
        "n_value_mismatch": n_mismatch, "n_missing": n_missing,
        "n_extracted_total": len(ms),
        "n_extra_extracted": len(ms) - len(used),
        "recall": (n_matched / len(gold["items"])) if gold["items"] else None,
        "policy": "strict matching; extras are informational (gold may be partial)",
    }
    return {"summary": summary, "rows": rows}


def write_compare_report(report: dict, out_prefix: str):
    out_csv = Path(out_prefix + "_compare.csv")
    out_json = Path(out_prefix + "_compare.json")
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows = report["rows"]
    cols = list(rows[0].keys()) if rows else ["_empty"]
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        wr.writerows(rows)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    return [str(out_csv), str(out_json)]
