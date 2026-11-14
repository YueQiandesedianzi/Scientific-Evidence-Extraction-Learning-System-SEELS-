"""API-level tests of the review UI backend (in-process server, ephemeral port)."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from seels.ui import server as srv


@pytest.fixture()
def client(synth_ingested, monkeypatch):
    db, pid = synth_ingested
    monkeypatch.setattr(srv, "_DB", db)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    yield base, db, pid
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=60) as r:
        ct = r.headers.get("content-type", "")
        body = r.read()
        return r.status, (json.loads(body) if "json" in ct else body)


def _post(base, path, obj):
    req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read())


def test_core_endpoints(client):
    base, db, pid = client
    s, papers = _get(base, "/api/papers")
    assert s == 200 and any(p["paper_id"] == pid for p in papers)
    s, info = _get(base, "/api/paper?id=" + pid)
    assert info["paper"]["pdf_kind"] == "native" and len(info["pages"]) == 2
    s, ms = _get(base, "/api/measurements?paper_id=" + pid)
    assert s == 200 and len(ms) >= 8
    s, img = _get(base, "/api/page_image?paper_id=%s&page=1&dpi=72" % pid)
    assert img[:8] == b"\x89PNG\r\n\x1a\n"
    s, evs = _get(base, "/api/page_evidence?paper_id=%s&page=2" % pid)
    assert s == 200 and evs
    s, m = _get(base, "/api/measurement?id=" + ms[0]["measurement_id"])
    assert s == 200 and m["evidence"]
    # evidence -> measurement reverse lookup
    evid = evs[0]["evidence_id"]
    s, linked = _get(base, "/api/measurements_by_evidence?evidence_id=" + evid)
    assert s == 200


def test_review_flow_persists(client):
    base, db, pid = client
    s, ms = _get(base, "/api/measurements?paper_id=" + pid)
    mid = ms[0]["measurement_id"]
    s, r = _post(base, "/api/review", {"target_type": "measurement", "target_id": mid,
                                       "action": "accept", "reviewer": "tester",
                                       "comment": "looks right"})
    assert s == 200 and r["review_id"]
    s, m = _get(base, "/api/measurement?id=" + mid)
    assert m["status"] == "accepted"
    assert m["reviews"][0]["comment"] == "looks right"
    assert m["evidence"][0]["review_status"] == "accepted"
    # modify recomputes standardization and keeps audit trail
    s, r = _post(base, "/api/review", {"target_type": "measurement", "target_id": mid,
                                       "action": "modify", "reviewer": "tester",
                                       "new_value": {"value_raw": "25", "unit_raw": "\u00b0C"},
                                       "comment": "corrected"})
    s, m = _get(base, "/api/measurement?id=" + mid)
    assert m["value_raw"] == "25" and m["unit_raw"] == "\u00b0C"
    assert abs(m["value_std"] - 298.15) < 1e-9 and m["unit_std"] == "K"
    assert m["status"] == "accepted"
    actions = [rv["action"] for rv in m["reviews"]]
    assert actions == ["modify", "accept"]          # append-only trail
    assert m["reviews"][0]["old_value"]["value_raw"] != "25"
    # reject + uncertain
    _post(base, "/api/review", {"target_type": "measurement", "target_id": mid,
                                "action": "reject", "reviewer": "tester"})
    s, m = _get(base, "/api/measurement?id=" + mid)
    assert m["status"] == "rejected"
    # learning card reflects the review state
    _post(base, "/api/cards", {"paper_id": pid, "limit": 5})
    s, card = _get(base, "/api/cards?target_type=measurement&target_id=" + mid)
    assert card and card["dimensions"]["reliability_and_limits"]["content"]


def test_pipeline_endpoints(client):
    base, db, pid = client
    s, r = _post(base, "/api/figures", {"paper_id": pid})
    assert s == 200 and r["n"] >= 1
    s, curves = _get(base, "/api/curves?paper_id=" + pid)
    assert curves and curves[0]["figure_ref"] == "Figure 1"
    s, r = _post(base, "/api/qc", {"paper_id": pid})
    assert s == 200 and "by_check" in r
    s, flags = _get(base, "/api/flags?paper_id=" + pid + "&resolved=0")
    assert isinstance(flags, list)
    s, r = _get(base, "/api/export?fmt=csv&paper_id=" + pid)
    assert r["files"]
