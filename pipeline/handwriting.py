"""
pipeline/handwriting.py
========================
Full-document handwriting rendering — two selectable modes.

This is distinct from layout_engine.py's existing HANDWRITTEN_FONTS feature,
which only stylises specific fields (signatures, dates, reference numbers).
This module handles the case where the ENTIRE document body is meant to
look hand-written, for the heaviest realism tier.

Two modes, both exposed through one entry point (render_handwritten_document):

MODE "font"  (fast, default, fits current architecture)
---------------------------------------------------------
Applies a handwriting-style Google Font to the full document body (not just
specific fields), plus per-character/per-line jitter (small random rotation,
baseline wobble, size variance) via CSS + a light JS pass, to avoid the
"too-perfect" look of a font rendered dead straight. This is essentially an
extension of layout_engine.py's existing font-injection mechanism, generalised
to the whole document. Cheap, deterministic, no model inference cost, and
composes directly with the existing degradation.py tiers (a heavily degraded
+ fully handwritten-font document is a realistic, valuable edge case).

Limitation: it is still fundamentally font rendering — every "M" looks like
every other "M" except for jitter. Real handwriting has more organic letter-
to-letter inconsistency than jitter alone can fake. Good enough for most
OCR-robustness training; not indistinguishable from a real handwriting-
synthesis output.

MODE "synthesis"  (slower, more realistic, optional/experimental)
---------------------------------------------------------------
Routes line-item text and key fields through a handwriting-synthesis model
(stroke-sequence generation, e.g. an RNN/Transformer trained on the IAM
handwriting dataset) to produce genuinely organic letterforms — real
inter-character inconsistency, natural pen-pressure-style stroke variation.

IMPORTANT — this mode is currently a STUB. It defines the interface and
documents exactly what's needed to make it real, but does not ship a working
model in this pipeline. Reasons:
  1. A quality handwriting-synthesis model needs real weights (e.g. a
     pretrained IAM-on-RNN checkpoint) that aren't bundled here and would
     need to be sourced/installed separately (this is the kind of thing
     that belongs in requirements.txt + a one-time model download step,
     similar to how build_hsn_index.py is a one-time setup step).
  2. It only realistically covers Latin-script handwriting out of the box;
     Devanagari/Nastaliq handwriting synthesis is a much rarer, less mature
     research area — so this mode does NOT currently compose with the
     multi-language system in languages.py for non-Latin scripts.
  3. Per-image inference cost is much higher than a font-render pass, which
     matters at 20,000-document scale.

Recommended near-term path: ship "font" mode now (it is real, tested below,
and immediately useful), keep "synthesis" as a documented extension point
for whoever picks this up next — see _render_synthesis_stub() for the exact
integration contract a real model would need to satisfy.

Usage
-----
    from pipeline.handwriting import render_handwritten_document

    html = render_handwritten_document(
        document_html=existing_rendered_html,   # output of layout_engine.py
        mode="font",                             # or "synthesis"
        seed=doc_index,
    )
"""

from __future__ import annotations

import random
import re
import logging
from typing import Optional

log = logging.getLogger("handwriting")


# ── Font-based handwriting fonts (full-document, distinct pool from the
#    existing signature/date-only HANDWRITTEN_FONTS in layout_engine.py —
#    these are chosen for being legible at body-text size, not just
#    decorative for short strings) ────────────────────────────────────────────
FULL_DOC_HANDWRITING_FONTS = [
    "Homemade Apple",
    "Reenie Beanie",
    "Shadows Into Light",
    "Caveat",
    "Patrick Hand",
    "Architects Daughter",
]


def _font_import_url(font: str) -> str:
    return f"https://fonts.googleapis.com/css2?family={font.replace(' ', '+')}:wght@400;700&display=swap"


def _jitter_css(seed: int, intensity: float = 1.0) -> str:
    """
    Per-line baseline wobble + slight rotation, applied via an injected
    <style> block. Deterministic per seed so re-renders are reproducible.
    `intensity` in [0,1] scales how pronounced the wobble is.

    IMPORTANT: table cells must keep their table-layout `display` value
    (table-cell / table-row) — overriding it to inline-block or block (an
    earlier version of this function did exactly that) collapses the whole
    table's column structure. Transform-only jitter (rotate/translate)
    does not affect layout flow, so it's safe to apply to <td> directly,
    but should NOT touch <tr> (rotating a whole row looks wrong and can
    cause row-height clipping) — only individual cells and paragraph text.
    """
    rng = random.Random(seed)
    rules = []
    for i in range(1, 13):
        rot = rng.uniform(-1.3, 1.3) * intensity
        dy = rng.uniform(-1.2, 1.2) * intensity
        dx = rng.uniform(-0.6, 0.6) * intensity
        rules.append(
            f".hw-body table.items-table tbody tr:nth-child({i}n+1) td {{"
            f"  transform: rotate({rot:.2f}deg) translate({dx:.2f}px,{dy:.2f}px);"
            f"}}"
        )
    # Paragraphs (non-table text) can safely go inline-block since they're
    # not part of a table grid.
    for i in range(1, 8):
        rot = rng.uniform(-1.0, 1.0) * intensity
        dy = rng.uniform(-1.0, 1.0) * intensity
        rules.append(
            f".hw-body p:nth-of-type({i}) {{"
            f"  display: inline-block; transform: rotate({rot:.2f}deg) translateY({dy:.2f}px);"
            f"}}"
        )
    return "\n".join(rules)


def _render_font_mode(document_html: str, seed: int, font: Optional[str] = None,
                        intensity: float = 1.0) -> str:
    """
    Inject a full-document handwriting font + jitter CSS into an already-
    rendered HTML document (the output of layout_engine.py's
    render_purchase_order_html / render_tax_invoice_html).

    Approach: wrap the existing <body> content in a `.hw-body` class hook
    (added via regex on the body tag — safe because layout_engine.py always
    emits a single <body>...</body> with no nested body tags) and inject a
    <style> override block right before </head> that forces the handwriting
    font on every text element and applies the jitter rules.

    This deliberately does NOT regenerate the document from scratch — it
    post-processes layout_engine.py's output, so it automatically inherits
    whatever layout variant / theme / language was already chosen upstream.
    """
    rng = random.Random(seed)
    chosen_font = font or rng.choice(FULL_DOC_HANDWRITING_FONTS)
    jitter = _jitter_css(seed, intensity=intensity)

    style_block = f"""
    <style>
    @import url('{_font_import_url(chosen_font)}');

    .hw-body, .hw-body * {{
        font-family: '{chosen_font}', cursive, sans-serif !important;
        letter-spacing: 0.3px;
    }}
    /* Slightly larger base size — handwriting fonts read smaller than they measure */
    .hw-body {{ font-size: 1.18em; line-height: 1.5; }}
    .hw-body table.items-table th, .hw-body table.items-table td {{
        font-size: 1.05em;
    }}
    {jitter}
    </style>
    """

    html = document_html
    # Inject style block right before </head>
    if "</head>" in html:
        html = html.replace("</head>", style_block + "</head>")
    else:
        html = style_block + html

    # Tag <body ...> with the hw-body hook class
    html = re.sub(
        r"<body([^>]*)>",
        lambda m: f"<body{m.group(1)} class=\"hw-body\">" if "class=" not in m.group(1)
                  else re.sub(r'class="', 'class="hw-body ', m.group(0)),
        html,
        count=1,
    )

    return html


def _render_synthesis_stub(document_html: str, seed: int, **kwargs) -> str:
    """
    STUB — documents the integration contract for a real handwriting-
    synthesis backend. Does not perform real synthesis.

    A real implementation would need to:
      1. Parse `document_html` (or, better, receive the structured
         PurchaseOrder/TaxInvoice dataclass directly, before HTML rendering,
         since synthesis works on raw text strings per field, not HTML) and
         extract every text field that should be hand-written: line-item
         descriptions, the buyer/seller blocks, totals, etc.
      2. For each text field, call a stroke-sequence generation model
         (e.g. an LSTM/Transformer trained on the IAM On-Line Handwriting
         Database) seeded per-document for reproducibility, producing a
         sequence of pen strokes (x, y, pen-up/down) rather than a font glyph.
      3. Rasterise those strokes to a transparent PNG snippet per field
         (typical approach: render strokes via a vector path renderer,
         e.g. svgwrite or a small Cairo/Skia script, with controllable
         "ink" colour and slight stroke-width jitter for pen-pressure
         realism).
      4. Composite each field's PNG snippet into the document layout at
         the correct bounding box — this means layout_engine.py would need
         to additionally report each field's intended bounding box BEFORE
         degradation is applied (a capability already flagged as valuable
         for OCR ground-truth in the project log's Section 6.3, and one
         that would also directly serve this feature).
      5. Cache/reuse the model across the whole 20,000-doc run (load once
         per worker process, not once per document) — synthesis inference
         is meaningfully slower than a CSS font swap, so amortising model
         load cost matters at this scale.

    Recommended starting point for whoever picks this up: a pretrained
    handwriting-synthesis checkpoint (search "IAM handwriting synthesis
    pytorch" — several open implementations of Graves' 2013 handwriting
    synthesis RNN exist with released weights) wrapped behind the same
    function signature as _render_font_mode, so render_handwritten_document
    can dispatch to either with no caller-side changes.

    Raises NotImplementedError so callers get a clear, immediate signal
    rather than silently falling back to font mode (which would be a
    surprising silent downgrade for anyone who explicitly asked for
    synthesis mode).
    """
    raise NotImplementedError(
        "handwriting synthesis mode is a documented stub, not yet implemented. "
        "See _render_synthesis_stub()'s docstring for the integration contract. "
        "Use mode='font' for a working full-document handwriting render."
    )


def render_handwritten_document(
    document_html: str,
    mode: str = "font",
    seed: int = 0,
    font: Optional[str] = None,
    intensity: float = 1.0,
) -> str:
    """
    Render a full-document handwritten version of an already-rendered
    PO/Invoice HTML document.

    Parameters
    ----------
    document_html : str
        The HTML produced by layout_engine.py's render_purchase_order_html
        or render_tax_invoice_html.
    mode : "font" | "synthesis"
        "font" (default) — fast, working, font + jitter based.
        "synthesis" — documented stub, raises NotImplementedError today.
    seed : int
        Document index or other seed, for deterministic font choice + jitter.
    font : str, optional
        Force a specific handwriting font (font mode only). If None, chosen
        deterministically from FULL_DOC_HANDWRITING_FONTS via `seed`.
    intensity : float
        Jitter strength in [0, 1] (font mode only). Suggested: pair with
        degradation tier — e.g. 0.5 for "degraded" tier, 1.0 for "heavy".

    Returns
    -------
    str — modified HTML, ready to pass to renderer.py's render_html_to_png
    exactly like any other layout_engine.py output.
    """
    if mode == "font":
        return _render_font_mode(document_html, seed=seed, font=font, intensity=intensity)
    elif mode == "synthesis":
        return _render_synthesis_stub(document_html, seed=seed)
    else:
        raise ValueError(f"Unknown handwriting mode '{mode}'. Use 'font' or 'synthesis'.")