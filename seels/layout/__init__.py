"""Layout analysis subsystem (block-level figure/table regions).

Providers run heavy engines out-of-process (see docling_provider) and produce
cached sidecar JSON (schema seels.layout/1.0) keyed by the PDF sha256.
"""
