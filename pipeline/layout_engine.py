"""
pipeline/layout_engine.py
=========================
Block-Based Floating Layout Engine

Generates dynamic HTML/CSS documents that simulate disjointed enterprise
software printing. Blocks float and flex-wrap organically based on:
  - layout_variant (0–7): alters block order, margin biases, font mix
  - Typography: Google Fonts Caveat (handwritten) injected for signatures,
    dates, and annotated fields; tabular data uses system sans-serif.
  - Color palette: randomly selected from a pool of realistic corporate themes.
  - No rigid templates — every field label is drawn from a shuffled pool.
"""

import random
import html
from typing import Any

from .data_models import PurchaseOrder, TaxInvoice, LineItem

# ── Google Fonts (handwritten) ────────────────────────────────────────────────
# Caveat is open-source (OFL). Loaded via Google Fonts CDN in generated HTML.
HANDWRITTEN_FONTS = ["Caveat", "Dancing Script", "Kalam", "Patrick Hand"]

# ── Corporate colour themes ───────────────────────────────────────────────────
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

# ── Layout block ordering variants ────────────────────────────────────────────
# Each variant is a list of block keys; engine renders them in that order.
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

# ── Stamp SVGs (red ink, rotatable) ──────────────────────────────────────────
STAMP_TEXTS = ["ORIGINAL", "VERIFIED", "APPROVED", "TAX INVOICE", "PAID", "PROCESSED"]


def _esc(s: Any) -> str:
    return html.escape(str(s))


def _money(v: float) -> str:
    return f"₹{v:,.2f}"


def _hand_style(font: str, color: str = "#1a1a2e") -> str:
    return f'style="font-family: \'{font}\', cursive; color: {color}; font-size: 1.15em;"'


def _stamp_svg(text: str, opacity: float = 0.18, rotation: int = -25) -> str:
    """Generate a semi-transparent red ink stamp SVG overlay."""
    return f"""
    <div style="position:absolute; top:{random.randint(30,55)}%; left:{random.randint(35,55)}%;
         transform: rotate({rotation}deg); opacity:{opacity:.2f}; pointer-events:none; z-index:100;">
      <svg width="220" height="90" xmlns="http://www.w3.org/2000/svg">
        <rect x="4" y="4" width="212" height="82" rx="8" ry="8"
              fill="none" stroke="#cc0000" stroke-width="4"/>
        <rect x="10" y="10" width="200" height="70" rx="5" ry="5"
              fill="none" stroke="#cc0000" stroke-width="1.5"/>
        <text x="110" y="56" font-family="Arial Black, sans-serif" font-size="22"
              font-weight="900" text-anchor="middle" fill="#cc0000" letter-spacing="3">
          {text}
        </text>
      </svg>
    </div>"""


def _base_css(theme: dict, hand_font: str) -> str:
    """Generate base CSS with theme variables and handwritten font import."""
    return f"""
    @import url('https://fonts.googleapis.com/css2?family={hand_font.replace(" ", "+")}:wght@400;700&display=swap');

    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
        font-family: 'Segoe UI', 'Arial', sans-serif;
        font-size: 12px;
        color: #1a1a1a;
        background: #f4f4f4;
        padding: 20px;
    }}

    .document {{
        background: #ffffff;
        width: 794px;
        min-height: 1123px;
        margin: 0 auto;
        padding: 32px 36px;
        position: relative;
        box-shadow: 0 2px 12px rgba(0,0,0,0.15);
        overflow: hidden;
    }}

    .doc-header {{
        background: {theme["primary"]};
        color: {theme["header_text"]};
        padding: 18px 24px;
        margin: -32px -36px 24px -36px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}

    .doc-title {{
        font-size: 22px;
        font-weight: 700;
        letter-spacing: 1px;
    }}

    .doc-id-block {{
        text-align: right;
        font-size: 11px;
    }}

    .doc-id-block .ref {{
        font-size: 15px;
        font-weight: 700;
        margin-top: 4px;
    }}

    .blocks-row {{
        display: flex;
        flex-wrap: wrap;
        gap: 14px;
        margin-bottom: 16px;
    }}

    .block {{
        background: {theme["accent"]};
        border: 1px solid {theme["border"]};
        border-radius: 4px;
        padding: 12px 14px;
        flex: 1 1 220px;
        min-width: 180px;
    }}

    .block-title {{
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        color: {theme["primary"]};
        border-bottom: 1.5px solid {theme["border"]};
        padding-bottom: 5px;
        margin-bottom: 8px;
    }}

    .block-val {{
        font-size: 12px;
        line-height: 1.6;
    }}

    .block-val .company {{
        font-weight: 700;
        font-size: 13px;
        margin-bottom: 2px;
    }}

    .gstin-tag {{
        display: inline-block;
        background: {theme["primary"]};
        color: {theme["header_text"]};
        font-size: 9px;
        padding: 1px 6px;
        border-radius: 3px;
        margin-top: 4px;
        letter-spacing: 0.5px;
        font-weight: 600;
    }}

    table.items-table {{
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 12px;
        font-size: 11px;
    }}

    table.items-table thead tr {{
        background: {theme["primary"]};
        color: {theme["header_text"]};
    }}

    table.items-table thead th {{
        padding: 8px 10px;
        text-align: left;
        font-weight: 600;
        letter-spacing: 0.3px;
    }}

    table.items-table thead th.num {{
        text-align: right;
    }}

    table.items-table tbody tr:nth-child(even) {{
        background: {theme["accent"]};
    }}

    table.items-table tbody td {{
        padding: 7px 10px;
        border-bottom: 1px solid #e0e0e0;
        vertical-align: top;
    }}

    table.items-table tbody td.num {{
        text-align: right;
        font-variant-numeric: tabular-nums;
    }}

    .summary-block {{
        margin-left: auto;
        width: 320px;
        border: 1px solid {theme["border"]};
        border-radius: 4px;
        overflow: hidden;
        font-size: 12px;
        margin-bottom: 20px;
    }}

    .summary-row {{
        display: flex;
        justify-content: space-between;
        padding: 6px 14px;
        border-bottom: 1px solid #e8e8e8;
    }}

    .summary-row:last-child {{
        border-bottom: none;
    }}

    .summary-row.total {{
        background: {theme["primary"]};
        color: {theme["header_text"]};
        font-weight: 700;
        font-size: 13px;
        padding: 9px 14px;
    }}

    .summary-row.subtotal {{
        background: {theme["accent"]};
        font-weight: 600;
    }}

    .footer-block {{
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        margin-top: 24px;
        padding-top: 14px;
        border-top: 1.5px solid {theme["border"]};
    }}

    .payment-terms {{
        font-size: 11px;
        color: #444;
        max-width: 320px;
    }}

    .signature-area {{
        text-align: right;
        font-size: 11px;
    }}

    .sig-line {{
        border-top: 1px solid #333;
        width: 180px;
        margin: 40px 0 4px auto;
    }}

    .handwritten {{
        font-family: '{hand_font}', cursive;
    }}

    .stamp-container {{
        position: absolute;
        top: 0; left: 0; right: 0; bottom: 0;
        pointer-events: none;
    }}
    """


# ─────────────────────────────────────────────────────────────────────────────
#  PURCHASE ORDER RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def render_purchase_order_html(po: PurchaseOrder) -> str:
    rng = random.Random(po.doc_index * 999 + 1)
    theme = THEMES[po.layout_variant % len(THEMES)]
    hand_font = rng.choice(HANDWRITTEN_FONTS)
    block_order = BLOCK_ORDERS[po.layout_variant]
    add_stamp = rng.random() < 0.55
    stamp_text = rng.choice(STAMP_TEXTS)
    stamp_rot = rng.randint(-35, -10)

    # ── Build individual blocks ────────────────────────────────────────────────
    def seller_block():
        addr = po.seller_address
        return f"""
        <div class="block">
          <div class="block-title">{_esc(po.seller_term)}</div>
          <div class="block-val">
            <div class="company">{_esc(po.seller_name)}</div>
            <div>{_esc(addr["line1"])}</div>
            <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_esc(addr["pin"])}</div>
            <div>{_esc(addr["country"])}</div>
            <span class="gstin-tag">GSTIN: {_esc(po.seller_gstin)}</span>
          </div>
        </div>"""

    def buyer_block():
        addr = po.buyer_address
        return f"""
        <div class="block">
          <div class="block-title">{_esc(po.buyer_term)}</div>
          <div class="block-val">
            <div class="company">{_esc(po.buyer_name)}</div>
            <div>{_esc(addr["line1"])}</div>
            <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_esc(addr["pin"])}</div>
            <div>{_esc(addr["country"])}</div>
            <span class="gstin-tag">GSTIN: {_esc(po.buyer_gstin)}</span>
          </div>
        </div>"""

    def shipping_block():
        addr = po.shipping_address
        return f"""
        <div class="block">
          <div class="block-title">{_esc(po.ship_term)}</div>
          <div class="block-val">
            <div>{_esc(addr["line1"])}</div>
            <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_esc(addr["pin"])}</div>
            <div>{_esc(addr["country"])}</div>
          </div>
        </div>"""

    def meta_block():
        return f"""
        <div class="block" style="flex: 0 1 200px;">
          <div class="block-title">Order Details</div>
          <div class="block-val">
            <div><b>{_esc(po.po_num_term)}:</b></div>
            <div class="handwritten" style="font-size:1.1em; color:#1a3c5e; margin:2px 0 8px;">
              {_esc(po.po_number)}
            </div>
            <div><b>{_esc(po.date_term)}:</b></div>
            <div class="handwritten">{_esc(po.po_date)}</div>
            <div style="margin-top:8px;"><b>Payment Terms:</b></div>
            <div>{_esc(po.payment_terms)}</div>
          </div>
        </div>"""

    def items_block():
        rows = ""
        for i, li in enumerate(po.line_items, 1):
            rows += f"""
            <tr>
              <td>{i}</td>
              <td>
                <div style="font-weight:600;">{_esc(li.description)}</div>
                <div style="color:#666; font-size:10px;">HSN: {_esc(li.hsn_code)} | {_esc(li.category)}</div>
              </td>
              <td class="num">{_esc(li.unit)}</td>
              <td class="num">{li.quantity:,}</td>
              <td class="num">{_money(li.unit_cost_inr)}</td>
              <td class="num">{_money(li.line_total)}</td>
            </tr>"""
        return f"""
        <table class="items-table">
          <thead>
            <tr>
              <th style="width:30px;">#</th>
              <th>Description</th>
              <th class="num" style="width:50px;">Unit</th>
              <th class="num" style="width:60px;">Qty</th>
              <th class="num" style="width:100px;">Unit Price</th>
              <th class="num" style="width:100px;">Amount</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    def summary_block():
        return f"""
        <div class="summary-block">
          <div class="summary-row subtotal">
            <span>Sub-Total</span>
            <span>{_money(po.subtotal)}</span>
          </div>
          <div class="summary-row">
            <span style="color:#888; font-size:11px;">* GST applicable as per Tax Invoice</span>
          </div>
          <div class="summary-row total">
            <span>PO Value (excl. GST)</span>
            <span>{_money(po.subtotal)}</span>
          </div>
        </div>"""

    def footer_block():
        return f"""
        <div class="footer-block">
          <div class="payment-terms">
            <b>Payment Terms:</b> {_esc(po.payment_terms)}<br/>
            <span style="color:#777;">This is a computer-generated Purchase Order.
            Subject to terms and conditions of {_esc(po.buyer_name)}.</span>
          </div>
          <div class="signature-area">
            <div class="sig-line"></div>
            <div class="handwritten" style="font-size:1.3em; color:#1a3c5e;">
              {_esc(rng.choice(["A. Kumar", "P. Sharma", "R. Singh", "S. Mehta", "V. Patel"]))}
            </div>
            <div style="font-size:10px; color:#555;">{_esc(po.sign_off)}</div>
            <div style="font-size:10px; color:#555;">{_esc(po.buyer_name)}</div>
            <div class="handwritten" style="font-size:0.95em; color:#666;">{_esc(po.po_date)}</div>
          </div>
        </div>"""

    BLOCK_RENDERERS = {
        "seller": seller_block,
        "buyer": buyer_block,
        "shipping": shipping_block,
        "meta": meta_block,
        "items": items_block,
        "summary": summary_block,
        "footer": footer_block,
    }

    # ── Assemble blocks in variant order ──────────────────────────────────────
    inline_blocks = []   # flex-wrap row blocks
    full_width = []      # items, summary, footer go full-width

    FULL_WIDTH_KEYS = {"items", "summary", "footer"}
    rows_html = ""
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
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Purchase Order {_esc(po.po_number)}</title>
<style>{_base_css(theme, hand_font)}</style>
</head>
<body>
<div class="document">
  <div class="doc-header">
    <div class="doc-title">PURCHASE ORDER</div>
    <div class="doc-id-block">
      <div>{_esc(po.po_num_term)}</div>
      <div class="ref">{_esc(po.po_number)}</div>
      <div style="margin-top:4px;">{_esc(po.date_term)}: {_esc(po.po_date)}</div>
    </div>
  </div>
  {rows_html}
  <div class="stamp-container">{stamp_html}</div>
</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
#  TAX INVOICE RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def render_tax_invoice_html(inv: TaxInvoice) -> str:
    rng = random.Random(inv.doc_index * 999 + 2)
    theme = THEMES[(inv.layout_variant + 2) % len(THEMES)]   # slight offset from PO
    hand_font = rng.choice(HANDWRITTEN_FONTS)
    block_order = BLOCK_ORDERS[(inv.layout_variant + 1) % len(BLOCK_ORDERS)]
    add_stamp = rng.random() < 0.65
    stamp_text = rng.choice(["TAX INVOICE", "ORIGINAL", "VERIFIED", "PROCESSED"])
    stamp_rot = rng.randint(-30, -5)

    tax_mode = "IGST" if inv.is_interstate else "CGST + SGST"

    def seller_block():
        addr = inv.seller_address
        return f"""
        <div class="block">
          <div class="block-title">{_esc(inv.seller_term)}</div>
          <div class="block-val">
            <div class="company">{_esc(inv.seller_name)}</div>
            <div>{_esc(addr["line1"])}</div>
            <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_esc(addr["pin"])}</div>
            <div>{_esc(addr["country"])}</div>
            <span class="gstin-tag">GSTIN: {_esc(inv.seller_gstin)}</span>
          </div>
        </div>"""

    def buyer_block():
        addr = inv.buyer_address
        return f"""
        <div class="block">
          <div class="block-title">{_esc(inv.buyer_term)}</div>
          <div class="block-val">
            <div class="company">{_esc(inv.buyer_name)}</div>
            <div>{_esc(addr["line1"])}</div>
            <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_esc(addr["pin"])}</div>
            <div>{_esc(addr["country"])}</div>
            <span class="gstin-tag">GSTIN: {_esc(inv.buyer_gstin)}</span>
          </div>
        </div>"""

    def shipping_block():
        addr = inv.shipping_address
        return f"""
        <div class="block">
          <div class="block-title">{_esc(inv.ship_term)}</div>
          <div class="block-val">
            <div>{_esc(addr["line1"])}</div>
            <div>{_esc(addr["city"])}, {_esc(addr["state"])} — {_esc(addr["pin"])}</div>
            <div>{_esc(addr["country"])}</div>
          </div>
        </div>"""

    def meta_block():
        return f"""
        <div class="block" style="flex: 0 1 220px;">
          <div class="block-title">Invoice Details</div>
          <div class="block-val">
            <div><b>{_esc(inv.inv_num_term)}:</b></div>
            <div class="handwritten" style="font-size:1.1em; color:#1a3c5e; margin:2px 0 6px;">
              {_esc(inv.invoice_number)}
            </div>
            <div><b>{_esc(inv.inv_date_term)}:</b></div>
            <div class="handwritten">{_esc(inv.invoice_date)}</div>
            <div style="margin-top:6px;"><b>Against PO:</b></div>
            <div style="font-size:10px; color:#555;">{_esc(inv.po_number)}</div>
            <div style="margin-top:6px;"><b>Due Date:</b></div>
            <div class="handwritten" style="color:#cc0000;">{_esc(inv.due_date)}</div>
            <div style="margin-top:6px; font-size:10px;">
              <b>Supply Type:</b> {"Inter-State (IGST)" if inv.is_interstate else "Intra-State (CGST+SGST)"}
            </div>
          </div>
        </div>"""

    def items_block():
        rows = ""
        for i, li in enumerate(inv.line_items, 1):
            taxes = li.tax_amounts(inv.is_interstate)
            if inv.is_interstate:
                tax_cell = f'IGST {li.gst_rate}% = {_money(taxes["IGST"])}'
            else:
                tax_cell = (
                    f'CGST {li.cgst_rate}% = {_money(taxes["CGST"])}<br/>'
                    f'SGST {li.sgst_rate}% = {_money(taxes["SGST"])}'
                )
            rows += f"""
            <tr>
              <td>{i}</td>
              <td>
                <div style="font-weight:600;">{_esc(li.description)}</div>
                <div style="color:#666; font-size:10px;">HSN: {_esc(li.hsn_code)} | {_esc(li.category)}</div>
              </td>
              <td class="num">{_esc(li.unit)}</td>
              <td class="num">{li.quantity:,}</td>
              <td class="num">{_money(li.unit_cost_inr)}</td>
              <td class="num">{_money(li.line_total)}</td>
              <td class="num" style="font-size:10px;">{tax_cell}</td>
              <td class="num" style="font-weight:600;">{_money(li.line_total + taxes["total_tax"])}</td>
            </tr>"""
        return f"""
        <table class="items-table">
          <thead>
            <tr>
              <th style="width:25px;">#</th>
              <th>Description</th>
              <th class="num" style="width:40px;">Unit</th>
              <th class="num" style="width:45px;">Qty</th>
              <th class="num" style="width:85px;">Rate (₹)</th>
              <th class="num" style="width:85px;">Taxable (₹)</th>
              <th class="num" style="width:130px;">Tax ({tax_mode})</th>
              <th class="num" style="width:90px;">Total (₹)</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>"""

    def summary_block():
        rows_html = f"""
          <div class="summary-row subtotal">
            <span>Sub-Total (Taxable)</span><span>{_money(inv.subtotal)}</span>
          </div>"""
        if not inv.is_interstate:
            rows_html += f"""
          <div class="summary-row">
            <span>CGST</span><span>{_money(inv.total_cgst)}</span>
          </div>
          <div class="summary-row">
            <span>SGST</span><span>{_money(inv.total_sgst)}</span>
          </div>"""
        else:
            rows_html += f"""
          <div class="summary-row">
            <span>IGST</span><span>{_money(inv.total_igst)}</span>
          </div>"""
        rows_html += f"""
          <div class="summary-row">
            <span><b>Total Tax</b></span><span><b>{_money(inv.total_tax)}</b></span>
          </div>
          <div class="summary-row total">
            <span>GRAND TOTAL</span><span>{_money(inv.grand_total)}</span>
          </div>"""
        return f'<div class="summary-block">{rows_html}</div>'

    def footer_block():
        return f"""
        <div class="footer-block">
          <div class="payment-terms">
            <b>Payment Terms:</b> {_esc(inv.payment_terms)}<br/>
            <b>Due Date:</b> <span class="handwritten" style="color:#cc0000;">{_esc(inv.due_date)}</span><br/>
            <span style="color:#777; font-size:10px;">
              This is a computer-generated Tax Invoice under GST regulations.
              No signature required if generated electronically.
            </span>
          </div>
          <div class="signature-area">
            <div class="sig-line"></div>
            <div class="handwritten" style="font-size:1.3em; color:#1a3c5e;">
              {_esc(rng.choice(["R. Gupta", "N. Joshi", "K. Reddy", "M. Rao", "D. Bhat"]))}
            </div>
            <div style="font-size:10px; color:#555;">{_esc(inv.sign_off)}</div>
            <div style="font-size:10px; color:#555;">{_esc(inv.seller_name)}</div>
            <div class="handwritten" style="font-size:0.95em; color:#666;">{_esc(inv.invoice_date)}</div>
          </div>
        </div>"""

    BLOCK_RENDERERS = {
        "seller": seller_block,
        "buyer": buyer_block,
        "shipping": shipping_block,
        "meta": meta_block,
        "items": items_block,
        "summary": summary_block,
        "footer": footer_block,
    }

    FULL_WIDTH_KEYS = {"items", "summary", "footer"}
    rows_html = ""
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
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Tax Invoice {_esc(inv.invoice_number)}</title>
<style>{_base_css(theme, hand_font)}</style>
</head>
<body>
<div class="document">
  <div class="doc-header">
    <div class="doc-title">TAX INVOICE</div>
    <div class="doc-id-block">
      <div>{_esc(inv.inv_num_term)}</div>
      <div class="ref">{_esc(inv.invoice_number)}</div>
      <div style="margin-top:4px;">{_esc(inv.inv_date_term)}: {_esc(inv.invoice_date)}</div>
      <div style="margin-top:2px; font-size:10px;">PO Ref: {_esc(inv.po_number)}</div>
    </div>
  </div>
  {rows_html}
  <div class="stamp-container">{stamp_html}</div>
</div>
</body>
</html>"""
