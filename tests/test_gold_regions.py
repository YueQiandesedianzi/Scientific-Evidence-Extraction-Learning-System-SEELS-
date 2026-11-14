"""Hermetic tests for the P3 region-gold evaluator (no docling needed)."""
import json

from seels.gold_regions import compare_regions, iou


def test_iou_math():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [5, 0, 15, 10]) == 1 / 3
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert iou(None, [0, 0, 1, 1]) == 0.0


def _mk(db, pid, cid, page, bbox, source="layout:docling"):
    db.insert_curve({"curve_id": cid, "paper_id": pid, "page": page,
                     "figure_ref": "Figure 1", "image_path": "x.png",
                     "region_bbox": bbox, "region_source": source})


def test_compare_pass_fail_missing(db, tmp_path):
    pid = "P-goldreg01"
    db.upsert_paper({"paper_id": pid, "source_path": "x.pdf", "sha256": "33" * 32})
    _mk(db, pid, "C-a", 2, [50, 200, 300, 340])            # good region
    _mk(db, pid, "C-b", 3, [0, 0, 40, 40], "heuristic_fallback")  # wrong place
    gold = {"annotator": "test", "items": [
        {"id": "RG-1", "paper_id": pid, "page": 2, "figure_ref": "Figure 1",
         "gold_bbox": [52, 198, 298, 338]},                      # ~PASS
        {"id": "RG-2", "paper_id": pid, "page": 3, "figure_ref": "Figure 2",
         "gold_bbox": [300, 500, 500, 700]},                     # FAIL (C-b far off)
        {"id": "RG-3", "paper_id": pid, "page": 9, "figure_ref": "Figure 9",
         "gold_bbox": [10, 10, 200, 200]},                       # MISSING (no region p9)
        {"id": "RG-4", "paper_id": pid, "page": 2, "gold_bbox": None},  # SKIPPED
    ]}
    gp = tmp_path / "gold.json"
    gp.write_text(json.dumps(gold), encoding="utf-8")
    rep = compare_regions(db, json.loads(gp.read_text(encoding="utf-8")))
    s = rep["summary"]
    assert s["n_evaluated"] == 3 and s["n_skipped_null_gold"] == 1
    assert s["n_pass"] == 1 and s["n_missing_region"] == 1
    by_id = {r["id"]: r for r in rep["rows"]}
    assert by_id["RG-1"]["status"] == "PASS" and by_id["RG-1"]["our_best_iou"] > 0.9
    assert by_id["RG-2"]["status"] == "FAIL"
    assert by_id["RG-3"]["status"] == "MISSING"
    assert by_id["RG-4"]["status"] == "SKIPPED"
