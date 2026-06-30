"""
pipeline/pdf_degradation.py
============================
PDF Visual Degradation — rasterize / degrade / rebuild.

Why this exists
----------------
degradation.py's degrade_image() is a pixel-array (OpenCV) pipeline: it
cv2.imdecode()'s a PNG, runs noise/stains/crumple/rotation/jpeg-recompress,
and cv2.imencode()'s back to PNG. That is fundamentally incompatible with
a Playwright-generated PDF, which is vector/text content, not pixels —
there is nothing for OpenCV to operate on. This is *why* the PDF output
path in assembler.py / renderer.py currently produces zero degradation
even when a "degraded" or "heavy" tier is assigned: render_html_to_pdf()
writes Playwright's page.pdf() bytes straight to disk, and degrade_image()
is never called for PDFs at all (only for the PNG branch).

This module closes that gap WITHOUT inventing a second degradation system:

    PDF bytes
        │  (1) rasterize each page to a high-res RGB image (PyMuPDF)
        ▼
    page images (PNG bytes, one per page)
        │  (2) run the EXISTING degrade_image() from degradation.py,
        │      same tier/profile/seed logic as the PNG path
        ▼
    degraded page images
        │  (3) re-embed each degraded page image as a full-bleed page
        │      in a new PDF (PyMuPDF)
        ▼
    degraded PDF bytes

This means a "heavy" tier PDF and a "heavy" tier PNG of the same doc_index
go through the identical degrade_image() call (same profile, same noise/
stain/crumple parameters) — they will look consistent with each other,
just one is a scanned-looking raster-in-pdf and the other is a raw image.

Multi-page documents (26-50 line item invoices spanning 2-4 A4 pages) are
handled by degrading every page independently with a derived-but-distinct
seed per page, so page 2 of a "heavy" doc doesn't look like a duplicate
of page 1 — real multi-page scans don't degrade identically page to page
(different crumple, different stain placement, etc).

Dependency
----------
Requires PyMuPDF:
    pip install pymupdf

PyMuPDF is used instead of pdf2image/Wand because it has no external
binary dependency (no poppler/imagemagick install needed) — it's a single
pip package, which matters for reproducing this pipeline on a fresh machine
the same way build_hsn_index.py's "one dependency, no network" philosophy
does for the HSN index.

Usage
-----
    from pipeline.pdf_degradation import degrade_pdf

    degraded_pdf_bytes = degrade_pdf(
        pdf_bytes,        # raw bytes from render_html_to_pdf()
        doc_index,
        tier="heavy",      # "clean" | "degraded" | "heavy" | None (derives via assign_tier)
    )

Clean tier
----------
For tier == "clean", this still rasterizes and re-embeds (so output is
always a consistent raster-in-pdf format across tiers, which matters if
downstream OCR training expects uniform PDF structure) but degrade_image()
internally no-ops almost everything for the "pristine"/"clean_laser"/
"high_res_clean" profiles (near-zero noise, zero stain/crumple/downscale).
If you'd rather keep clean-tier PDFs as pure vector text (smaller files,
perfect text layer, no rasterization at all), pass skip_clean_rasterize=True
— see degrade_pdf()'s docstring below.
"""

from __future__ import annotations

import io
import logging
from typing import Optional

log = logging.getLogger("pdf_degradation")

try:
    import fitz  # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False
    log.warning("PyMuPDF (fitz) not installed — PDF degradation will be skipped. "
                "Run: pip install pymupdf")

# Reuse the EXISTING degradation pipeline — no second implementation.
from pipeline.degradation import degrade_image, assign_tier, get_degradation_metadata

# Rasterization DPI. 200 DPI is a good balance: high enough that degradation
# artifacts (noise, low-ink streaks, stains) look natural at print resolution,
# without ballooning file size/render time the way 300+ DPI would across a
# 4-page heavy-tier invoice. renderer.py's PNG path effectively targets
# ~150dpi equivalent (794x1123 @ scale=2); 200dpi here is intentionally a
# bit higher since PDF pages are full A4 and tend to read more text-dense.
RASTER_DPI = 200


def _rasterize_pdf_pages(pdf_bytes: bytes, dpi: int = RASTER_DPI) -> list[bytes]:
    """
    Render every page of a PDF to PNG bytes using PyMuPDF.

    Returns a list of PNG byte-strings, one per page, in page order.
    """
    pages: list[bytes] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        zoom = dpi / 72.0  # PDF base unit is 72 dpi
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(pix.tobytes("png"))
    finally:
        doc.close()
    return pages


def _rebuild_pdf_from_pages(page_png_list: list[bytes]) -> bytes:
    """
    Build a new PDF where each page is a single full-bleed image, sized to
    match the original A4 page (so margins/print dimensions are preserved —
    important since renderer.py's render_html_to_pdf() sets all margins to
    0mm specifically so content fills the page edge-to-edge).
    """
    out_doc = fitz.open()
    try:
        for png_bytes in page_png_list:
            img_doc = fitz.open(stream=png_bytes, filetype="png")
            try:
                img_page = img_doc[0]
                img_rect = img_page.rect  # pixel-space rect from the raster
                # A4 in points: 595.28 x 841.89. We reproduce A4 exactly so
                # the degraded PDF has the same physical page size as the
                # clean one — only the pixel content inside changes.
                a4_rect = fitz.paper_rect("a4")
                new_page = out_doc.new_page(width=a4_rect.width, height=a4_rect.height)
                new_page.insert_image(a4_rect, stream=png_bytes)
            finally:
                img_doc.close()
        return out_doc.tobytes()
    finally:
        out_doc.close()


def degrade_pdf(
    pdf_bytes: bytes,
    doc_index: int,
    tier: Optional[str] = None,
    apply_degradation: bool = True,
    skip_clean_rasterize: bool = False,
) -> bytes:
    """
    Apply the SAME tiered degradation pipeline used for PNGs to a PDF, by
    rasterizing each page, running degradation.degrade_image() on it, and
    rebuilding a new PDF from the degraded page images.

    Parameters
    ----------
    pdf_bytes             : raw PDF bytes from renderer.render_html_to_pdf()
    doc_index             : document index — used as the degradation RNG
                             seed, same convention as the PNG path
                             (idx*2 for PO, idx*2+1 for invoice — pass
                             whichever seed you used for the matching PNG
                             so tiers/profiles are consistent across formats)
    tier                   : "clean" | "degraded" | "heavy" | None.
                             If None, derived via degradation.assign_tier().
    apply_degradation      : if False, returns pdf_bytes unchanged (mirrors
                             degrade_image()'s same-named parameter)
    skip_clean_rasterize   : if True AND tier == "clean", skip rasterization
                             entirely and return the original vector PDF
                             unchanged (smaller file, perfect text layer).
                             Default False so clean/degraded/heavy PDFs all
                             share the same raster-in-pdf structure.

    Returns
    -------
    PDF bytes — same page count and A4 page size as the input, with each
    page's content replaced by its degraded raster image.
    """
    if not apply_degradation or not pdf_bytes:
        return pdf_bytes

    if not FITZ_AVAILABLE:
        log.warning(f"[{doc_index}] PyMuPDF unavailable — returning undegraded PDF")
        return pdf_bytes

    if tier is None:
        tier = assign_tier(doc_index)

    if tier == "clean" and skip_clean_rasterize:
        return pdf_bytes

    try:
        raw_pages = _rasterize_pdf_pages(pdf_bytes)
    except Exception as e:
        log.error(f"[{doc_index}] PDF rasterization failed: {e} — returning undegraded PDF")
        return pdf_bytes

    if not raw_pages:
        log.warning(f"[{doc_index}] PDF had no pages after rasterization")
        return pdf_bytes

    degraded_pages: list[bytes] = []
    for page_num, raw_png in enumerate(raw_pages):
        # Distinct-but-derived seed per page: page 2 of a multi-page heavy
        # invoice should NOT look like an identical clone of page 1 (real
        # multi-page scans vary stain placement/crumple page to page), but
        # it must still be deterministic for a given (doc_index, tier) so
        # re-runs are reproducible.
        page_seed = doc_index * 1000 + page_num
        try:
            degraded = degrade_image(
                raw_png, page_seed, tier=tier, apply_degradation=True
            )
        except Exception as e:
            log.error(f"[{doc_index}] page {page_num} degradation failed: {e} — using raw page")
            degraded = raw_png
        degraded_pages.append(degraded)

    try:
        return _rebuild_pdf_from_pages(degraded_pages)
    except Exception as e:
        log.error(f"[{doc_index}] PDF rebuild failed: {e} — returning undegraded PDF")
        return pdf_bytes


def get_pdf_degradation_metadata(doc_index: int, tier: Optional[str] = None, num_pages: int = 1) -> dict:
    """
    Return per-page degradation metadata for a PDF — same shape as
    degradation.get_degradation_metadata(), but one entry per page since
    each page gets its own derived seed (see degrade_pdf() docstring).
    Useful for logging to database.py alongside the existing PNG metadata.
    """
    if tier is None:
        tier = assign_tier(doc_index)
    return {
        "tier": tier,
        "pages": [
            get_degradation_metadata(doc_index * 1000 + p, tier=tier)
            for p in range(num_pages)
        ],
    }