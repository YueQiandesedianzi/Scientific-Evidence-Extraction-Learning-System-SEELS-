"""Fetch a diversified open-access test corpus from arXiv for SEELS.

Downloads PDFs into data/papers/ and writes manifest.json (metadata + sha256).
Originals are never modified afterwards; the ingest pipeline only reads them.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "data" / "papers"
OUT.mkdir(parents=True, exist_ok=True)

ATOM = "{http://www.w3.org/2005/Atom}"

QUERIES = [
    ("cat:cond-mat.mtrl-sci", 6),
    ("cat:physics.app-ph", 2),
    ("cat:cond-mat.supr-con", 2),
    ("cat:cond-mat.mes-hall", 2),
]

UA = "SEELS-MVP-test-corpus/0.1 (research prototype)"


def fetch_listing(query: str, n: int) -> bytes:
    url = ("http://export.arxiv.org/api/query?search_query="
           + urllib.parse.quote(query)
           + "&start=0&max_results=" + str(n)
           + "&sortBy=submittedDate&sortOrder=descending")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:80]


def main() -> None:
    manifest = []
    existing = {p.name for p in OUT.glob("*.pdf")}
    for query, n in QUERIES:
        xmlb = fetch_listing(query, n)
        root = ET.fromstring(xmlb)
        for entry in root.findall(ATOM + "entry"):
            aid = (entry.findtext(ATOM + "id", "") or "").strip()
            raw_title = (entry.findtext(ATOM + "title", "") or "").strip()
            title = re.sub(r"\s+", " ", raw_title)
            m = re.search(r"abs/(.+?)(v\d+)?$", aid)
            short = m.group(1) if m else aid.rsplit("/", 1)[-1]
            pdf_url = "https://arxiv.org/pdf/" + short
            fname = "arxiv_" + safe_name(short) + ".pdf"
            rec = {
                "arxiv_id": short,
                "title": title,
                "category": query,
                "abs_url": aid,
                "pdf_url": pdf_url,
                "file": fname,
            }
            if fname in existing:
                rec["status"] = "already_downloaded"
            else:
                try:
                    req = urllib.request.Request(pdf_url, headers={"User-Agent": UA})
                    with urllib.request.urlopen(req, timeout=120) as r:
                        data = r.read()
                    if not data.startswith(b"%PDF"):
                        rec["status"] = "not_a_pdf (" + str(len(data)) + " bytes)"
                    else:
                        (OUT / fname).write_bytes(data)
                        rec["status"] = "ok"
                        rec["bytes"] = len(data)
                        rec["sha256"] = hashlib.sha256(data).hexdigest()
                except Exception as e:
                    rec["status"] = "error: " + repr(e)
            manifest.append(rec)
            print(rec["arxiv_id"], "|", rec["status"], "|", title[:70])
            time.sleep(3)
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in manifest if r["status"] in ("ok", "already_downloaded"))
    print("DONE " + str(ok) + "/" + str(len(manifest)) + " papers")


if __name__ == "__main__":
    main()
