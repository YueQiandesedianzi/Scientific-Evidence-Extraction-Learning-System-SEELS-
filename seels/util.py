"""Small shared helpers: ids, hashing, time, json, quote locating."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:12]


def sha256_file(path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jdump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def jload(s):
    return json.loads(s) if s else None


_WS = re.compile(r"\s+")
_PUNCT = re.compile("[\u2010-\u2015\u2212]")  # unicode dashes/minus -> ascii hyphen


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = _PUNCT.sub("-", s)
    s = s.replace("\u00a0", " ")
    return _WS.sub(" ", s).strip()


def norm_tokens(s: str) -> list:
    return normalize_text(s).split(" ")


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def union_bbox(items: Iterable[dict]) -> Optional[list]:
    boxes = [(i["x0"], i["top"], i["x1"], i["bottom"]) for i in items
             if None not in (i.get("x0"), i.get("top"), i.get("x1"), i.get("bottom"))]
    if not boxes:
        return None
    return [round(min(b[0] for b in boxes), 2),
            round(min(b[1] for b in boxes), 2),
            round(max(b[2] for b in boxes), 2),
            round(max(b[3] for b in boxes), 2)]


def bbox_iou(a, b) -> float:
    """Intersection-over-union of two [x0,y0,x1,y1] boxes."""
    if not a or not b:
        return 0.0
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(0.0, (a[2] - a[0])) * max(0.0, (a[3] - a[1]))
    area_b = max(0.0, (b[2] - b[0])) * max(0.0, (b[3] - b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def locate_quote_in_words(words: Sequence[dict], quote: str, min_score: float = 0.90):
    """Locate a quote inside a page's word list (word: text,x0,top,x1,bottom).

    Returns (bbox, matched_text, score); bbox is [x0,y0,x1,y1] in top-left-pts.
    Returns (None, None, best_score) when the quote cannot be grounded, so
    callers can record null + reason instead of inventing a location.
    """
    if not words or not quote:
        return None, None, 0.0
    q = norm_tokens(quote)
    if not q:
        return None, None, 0.0
    wtxt = [normalize_text(w["text"]) for w in words]
    n, m = len(wtxt), len(q)
    target = " ".join(q)
    best_score, best = 0.0, None
    for start in range(n):
        for extra in (0, 1, 2):
            end = start + m + extra
            if end > n:
                continue
            cand = " ".join(wtxt[start:end])
            if abs(len(cand) - len(target)) > max(6, int(0.3 * len(target))):
                continue
            sc = similarity(cand, target)
            if sc > best_score:
                best_score, best = sc, (start, end)
        if best_score >= 0.999:
            break
    if best is None or best_score < min_score:
        return None, None, round(best_score, 3)
    start, end = best
    bb = union_bbox(words[start:end])
    matched = " ".join(w["text"] for w in words[start:end])
    return bb, matched, round(best_score, 3)


def garbled_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        o = ord(ch)
        if ch in ("\ufffd", "\uf8ff", "\u25a1") or (0xE000 <= o <= 0xF8FF):
            bad += 1
    return bad / len(text)


def rel_to(path, root) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve())).replace("\\", "/")
    except ValueError:
        return str(path)
