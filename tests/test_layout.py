"""Layout-fusion tests with a FAKE provider (hermetic: no docling in CI).

The real engine runs out-of-process (scripts/layout_worker.py under the
ASCII-path layout venv); here we monkeypatch the provider class that
figures.extract_figures imports lazily.
"""
import pytest

from seels.curves import figures as F
from seels.parsing.pipeline import ingest


def _fake_layout_doc(sha):
    return {
        "schema": "seels.layout/1.0", "engine": "docling", "engine_version": "test",
        "pdf_sha256": sha, "seconds": 0.1,
        "pages": [{"page_no": 2, "width_pts": 595.0, "height_pts": 842.0, "blocks": [
            {"type": "figure", "page_no": 2, "bbox": [50.0, 200.0, 300.0, 340.0],
             "label": "PICTURE"},
            {"type": "figure", "page_no": 2, "bbox": [400.0, 60.0, 550.0, 180.0]},
            {"type": "table", "page_no": 2, "bbox": [0.0, 0.0, 10.0, 10.0]},
        ]}],
    }


class _FakeProv:
    def __init__(self, sha_getter):
        self._sha = sha_getter

    def available(self):
        return True

    def analyze(self, paper, force=False):
        return _fake_layout_doc(paper["sha256"])


class _UnavailableProv:
    def available(self):
        return False

    def analyze(self, paper, force=False):
        raise AssertionError("must not be called")


def _patch(monkeypatch, prov_cls):
    import seels.layout.docling_provider as dp
    monkeypatch.setattr(dp, "DoclingLayoutProvider", prov_cls)


def test_layout_regions_win_over_heuristics(db, synth_pdf, monkeypatch):
    _patch(monkeypatch, lambda *a, **k: _FakeProv(lambda: None))
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    ids = F.extract_figures(db, pid)
    curves = db.list_curves(pid)
    by_ref = {c["figure_ref"]: c for c in curves}
    assert "Figure 1" in by_ref
    c1 = by_ref["Figure 1"]
    assert c1["region_source"] == "layout:docling"
    assert c1["region_bbox"] == [50.0, 200.0, 300.0, 340.0]
    from pathlib import Path
    assert Path(c1["image_path"]).exists()
    # unmatched large block becomes an "Unnamed" region
    unnamed = [c for c in curves if c["figure_ref"].startswith("Unnamed p2#")]
    assert len(unnamed) == 1 and unnamed[0]["region_source"] == "layout:docling"
    # idempotent re-run keeps identical ids
    ids2 = F.extract_figures(db, pid)
    assert sorted(ids) == sorted(ids2)
    logs = [l["code"] for l in db.list_parse_logs(pid)]
    assert "layout_ready" in logs


def test_manual_region_protected_from_layout(db, synth_pdf, monkeypatch):
    _patch(monkeypatch, lambda *a, **k: _FakeProv(lambda: None))
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    F.extract_figures(db, pid)
    c1 = [c for c in db.list_curves(pid) if c["figure_ref"] == "Figure 1"][0]
    manual_bbox = [60.0, 210.0, 290.0, 330.0]
    F.update_region(db, c1["curve_id"], manual_bbox, reviewer="tester")
    F.extract_figures(db, pid)                     # layout re-run must not clobber
    c1b = db.get_curve(c1["curve_id"])
    assert c1b["region_source"] == "manual"
    assert c1b["region_bbox"] == manual_bbox


def test_stale_legacy_regions_pruned(db, synth_pdf, monkeypatch):
    _patch(monkeypatch, lambda *a, **k: _FakeProv(lambda: None))
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    db.insert_curve({"curve_id": "C-legacystale1", "paper_id": pid, "page": 2,
                     "figure_ref": "Figure 9", "image_path": "nonexistent.png",
                     "region_bbox": [1, 2, 300, 400], "region_source": None,
                     "method": "manual"})
    F.extract_figures(db, pid)
    assert db.get_curve("C-legacystale1") is None
    logs = [l["code"] for l in db.list_parse_logs(pid)]
    assert "pruned_stale_regions" in logs


def test_heuristic_path_when_layout_unavailable(db, synth_pdf, monkeypatch):
    _patch(monkeypatch, lambda *a, **k: _UnavailableProv())
    r = ingest(db, synth_pdf)
    pid = r["paper"]["paper_id"]
    ids = F.extract_figures(db, pid)
    assert ids
    curves = db.list_curves(pid)
    assert all(c["region_source"] in ("embedded_images", "heuristic_fallback")
               for c in curves)
    logs = [l["code"] for l in db.list_parse_logs(pid)]
    assert "layout_unavailable" in logs
