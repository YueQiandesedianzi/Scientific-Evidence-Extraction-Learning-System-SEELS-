"""SQLite storage layer for SEELS.

Design rules:
- Reviews are append-only (human audit trail); nothing updates/deletes them.
- Re-running an extractor only replaces *candidate* rows of the previous run of
  the same extractor for the same paper; human-reviewed rows are preserved.
- QC flags are derived data: a QC run clears the paper's unresolved flags and
  re-inserts current ones (resolved flags stay as history).
- Original PDFs are never written; we only store path + sha256 references.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import config
from .util import jdump, jload, utc_now

DDL = """
CREATE TABLE IF NOT EXISTS schema_info(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS papers(
  paper_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  title TEXT, authors TEXT, doi TEXT, arxiv_id TEXT,
  n_pages INTEGER, pdf_kind TEXT, parse_status TEXT,
  parse_json_path TEXT, meta_json TEXT,
  ingested_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS parse_logs(
  log_id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  page INTEGER, engine TEXT, level TEXT, code TEXT, message TEXT,
  details_json TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_plog_paper ON parse_logs(paper_id);

CREATE TABLE IF NOT EXISTS extraction_runs(
  run_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  extractor TEXT NOT NULL, extractor_version TEXT,
  config_json TEXT, status TEXT, stats_json TEXT,
  started_at TEXT NOT NULL, finished_at TEXT);

CREATE TABLE IF NOT EXISTS evidence(
  evidence_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  page INTEGER NOT NULL,
  bbox_json TEXT, coord_system TEXT DEFAULT 'top_left_pts',
  quote_text TEXT, element_kind TEXT, element_ref TEXT,
  source_type TEXT, extraction_method TEXT,
  review_status TEXT DEFAULT 'unreviewed',
  pdf_sha256 TEXT NOT NULL, pdf_path TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_ev_paper_page ON evidence(paper_id, page);

CREATE TABLE IF NOT EXISTS samples(
  sample_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  name TEXT, fields_json TEXT, notes TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sample_evidence(
  sample_id TEXT NOT NULL REFERENCES samples(sample_id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
  field_path TEXT NOT NULL,
  PRIMARY KEY(sample_id, evidence_id, field_path));

CREATE TABLE IF NOT EXISTS measurements(
  measurement_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  sample_id TEXT REFERENCES samples(sample_id) ON DELETE SET NULL,
  run_id TEXT REFERENCES extraction_runs(run_id) ON DELETE SET NULL,
  quantity TEXT, value_raw TEXT, unit_raw TEXT,
  value_std REAL, unit_std TEXT, std_method TEXT, std_null_reason TEXT,
  conditions_json TEXT, test_method TEXT,
  source_type TEXT, extraction_method TEXT,
  status TEXT NOT NULL DEFAULT 'candidate',
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_meas_paper ON measurements(paper_id);
CREATE INDEX IF NOT EXISTS idx_meas_status ON measurements(status);

CREATE TABLE IF NOT EXISTS measurement_evidence(
  measurement_id TEXT NOT NULL REFERENCES measurements(measurement_id) ON DELETE CASCADE,
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'primary',
  PRIMARY KEY(measurement_id, evidence_id, role));

CREATE TABLE IF NOT EXISTS curves(
  curve_id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE,
  page INTEGER, figure_ref TEXT,
  image_path TEXT, image_sha256 TEXT, region_bbox_json TEXT,
  region_source TEXT, calibration_json TEXT, method TEXT, series_name TEXT,
  verification_status TEXT DEFAULT 'unverified',
  notes TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS curve_points(
  point_id INTEGER PRIMARY KEY AUTOINCREMENT,
  curve_id TEXT NOT NULL REFERENCES curves(curve_id) ON DELETE CASCADE,
  x_raw REAL, y_raw REAL, x_std REAL, y_std REAL,
  pixel_x REAL, pixel_y REAL, source TEXT);
CREATE INDEX IF NOT EXISTS idx_cp_curve ON curve_points(curve_id);

CREATE TABLE IF NOT EXISTS reviews(
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id TEXT, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  action TEXT NOT NULL, old_value_json TEXT, new_value_json TEXT,
  comment TEXT, reviewer TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_rev_target ON reviews(target_type, target_id);

CREATE TABLE IF NOT EXISTS learning_cards(
  card_id TEXT PRIMARY KEY,
  paper_id TEXT, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  dimensions_json TEXT NOT NULL, ai_explanation TEXT, ai_source TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(target_type, target_id));

CREATE TABLE IF NOT EXISTS qc_flags(
  flag_id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id TEXT, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
  check_code TEXT NOT NULL, severity TEXT NOT NULL,
  message TEXT, details_json TEXT,
  resolved INTEGER DEFAULT 0, resolved_by TEXT, resolved_at TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_qc_paper ON qc_flags(paper_id, resolved);
"""

MEAS_COLUMNS = ("measurement_id", "paper_id", "sample_id", "run_id", "quantity",
                "value_raw", "unit_raw", "value_std", "unit_std", "std_method",
                "std_null_reason", "conditions_json", "test_method", "source_type",
                "extraction_method", "status", "created_at")


class DB:
    def __init__(self, path: Optional[str] = None):
        self.path = str(path or config.DB_PATH)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    # -- connection ------------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # check_same_thread=False: the UI server serializes every DB access
            # with a global lock (seels.ui.server._LOCK); CLI usage is
            # single-threaded. No concurrent writers exist by design.
            self._conn = sqlite3.connect(self.path, timeout=60, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def init_schema(self) -> None:
        c = self.conn()
        c.executescript(DDL)
        # lightweight forward-only migrations
        cols = [r[1] for r in c.execute("PRAGMA table_info(curves)")]
        if "region_source" not in cols:
            c.execute("ALTER TABLE curves ADD COLUMN region_source TEXT")
        c.execute("INSERT OR REPLACE INTO schema_info(key,value) VALUES('version',?)",
                  (config.SCHEMA_VERSION,))
        c.commit()

    # -- papers ------------------------------------------------------------
    def upsert_paper(self, p: dict) -> str:
        c = self.conn()
        c.execute("""INSERT INTO papers(paper_id,source_path,sha256,title,authors,doi,
                     arxiv_id,n_pages,pdf_kind,parse_status,parse_json_path,meta_json,ingested_at)
                     VALUES(:paper_id,:source_path,:sha256,:title,:authors,:doi,:arxiv_id,
                     :n_pages,:pdf_kind,:parse_status,:parse_json_path,:meta_json,:ingested_at)
                     ON CONFLICT(paper_id) DO UPDATE SET
                       source_path=excluded.source_path, title=excluded.title,
                       authors=excluded.authors, doi=excluded.doi, arxiv_id=excluded.arxiv_id,
                       n_pages=excluded.n_pages, pdf_kind=excluded.pdf_kind,
                       parse_status=excluded.parse_status,
                       parse_json_path=excluded.parse_json_path,
                       meta_json=excluded.meta_json""",
                  {**_paper_defaults(), **p})
        c.commit()
        return p["paper_id"]

    def get_paper(self, paper_id: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
        return _paper_out(r) if r else None

    def find_paper_by_sha(self, sha: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM papers WHERE sha256=?", (sha,)).fetchone()
        return _paper_out(r) if r else None

    def list_papers(self) -> list:
        rs = self.conn().execute("SELECT * FROM papers ORDER BY ingested_at").fetchall()
        return [_paper_out(r) for r in rs]

    def update_paper(self, paper_id: str, **fields) -> None:
        if not fields:
            return
        if "meta" in fields:
            fields["meta_json"] = jdump(fields.pop("meta"))
        sets = ", ".join(k + "=?" for k in fields)
        self.conn().execute("UPDATE papers SET " + sets + " WHERE paper_id=?",
                            (*fields.values(), paper_id))
        self.conn().commit()

    # -- parse logs ----------------------------------------------------------
    def log_parse(self, paper_id: str, page: Optional[int], engine: str, level: str,
                  code: str, message: str, details: Optional[dict] = None) -> None:
        self.conn().execute(
            """INSERT INTO parse_logs(paper_id,page,engine,level,code,message,details_json,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (paper_id, page, engine, level, code, message, jdump(details), utc_now()))
        self.conn().commit()

    def list_parse_logs(self, paper_id: str, level: Optional[str] = None) -> list:
        q = "SELECT * FROM parse_logs WHERE paper_id=?"
        args: list = [paper_id]
        if level:
            q += " AND level=?"
            args.append(level)
        q += " ORDER BY log_id"
        return [dict(r, details=jload(r["details_json"])) for r in self.conn().execute(q, args)]

    # -- extraction runs -----------------------------------------------------
    def start_run(self, paper_id: str, extractor: str, version: str,
                  cfg: Optional[dict] = None) -> str:
        from .util import new_id
        run_id = new_id("R")
        self.conn().execute(
            """INSERT INTO extraction_runs(run_id,paper_id,extractor,extractor_version,
               config_json,status,started_at) VALUES(?,?,?,?,?,?,?)""",
            (run_id, paper_id, extractor, version, jdump(cfg or {}), "running", utc_now()))
        self.conn().commit()
        return run_id

    def finish_run(self, run_id: str, status: str, stats: Optional[dict] = None) -> None:
        self.conn().execute(
            "UPDATE extraction_runs SET status=?, stats_json=?, finished_at=? WHERE run_id=?",
            (status, jdump(stats or {}), utc_now(), run_id))
        self.conn().commit()

    def get_run(self, run_id: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM extraction_runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(r, config=jload(r["config_json"]), stats=jload(r["stats_json"])) if r else None

    def latest_run(self, paper_id: str, extractor: str) -> Optional[dict]:
        r = self.conn().execute(
            """SELECT * FROM extraction_runs WHERE paper_id=? AND extractor=?
               ORDER BY started_at DESC LIMIT 1""", (paper_id, extractor)).fetchone()
        return dict(r, config=jload(r["config_json"]), stats=jload(r["stats_json"])) if r else None

    def list_runs(self, paper_id: Optional[str] = None) -> list:
        q = "SELECT * FROM extraction_runs"
        args: list = []
        if paper_id:
            q += " WHERE paper_id=?"
            args.append(paper_id)
        q += " ORDER BY started_at DESC"
        return [dict(r, config=jload(r["config_json"]), stats=jload(r["stats_json"]))
                for r in self.conn().execute(q, args)]

    # -- evidence --------------------------------------------------------------
    def insert_evidence(self, ev: dict) -> str:
        row = {
            "evidence_id": ev["evidence_id"], "paper_id": ev["paper_id"], "page": ev["page"],
            "bbox_json": jdump(ev.get("bbox")), "coord_system": ev.get("coord_system", "top_left_pts"),
            "quote_text": ev.get("quote_text", ""), "element_kind": ev.get("element_kind", "text"),
            "element_ref": ev.get("element_ref", ""), "source_type": ev.get("source_type", "native_text"),
            "extraction_method": ev.get("extraction_method", ""),
            "review_status": ev.get("review_status", "unreviewed"),
            "pdf_sha256": ev["pdf_sha256"], "pdf_path": ev["pdf_path"],
            "created_at": ev.get("created_at") or utc_now(),
        }
        self.conn().execute(
            """INSERT OR IGNORE INTO evidence(evidence_id,paper_id,page,bbox_json,coord_system,
               quote_text,element_kind,element_ref,source_type,extraction_method,review_status,
               pdf_sha256,pdf_path,created_at)
               VALUES(:evidence_id,:paper_id,:page,:bbox_json,:coord_system,:quote_text,
               :element_kind,:element_ref,:source_type,:extraction_method,:review_status,
               :pdf_sha256,:pdf_path,:created_at)""", row)
        return row["evidence_id"]

    def get_evidence(self, evidence_id: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        return _evidence_out(r) if r else None

    def list_evidence(self, paper_id: Optional[str] = None, page: Optional[int] = None) -> list:
        q = "SELECT * FROM evidence WHERE 1=1"
        args: list = []
        if paper_id:
            q += " AND paper_id=?"
            args.append(paper_id)
        if page is not None:
            q += " AND page=?"
            args.append(page)
        q += " ORDER BY page, evidence_id"
        return [_evidence_out(r) for r in self.conn().execute(q, args)]

    def set_evidence_review_status(self, evidence_id: str, status: str) -> None:
        self.conn().execute("UPDATE evidence SET review_status=? WHERE evidence_id=?",
                            (status, evidence_id))
        self.conn().commit()

    # -- samples -----------------------------------------------------------
    def insert_sample(self, s: dict, evidence_refs: Optional[list] = None) -> str:
        self.conn().execute(
            """INSERT INTO samples(sample_id,paper_id,name,fields_json,notes,created_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(sample_id) DO UPDATE SET name=excluded.name,
               fields_json=excluded.fields_json, notes=excluded.notes""",
            (s["sample_id"], s["paper_id"], s.get("name"), jdump(s.get("fields", {})),
             s.get("notes", ""), s.get("created_at") or utc_now()))
        for eid, field_path in (evidence_refs or []):
            self.conn().execute(
                "INSERT OR IGNORE INTO sample_evidence(sample_id,evidence_id,field_path) VALUES(?,?,?)",
                (s["sample_id"], eid, field_path))
        self.conn().commit()
        return s["sample_id"]

    def get_sample(self, sample_id: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM samples WHERE sample_id=?", (sample_id,)).fetchone()
        return _sample_out(r) if r else None

    def list_samples(self, paper_id: str) -> list:
        rs = self.conn().execute("SELECT * FROM samples WHERE paper_id=? ORDER BY created_at",
                                 (paper_id,)).fetchall()
        return [_sample_out(r) for r in rs]

    def find_sample_by_name(self, paper_id: str, name: str) -> Optional[dict]:
        r = self.conn().execute(
            "SELECT * FROM samples WHERE paper_id=? AND lower(name)=lower(?) LIMIT 1",
            (paper_id, name)).fetchone()
        return _sample_out(r) if r else None

    # -- measurements ------------------------------------------------------
    def insert_measurement(self, m: dict, evidence_links: Optional[list] = None) -> str:
        row = {
            "measurement_id": m["measurement_id"], "paper_id": m["paper_id"],
            "sample_id": m.get("sample_id"), "run_id": m.get("run_id"),
            "quantity": m.get("quantity"), "value_raw": m.get("value_raw"),
            "unit_raw": m.get("unit_raw"), "value_std": m.get("value_std"),
            "unit_std": m.get("unit_std"), "std_method": m.get("std_method"),
            "std_null_reason": m.get("std_null_reason"),
            "conditions_json": jdump(m.get("conditions") or {}),
            "test_method": m.get("test_method"), "source_type": m.get("source_type", "text"),
            "extraction_method": m.get("extraction_method", ""),
            "status": m.get("status", "candidate"),
            "created_at": m.get("created_at") or utc_now(),
        }
        self.conn().execute(
            """INSERT INTO measurements(measurement_id,paper_id,sample_id,run_id,quantity,
               value_raw,unit_raw,value_std,unit_std,std_method,std_null_reason,conditions_json,
               test_method,source_type,extraction_method,status,created_at)
               VALUES(:measurement_id,:paper_id,:sample_id,:run_id,:quantity,:value_raw,:unit_raw,
               :value_std,:unit_std,:std_method,:std_null_reason,:conditions_json,:test_method,
               :source_type,:extraction_method,:status,:created_at)""", row)
        for eid, role in (evidence_links or []):
            self.conn().execute(
                """INSERT OR IGNORE INTO measurement_evidence(measurement_id,evidence_id,role)
                   VALUES(?,?,?)""", (row["measurement_id"], eid, role))
        self.conn().commit()
        return row["measurement_id"]

    def get_measurement(self, measurement_id: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM measurements WHERE measurement_id=?",
                                (measurement_id,)).fetchone()
        if not r:
            return None
        m = _measurement_out(r)
        evs = self.conn().execute(
            """SELECT e.*, me.role FROM measurement_evidence me
               JOIN evidence e ON e.evidence_id=me.evidence_id
               WHERE me.measurement_id=? ORDER BY me.role, e.page""", (measurement_id,)).fetchall()
        m["evidence"] = [dict(_evidence_out(e), role=e["role"]) for e in evs]
        return m

    def list_measurements(self, paper_id: Optional[str] = None, status: Optional[str] = None,
                          sample_id: Optional[str] = None, q: Optional[str] = None,
                          limit: int = 500) -> list:
        sql = "SELECT * FROM measurements WHERE 1=1"
        args: list = []
        if paper_id:
            sql += " AND paper_id=?"; args.append(paper_id)
        if status:
            sql += " AND status=?"; args.append(status)
        if sample_id:
            sql += " AND sample_id=?"; args.append(sample_id)
        if q:
            sql += " AND (quantity LIKE ? OR value_raw LIKE ? OR unit_raw LIKE ?)"
            like = "%" + q + "%"
            args += [like, like, like]
        sql += " ORDER BY created_at LIMIT ?"
        args.append(limit)
        out = []
        for r in self.conn().execute(sql, args):
            m = _measurement_out(r)
            n = self.conn().execute(
                "SELECT COUNT(*) FROM measurement_evidence WHERE measurement_id=?",
                (m["measurement_id"],)).fetchone()[0]
            m["n_evidence"] = n
            out.append(m)
        return out

    def measurements_for_evidence(self, evidence_id: str) -> list:
        rs = self.conn().execute(
            """SELECT m.* FROM measurements m JOIN measurement_evidence me
               ON me.measurement_id = m.measurement_id WHERE me.evidence_id=?""",
            (evidence_id,)).fetchall()
        return [_measurement_out(r) for r in rs]

    def set_measurement_status(self, measurement_id: str, status: str) -> None:
        self.conn().execute("UPDATE measurements SET status=? WHERE measurement_id=?",
                            (status, measurement_id))
        self.conn().commit()

    def update_measurement(self, measurement_id: str, **fields) -> None:
        if not fields:
            return
        if "conditions" in fields:
            fields["conditions_json"] = jdump(fields.pop("conditions"))
        sets = ", ".join(k + "=?" for k in fields)
        self.conn().execute("UPDATE measurements SET " + sets + " WHERE measurement_id=?",
                            (*fields.values(), measurement_id))
        self.conn().commit()

    def add_measurement_evidence(self, measurement_id: str, evidence_id: str,
                                 role: str = "supporting") -> None:
        self.conn().execute(
            """INSERT OR IGNORE INTO measurement_evidence(measurement_id,evidence_id,role)
               VALUES(?,?,?)""", (measurement_id, evidence_id, role))
        self.conn().commit()

    def delete_candidate_run_data(self, paper_id: str, extractor: str) -> int:
        """Delete candidate (never human-reviewed) measurements/samples/evidence from
        previous runs of the same extractor for this paper. Returns count deleted."""
        c = self.conn()
        runs = [r["run_id"] for r in c.execute(
            "SELECT run_id FROM extraction_runs WHERE paper_id=? AND extractor=?",
            (paper_id, extractor))]
        if not runs:
            return 0
        marks = ",".join("?" * len(runs))
        mids = [r["measurement_id"] for r in c.execute(
            "SELECT measurement_id FROM measurements WHERE run_id IN (" + marks +
            ") AND status='candidate'", runs)]
        for mid in mids:
            c.execute("DELETE FROM measurements WHERE measurement_id=?", (mid,))
        sids = [r["sample_id"] for r in c.execute(
            "SELECT sample_id FROM samples WHERE paper_id=?", (paper_id,))]
        for sid in sids:
            used = c.execute(
                "SELECT COUNT(*) FROM measurements WHERE sample_id=?", (sid,)).fetchone()[0]
            if used == 0:
                c.execute("DELETE FROM samples WHERE sample_id=?", (sid,))
        orphan = c.execute(
            """SELECT evidence_id FROM evidence WHERE paper_id=?
               AND evidence_id NOT IN (SELECT evidence_id FROM measurement_evidence)
               AND evidence_id NOT IN (SELECT evidence_id FROM sample_evidence)""",
            (paper_id,)).fetchall()
        for r in orphan:
            c.execute("DELETE FROM evidence WHERE evidence_id=?", (r["evidence_id"],))
        c.commit()
        return len(mids)

    # -- curves ------------------------------------------------------------
    def insert_curve(self, cv: dict) -> str:
        self.conn().execute(
            """INSERT INTO curves(curve_id,paper_id,page,figure_ref,image_path,image_sha256,
               region_bbox_json,region_source,calibration_json,method,series_name,
               verification_status,notes,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(curve_id) DO UPDATE SET page=excluded.page,
               figure_ref=excluded.figure_ref, image_path=excluded.image_path,
               image_sha256=excluded.image_sha256, region_bbox_json=excluded.region_bbox_json,
               region_source=excluded.region_source,
               calibration_json=excluded.calibration_json, method=excluded.method,
               series_name=excluded.series_name,
               verification_status=excluded.verification_status, notes=excluded.notes""",
            (cv["curve_id"], cv["paper_id"], cv.get("page"), cv.get("figure_ref", ""),
             cv.get("image_path", ""), cv.get("image_sha256", ""),
             jdump(cv.get("region_bbox")), cv.get("region_source"),
             jdump(cv.get("calibration")),
             cv.get("method", "manual"), cv.get("series_name", ""),
             cv.get("verification_status", "unverified"), cv.get("notes", ""),
             cv.get("created_at") or utc_now()))
        self.conn().commit()
        return cv["curve_id"]

    def update_curve(self, curve_id: str, **fields) -> None:
        if not fields:
            return
        if "calibration" in fields:
            fields["calibration_json"] = jdump(fields.pop("calibration"))
        if "region_bbox" in fields:
            fields["region_bbox_json"] = jdump(fields.pop("region_bbox"))
        sets = ", ".join(k + "=?" for k in fields)
        self.conn().execute("UPDATE curves SET " + sets + " WHERE curve_id=?",
                            (*fields.values(), curve_id))
        self.conn().commit()

    def get_curve(self, curve_id: str) -> Optional[dict]:
        r = self.conn().execute("SELECT * FROM curves WHERE curve_id=?", (curve_id,)).fetchone()
        return _curve_out(r) if r else None

    def list_curves(self, paper_id: Optional[str] = None) -> list:
        q = "SELECT * FROM curves"
        args: list = []
        if paper_id:
            q += " WHERE paper_id=?"
            args.append(paper_id)
        q += " ORDER BY created_at"
        return [_curve_out(r) for r in self.conn().execute(q, args)]

    def replace_curve_points(self, curve_id: str, points: list) -> int:
        c = self.conn()
        c.execute("DELETE FROM curve_points WHERE curve_id=?", (curve_id,))
        c.executemany(
            """INSERT INTO curve_points(curve_id,x_raw,y_raw,x_std,y_std,pixel_x,pixel_y,source)
               VALUES(?,?,?,?,?,?,?,?)""",
            [(curve_id, p.get("x_raw"), p.get("y_raw"), p.get("x_std"), p.get("y_std"),
              p.get("pixel_x"), p.get("pixel_y"), p.get("source", "manual")) for p in points])
        c.commit()
        return len(points)

    def list_curve_points(self, curve_id: str) -> list:
        return [dict(r) for r in self.conn().execute(
            "SELECT * FROM curve_points WHERE curve_id=? ORDER BY point_id", (curve_id,))]

    # -- reviews (append-only) ------------------------------------------------
    def add_review(self, paper_id: Optional[str], target_type: str, target_id: str,
                   action: str, old_value: Optional[dict] = None,
                   new_value: Optional[dict] = None, comment: str = "",
                   reviewer: str = "anonymous") -> int:
        c = self.conn()
        cur = c.execute(
            """INSERT INTO reviews(paper_id,target_type,target_id,action,old_value_json,
               new_value_json,comment,reviewer,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (paper_id, target_type, target_id, action, jdump(old_value), jdump(new_value),
             comment, reviewer, utc_now()))
        c.commit()
        return int(cur.lastrowid)

    def list_reviews(self, target_type: Optional[str] = None, target_id: Optional[str] = None,
                     paper_id: Optional[str] = None) -> list:
        q = "SELECT * FROM reviews WHERE 1=1"
        args: list = []
        if target_type:
            q += " AND target_type=?"; args.append(target_type)
        if target_id:
            q += " AND target_id=?"; args.append(target_id)
        if paper_id:
            q += " AND paper_id=?"; args.append(paper_id)
        q += " ORDER BY review_id DESC LIMIT 1000"
        return [dict(r, old_value=jload(r["old_value_json"]), new_value=jload(r["new_value_json"]))
                for r in self.conn().execute(q, args)]

    # -- learning cards -------------------------------------------------------
    def upsert_learning_card(self, card: dict) -> str:
        now = utc_now()
        self.conn().execute(
            """INSERT INTO learning_cards(card_id,paper_id,target_type,target_id,dimensions_json,
               ai_explanation,ai_source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(target_type,target_id) DO UPDATE SET
               dimensions_json=excluded.dimensions_json, ai_explanation=excluded.ai_explanation,
               ai_source=excluded.ai_source, updated_at=excluded.updated_at""",
            (card["card_id"], card.get("paper_id"), card["target_type"], card["target_id"],
             jdump(card.get("dimensions", {})), card.get("ai_explanation", ""),
             card.get("ai_source", "rule_template"), card.get("created_at") or now, now))
        self.conn().commit()
        return card["card_id"]

    def get_learning_card(self, target_type: str, target_id: str) -> Optional[dict]:
        r = self.conn().execute(
            "SELECT * FROM learning_cards WHERE target_type=? AND target_id=?",
            (target_type, target_id)).fetchone()
        return _card_out(r) if r else None

    def list_learning_cards(self, paper_id: Optional[str] = None) -> list:
        q = "SELECT * FROM learning_cards"
        args: list = []
        if paper_id:
            q += " WHERE paper_id=?"
            args.append(paper_id)
        return [_card_out(r) for r in self.conn().execute(q, args)]

    # -- qc flags ----------------------------------------------------------
    def insert_qc_flag(self, f: dict) -> int:
        c = self.conn()
        cur = c.execute(
            """INSERT INTO qc_flags(paper_id,target_type,target_id,check_code,severity,
               message,details_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (f.get("paper_id"), f["target_type"], f["target_id"], f["check_code"],
             f["severity"], f.get("message", ""), jdump(f.get("details")), utc_now()))
        c.commit()
        return int(cur.lastrowid)

    def clear_unresolved_flags(self, paper_id: str, codes=None) -> int:
        c = self.conn()
        if codes:
            marks = ",".join("?" * len(codes))
            cur = c.execute("DELETE FROM qc_flags WHERE paper_id=? AND resolved=0"
                            " AND check_code IN (" + marks + ")", (paper_id, *codes))
        else:
            cur = c.execute("DELETE FROM qc_flags WHERE paper_id=? AND resolved=0", (paper_id,))
        c.commit()
        return cur.rowcount

    def list_qc_flags(self, paper_id: Optional[str] = None, resolved: Optional[bool] = None,
                      target_type: Optional[str] = None, target_id: Optional[str] = None) -> list:
        q = "SELECT * FROM qc_flags WHERE 1=1"
        args: list = []
        if paper_id:
            q += " AND paper_id=?"; args.append(paper_id)
        if resolved is not None:
            q += " AND resolved=?"; args.append(1 if resolved else 0)
        if target_type:
            q += " AND target_type=?"; args.append(target_type)
        if target_id:
            q += " AND target_id=?"; args.append(target_id)
        q += " ORDER BY flag_id DESC"
        return [dict(r, details=jload(r["details_json"]), resolved=bool(r["resolved"]))
                for r in self.conn().execute(q, args)]

    def resolve_flag(self, flag_id: int, by: str) -> None:
        self.conn().execute(
            "UPDATE qc_flags SET resolved=1, resolved_by=?, resolved_at=? WHERE flag_id=?",
            (by, utc_now(), flag_id))
        self.conn().commit()

    # -- summaries -----------------------------------------------------------
    def paper_summary(self, paper_id: str) -> dict:
        c = self.conn()
        def one(sql, *a):
            return c.execute(sql, a).fetchone()[0]
        return {
            "n_samples": one("SELECT COUNT(*) FROM samples WHERE paper_id=?", paper_id),
            "n_measurements": one("SELECT COUNT(*) FROM measurements WHERE paper_id=?", paper_id),
            "n_evidence": one("SELECT COUNT(*) FROM evidence WHERE paper_id=?", paper_id),
            "n_curves": one("SELECT COUNT(*) FROM curves WHERE paper_id=?", paper_id),
            "n_open_flags": one("SELECT COUNT(*) FROM qc_flags WHERE paper_id=? AND resolved=0",
                                paper_id),
            "n_reviews": one("SELECT COUNT(*) FROM reviews WHERE paper_id=?", paper_id),
            "status_counts": {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) n FROM measurements WHERE paper_id=? GROUP BY status",
                (paper_id,))},
        }


def _paper_defaults() -> dict:
    return {"title": None, "authors": None, "doi": None, "arxiv_id": None, "n_pages": None,
            "pdf_kind": "unknown", "parse_status": "pending", "parse_json_path": None,
            "meta_json": "{}", "ingested_at": utc_now()}


def _paper_out(r) -> dict:
    d = dict(r)
    d["meta"] = jload(d.pop("meta_json", None)) or {}
    return d


def _evidence_out(r) -> dict:
    d = dict(r)
    d["bbox"] = jload(d.pop("bbox_json", None))
    return d


def _sample_out(r) -> dict:
    d = dict(r)
    d["fields"] = jload(d.pop("fields_json", None)) or {}
    return d


def _measurement_out(r) -> dict:
    d = dict(r)
    d["conditions"] = jload(d.pop("conditions_json", None)) or {}
    return d


def _curve_out(r) -> dict:
    d = dict(r)
    d["calibration"] = jload(d.pop("calibration_json", None))
    d["region_bbox"] = jload(d.pop("region_bbox_json", None))
    return d


def _card_out(r) -> dict:
    d = dict(r)
    d["dimensions"] = jload(d.pop("dimensions_json", None)) or {}
    return d


def open_db(path: Optional[str] = None, init: bool = True) -> DB:
    db = DB(path)
    if init:
        db.init_schema()
    return db
