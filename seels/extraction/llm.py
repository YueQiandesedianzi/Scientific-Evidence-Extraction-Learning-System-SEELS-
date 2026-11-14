"""LLM-based extractors with mandatory quote grounding.

Hard policy rules:
- An LLM item is accepted ONLY if its "quote" is located on a page of the
  parsed document (fuzzy match >= config.QUOTE_MATCH_MIN_SCORE). Otherwise the
  item is REJECTED and recorded with a reason - never stored as a value.
- LLM-reported confidence is kept in run stats for audit only; it never
  influences measurement status or verification (status changes require human
  review or deterministic QC checks).
- Missing/unparseable values yield uncertain records (null + reason).

Backends:
- llm:openai-compat  any OpenAI-compatible /chat/completions endpoint
                     (OpenAI, DeepSeek, vLLM, Ollama, LM Studio, ...)
- llm:langextract    google/langextract with char-interval grounding
                     (optional dependency; needs an LLM backend key/ollama)
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

from .. import config
from ..util import locate_quote_in_words, normalize_text
from .schema import LANGEXTRACT_EXAMPLES, LANGEXTRACT_PROMPT, SYSTEM_PROMPT
from .units import parse_value_unit, standardize

PAGE_MARK = "[[PAGE:%d]]"
_FENCE = chr(96) * 3  # code-fence marker, built without literal backticks


class ConfigError(Exception):
    pass


def build_pages_text(parse_doc: dict, max_chars: int = 20000):
    """Yield chunks: (list_of_page_nos, text) with page markers embedded."""
    chunks = []
    cur_pages, cur_parts, cur_len = [], [], 0
    for page in parse_doc.get("pages") or []:
        text = page.get("text") or ""
        if not text.strip():
            continue
        marker = "\n" + (PAGE_MARK % page["page_no"]) + "\n"
        if cur_len + len(text) + len(marker) > max_chars and cur_parts:
            chunks.append((cur_pages, "".join(cur_parts)))
            cur_pages, cur_parts, cur_len = [], [], 0
        cur_pages.append(page["page_no"])
        cur_parts.append(marker + text)
        cur_len += len(text) + len(marker)
    if cur_parts:
        chunks.append((cur_pages, "".join(cur_parts)))
    return chunks


def _parse_json_array(content: str):
    content = content.strip()
    fence_pat = "^" + _FENCE + r"(?:json)?\s*|\s*" + _FENCE + r"$"
    content = re.sub(fence_pat, "", content, flags=re.S)
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", content, flags=re.S)
        if not m:
            raise ValueError("no JSON found in model output")
        obj = json.loads(m.group(0))
    if isinstance(obj, dict):
        for k in ("items", "measurements", "data", "results"):
            if isinstance(obj.get(k), list):
                return obj[k]
        return [obj]
    if isinstance(obj, list):
        return obj
    raise ValueError("unexpected JSON shape")


def ground_items(items: list, parse_doc: dict, method: str) -> dict:
    """Ground raw LLM items against parsed pages; enforce the quote policy."""
    pages = {p["page_no"]: p for p in parse_doc.get("pages") or []}
    evidence: list = []
    measurements: list = []
    uncertain: list = []
    rejected: list = []
    seen = set()

    def add_ev(**kw) -> int:
        evidence.append(kw)
        return len(evidence) - 1

    for it in items:
        quote = normalize_text(str(it.get("quote") or ""))
        if not quote:
            rejected.append({**it, "reject_reason": "missing_quote"})
            continue
        page_no = it.get("page")
        located = None
        order = ([page_no] if page_no in pages else []) + \
                [p for p in sorted(pages) if p != page_no]
        for pno in order:
            pg = pages.get(pno)
            if not pg:
                continue
            bb, matched, score = locate_quote_in_words(
                pg.get("words") or [], quote, min_score=config.QUOTE_MATCH_MIN_SCORE)
            if bb is not None:
                located = (pno, bb, matched, score)
                break
        if located is None:
            rejected.append({**it, "reject_reason": "quote_not_grounded"})
            continue
        pno, bb, matched, score = located
        key = (pno, matched[:120], str(it.get("value_raw")))
        if key in seen:
            continue
        seen.add(key)
        val_raw = str(it.get("value_raw") or "")
        unit_raw = it.get("unit_raw") or None
        parsed = parse_value_unit((val_raw + " " + unit_raw).strip() if unit_raw else val_raw)
        ev = add_ev(page=pno, bbox=bb, quote_text=matched,
                    element_kind="text", element_ref="LLM-quote p%d [%g,%g]" % (pno, bb[0], bb[1]),
                    source_type="native_text", extraction_method=method)
        if parsed["value"] is None:
            uncertain.append({"field": it.get("quantity"), "reason": parsed["reason"],
                              "page": pno, "quote": matched,
                              "llm_value_raw": val_raw, "llm_confidence": it.get("confidence")})
            continue
        conds = {}
        craw = it.get("conditions_raw")
        if craw:
            page_text_norm = normalize_text(pages[pno].get("text") or "")
            if normalize_text(craw)[:60] in page_text_norm:
                cp = parse_value_unit(craw)
                conds["raw"] = {"value": cp["value"], "unit": cp["unit"],
                                "raw": normalize_text(craw),
                                "evidence_ids": [ev], "null_reason": cp["reason"]}
            else:
                conds["raw"] = {"value": None, "unit": None, "raw": craw,
                                "evidence_ids": [ev],
                                "null_reason": "conditions_quote_unverified"}
        std = standardize(parsed["value"], unit_raw or parsed["unit"])
        measurements.append({
            "quantity": normalize_text(str(it.get("quantity") or "")) or None,
            "value_raw": val_raw, "unit_raw": unit_raw,
            "value_std": std["value_std"], "unit_std": std["unit_std"],
            "std_method": std["method"], "std_null_reason": std["reason"],
            "conditions": conds,
            "test_method": (normalize_text(str(it["method_raw"]))[:60]
                            if it.get("method_raw") else None),
            "source_type": it.get("source_kind") or "text",
            "extraction_method": method,
            "sample_name": (normalize_text(str(it["sample"]))[:80] if it.get("sample") else None),
            "sample_null_reason": None if it.get("sample") else "llm_did_not_report_sample",
            "evidence_links": [(ev, "primary")],
            "value_uncertainty": parsed["uncertainty"],
            "approximate": parsed["approx"],
            "llm_meta": {"confidence": it.get("confidence"), "match_score": score,
                         "page_claimed": page_no, "page_grounded": pno},
        })
    return {"evidence": evidence, "samples": [], "measurements": measurements,
            "uncertain": uncertain,
            "stats": {"n_items": len(items), "n_grounded": len(measurements) + len(uncertain),
                      "n_rejected": len(rejected), "rejected": rejected[:50]}}


class BaseLLMExtractor:
    name = "llm-base"
    version = "0.1.0"
    kind = "llm"

    def __init__(self, cfg: dict = None):
        self.cfg = cfg or {}

    def extract(self, paper: dict, parse_doc: dict) -> dict:
        raise NotImplementedError


class OpenAICompatExtractor(BaseLLMExtractor):
    name = "llm:openai-compat"

    def __init__(self, cfg: dict = None):
        super().__init__(cfg)
        self.endpoint = self.cfg.get("endpoint") or "https://api.openai.com/v1/chat/completions"
        self.api_key = self.cfg.get("api_key")
        self.model = self.cfg.get("model") or "gpt-4o-mini"
        self.timeout = int(self.cfg.get("timeout", 120))
        self.max_chars = int(self.cfg.get("max_chars_per_call", 20000))
        self.max_retries = int(self.cfg.get("max_retries", 2))
        self.caller = self.cfg.get("caller")  # injection point for tests

    def _call(self, text: str) -> list:
        if self.caller is not None:
            return _parse_json_array(self.caller(self.model, text))
        body = {
            "model": self.model, "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": text}],
        }
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + (self.api_key or "")})
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                content = resp["choices"][0]["message"]["content"]
                return _parse_json_array(content)
            except Exception as e:
                last = e
                time.sleep(2 ** attempt)
        raise ConfigError("LLM call failed after retries: %r" % (last,))

    def extract(self, paper: dict, parse_doc: dict) -> dict:
        if not self.api_key and not self.caller:
            raise ConfigError("llm:openai-compat requires cfg.api_key (or test caller)")
        items = []
        for _pages, text in build_pages_text(parse_doc, self.max_chars):
            items.extend(self._call(text))
        return ground_items(items, parse_doc, self.name)


class LangExtractExtractor(BaseLLMExtractor):
    """Adapter for google/langextract (Apache-2.0).

    Uses langextract's char_interval grounding: source text is the concatenation
    of page texts with [[PAGE:n]] markers, so char offsets map back to pages;
    the quote is then re-grounded to word-level bboxes via the parse words.
    Requires: pip install langextract + a configured backend
    (LANGEXTRACT_API_KEY / OPENAI_API_KEY / local Ollama).
    """
    name = "llm:langextract"

    def __init__(self, cfg: dict = None):
        super().__init__(cfg)
        self.model_id = self.cfg.get("model_id") or "gemini-2.5-flash"
        self.model_url = self.cfg.get("model_url")  # e.g. http://localhost:11434 (Ollama)

    def _import(self):
        try:
            import langextract as lx
            return lx
        except ImportError as e:
            raise ConfigError("langextract not installed (pip install langextract): %r" % (e,))

    def extract(self, paper: dict, parse_doc: dict) -> dict:
        lx = self._import()
        page_marks = []  # (char_offset_after_marker, page_no)
        parts = []
        pos = 0
        for page in parse_doc.get("pages") or []:
            text = page.get("text") or ""
            if not text.strip():
                continue
            marker = "\n" + (PAGE_MARK % page["page_no"]) + "\n"
            parts.append(marker)
            pos += len(marker)
            parts.append(text)
            page_marks.append((pos, page["page_no"]))
            pos += len(text)
        source_text = "".join(parts)
        examples = [
            lx.data.ExampleData(text=e["text"], extraction=e["extraction"])
            for e in LANGEXTRACT_EXAMPLES
        ]
        kwargs = {"text_or_documents": source_text,
                  "prompt_description": LANGEXTRACT_PROMPT,
                  "examples": examples,
                  "model_id": self.model_id,
                  "max_char_buffer": int(self.cfg.get("max_char_buffer", 1800))}
        if self.model_url:
            kwargs["model_url"] = self.model_url
        result = lx.extract(**kwargs)

        def offset_to_page(off: int):
            page = None
            for start, pno in page_marks:
                if off >= start:
                    page = pno
                else:
                    break
            return page

        items = []
        unaligned = 0
        for ex in getattr(result, "extractions", []) or []:
            if getattr(ex, "char_interval", None) is None:
                unaligned += 1
                continue
            attrs = getattr(ex, "attributes", None) or {}
            items.append({
                "sample": attrs.get("sample"),
                "quantity": attrs.get("quantity") or getattr(ex, "extraction_class", None),
                "value_raw": attrs.get("value_raw") or getattr(ex, "extraction_text", ""),
                "unit_raw": attrs.get("unit_raw"),
                "conditions_raw": attrs.get("conditions_raw"),
                "method_raw": attrs.get("method_raw"),
                "quote": getattr(ex, "extraction_text", ""),
                "page": offset_to_page(ex.char_interval.start_pos),
            })
        out = ground_items(items, parse_doc, self.name)
        out["stats"]["langextract_unaligned"] = unaligned
        return out
