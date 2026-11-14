"""Deterministic rule-based extractor (rules-v1).

Reads the parse.json produced by the parsing subsystem and extracts:
  - samples + identity fields from table label columns
  - measurements from table cells (header = quantity/unit, row = sample)
  - measurements from conservative text patterns (symbol/name = value unit)

Provenance discipline:
  - every emitted measurement carries >=1 evidence entry (page, bbox, quote);
  - evidence indices refer to result["evidence"]; the pipeline materializes ids;
  - anything ambiguous becomes an "uncertain" record: field + reason + quote,
    never a value;
  - raw value/unit are preserved; standardization is separate and may be null
    with its own reason.
"""
from __future__ import annotations

import re

from ..util import locate_quote_in_words, normalize_text
from .schema import QUANTITY_ALIASES, field_val
from .units import parse_value_unit, standardize, unit_key, UNITS

UNIT_PAREN_RE = re.compile(r"[\(\[]([^\)\]]{1,24})[\)\]]\s*$")
METHOD_RE = re.compile(
    r"(?:measured|determined|obtained|recorded|evaluated|performed)\s+"
    r"(?:by|using|with|via|on)\s+([A-Za-z0-9 \-,]{3,40})", re.IGNORECASE)
COND_RE = re.compile(
    r"\bat\s+(?:a\s+|an\s+)?(~?\d+(?:\.\d+)?\s*(?:K|k|\u00b0C|\u2103|T|mT|GPa|MPa|kPa|bar|atm|Torr|V|mV|A|mA|Hz|kHz|MHz|GHz|s|ms|min|h)"
    r"|room\s+temperature|RT\b|low\s+temperature|\d+\s?K\b)", re.IGNORECASE)
ATMOS_RE = re.compile(
    r"\b(?:under|in)\s+(vacuum|air|N2|nitrogen|Ar\b|argon|He\b|helium|O2|oxygen)", re.IGNORECASE)
FORMULA_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-\.\(\),= ]{1,40}$")

_VALUE = (r"[-+]?\d+(?:[.,]\d+)?"
          r"(?:\s*[\u00d7x]\s*10\s*\^?\s*[-+]?\d+"
          r"|[\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b]+"
          r"|[eE][-+]?\d+)?")
_UNIT = r"(?:%|[A-Za-z\u03a9\u2126\u00b0\u00b5\u03bc\u00c5][A-Za-z\u03a9\u2126\u00b0\u00b5\u03bc\u00c5/\.\^\-0-9]{0,12})"
_SYM = (r"(?:T\s?c|T\s?g|T\s?N|T\s?C|ZT|J\s?c|H\s?c2?|E\s?g|R\s?H|"
        r"\u03c3|\u03c1|\u03ba|\u03b7|\u03bc)")
_NAME = (r"(?:critical\s+temperature|superconducting\s+transition\s+temperature|"
         r"N[eé]el\s+temperature|glass\s+transition\s+temperature|Curie\s+temperature|"
         r"band\s?-?gap|Seebeck\s+coefficient|power\s+factor|thermal\s+conductivity|"
         r"electrical\s+conductivity|resistivity|power\s+conversion\s+efficiency|"
         r"coercivity|saturation\s+magnetization|specific\s+capacity|carrier\s+mobility|"
         r"critical\s+current\s+density|upper\s+critical\s+field|transition\s+temperature|"
         r"insertion\s+loss(?:es)?|extinction\s+ratio|quality\s+factor|responsivity|"
         r"threshold\s+voltage|turn-on\s+voltage|sheet\s+resistance|carrier\s+concentration|"
         r"specific\s+heat|heat\s+capacity|(?:gaussian\s+)?flux|emissivity|energy\s+density|"
         r"power\s+density|adhesion\s+energy|interface\s+energy|melting\s+point|"
         r"decomposition\s+temperature|on/off\s+ratio|efficiency)")

# (regex, kind): kind drives post-processing policy.
#   sym         curated symbol list, unit optional
#   name        quantity name + connector (+ up to 6 bridging words), unit optional
#   pce         percent-efficiency shorthand
#   name_eq     quantity name ... = value   (e.g. "thermal conductivity kSiC-BN =11.65 W/mK")
#   sym_generic any short symbol = value unit  (unit REQUIRED and must be known,
#               otherwise the hit becomes an uncertain record, not a measurement)
TEXT_PATTERNS = [
    (re.compile(r"(?P<q>" + _SYM + r")\s*(?:=|\u2248|~)\s*(?P<val>" + _VALUE + r")\s*(?P<unit>" + _UNIT + r")?"), "sym"),
    (re.compile(r"(?P<q>" + _NAME + r")\s+(?:of|is|was|were|=|:|\u2248|~|around|approximately|near|up\s+to|exceeding|reaching|about|below|above|as\s+high\s+as|as\s+low\s+as)\s+(?:[A-Za-z][A-Za-z0-9\-\.,()]*\s+){0,6}(?P<val>" + _VALUE + r")\s*(?P<unit>" + _UNIT + r")?", re.IGNORECASE), "name"),
    (re.compile(r"(?P<q>PCE|pce|efficiency)\s+(?:of|is|was|=|around)?\s*(?P<val>" + _VALUE + r")\s*(?P<unit>%)"), "pce"),
    (re.compile(r"(?P<q>" + _NAME + r")(?:\s+[A-Za-z\u0394\u03b1-\u03c9][A-Za-z0-9\u2032\u0394\u03b1-\u03c9\-\.,()']*){0,4}\s*(?:=|\u2248)\s*(?P<val>" + _VALUE + r")\s*(?P<unit>" + _UNIT + r")?", re.IGNORECASE), "name_eq"),
    (re.compile(r"(?<![A-Za-z0-9])(?P<q>[A-Za-z\u0394\u03b1-\u03c9]{1,3}[0-9]?)\s*(?:=|\u2248)\s*(?P<val>" + _VALUE + r")\s+(?P<unit>" + _UNIT + r")"), "sym_generic"),
]

SAMPLE_HEADER_HINTS = ("sample", "composition", "compound", "material", "specimen",
                       "formula", "alloy", "name", "id", "x", "content", "device", "film")
SAMPLE_LABEL_RE = re.compile(
    r"^(?:(?:sample|device|specimen|film|alloy|cell)\s*[-#:.]?\s*[A-Za-z0-9\-]{1,12}"
    r"|x\s*=\s*[-+0-9.,\s]+$)", re.IGNORECASE)


def _looks_like_unit(tok: str) -> bool:
    tok = (tok or "").strip()
    if not tok or len(tok) > 24:
        return False
    return unit_key(tok) in UNITS


def _split_header_unit(text: str):
    """'Conductivity (S/cm)' -> ('Conductivity', 'S/cm')"""
    if not text:
        return "", None
    m = UNIT_PAREN_RE.search(text)
    if m and _looks_like_unit(m.group(1)):
        return text[:m.start()].strip(), m.group(1).strip()
    return text.strip(), None


def _canon_quantity(text: str, ambiguous_symbol: bool = False) -> str:
    t = normalize_text(text or "").lower()
    t = t.replace("_", " ")
    for alias, canon in sorted(QUANTITY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if alias in ("s",):     # too ambiguous alone
            continue
        if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", t):
            return canon
    if ambiguous_symbol and t:
        return "symbol_%s_ambiguous" % t.replace(" ", "")
    return normalize_text(text or "") or None


def _is_formula(label: str) -> bool:
    s = normalize_text(label or "")
    if not s or len(s) < 3 or " " in s.strip():
        return False
    if not FORMULA_RE.match(s):
        return False
    has_upper = re.search(r"[A-Z]", s)
    has_digit = re.search(r"\d", s)
    return bool(has_upper and has_digit)


def _nearest_table_caption(page: dict, tbbox):
    best, best_d = None, 1e9
    for cap in page.get("captions") or []:
        if cap["kind"] != "table":
            continue
        cb = cap["bbox"]
        d = abs(cb[1] - tbbox[3]) if cb[1] >= tbbox[3] - 6 else abs(tbbox[1] - cb[3])
        # require some horizontal overlap
        ox = min(cb[2], tbbox[2]) - max(cb[0], tbbox[0])
        if ox < 20:
            d += 1000
        if d < best_d:
            best, best_d = cap, d
    return best


def _cell_bbox(table: dict, r: int, c: int):
    for cell in table.get("cells") or []:
        if cell["r"] == r and cell["c"] == c:
            return cell["bbox"]
    return None


def _split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.;:])\s+|\n+", text or "")
    return [p for p in parts if p and p.strip()]


class RuleExtractor:
    name = "rules-v1"
    version = "0.2.0"   # 0.2.0: +name_eq/sym_generic patterns, NFKC-normalized matching
    kind = "rules"

    def __init__(self, cfg: dict = None):
        cfg = cfg or {}
        self.max_table_items = int(cfg.get("max_table_items", 600))
        self.max_text_items = int(cfg.get("max_text_items", 300))
        self.text_enabled = bool(cfg.get("text_enabled", True))

    # ------------------------------------------------------------------
    def extract(self, paper: dict, parse_doc: dict) -> dict:
        evidence: list = []
        samples: dict = {}
        measurements: list = []
        uncertain: list = []
        seen = set()

        def add_ev(**kw) -> int:
            evidence.append(kw)
            return len(evidence) - 1

        ctx = {"evidence": evidence, "samples": samples, "measurements": measurements,
               "uncertain": uncertain, "seen": seen, "add_ev": add_ev,
               "n_table_items": 0, "n_text_items": 0}
        for page in parse_doc.get("pages") or []:
            st = page.get("status")
            if st == "failed":
                uncertain.append({"field": "page", "reason": "page_parse_failed",
                                  "page": page["page_no"], "quote": None})
                continue
            if st == "needs_ocr":
                uncertain.append({"field": "page", "reason": "page_needs_ocr_run_seels_ocr",
                                  "page": page["page_no"], "quote": None})
                continue
            self._extract_tables(page, ctx)
        if self.text_enabled:
            for page in parse_doc.get("pages") or []:
                if page.get("status") in ("failed", "needs_ocr"):
                    continue
                self._extract_text(page, ctx)
        stats = {
            "n_evidence": len(evidence), "n_samples": len(samples),
            "n_measurements": len(measurements), "n_uncertain": len(uncertain),
            "n_table_items": ctx["n_table_items"], "n_text_items": ctx["n_text_items"],
        }
        return {"evidence": evidence, "samples": list(samples.values()),
                "measurements": measurements, "uncertain": uncertain, "stats": stats}

    # ------------------------------------------------------------------
    def _register_sample(self, ctx, label: str, page_no: int, ev_idx: int, origin: str):
        name = normalize_text(label)
        if not name or len(name) > 80:
            return None
        key = name.lower()
        samples = ctx["samples"]
        if key in samples:
            return samples[key]["sample_id"]
        ident = {"name": field_val(value=name, raw=label, evidence_ids=[ev_idx])}
        if _is_formula(name):
            ident["formula"] = field_val(value=name, raw=label, evidence_ids=[ev_idx])
        else:
            ident["formula"] = field_val(null_reason="no_formula_pattern_detected")
        sid = "S-" + key[:24].replace(" ", "_")
        samples[key] = {"sample_id": sid, "name": name,
                        "fields": {"identity": ident, "composition": []},
                        "evidence_refs": [(ev_idx, "identity.name")],
                        "notes": "auto-registered from " + origin}
        return sid

    def _extract_tables(self, page: dict, ctx) -> None:
        page_no = page["page_no"]
        words = page.get("words") or []
        for t in page.get("tables") or []:
            rows = t.get("rows") or []
            if len(rows) < 2:
                continue
            ti = t["table_index"]
            n_cols = max(len(r) for r in rows)
            # ---- header detection (first 1-2 rows) ----
            header_rows = []
            for ri in range(min(2, len(rows) - 1)):
                cells = [c for c in (rows[ri] or []) if c and str(c).strip()]
                if not cells:
                    continue
                hits = 0
                for ctext in cells:
                    base, unit = _split_header_unit(str(ctext))
                    if unit or _canon_quantity(base) in QUANTITY_ALIASES.values() or \
                            normalize_text(base).lower() in SAMPLE_HEADER_HINTS:
                        hits += 1
                if hits >= max(1, len(cells) // 2):
                    header_rows.append(ri)
            if not header_rows:
                header_rows = [0]
            last_h = header_rows[-1]
            headers = []
            for c in range(n_cols):
                parts = []
                for ri in header_rows:
                    row = rows[ri] or []
                    if c < len(row) and row[c] and str(row[c]).strip():
                        parts.append(normalize_text(str(row[c])))
                headers.append(" ".join(dict.fromkeys(parts)))
            col_base = []
            col_unit = []
            for h in headers:
                b, u = _split_header_unit(h)
                col_base.append(b)
                col_unit.append(u)
            first_col_sample = (normalize_text(headers[0] if headers else "").lower()
                                in SAMPLE_HEADER_HINTS)
            # ---- table caption ----
            cap = _nearest_table_caption(page, t["bbox"])
            cap_ev = None
            cap_conditions = {}
            cap_method = None
            if cap:
                cap_ev = ctx["add_ev"](page=page_no, bbox=cap["bbox"], quote_text=cap["text"],
                                       element_kind="caption", element_ref=cap["ref"] + " (p%d)" % page_no,
                                       source_type="caption", extraction_method=self.name)
                cap_conditions = self._conditions_from_text(cap["text"], cap_ev)
                mm = METHOD_RE.search(cap["text"])
                if mm:
                    cap_method = normalize_text(mm.group(1))[:60]
            # ---- data rows ----
            for r in range(last_h + 1, len(rows)):
                row = rows[r] or []
                label = None
                label_c = None
                for c, cell in enumerate(row):
                    if cell and str(cell).strip():
                        label, label_c = normalize_text(str(cell)), c
                        break
                if label is None:
                    continue
                if self._label_is_sample(label, first_col_sample):
                    lb = _cell_bbox(t, r, label_c)
                    if lb:
                        lev = ctx["add_ev"](page=page_no, bbox=lb, quote_text=label,
                                            element_kind="table_cell",
                                            element_ref="Table#%d p%d [r%d,c%d] label" % (ti, page_no, r, label_c),
                                            source_type="table", extraction_method=self.name)
                        self._register_sample(ctx, label, page_no, lev,
                                              "Table#%d p%d row %d" % (ti, page_no, r))
                start_c = (label_c + 1) if self._label_is_sample(label, first_col_sample) else 0
                for c in range(start_c, len(row)):
                    cell = row[c]
                    if not cell or not str(cell).strip():
                        continue
                    cell_s = normalize_text(str(cell))
                    cb = _cell_bbox(t, r, c)
                    if cb is None:
                        ctx["uncertain"].append({"field": col_base[c] if c < len(col_base) else None,
                                                 "reason": "cell_geometry_missing",
                                                 "page": page_no, "quote": cell_s})
                        continue
                    parsed = parse_value_unit(cell_s)
                    unit_raw = parsed["unit"] or (col_unit[c] if c < len(col_unit) else None)
                    qty = _canon_quantity(col_base[c]) if c < len(col_base) else None
                    ev = ctx["add_ev"](page=page_no, bbox=cb, quote_text=cell_s,
                                       element_kind="table_cell",
                                       element_ref="Table#%d p%d [r%d,c%d]" % (ti, page_no, r, c),
                                       source_type="table", extraction_method=self.name)
                    links = [(ev, "primary")]
                    if c < len(headers) and headers[c]:
                        hb = _cell_bbox(t, header_rows[-1], c) or _cell_bbox(t, header_rows[0], c)
                        if hb:
                            hev = ctx["add_ev"](page=page_no, bbox=hb, quote_text=headers[c],
                                                element_kind="table_cell",
                                                element_ref="Table#%d p%d [header,c%d]" % (ti, page_no, c),
                                                source_type="table", extraction_method=self.name)
                            links.append((hev, "supporting"))
                    if cap_ev is not None:
                        links.append((cap_ev, "supporting"))
                    key = (page_no, ti, r, c, cell_s)
                    if key in ctx["seen"] or ctx["n_table_items"] >= self.max_table_items:
                        continue
                    ctx["seen"].add(key)
                    if parsed["value"] is None:
                        if re.search(r"\d", cell_s):
                            ctx["uncertain"].append({"field": qty, "reason": parsed["reason"],
                                                     "page": page_no, "quote": cell_s})
                        continue
                    std = standardize(parsed["value"], unit_raw)
                    m = {
                        "quantity": qty, "value_raw": cell_s, "unit_raw": unit_raw,
                        "value_std": std["value_std"], "unit_std": std["unit_std"],
                        "std_method": std["method"], "std_null_reason": std["reason"],
                        "conditions": cap_conditions,
                        "test_method": cap_method,
                        "source_type": "table", "extraction_method": self.name,
                        "sample_name": label if not self._cell_is_numeric(label) else None,
                        "evidence_links": links,
                        "value_uncertainty": parsed["uncertainty"],
                        "approximate": parsed["approx"],
                    }
                    if not qty:
                        ctx["uncertain"].append({"field": "quantity", "reason": "header_quantity_unresolved",
                                                 "page": page_no, "quote": headers[c] if c < len(headers) else None})
                    ctx["measurements"].append(m)
                    ctx["n_table_items"] += 1

    @staticmethod
    def _cell_is_numeric(label: str) -> bool:
        p = parse_value_unit(label)
        return p["value"] is not None and not p["unit"]

    @staticmethod
    def _label_is_sample(label: str, first_col_sample: bool) -> bool:
        if not label or RuleExtractor._cell_is_numeric(label):
            return False
        if _is_formula(label) or SAMPLE_LABEL_RE.match(label):
            return True
        return bool(first_col_sample) and len(label) <= 40

    def _conditions_from_text(self, text: str, ev_idx: int) -> dict:
        conds = {}
        m = COND_RE.search(text or "")
        if m:
            raw = normalize_text(m.group(1))
            if raw.lower() in ("room temperature", "rt"):
                conds["temperature"] = field_val(value=298.15, unit="K", raw=raw,
                                                 evidence_ids=[ev_idx])
                conds["temperature"]["note"] = "room_temperature_convention_298.15K"
            else:
                p = parse_value_unit(raw)
                conds["temperature" if p["unit"] in ("K", "k", "\u00b0C", "\u2103") else "condition"] = \
                    field_val(value=p["value"], unit=p["unit"], raw=raw,
                              evidence_ids=[ev_idx], null_reason=p["reason"])
        m2 = ATMOS_RE.search(text or "")
        if m2:
            conds["atmosphere"] = field_val(value=normalize_text(m2.group(1)), raw=m2.group(0),
                                            evidence_ids=[ev_idx])
        return conds

    # ------------------------------------------------------------------
    def _extract_text(self, page: dict, ctx) -> None:
        page_no = page["page_no"]
        src_type = "ocr" if page.get("status") == "ocr_done" else "native_text"
        # match on NFKC-normalized text so subscript/superscript glyph variants
        # (T\u2081 -> T1, \u209a -> p) unify with the word list used for grounding
        text = normalize_text(page.get("text") or "")
        if len(text.strip()) < 20:
            return
        sentences = _split_sentences(text)
        known_samples = {normalize_text(s["name"]).lower(): sid
                         for sid, s in ((v["sample_id"], v) for v in ctx["samples"].values())}
        for pat, kind in TEXT_PATTERNS:
            for m in pat.finditer(text):
                if ctx["n_text_items"] >= self.max_text_items:
                    ctx["uncertain"].append({"field": "text", "reason": "text_item_cap_reached",
                                             "page": page_no, "quote": None})
                    return
                quote = m.group(0)
                val_s = m.group("val")
                unit_s = m.groupdict().get("unit")
                key = (page_no, normalize_text(quote)[:120])
                if key in ctx["seen"]:
                    continue
                ctx["seen"].add(key)
                tail = text[m.end():m.end() + 12]
                if re.match(r"\s*(?:-|\u2013|\u2014|to)\s*[-+]?\d", tail):
                    ctx["uncertain"].append({
                        "field": m.group("q"), "reason": "range_not_single_value",
                        "page": page_no, "quote": normalize_text(quote + tail.strip())})
                    continue
                bbox, matched, score = locate_quote_in_words(page.get("words") or [], quote)
                if bbox is None:
                    ctx["uncertain"].append({"field": m.group("q"), "reason": "quote_not_locatable(score=%s)" % score,
                                             "page": page_no, "quote": quote})
                    continue
                parsed = parse_value_unit((val_s or "") + (" " + unit_s if unit_s else ""))
                if kind == "sym_generic":
                    # policy: a bare symbol only becomes data when its unit is known;
                    # otherwise it is an equation fragment we refuse to interpret
                    if not (parsed["unit"] and unit_key(parsed["unit"]) in UNITS):
                        ctx["uncertain"].append({
                            "field": m.group("q"), "reason": "generic_symbol_unknown_unit",
                            "page": page_no, "quote": quote})
                        continue
                ev = ctx["add_ev"](page=page_no, bbox=bbox, quote_text=matched or quote,
                                   element_kind="text",
                                   element_ref="Text p%d [%g,%g]" % (page_no, bbox[0], bbox[1]),
                                   source_type=src_type, extraction_method=self.name)
                sent = next((s for s in sentences if normalize_text(quote)[:40] in normalize_text(s)), quote)
                conds = self._conditions_from_text(sent, ev)
                mm = METHOD_RE.search(sent)
                sample_name = None
                low = normalize_text(sent).lower()
                for nm, sid in known_samples.items():
                    if nm and nm in low:
                        sample_name = nm
                        break
                if parsed["value"] is None:
                    ctx["uncertain"].append({"field": m.group("q"), "reason": parsed["reason"],
                                             "page": page_no, "quote": quote})
                    continue
                std = standardize(parsed["value"], parsed["unit"])
                qty = _canon_quantity(m.group("q"), ambiguous_symbol=(kind == "sym_generic"))
                tkey = (page_no, qty, parsed["value"], parsed["unit"])
                prev = ctx.get("text_index", {}).get(tkey)
                if prev is not None:
                    prev["evidence_links"].append((ev, "supporting"))
                    continue
                ctx["measurements"].append({
                    "quantity": qty, "value_raw": normalize_text(val_s),
                    "unit_raw": parsed["unit"],
                    "value_std": std["value_std"], "unit_std": std["unit_std"],
                    "std_method": std["method"], "std_null_reason": std["reason"],
                    "conditions": conds,
                    "test_method": normalize_text(mm.group(1))[:60] if mm else None,
                    "source_type": "text", "extraction_method": self.name,
                    "sample_name": sample_name,
                    "sample_null_reason": None if sample_name else "sample_not_identified_in_sentence",
                    "evidence_links": [(ev, "primary")],
                    "value_uncertainty": parsed["uncertainty"],
                    "approximate": parsed["approx"],
                })
                ctx.setdefault("text_index", {})[tkey] = ctx["measurements"][-1]
                ctx["n_text_items"] += 1
