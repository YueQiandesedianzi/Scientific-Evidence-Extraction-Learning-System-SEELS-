"""Extraction orchestration: runs, materialization, evidence ids, resume.

- run_extraction() is idempotent per (paper, extractor): previous *candidate*
  rows of the same extractor are replaced; human-reviewed rows are kept.
- Every measurement row is written only together with >=1 evidence link.
- Uncertain fields become visible qc_flags (check EX_NULL_FIELD) - errors and
  missing information are never silently dropped or filled.
"""
from __future__ import annotations

from ..db import DB
from ..parsing.pipeline import load_parse
from ..util import jdump, new_id, normalize_text, sha256_bytes, utc_now
from .llm import LangExtractExtractor, OpenAICompatExtractor
from .rules import RuleExtractor

EXTRACTORS = {
    "rules-v1": RuleExtractor,
    "llm:openai-compat": OpenAICompatExtractor,
    "llm:langextract": LangExtractExtractor,
}


def build_extractor(name: str, cfg: dict = None):
    cls = EXTRACTORS.get(name)
    if cls is None:
        raise ValueError("unknown extractor %r (known: %s)" % (name, sorted(EXTRACTORS)))
    return cls(cfg or {})


def det_evidence_id(paper_id: str, ev: dict) -> str:
    key = "|".join([paper_id, str(ev.get("page")), ev.get("element_kind") or "",
                    ev.get("element_ref") or "", normalize_text(ev.get("quote_text") or "")[:300],
                    jdump(ev.get("bbox"))])
    return "EV-" + sha256_bytes(key.encode("utf-8"))[:16]


def _resolve_idx(obj, ev_map: dict):
    """Recursively replace integer evidence_ids (indices) with real ids."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "evidence_ids" and isinstance(v, list):
                ids = []
                for x in v:
                    if isinstance(x, int) and x in ev_map:
                        ids.append(ev_map[x])
                    elif isinstance(x, str):
                        ids.append(x)
                out[k] = ids
            else:
                out[k] = _resolve_idx(v, ev_map)
        return out
    if isinstance(obj, list):
        return [_resolve_idx(x, ev_map) for x in obj]
    return obj


def _materialize(db: DB, paper: dict, run_id: str, result: dict, method: str) -> dict:
    paper_id = paper["paper_id"]
    sha, path = paper["sha256"], paper["source_path"]
    ev_map: dict = {}
    n_new_ev = 0
    for idx, ev in enumerate(result.get("evidence") or []):
        eid = det_evidence_id(paper_id, ev)
        existed = db.get_evidence(eid)
        db.insert_evidence({**ev, "evidence_id": eid, "paper_id": paper_id,
                            "pdf_sha256": sha, "pdf_path": path,
                            "extraction_method": ev.get("extraction_method") or method})
        ev_map[idx] = eid
        if not existed:
            n_new_ev += 1
    n_samples = 0
    for s in result.get("samples") or []:
        fields = _resolve_idx(s.get("fields") or {}, ev_map)
        refs = [(ev_map[i], fp) for i, fp in (s.get("evidence_refs") or []) if i in ev_map]
        db.insert_sample({"sample_id": s["sample_id"], "paper_id": paper_id,
                          "name": s.get("name"), "fields": fields,
                          "notes": s.get("notes", "")}, refs)
        n_samples += 1
    n_meas = 0
    for m in result.get("measurements") or []:
        links = [(ev_map[i], role) for i, role in (m.get("evidence_links") or []) if i in ev_map]
        if not links:
            continue  # policy: never store a measurement without evidence
        sample_id = None
        if m.get("sample_name"):
            smp = db.find_sample_by_name(paper_id, normalize_text(m["sample_name"]))
            sample_id = smp["sample_id"] if smp else None
        row = {
            "measurement_id": new_id("M"), "paper_id": paper_id, "sample_id": sample_id,
            "run_id": run_id, "quantity": m.get("quantity"),
            "value_raw": m.get("value_raw"), "unit_raw": m.get("unit_raw"),
            "value_std": m.get("value_std"), "unit_std": m.get("unit_std"),
            "std_method": m.get("std_method"), "std_null_reason": m.get("std_null_reason"),
            "conditions": _resolve_idx(m.get("conditions") or {}, ev_map),
            "test_method": m.get("test_method"),
            "source_type": m.get("source_type") or "text",
            "extraction_method": method, "status": "candidate",
            "created_at": utc_now(),
        }
        db.insert_measurement(row, links)
        n_meas += 1
    return {"n_evidence_new": n_new_ev, "n_samples": n_samples, "n_measurements": n_meas}


def run_extraction(db: DB, paper_id: str, extractor_name: str = "rules-v1",
                   cfg: dict = None, replace_candidates: bool = True) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise KeyError("paper not in store: %s (ingest it first)" % paper_id)
    parse_doc = load_parse(paper_id)
    if not parse_doc:
        raise RuntimeError("paper %s has no parse.json (parse it first)" % paper_id)
    ex = build_extractor(extractor_name, cfg)
    if replace_candidates:
        removed = db.delete_candidate_run_data(paper_id, ex.name)
    else:
        removed = 0
    run_id = db.start_run(paper_id, ex.name, getattr(ex, "version", ""), cfg)
    try:
        result = ex.extract(paper, parse_doc)
    except Exception as e:
        db.finish_run(run_id, "failed", {"error": repr(e)})
        db.log_parse(paper_id, None, ex.name, "error", "extraction_failed", repr(e))
        raise
    counts = _materialize(db, paper, run_id, result, ex.name)
    n_flags = 0
    for u in (result.get("uncertain") or [])[:1000]:
        db.insert_qc_flag({
            "paper_id": paper_id, "target_type": "extraction_run", "target_id": run_id,
            "check_code": "EX_NULL_FIELD", "severity": "info",
            "message": "field=%s reason=%s" % (u.get("field"), u.get("reason")),
            "details": u})
        n_flags += 1
    stats = {**(result.get("stats") or {}), **counts,
             "n_uncertain_flags": n_flags, "replaced_candidates": removed}
    db.finish_run(run_id, "done", stats)
    return {"run_id": run_id, "extractor": ex.name, "stats": stats}
