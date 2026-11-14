"""SEELS - Scientific Evidence Extraction & Learning System (MVP).

Design principles:
- Every extracted value must carry provenance (page + bbox + quote).
- Missing evidence yields null + reason, never a silently filled value.
- LLM self-reported confidence is NOT treated as correctness proof.
- Original PDFs are read-only; all derived data lives under the work dir.
"""

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0.0"
