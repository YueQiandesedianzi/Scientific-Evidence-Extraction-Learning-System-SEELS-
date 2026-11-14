"""Secondary parse engine + renderer: pypdfium2 (PDFium; BSD/Apache licensed).

Roles:
- fallback text/word extraction when the primary engine fails or looks garbled;
- page rendering to PNG (used by the review UI and curve modules);
- independent char count used by the native/scanned classification cross-check.

Coordinate conversion (empirically verified in scripts/_probe2.py):
  PdfTextPage.get_charbox(i) returns (left, bottom, right, top) in PDF
  user space with BOTTOM-LEFT origin. Conversion to canonical top-left-pts:
      top_tl    = page_height - top_bl
      bottom_tl = page_height - bottom_bl
"""
from __future__ import annotations

from .base import ParseEngine, find_captions_in_lines, group_words_into_lines
from ..util import garbled_ratio, normalize_text

_WS = {" ", "\t", "\r", "\n", "\x0c"}


class PdfiumEngine(ParseEngine):
    name = "pypdfium2"

    def open(self, path: str):
        import pypdfium2 as pdfium
        try:
            return pdfium.PdfDocument(path)
        except Exception as e:
            from .base import EngineError
            raise EngineError("pypdfium2 cannot open %s: %r" % (path, e))

    def n_pages(self, doc) -> int:
        return len(doc)

    def metadata(self, doc) -> dict:
        try:
            md = doc.get_metadata_dict() or {}
            return {k: v for k, v in md.items() if v}
        except Exception:
            return {}

    def parse_page(self, doc, index: int) -> dict:
        page = doc[index]
        width, height = page.get_size()
        tp = page.get_textpage()
        words: list = []
        warnings: list = []
        try:
            n = tp.count_chars()
            cur_chars: list = []
            cur_box = None

            def flush():
                nonlocal cur_chars, cur_box
                if cur_chars and cur_box is not None:
                    l, b, r, t = cur_box
                    words.append({"text": "".join(cur_chars),
                                  "x0": round(l, 2), "top": round(height - t, 2),
                                  "x1": round(r, 2), "bottom": round(height - b, 2)})
                cur_chars, cur_box = [], None

            for ci in range(n):
                ch = tp.get_text_range(ci, 1)
                if not ch or ch in _WS:
                    flush()
                    continue
                l, b, r, t = tp.get_charbox(ci)
                if cur_box is None:
                    cur_box = [l, b, r, t]
                else:
                    cur_box[0] = min(cur_box[0], l)
                    cur_box[1] = min(cur_box[1], b)
                    cur_box[2] = max(cur_box[2], r)
                    cur_box[3] = max(cur_box[3], t)
                cur_chars.append(ch)
            flush()
            text = normalize_text(tp.get_text_bounded() or "")
        except Exception as e:
            text = ""
            warnings.append("textpage extraction failed: %r" % (e,))
        finally:
            try:
                tp.close()
            except Exception:
                pass
        captions = find_captions_in_lines(group_words_into_lines(words))
        result = {
            "index": index, "page_no": index + 1,
            "width_pts": round(width, 2), "height_pts": round(height, 2),
            "engine": self.name, "status": "ok",
            "char_count": len(text.strip()),
            "garbled": round(garbled_ratio(text), 3),
            "words": words,
            "tables": [],       # fallback engine: layout tables not implemented in MVP
            "images": [],       # fallback engine: image objects not implemented in MVP
            "captions": captions,
            "text": text,
            "warnings": warnings + ["fallback engine: tables/images not extracted (MVP)"],
            "fallback": None,
        }
        try:
            page.close()
        except Exception:
            pass
        return result

    def render_page(self, doc, index: int, dpi: int = 144):
        """Render page to PIL RGB image. scale = dpi/72."""
        page = doc[index]
        try:
            bmp = page.render(scale=dpi / 72.0)
            img = bmp.to_pil()
            return img.convert("RGB")
        finally:
            try:
                page.close()
            except Exception:
                pass
