"""SEELS review & learning server.

Stdlib-only HTTP server bound to 127.0.0.1: JSON API + static single-page app.
No CDN dependencies: PDF pages are rendered locally (cached PNG) and evidence
boxes are drawn as SVG overlays, so record -> PDF position navigation works
offline.

Run:  python -m seels.cli serve   (or seels.ui.server.main())
"""
from __future__ import annotations

import json
import mimetypes
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import config
from ..curves import figures as cfig
from ..db import DB, open_db
from ..extraction.pipeline import run_extraction
from ..extraction.units import parse_value_unit, standardize
from ..learning import build_card_for_measurement, generate_cards_for_paper
from ..parsing.pipeline import load_parse, render_page_image
from ..qc.checks import run_qc
from ..util import utc_now

STATIC = Path(__file__).resolve().parent / "static"
_LOCK = threading.Lock()
_DB: DB | None = None

REVIEW_ACTIONS = ("accept", "modify", "reject", "flag_uncertain", "reopen", "back_to_evidence")


def get_db() -> DB:
    global _DB
    if _DB is None:
        _DB = open_db()
    return _DB


# ------------------------------------------------------------------ reviews
def apply_review(db: DB, payload: dict) -> dict:
    ttype = payload.get("target_type")
    tid = payload.get("target_id")
    action = payload.get("action")
    if action not in REVIEW_ACTIONS:
        raise ValueError("action must be one of %s" % (REVIEW_ACTIONS,))
    reviewer = (payload.get("reviewer") or "anonymous").strip()[:60]
    comment = (payload.get("comment") or "").strip()[:2000]
    new_value = payload.get("new_value")
    old = None
    paper_id = payload.get("paper_id")

    if ttype == "measurement":
        m = db.get_measurement(tid)
        if not m:
            raise KeyError("measurement %s not found" % tid)
        paper_id = paper_id or m["paper_id"]
        keys = ("quantity", "value_raw", "unit_raw", "value_std", "unit_std",
                "status", "sample_id", "test_method")
        old = {k: m.get(k) for k in keys}
        if action == "accept":
            db.set_measurement_status(tid, "accepted")
            for ev in m.get("evidence") or []:
                db.set_evidence_review_status(ev["evidence_id"], "accepted")
        elif action == "reject":
            db.set_measurement_status(tid, "rejected")
            for ev in m.get("evidence") or []:
                db.set_evidence_review_status(ev["evidence_id"], "rejected")
        elif action == "flag_uncertain":
            db.set_measurement_status(tid, "uncertain")
        elif action == "reopen":
            db.set_measurement_status(tid, "candidate")
        elif action == "modify":
            nv = new_value or {}
            fields = {}
            for k in ("quantity", "test_method", "sample_id"):
                if k in nv:
                    fields[k] = nv[k] or None
            if "value_raw" in nv or "unit_raw" in nv:
                vr = nv.get("value_raw", m.get("value_raw"))
                ur = nv.get("unit_raw", m.get("unit_raw"))
                fields["value_raw"] = vr
                fields["unit_raw"] = ur
                parsed = parse_value_unit(
                    (str(vr or "") + (" " + ur if ur else "")).strip())
                if parsed["value"] is not None:
                    std = standardize(parsed["value"], ur)
                    fields["value_std"] = std["value_std"]
                    fields["unit_std"] = std["unit_std"]
                    fields["std_method"] = (std["method"] or "") + "|human_modified"
                    fields["std_null_reason"] = std["reason"]
                else:
                    fields["value_std"] = None
                    fields["unit_std"] = None
                    fields["std_method"] = None
                    fields["std_null_reason"] = "human_modified_unparseable:" + str(parsed["reason"])
            if fields:
                db.update_measurement(tid, **fields)
            db.set_measurement_status(tid, "accepted")
        elif action == "back_to_evidence":
            pass  # navigation intent; still recorded in the audit trail
        db.insert_qc_flag  # noqa (keep import usage explicit)
    elif ttype == "curve":
        cv = db.get_curve(tid)
        if not cv:
            raise KeyError("curve %s not found" % tid)
        paper_id = paper_id or cv["paper_id"]
        old = {"verification_status": cv.get("verification_status")}
        if action == "accept":
            db.update_curve(tid, verification_status="human_verified")
        elif action == "reject":
            db.update_curve(tid, verification_status="unverified")
        elif action == "flag_uncertain":
            db.update_curve(tid, verification_status="unverified")
        elif action == "reopen":
            db.update_curve(tid, verification_status="unverified")
    elif ttype == "evidence":
        ev = db.get_evidence(tid)
        if not ev:
            raise KeyError("evidence %s not found" % tid)
        paper_id = paper_id or ev["paper_id"]
        old = {"review_status": ev.get("review_status")}
        mapping = {"accept": "accepted", "reject": "rejected",
                   "flag_uncertain": "uncertain", "reopen": "unreviewed"}
        if action in mapping:
            db.set_evidence_review_status(tid, mapping[action])
    else:
        raise ValueError("unsupported target_type %r" % ttype)

    rid = db.add_review(paper_id, ttype, tid, action, old, new_value, comment, reviewer)
    out = {"review_id": rid, "recorded_at": utc_now()}
    if ttype == "measurement" and action in ("accept", "modify", "reject", "flag_uncertain"):
        try:
            build_card_for_measurement(db, tid)   # refresh reliability dimension
        except Exception as e:
            out["card_refresh_error"] = repr(e)
    return out


# ------------------------------------------------------------------ handler
class Handler(BaseHTTPRequestHandler):
    server_version = "SEELS/0.1"

    def log_message(self, fmt, *args):  # quieter console
        pass

    # -- helpers --
    def _send(self, code: int, body: bytes, ctype: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _err(self, code, msg):
        self._json({"error": str(msg)}, code)

    def _file(self, path: Path, ctype=None):
        if not path.exists():
            return self._err(404, "file not found: %s" % path.name)
        ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send(200, path.read_bytes(), ctype)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw.decode("utf-8") or "{}")

    def _q(self) -> dict:
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.path.split("?", 1)[-1]).items()} \
            if "?" in self.path else {}

    # -- GET --
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        q = self._q()
        try:
            with _LOCK:
                self._route_get(path, q)
        except Exception as e:
            self._err(500, repr(e))

    def _route_get(self, path: str, q: dict):
        db = get_db()
        if path == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            fp = (STATIC / path[len("/static/"):]).resolve()
            if not str(fp).startswith(str(STATIC.resolve())):
                return self._err(403, "forbidden")
            return self._file(fp)
        if path == "/api/papers":
            papers = []
            for p in db.list_papers():
                papers.append({**p, "summary": db.paper_summary(p["paper_id"])})
            return self._json(papers)
        if path == "/api/paper":
            p = db.get_paper(q["id"])
            if not p:
                return self._err(404, "unknown paper")
            parse = load_parse(p["paper_id"]) or {}
            return self._json({
                "paper": p, "summary": db.paper_summary(p["paper_id"]),
                "classification": parse.get("classification"),
                "stats": parse.get("stats"),
                "pages": [{"page_no": pg["page_no"], "status": pg.get("status"),
                           "char_count": pg.get("char_count"),
                           "n_tables": len(pg.get("tables") or []),
                           "n_captions": len(pg.get("captions") or []),
                           "width_pts": pg.get("width_pts"), "height_pts": pg.get("height_pts")}
                          for pg in parse.get("pages") or []],
                "runs": db.list_runs(p["paper_id"])[:20],
            })
        if path == "/api/page_image":
            p = db.get_paper(q["paper_id"])
            dpi = int(q.get("dpi", "140"))
            img = render_page_image(p, int(q["page"]), dpi=dpi)
            return self._file(img, "image/png")
        if path == "/api/page_evidence":
            evs = db.list_evidence(paper_id=q["paper_id"], page=int(q["page"]))
            return self._json(evs)
        if path == "/api/measurements":
            rows = db.list_measurements(paper_id=q.get("paper_id"), status=q.get("status"),
                                        q=q.get("q"), limit=int(q.get("limit", "500")))
            return self._json(rows)
        if path == "/api/measurement":
            m = db.get_measurement(q["id"])
            if not m:
                return self._err(404, "unknown measurement")
            m["reviews"] = db.list_reviews(target_type="measurement", target_id=m["measurement_id"])
            m["flags"] = db.list_qc_flags(target_type="measurement",
                                          target_id=m["measurement_id"])
            m["card"] = db.get_learning_card("measurement", m["measurement_id"])
            if m.get("sample_id"):
                m["sample"] = db.get_sample(m["sample_id"])
            return self._json(m)
        if path == "/api/measurements_by_evidence":
            return self._json(db.measurements_for_evidence(q["evidence_id"]))
        if path == "/api/samples":
            return self._json(db.list_samples(q["paper_id"]))
        if path == "/api/curves":
            out = []
            for cv in db.list_curves(q.get("paper_id")):
                pts = db.list_curve_points(cv["curve_id"])
                out.append({**cv, "n_points": len(pts),
                            "has_calibration": bool(cv.get("calibration")),
                            "image_exists": Path(cv.get("image_path") or "").exists()})
            return self._json(out)
        if path == "/api/curve":
            cv = db.get_curve(q["id"])
            if not cv:
                return self._err(404, "unknown curve")
            base = Path(cv.get("image_path") or "")
            cv["has_overlay"] = base.with_name(base.stem + "_overlay.png").exists()
            cv["has_redraw"] = base.with_name(base.stem + "_redraw.png").exists()
            cv["points"] = db.list_curve_points(cv["curve_id"])
            cv["reviews"] = db.list_reviews(target_type="curve", target_id=cv["curve_id"])
            cv["flags"] = db.list_qc_flags(target_type="curve", target_id=cv["curve_id"])
            return self._json(cv)
        if path == "/api/curve_image":
            cv = db.get_curve(q["id"])
            kind = q.get("kind", "region")
            base = Path(cv["image_path"])
            if kind == "region":
                return self._file(base, "image/png")
            p = base.with_name(base.stem + ("_overlay.png" if kind == "overlay" else "_redraw.png"))
            return self._file(p, "image/png")
        if path == "/api/flags":
            resolved = None if "resolved" not in q else q["resolved"] == "1"
            return self._json(db.list_qc_flags(paper_id=q.get("paper_id"), resolved=resolved))
        if path == "/api/reviews":
            return self._json(db.list_reviews(paper_id=q.get("paper_id"),
                                              target_id=q.get("target_id")))
        if path == "/api/cards":
            if q.get("target_id"):
                return self._json(db.get_learning_card(q.get("target_type", "measurement"),
                                                       q["target_id"]))
            return self._json(db.list_learning_cards(q.get("paper_id")))
        if path == "/api/parse_logs":
            return self._json(db.list_parse_logs(q["paper_id"])[:400])
        if path == "/api/export":
            from ..export import export_all
            fmt = q.get("fmt", "csv")
            files = export_all(db, paper_id=q.get("paper_id") or None,
                               fmt=("xlsx" if fmt == "xlsx" else "csv"))
            return self._json({"files": files})
        if path == "/api/download":
            fp = Path(q["path"]).resolve()
            allowed = [config.EXPORT_DIR.resolve(), config.CURVE_DIR.resolve(),
                       config.RENDER_DIR.resolve(), config.WORK_DIR.resolve()]
            if not any(str(fp).startswith(str(a)) for a in allowed):
                return self._err(403, "path outside export area")
            return self._file(fp)
        return self._err(404, "no route " + path)

    # -- POST --
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            payload = self._body()
            with _LOCK:
                self._route_post(path, payload)
        except Exception as e:
            self._err(400, repr(e))

    def _route_post(self, path: str, payload: dict):
        db = get_db()
        if path == "/api/review":
            return self._json(apply_review(db, payload))
        if path == "/api/extract":
            res = run_extraction(db, payload["paper_id"],
                                 payload.get("extractor", "rules-v1"),
                                 payload.get("cfg"))
            return self._json(res)
        if path == "/api/qc":
            return self._json(run_qc(db, payload["paper_id"]))
        if path == "/api/cards":
            n = generate_cards_for_paper(db, payload["paper_id"],
                                         limit=int(payload.get("limit", 200)))
            return self._json({"cards": n})
        if path == "/api/figures":
            ids = cfig.extract_figures(db, payload["paper_id"],
                                       use_layout=bool(payload.get("use_layout", True)))
            return self._json({"curves": ids, "n": len(ids)})
        if path == "/api/curve_calibration":
            cv = cfig.set_calibration(db, payload["curve_id"], payload["spec"])
            ov = None
            if db.list_curve_points(payload["curve_id"]):
                from ..curves.redraw import overlay_curve, redraw_curve
                ov = overlay_curve(db, payload["curve_id"])
                redraw_curve(db, payload["curve_id"])
            return self._json({"curve": cv, "overlay": ov})
        if path == "/api/curve_points":
            return self._json(cfig.add_manual_points(db, payload["curve_id"],
                                                     payload["points"]))
        if path == "/api/curve_wpd":
            return self._json(cfig.import_wpd_csv(db, payload["curve_id"],
                                                  payload["csv_text"],
                                                  payload.get("series")))
        if path == "/api/curve_region":
            cv = cfig.update_region(db, payload["curve_id"], payload["bbox"],
                                    reviewer=(payload.get("reviewer") or "anonymous"))
            return self._json({"curve": cv})
        if path == "/api/flag_resolve":
            db.resolve_flag(int(payload["flag_id"]),
                            (payload.get("reviewer") or "anonymous"))
            return self._json({"ok": True})
        return self._err(404, "no route " + path)


def main(host: str = None, port: int = None):
    config.ensure_dirs()
    host = host or config.UI_HOST
    port = port or config.UI_PORT
    get_db()
    srv = ThreadingHTTPServer((host, port), Handler)
    print("SEELS review UI: http://%s:%d  (Ctrl+C to stop)" % (host, port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("bye")
        srv.shutdown()


if __name__ == "__main__":
    main()
