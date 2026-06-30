"""
pipeline/layout_engine.py
=========================
Block-Based Floating Layout Engine — Language-Integrated Edition

render_purchase_order_html(po, lang_cfg=None)
render_tax_invoice_html(inv, lang_cfg=None)

If lang_cfg (a pipeline.languages.LanguageConfig) is passed:
  - All field labels, column headers, terminology pools, and summary labels
    come from lang_cfg.labels rather than hardcoded English strings.
  - RTL scripts (Urdu, Arabic, etc.) get dir="rtl" on the document body
    and <span dir="ltr"> wrapping around all numeric/code fields so that
    numbers and HSN codes stay left-to-right inside a right-to-left layout,
    matching real-world mixed-direction invoice practice.
  - Font family and import URL come from lang_cfg, replacing the default
    system-sans fallback.

If lang_cfg is None, the renderer behaves exactly as before (English only,
no change to existing test or bootstrap behaviour).
"""

import random
import html
from typing import Any, Optional

from .data_models import PurchaseOrder, TaxInvoice, LineItem

# ── Google Fonts (handwritten — signatures/dates/ref numbers only) ─────────
HANDWRITTEN_FONTS = ["Caveat", "Dancing Script", "Kalam", "Patrick Hand"]

# ── Corporate colour themes ──────────────────────────────────────────────────
THEMES = [
    {"primary": "#1a3c5e", "accent": "#e8f0f7", "header_text": "#ffffff", "border": "#2d6a9f"},
    {"primary": "#2e7d32", "accent": "#f1f8e9", "header_text": "#ffffff", "border": "#43a047"},
    {"primary": "#4a148c", "accent": "#f3e5f5", "header_text": "#ffffff", "border": "#7b1fa2"},
    {"primary": "#bf360c", "accent": "#fbe9e7", "header_text": "#ffffff", "border": "#d84315"},
    {"primary": "#37474f", "accent": "#eceff1", "header_text": "#ffffff", "border": "#546e7a"},
    {"primary": "#006064", "accent": "#e0f7fa", "header_text": "#ffffff", "border": "#00838f"},
    {"primary": "#1b0000", "accent": "#fff8f0", "header_text": "#f5deb3", "border": "#8b4513"},
    {"primary": "#263238", "accent": "#f5f5f5", "header_text": "#b0bec5", "border": "#455a64"},
]

BLOCK_ORDERS = [
    ["seller", "buyer", "shipping", "meta", "items", "summary", "footer"],
    ["meta", "seller", "buyer", "items", "shipping", "summary", "footer"],
    ["buyer", "seller", "meta", "shipping", "items", "summary", "footer"],
    ["seller", "meta", "buyer", "items", "summary", "shipping", "footer"],
    ["meta", "buyer", "seller", "shipping", "items", "summary", "footer"],
    ["seller", "buyer", "meta", "items", "shipping", "summary", "footer"],
    ["buyer", "meta", "seller", "items", "summary", "shipping", "footer"],
    ["meta", "seller", "shipping", "buyer", "items", "summary", "footer"],
]

STAMP_TEXTS_EN = ["ORIGINAL", "VERIFIED", "APPROVED", "TAX INVOICE", "PAID", "PROCESSED"]


def _esc(s: Any) -> str:
    return html.escape(str(s))


def _money(v: float, currency_symbol: str = "₹") -> str:
    return f"{currency_symbol}{v:,.2f}"


def _ltr(s: Any, is_rtl: bool) -> str:
    """Wrap a string in a LTR span when the document direction is RTL.
    Numbers, codes, monetary amounts and reference numbers must stay LTR
    even inside RTL documents — this matches real mixed-direction invoice
    practice (confirmed for Urdu/Arabic business documents)."""
    if is_rtl:
        return f'<span dir="ltr">{_esc(s)}</span>'
    return _esc(s)


def _stamp_svg(text: str, opacity: float = 0.18, rotation: int = -25) -> str:
    return f"""
<div style="position:absolute; top:{random.randint(30,55)}%; left:{random.randint(35,55)}%;
     transform: rotate({rotation}deg); opacity:{opacity:.2f};
     pointer-events:none; z-index:100;">
<svg width="220" height="90" xmlns="http://www.w3.org/2000/svg">
  <rect x="4" y="4" width="212" height="82" rx="8" ry="8" fill="none" stroke="#cc0000" stroke-width="4"/>
  <rect x="10" y="10" width="200" height="70" rx="5" ry="5" fill="none" stroke="#cc0000" stroke-width="1.5"/>
  <text x="110" y="56" font-family="Arial Black, sans-serif" font-size="22" font-weight="900"
        text-anchor="middle" fill="#cc0000" letter-spacing="3">{text}</text>
</svg>
</div>"""


def _base_css(theme: dict, hand_font: str, body_font: str, font_import_url: str, direction: str) -> str:
    extra_import = f"@import url('{font_import_url}');" if font_import_url else ""
    return f"""
@import url('https://fonts.googleapis.com/css2?family={hand_font.replace(" ", "+")}:wght@400;700&display=swap');
{extra_import}
*, *::before, ::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: '{body_font}', 'Segoe UI', 'Arial', sans-serif;
    font-size: 12px; color: #1a1a1a; background: #f4f4f4; padding: 20px;
    direction: {direction};
}}
.document {{
    background: #ffffff; width: 794px; min-height: 1123px; margin: 0 auto;
    padding: 32px 36px; position: relative;
    box-shadow: 0 2px 12px rgba(0,0,0,0.15); overflow: hidden;
    direction: {direction};
}}
.doc-header {{
    background: {theme["primary"]}; color: {theme["header_text"]};
    padding: 18px 24px; margin: -32px -36px 24px -36px;
    display: flex; justify-content: space-between; align-items: center;
}}
.doc-title {{ font-size: 22px; font-weight: 700; letter-spacing: 1px; }}
.doc-id-block {{ text-align: {"left" if direction == "rtl" else "right"}; font-size: 11px; }}
.doc-id-block .ref {{ font-size: 15px; font-weight: 700; margin-top: 4px; }}
.blocks-row {{ display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 16px; }}
.block {{
    background: {theme["accent"]}; border: 1px solid {theme["border"]};
    border-radius: 4px; padding: 12px 14px; flex: 1 1 220px; min-width: 180px;
}}
.block-title {{
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.8px; color: {theme["primary"]};
    border-bottom: 1.5px solid {theme["border"]}; padding-bottom: 5px; margin-bottom: 8px;
}}
.block-val {{ font-size: 12px; line-height: 1.6; }}
.block-val .company {{ font-weight: 700; font-size: 13px; margin-bottom: 2px; }}
.gstin-tag {{
    display: inline-block; background: {theme["primary"]}; color: {theme["header_text"]};
    font-size: 9px; padding: 1px 6px; border-radius: 3px; margin-top: 4px;
    letter-spacing: 0.5px; font-weight: 600;
}}
table.items-table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 11px; }}
table.items-table thead tr {{ background: {theme["primary"]}; color: {theme["header_text"]}; }}
table.items-table thead th {{ padding: 8px 10px; text-align: left; font-weight: 600; letter-spacing: 0.3px; }}
table.items-table thead th.num {{ text-align: right; }}
table.items-table tbody tr:nth-child(even) {{ background: {theme["accent"]}; }}
table.items-table tbody td {{ padding: 7px 10px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }}
table.items-table tbody td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.summary-block {{
    margin-{"right" if direction == "rtl" else "left"}: auto; width: 320px;
    border: 1px solid {theme["border"]}; border-radius: 4px; overflow: hidden;
    font-size: 12px; margin-bottom: 20px;
}}
.summary-row {{ display: flex; justify-content: space-between; padding: 6px 14px; border-bottom: 1px solid #e8e8e8; }}
.summary-row:last-child {{ border-bottom: none; }}
.summary-row.total {{ background: {theme["primary"]}; color: {theme["header_text"]}; font-weight: 700; font-size: 13px; padding: 9px 14px; }}
.summary-row.subtotal {{ background: {theme["accent"]}; font-weight: 600; }}
.footer-block {{ display: flex; justify-content: space-between; align-items: flex-end; margin-top: 24px; padding-top: 14px; border-top: 1.5px solid {theme["border"]}; }}
.payment-terms {{ font-size: 11px; color: #444; max-width: 320px; }}
.signature-area {{ text-align: {"left" if direction == "rtl" else "right"}; font-size: 11px; }}
.sig-line {{ border-top: 1px solid #333; width: 180px; margin: 40px 0 4px {"0 auto" if direction == "rtl" else "auto"}; }}
.handwritten {{ font-family: '{hand_font}', cursive; }}
.stamp-container {{ position: absolute; top: 0; left: 0; right: 0; bottom: 0; pointer-events: none; }}

/* ── PDF / print pagination ────────────────────────────────────────────────
   When Playwright renders to PDF (page.pdf()), the browser applies these
   rules and flows content across as many A4 pages as needed.
   - .document loses its fixed 794px width and fills the A4 page width.
   - The doc-header is "position:relative" in print so it stays in flow and
     is repeated if the browser decides to break before it (unlikely but safe).
   - Items table rows don't break mid-row.
   - Summary and footer blocks are kept together (avoid-break-inside). */
@media print {{
    body {{ background: #fff; padding: 0; }}
    .document {{
        width: 100%; min-height: unset; box-shadow: none;
        padding: 20px 28px; margin: 0;
    }}
    .doc-header {{ margin: -20px -28px 20px -28px; position: relative; }}
    table.items-table tbody tr {{ page-break-inside: avoid; }}
    .summary-block {{ page-break-inside: avoid; }}
    .footer-block {{ page-break-inside: avoid; page-break-before: auto; }}
    .stamp-container {{ display: none; }}
    @page {{ size: A4; margin: 12mm 14mm; }}
}}
"""


# ── Helper: resolve labels from lang_cfg or fall back to English defaults ─────

def _L(lang_cfg, key: str, default: str) -> str:
    """Get a single label string from lang_cfg, or return the English default."""
    if lang_cfg is None:
        return default
    return lang_cfg.labels.get(key, default)


def _LP(lang_cfg, key: str, default: list) -> list:
    """Get a pool (list) from lang_cfg, or return the English default."""
    if lang_cfg is None:
        return default
    val = lang_cfg.labels.get(key, default)
    return val if isinstance(val, list) else [val]


def _term(lang_cfg, pool_key: str, idx: int, english_value: str) -> str:
    """
    Resolve a "floating terminology" choice (buyer_term, sign_off, etc.) to
    its translated equivalent for the active language.

    `english_value` is the ground-truth English string already stored on
    the PurchaseOrder/TaxInvoice dataclass (data_models.py) — used as the
    fallback if lang_cfg is None, the pool key is missing, or the pool is
    shorter than `idx` (e.g. an older doc generated before a pool was
    extended). This is what actually fixes documents that were "partly"
    translated: previously these fields were rendered directly with no
    lang_cfg lookup at all, no matter what language was requested.
    """
    if lang_cfg is None:
        return english_value
    pool = lang_cfg.labels.get(pool_key)
    if not isinstance(pool, list) or idx >= len(pool) or idx < 0:
        return english_value
    return pool[idx]


# ─────────────────────────────────────────────────────────────────────────────
# PURCHASE ORDER RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def render_purchase_order_html(po: PurchaseOrder, lang_cfg=None) -> str:
    rng = random.Random(po.doc_index * 999 + 1)
    theme     = THEMES[po.layout_variant % len(THEMES)]
    hand_font = rng.choice(HANDWRITTEN_FONTS)
    block_order = BLOCK_ORDERS[po.layout_variant]
    add_stamp   = rng.random() < 0.55

    # Language-aware values
    direction   = getattr(lang_cfg, "direction", "ltr") if lang_cfg else "ltr"
    is_rtl      = direction == "rtl"
    body_font   = getattr(lang_cfg, "font_family", "Segoe UI") if lang_cfg else "Segoe UI"
    font_url    = getattr(lang_cfg, "font_import_url", "") if lang_cfg else ""
    currency    = getattr(lang_cfg, "currency_symbol", "₹") if lang_cfg else "₹"

    stamp_texts = _LP(lang_cfg, "stamp_texts", STAMP_TEXTS_EN)
    stamp_text  = rng.choice(stamp_texts)
    stamp_rot   = rng.randint(-35, -10)

    doc_title   = _L(lang_cfg, "doc_title_po", "PURCHASE ORDER")
    col_desc    = _L(lang_cfg, "col_description", "Description")
    col_hsn     = _L(lang_cfg, "col_hsn_sac", "HSN/SAC")
    col_unit    = _L(lang_cfg, "col_unit", "Unit")
    col_qty     = _L(lang_cfg, "col_qty", "Qty")
    col_price   = _L(lang_cfg, "col_unit_price", "Unit Price")
    col_total   = _L(lang_cfg, "col_total", "Total")
    lbl_subtotal = _L(lang_cfg, "subtotal", "Sub-Total")
    lbl_grand   = _L(lang_cfg, "grand_total", "GRAND TOTAL")
    lbl_meta_title   = _L(lang_cfg, "meta_title_po", "Order Details")
    lbl_payment_terms = _L(lang_cfg, "payment_terms", "Payment Terms")
    lbl_po_value     = _L(lang_cfg, "po_value_excl_gst", "PO Value (excl. GST)")
    lbl_gst_note     = _L(lang_cfg, "gst_note", "GST applicable as per Tax Invoice")
    lbl_footer_notice    = _L(lang_cfg, "footer_po_notice", "This is a computer-generated Purchase Order.")
    lbl_footer_subject_to = _L(lang_cfg, "footer_po_subject_to", "Subject to terms and conditions of")

    def _m(v): return _money(v, currency)
    def _c(s): return _ltr(s, is_rtl)

    def seller_block():
        addr = po.seller_address
        return f"""
<div class="block">
  <div class="block-title">{_esc(_term(lang_cfg, "seller_terms", po.seller_term_idx, po.seller_term))}</div>
  <div class="block-val">
    <div class="company">{_esc(po.seller_name)}</div>
    <div>{_esc(addr["line1"])}</div>
    <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_c(addr["pin"])}</div>
    <div>{_esc(addr["country"])}</div>
    <span class="gstin-tag">GSTIN: {_c(po.seller_gstin)}</span>
  </div>
</div>"""

    def buyer_block():
        addr = po.buyer_address
        return f"""
<div class="block">
  <div class="block-title">{_esc(_term(lang_cfg, "buyer_terms", po.buyer_term_idx, po.buyer_term))}</div>
  <div class="block-val">
    <div class="company">{_esc(po.buyer_name)}</div>
    <div>{_esc(addr["line1"])}</div>
    <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_c(addr["pin"])}</div>
    <div>{_esc(addr["country"])}</div>
    <span class="gstin-tag">GSTIN: {_c(po.buyer_gstin)}</span>
  </div>
</div>"""

    def shipping_block():
        addr = po.shipping_address
        return f"""
<div class="block">
  <div class="block-title">{_esc(_term(lang_cfg, "ship_terms", po.ship_term_idx, po.ship_term))}</div>
  <div class="block-val">
    <div>{_esc(addr["line1"])}</div>
    <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_c(addr["pin"])}</div>
    <div>{_esc(addr["country"])}</div>
  </div>
</div>"""

    def meta_block():
        return f"""
<div class="block" style="flex: 0 1 200px;">
  <div class="block-title">{_esc(lbl_meta_title)}</div>
  <div class="block-val">
    <div><b>{_esc(_term(lang_cfg, "po_num_terms", po.po_num_term_idx, po.po_num_term))}:</b></div>
    <div class="handwritten" style="font-size:1.1em; color:#1a3c5e; margin:2px 0 8px;">
      {_c(po.po_number)}
    </div>
    <div><b>{_esc(_term(lang_cfg, "date_terms", po.date_term_idx, po.date_term))}:</b></div>
    <div class="handwritten">{_c(po.po_date)}</div>
    <div style="margin-top:8px;"><b>{_esc(lbl_payment_terms)}:</b></div>
    <div>{_esc(_term(lang_cfg, "payment_terms_pool", po.payment_terms_idx, po.payment_terms))}</div>
  </div>
</div>"""

    def items_block():
        rows = ""
        for i, li in enumerate(po.line_items, 1):
            rows += f"""
<tr>
  <td>{_c(i)}</td>
  <td>
    <div style="font-weight:600;">{_esc(li.description)}</div>
    <div style="color:#666; font-size:10px;">{col_hsn}: {_c(li.hsn_code)} | {_esc(li.category)}</div>
  </td>
  <td class="num">{_c(li.unit)}</td>
  <td class="num">{_c(f'{li.quantity:,}')}</td>
  <td class="num">{_c(_m(li.unit_cost_inr))}</td>
  <td class="num">{_c(_m(li.line_total))}</td>
</tr>"""
        return f"""
<table class="items-table">
  <thead>
    <tr>
      <th style="width:30px;">#</th>
      <th>{col_desc}</th>
      <th class="num" style="width:50px;">{col_unit}</th>
      <th class="num" style="width:60px;">{col_qty}</th>
      <th class="num" style="width:100px;">{col_price}</th>
      <th class="num" style="width:100px;">{col_total}</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""

    def summary_block():
        return f"""
<div class="summary-block">
  <div class="summary-row subtotal">
    <span>{lbl_subtotal}</span><span>{_c(_m(po.subtotal))}</span>
  </div>
  <div class="summary-row">
    <span style="color:#888; font-size:11px;">{_esc(lbl_gst_note)}</span>
  </div>
  <div class="summary-row total">
    <span>{lbl_po_value}</span><span>{_c(_m(po.subtotal))}</span>
  </div>
</div>"""

    def footer_block():
        sig_names = ["A. Kumar", "P. Sharma", "R. Singh", "S. Mehta", "V. Patel"]
        return f"""
<div class="footer-block">
  <div class="payment-terms">
    <b>{_esc(lbl_payment_terms)}:</b> {_esc(_term(lang_cfg, "payment_terms_pool", po.payment_terms_idx, po.payment_terms))}<br/>
    <span style="color:#777;">{_esc(lbl_footer_notice)}
    {_esc(lbl_footer_subject_to)} {_esc(po.buyer_name)}.</span>
  </div>
  <div class="signature-area">
    <div class="sig-line"></div>
    <div class="handwritten" style="font-size:1.3em; color:#1a3c5e;">
      {_esc(rng.choice(sig_names))}
    </div>
    <div style="font-size:10px; color:#555;">{_esc(_term(lang_cfg, "sign_offs", po.sign_off_idx, po.sign_off))}</div>
    <div style="font-size:10px; color:#555;">{_esc(po.buyer_name)}</div>
    <div class="handwritten" style="font-size:0.95em; color:#666;">{_c(po.po_date)}</div>
  </div>
</div>"""

    BLOCK_RENDERERS = {
        "seller": seller_block, "buyer": buyer_block, "shipping": shipping_block,
        "meta": meta_block, "items": items_block, "summary": summary_block, "footer": footer_block,
    }
    FULL_WIDTH_KEYS = {"items", "summary", "footer"}

    rows_html   = ""
    current_row = []
    for key in block_order:
        if key in FULL_WIDTH_KEYS:
            if current_row:
                rows_html += f'<div class="blocks-row">{"".join(current_row)}</div>'
                current_row = []
            rows_html += BLOCK_RENDERERS[key]()
        else:
            current_row.append(BLOCK_RENDERERS[key]())
    if current_row:
        rows_html += f'<div class="blocks-row">{"".join(current_row)}</div>'

    stamp_html = _stamp_svg(stamp_text, opacity=rng.uniform(0.12, 0.25), rotation=stamp_rot) if add_stamp else ""

    return f"""<!DOCTYPE html>
<html lang="{getattr(lang_cfg, 'code', 'en') if lang_cfg else 'en'}" dir="{direction}">
<head>
  <meta charset="UTF-8"/>
  <title>Purchase Order {_esc(po.po_number)}</title>
  <style>{_base_css(theme, hand_font, body_font, font_url, direction)}</style>
</head>
<body>
<div class="document">
  <div class="doc-header">
    <div class="doc-title">{doc_title}</div>
    <div class="doc-id-block">
      <div>{_esc(_term(lang_cfg, "po_num_terms", po.po_num_term_idx, po.po_num_term))}</div>
      <div class="ref">{_c(po.po_number)}</div>
      <div style="margin-top:4px;">{_esc(_term(lang_cfg, "date_terms", po.date_term_idx, po.date_term))}: {_c(po.po_date)}</div>
    </div>
  </div>
  {rows_html}
  <div class="stamp-container">{stamp_html}</div>
</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# TAX INVOICE RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def render_tax_invoice_html(inv: TaxInvoice, lang_cfg=None) -> str:
    rng = random.Random(inv.doc_index * 999 + 2)
    theme       = THEMES[(inv.layout_variant + 2) % len(THEMES)]
    hand_font   = rng.choice(HANDWRITTEN_FONTS)
    block_order = BLOCK_ORDERS[(inv.layout_variant + 1) % len(BLOCK_ORDERS)]
    add_stamp   = rng.random() < 0.65

    direction  = getattr(lang_cfg, "direction", "ltr") if lang_cfg else "ltr"
    is_rtl     = direction == "rtl"
    body_font  = getattr(lang_cfg, "font_family", "Segoe UI") if lang_cfg else "Segoe UI"
    font_url   = getattr(lang_cfg, "font_import_url", "") if lang_cfg else ""
    currency   = getattr(lang_cfg, "currency_symbol", "₹") if lang_cfg else "₹"

    stamp_texts = _LP(lang_cfg, "stamp_texts", ["TAX INVOICE", "ORIGINAL", "VERIFIED", "PROCESSED"])
    stamp_text  = rng.choice(stamp_texts)
    stamp_rot   = rng.randint(-30, -5)

    doc_title    = _L(lang_cfg, "doc_title_invoice", "TAX INVOICE")
    col_desc     = _L(lang_cfg, "col_description", "Description")
    col_hsn      = _L(lang_cfg, "col_hsn_sac", "HSN/SAC")
    col_unit     = _L(lang_cfg, "col_unit", "Unit")
    col_qty      = _L(lang_cfg, "col_qty", "Qty")
    col_price    = _L(lang_cfg, "col_unit_price", "Rate (₹)")
    col_taxable  = _L(lang_cfg, "col_total", "Taxable (₹)")
    col_gst_rate = _L(lang_cfg, "col_gst_rate", "GST Rate")
    col_tax_amt  = _L(lang_cfg, "col_tax_amount", "Tax Amount")
    col_total    = _L(lang_cfg, "col_total", "Total (₹)")
    lbl_subtotal = _L(lang_cfg, "subtotal", "Sub-Total (Taxable)")
    lbl_total_tax= _L(lang_cfg, "total_tax", "Total Tax")
    lbl_grand    = _L(lang_cfg, "grand_total", "GRAND TOTAL")
    lbl_cgst     = _L(lang_cfg, "cgst", "CGST")
    lbl_sgst     = _L(lang_cfg, "sgst", "SGST")
    lbl_igst     = _L(lang_cfg, "igst", "IGST")

    lbl_meta_title_inv = _L(lang_cfg, "meta_title_invoice", "Invoice Details")
    lbl_payment_terms  = _L(lang_cfg, "payment_terms", "Payment Terms")
    lbl_against_po     = _L(lang_cfg, "against_po", "Against PO")
    lbl_due_date       = _L(lang_cfg, "due_date", "Due Date")
    lbl_supply_type    = _L(lang_cfg, "supply_type", "Supply Type")
    lbl_inter_state    = _L(lang_cfg, "inter_state", "Inter-State")
    lbl_intra_state    = _L(lang_cfg, "intra_state", "Intra-State")
    lbl_tax_header     = _L(lang_cfg, "col_tax_header", "Tax")
    lbl_footer_inv_notice = _L(lang_cfg, "footer_inv_notice",
                               "This is a computer-generated Tax Invoice under GST regulations.")
    lbl_footer_inv_no_sig = _L(lang_cfg, "footer_inv_no_sig",
                               "No signature required if generated electronically.")
    lbl_po_ref         = _L(lang_cfg, "po_ref", "PO Ref")

    tax_mode = lbl_igst if inv.is_interstate else f"{lbl_cgst} + {lbl_sgst}"

    def _m(v): return _money(v, currency)
    def _c(s): return _ltr(s, is_rtl)

    def seller_block():
        addr = inv.seller_address
        return f"""
<div class="block">
  <div class="block-title">{_esc(_term(lang_cfg, "seller_terms", inv.seller_term_idx, inv.seller_term))}</div>
  <div class="block-val">
    <div class="company">{_esc(inv.seller_name)}</div>
    <div>{_esc(addr["line1"])}</div>
    <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_c(addr["pin"])}</div>
    <div>{_esc(addr["country"])}</div>
    <span class="gstin-tag">GSTIN: {_c(inv.seller_gstin)}</span>
  </div>
</div>"""

    def buyer_block():
        addr = inv.buyer_address
        return f"""
<div class="block">
  <div class="block-title">{_esc(_term(lang_cfg, "buyer_terms", inv.buyer_term_idx, inv.buyer_term))}</div>
  <div class="block-val">
    <div class="company">{_esc(inv.buyer_name)}</div>
    <div>{_esc(addr["line1"])}</div>
    <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_c(addr["pin"])}</div>
    <div>{_esc(addr["country"])}</div>
    <span class="gstin-tag">GSTIN: {_c(inv.buyer_gstin)}</span>
  </div>
</div>"""

    def shipping_block():
        addr = inv.shipping_address
        return f"""
<div class="block">
  <div class="block-title">{_esc(_term(lang_cfg, "ship_terms", inv.ship_term_idx, inv.ship_term))}</div>
  <div class="block-val">
    <div>{_esc(addr["line1"])}</div>
    <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_c(addr["pin"])}</div>
    <div>{_esc(addr["country"])}</div>
  </div>
</div>"""

    def meta_block():
        return f"""
<div class="block" style="flex: 0 1 220px;">
  <div class="block-title">{_esc(lbl_meta_title_inv)}</div>
  <div class="block-val">
    <div><b>{_esc(_term(lang_cfg, "inv_num_terms", inv.inv_num_term_idx, inv.inv_num_term))}:</b></div>
    <div class="handwritten" style="font-size:1.1em; color:#1a3c5e; margin:2px 0 6px;">
      {_c(inv.invoice_number)}
    </div>
    <div><b>{_esc(_term(lang_cfg, "invdate_terms", inv.inv_date_term_idx, inv.inv_date_term))}:</b></div>
    <div class="handwritten">{_c(inv.invoice_date)}</div>
    <div style="margin-top:6px;"><b>{_esc(lbl_against_po)}:</b></div>
    <div style="font-size:10px; color:#555;">{_c(inv.po_number)}</div>
    <div style="margin-top:6px;"><b>{_esc(lbl_due_date)}:</b></div>
    <div class="handwritten" style="color:#cc0000;">{_c(inv.due_date)}</div>
    <div style="margin-top:6px; font-size:10px;">
      <b>{_esc(lbl_supply_type)}:</b> {_esc(lbl_inter_state) + " (" + lbl_igst + ")" if inv.is_interstate else _esc(lbl_intra_state) + " (" + lbl_cgst + "+" + lbl_sgst + ")"}
    </div>
  </div>
</div>"""

    def items_block():
        rows = ""
        for i, li in enumerate(inv.line_items, 1):
            taxes = li.tax_amounts(inv.is_interstate)
            if inv.is_interstate:
                tax_cell = f'{lbl_igst} {_c(li.gst_rate)}% = {_c(_m(taxes["IGST"]))}'
            else:
                tax_cell = (
                    f'{lbl_cgst} {_c(li.cgst_rate)}% = {_c(_m(taxes["CGST"]))}<br/>'
                    f'{lbl_sgst} {_c(li.sgst_rate)}% = {_c(_m(taxes["SGST"]))}'
                )
            rows += f"""
<tr>
  <td>{_c(i)}</td>
  <td>
    <div style="font-weight:600;">{_esc(li.description)}</div>
    <div style="color:#666; font-size:10px;">{col_hsn}: {_c(li.hsn_code)} | {_esc(li.category)}</div>
  </td>
  <td class="num">{_c(li.unit)}</td>
  <td class="num">{_c(f'{li.quantity:,}')}</td>
  <td class="num">{_c(_m(li.unit_cost_inr))}</td>
  <td class="num">{_c(_m(li.line_total))}</td>
  <td class="num" style="font-size:10px;">{tax_cell}</td>
  <td class="num" style="font-weight:600;">{_c(_m(li.line_total + taxes["total_tax"]))}</td>
</tr>"""
        return f"""
<table class="items-table">
  <thead>
    <tr>
      <th style="width:25px;">#</th>
      <th>{col_desc}</th>
      <th class="num" style="width:40px;">{col_unit}</th>
      <th class="num" style="width:45px;">{col_qty}</th>
      <th class="num" style="width:85px;">{col_price}</th>
      <th class="num" style="width:85px;">{col_taxable}</th>
      <th class="num" style="width:130px;">{_esc(lbl_tax_header)} ({tax_mode})</th>
      <th class="num" style="width:90px;">{col_total}</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""

    def summary_block():
        rows_html = f"""
<div class="summary-row subtotal">
  <span>{lbl_subtotal}</span><span>{_c(_m(inv.subtotal))}</span>
</div>"""
        if not inv.is_interstate:
            rows_html += f"""
<div class="summary-row">
  <span>{lbl_cgst}</span><span>{_c(_m(inv.total_cgst))}</span>
</div>
<div class="summary-row">
  <span>{lbl_sgst}</span><span>{_c(_m(inv.total_sgst))}</span>
</div>"""
        else:
            rows_html += f"""
<div class="summary-row">
  <span>{lbl_igst}</span><span>{_c(_m(inv.total_igst))}</span>
</div>"""
        rows_html += f"""
<div class="summary-row">
  <span><b>{lbl_total_tax}</b></span><span><b>{_c(_m(inv.total_tax))}</b></span>
</div>
<div class="summary-row total">
  <span>{lbl_grand}</span><span>{_c(_m(inv.grand_total))}</span>
</div>"""
        return f'<div class="summary-block">{rows_html}</div>'

    def footer_block():
        sig_names = ["R. Gupta", "N. Joshi", "K. Reddy", "M. Rao", "D. Bhat"]
        return f"""
<div class="footer-block">
  <div class="payment-terms">
    <b>{_esc(lbl_payment_terms)}:</b> {_esc(_term(lang_cfg, "payment_terms_pool", inv.payment_terms_idx, inv.payment_terms))}<br/>
    <b>{_esc(lbl_due_date)}:</b> <span class="handwritten" style="color:#cc0000;">{_c(inv.due_date)}</span><br/>
    <span style="color:#777; font-size:10px;">
      {_esc(lbl_footer_inv_notice)}
      {_esc(lbl_footer_inv_no_sig)}
    </span>
  </div>
  <div class="signature-area">
    <div class="sig-line"></div>
    <div class="handwritten" style="font-size:1.3em; color:#1a3c5e;">
      {_esc(rng.choice(sig_names))}
    </div>
    <div style="font-size:10px; color:#555;">{_esc(_term(lang_cfg, "sign_offs", inv.sign_off_idx, inv.sign_off))}</div>
    <div style="font-size:10px; color:#555;">{_esc(inv.seller_name)}</div>
    <div class="handwritten" style="font-size:0.95em; color:#666;">{_c(inv.invoice_date)}</div>
  </div>
</div>"""

    BLOCK_RENDERERS = {
        "seller": seller_block, "buyer": buyer_block, "shipping": shipping_block,
        "meta": meta_block, "items": items_block, "summary": summary_block, "footer": footer_block,
    }
    FULL_WIDTH_KEYS = {"items", "summary", "footer"}

    rows_html   = ""
    current_row = []
    for key in block_order:
        if key in FULL_WIDTH_KEYS:
            if current_row:
                rows_html += f'<div class="blocks-row">{"".join(current_row)}</div>'
                current_row = []
            rows_html += BLOCK_RENDERERS[key]()
        else:
            current_row.append(BLOCK_RENDERERS[key]())
    if current_row:
        rows_html += f'<div class="blocks-row">{"".join(current_row)}</div>'

    stamp_html = _stamp_svg(stamp_text, opacity=rng.uniform(0.14, 0.28), rotation=stamp_rot) if add_stamp else ""

    return f"""<!DOCTYPE html>
<html lang="{getattr(lang_cfg, 'code', 'en') if lang_cfg else 'en'}" dir="{direction}">
<head>
  <meta charset="UTF-8"/>
  <title>Tax Invoice {_esc(inv.invoice_number)}</title>
  <style>{_base_css(theme, hand_font, body_font, font_url, direction)}</style>
</head>
<body>
<div class="document">
  <div class="doc-header">
    <div class="doc-title">{doc_title}</div>
    <div class="doc-id-block">
      <div>{_esc(_term(lang_cfg, "inv_num_terms", inv.inv_num_term_idx, inv.inv_num_term))}</div>
      <div class="ref">{_c(inv.invoice_number)}</div>
      <div style="margin-top:4px;">{_esc(_term(lang_cfg, "invdate_terms", inv.inv_date_term_idx, inv.inv_date_term))}: {_c(inv.invoice_date)}</div>
      <div style="margin-top:2px; font-size:10px;">{_esc(lbl_po_ref)}: {_c(inv.po_number)}</div>
    </div>
  </div>
  {rows_html}
  <div class="stamp-container">{stamp_html}</div>
</div>
</body>
</html>"""