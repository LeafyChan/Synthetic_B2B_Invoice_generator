"""
pipeline/data_models.py
=======================
Deterministic field generators for Purchase Orders and Tax Invoices.

All randomisation is seeded per document index to ensure reproducibility.
Uses Faker (Indian locale) for addresses, company names, and dates.
"""

import random
import string
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Optional

from faker import Faker

# ── Faker setup ───────────────────────────────────────────────────────────────
_faker_cache: dict[int, Faker] = {}


def get_faker(seed: int) -> Faker:
    if seed not in _faker_cache:
        fk = Faker("en_IN")
        fk.seed_instance(seed)
        _faker_cache[seed] = fk
    return _faker_cache[seed]


# ── Terminology pools (for floating label randomisation) ──────────────────────
# IMPORTANT — these English pools are the ground-truth values stored in the
# PurchaseOrder/TaxInvoice dataclasses and logged to the DB's json_payload
# (so OCR ground truth stays comparable across languages). They are NOT
# meant to be the literal text printed on a non-English document — that
# would mean the "floating terminology" feature could never be translated
# (this used to be exactly what happened: layout_engine.py rendered
# po.buyer_term etc. directly with html.escape(), bypassing lang_cfg
# entirely, which is one of the reasons a non-English run still showed
# English text for these specific fields).
#
# Fix: each *_term field also gets a same-length integer "*_term_idx" field
# recording which pool entry was chosen. layout_engine.py uses the index to
# look up a translated equivalent from lang_cfg.labels (falling back to
# the English pool value here if lang_cfg has no translation for that
# index) instead of rendering the stored English string directly.
BUYER_TERMS = ["Bill To", "Buyer", "Purchaser", "Client", "Consignee", "Billed Party"]
SELLER_TERMS = ["Sold By", "Vendor", "Supplier", "From", "Issued By", "Seller"]
SHIP_TERMS = ["Ship To", "Delivery Address", "Dispatch To", "Consign To", "Deliver At"]
PO_TERMS = ["Purchase Order No.", "PO Number", "Order Reference", "PO Ref.", "Requisition No."]
DATE_TERMS = ["PO Date", "Order Date", "Issue Date", "Date of Order"]
INV_TERMS = ["Tax Invoice No.", "Invoice Number", "Invoice Ref.", "Bill No."]
INVDATE_TERMS = ["Invoice Date", "Bill Date", "Date of Invoice", "Issue Date"]
PAYMENT_TERMS_POOL = [
    "Net 30", "Net 45", "Net 60", "Due on Receipt", "2/10 Net 30",
    "Net 15", "COD", "30 Days EOM", "Immediate", "Net 90",
]
SIGN_OFFS = [
    "Authorised Signatory", "For & on behalf of", "Approved by",
    "Authorised by", "Signed", "Finance Head",
]

# ── Discrepancy injection ──────────────────────────────────────────────────────
# ~15% of pairs get a deliberate PO/Invoice mismatch — a realistic training
# signal for "does the invoice match the PO" downstream tasks. Assignment is
# deterministic per doc_index (separate RNG stream + prime salt, same pattern
# as degradation.assign_tier) so re-runs are reproducible.
#
# IMPORTANT: PurchaseOrder and TaxInvoice previously shared the *same*
# `line_items` list object (and the same LineItem instances inside it) —
# generate_document_pair() did `po = PurchaseOrder(line_items=line_items, ...)`
# and `inv = TaxInvoice(line_items=line_items, ...)` with the identical list.
# That meant mutating one side's line items to create a discrepancy would
# have silently mutated the PO's "ground truth" too, making the pair
# identical again (or corrupting non-discrepant pairs). Fixed by giving the
# invoice its own deepcopy of line_items before any discrepancy is applied —
# see generate_document_pair() below.
DISCREPANCY_RATE = 0.15

DISCREPANCY_KINDS = (
    "quantity_mismatch",   # invoice qty differs from PO qty on one line item
    "price_mismatch",      # invoice unit_cost differs from PO unit_cost
    "extra_line_item",     # invoice bills for an item not on the PO
    "missing_line_item",   # invoice omits an item that was on the PO
    "gst_rate_mismatch",   # invoice applies a different GST rate than PO line
)


def _assign_discrepancy(doc_index: int) -> bool:
    """Deterministically decide whether this doc pair gets a discrepancy."""
    rng = random.Random(doc_index * 524287 + 11)   # distinct prime salt
    return rng.random() < DISCREPANCY_RATE


def _apply_discrepancy(
    inv_line_items: list["LineItem"],
    rng: random.Random,
    catalog: list[dict],
) -> tuple[list["LineItem"], str, list[str]]:
    """
    Mutate a (already deep-copied) list of invoice LineItems to introduce
    exactly one realistic PO/Invoice mismatch. Returns
    (possibly-modified line_items, discrepancy_kind, human-readable notes).

    Operates ONLY on the list passed in — caller must have already deep-
    copied it from the PO's line_items so the PO's ground truth is untouched.
    """
    kind = rng.choice(DISCREPANCY_KINDS)
    notes: list[str] = []

    if not inv_line_items:
        return inv_line_items, kind, notes

    target_idx = rng.randrange(len(inv_line_items))
    target = inv_line_items[target_idx]

    if kind == "quantity_mismatch":
        delta = rng.choice([-1, 1]) * rng.randint(1, max(1, target.quantity // 4 or 1))
        new_qty = max(1, target.quantity + delta)
        if new_qty == target.quantity:
            new_qty += 1
        notes.append(
            f"Line {target_idx + 1} ({target.description[:40]}): "
            f"PO qty {target.quantity} vs Invoice qty {new_qty}"
        )
        inv_line_items[target_idx] = dataclass_replace_quantity(target, new_qty)

    elif kind == "price_mismatch":
        pct = rng.uniform(0.05, 0.25) * rng.choice([-1, 1])
        new_cost = round(max(0.01, target.unit_cost_inr * (1 + pct)), 2)
        notes.append(
            f"Line {target_idx + 1} ({target.description[:40]}): "
            f"PO unit_cost {target.unit_cost_inr} vs Invoice unit_cost {new_cost}"
        )
        inv_line_items[target_idx] = dataclass_replace_cost(target, new_cost)

    elif kind == "gst_rate_mismatch":
        # Pick a different valid rate than what's on the PO line item —
        # simulates the invoice being filed under the wrong HSN/SAC slab.
        from pipeline.gst_rate_schedule import VALID_GST_RATES
        other_rates = [r for r in VALID_GST_RATES if r != target.gst_rate]
        new_rate = rng.choice(other_rates) if other_rates else target.gst_rate
        notes.append(
            f"Line {target_idx + 1} ({target.description[:40]}): "
            f"PO GST {target.gst_rate}% vs Invoice GST {new_rate}%"
        )
        inv_line_items[target_idx] = dataclass_replace_gst(target, new_rate)

    elif kind == "extra_line_item":
        extra_src = rng.choice(catalog) if catalog else None
        if extra_src:
            extra = LineItem(
                description=extra_src["description"],
                hsn_code=extra_src["hsn_code"],
                unit=extra_src["unit"],
                quantity=rng.randint(1, 50),
                unit_cost_inr=extra_src["unit_cost_inr"],
                gst_rate=extra_src["gst_rate"],
                category=extra_src["category"],
            )
            inv_line_items.append(extra)
            notes.append(
                f"Invoice bills an extra item not on PO: {extra.description[:50]} "
                f"(qty {extra.quantity})"
            )

    elif kind == "missing_line_item":
        if len(inv_line_items) > 1:
            removed = inv_line_items.pop(target_idx)
            notes.append(
                f"Invoice omits PO line {target_idx + 1}: {removed.description[:50]} "
                f"(PO qty {removed.quantity})"
            )
        else:
            kind = "quantity_mismatch"  # fallback — can't remove the only item
            delta = 1
            new_qty = target.quantity + delta
            notes.append(
                f"Line {target_idx + 1} ({target.description[:40]}): "
                f"PO qty {target.quantity} vs Invoice qty {new_qty}"
            )
            inv_line_items[target_idx] = dataclass_replace_quantity(target, new_qty)

    return inv_line_items, kind, notes


def dataclass_replace_quantity(li: "LineItem", new_qty: int) -> "LineItem":
    from dataclasses import replace
    return replace(li, quantity=new_qty)


def dataclass_replace_cost(li: "LineItem", new_cost: float) -> "LineItem":
    from dataclasses import replace
    return replace(li, unit_cost_inr=new_cost)


def dataclass_replace_gst(li: "LineItem", new_rate: float) -> "LineItem":
    from dataclasses import replace
    return replace(li, gst_rate=new_rate)



STATE_CODES = [
    "01", "02", "03", "04", "05", "06", "07", "08", "09", "10",
    "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
    "21", "22", "23", "24", "25", "26", "27", "28", "29", "30",
    "31", "32", "33", "34", "35", "36", "37",
]


def _random_gstin(rng: random.Random) -> str:
    """Generate a plausible (not necessarily valid checksum) 15-char GSTIN."""
    state = rng.choice(STATE_CODES)
    pan_chars = "".join(rng.choices(string.ascii_uppercase, k=5))
    pan_digits = "".join(rng.choices(string.digits, k=4))
    pan_end = rng.choice(string.ascii_uppercase)
    entity = str(rng.randint(1, 9))
    z_char = "Z"
    checksum = rng.choice(string.ascii_uppercase + string.digits)
    return f"{state}{pan_chars}{pan_digits}{pan_end}{entity}{z_char}{checksum}"


def _random_po_number(rng: random.Random, idx: int) -> str:
    prefix = rng.choice(["PO", "ORD", "PR", "REQ", "PUR"])
    year = rng.randint(2022, 2024)
    seq = f"{idx:05d}"
    return f"{prefix}-{year}-{seq}"


def _random_invoice_number(rng: random.Random, idx: int, po_num: str) -> str:
    prefix = rng.choice(["INV", "TAX", "BILL", "GST", "SI"])
    year = rng.randint(2022, 2024)
    seq = f"{idx:05d}"
    return f"{prefix}-{year}-{seq}"


# ── Address helpers ───────────────────────────────────────────────────────────
def _generate_address(fk: Faker) -> dict:
    return {
        "line1": fk.street_address(),
        "city": fk.city(),
        "state": fk.state(),
        "pin": fk.postcode(),
        "country": "India",
    }


def _format_address(addr: dict) -> str:
    return f"{addr['line1']}, {addr['city']}, {addr['state']} — {addr['pin']}, {addr['country']}"


# ── Line item computation ─────────────────────────────────────────────────────
@dataclass
class LineItem:
    description: str
    hsn_code: str
    unit: str
    quantity: int
    unit_cost_inr: float
    gst_rate: int
    category: str

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_cost_inr, 2)

    @property
    def taxable_amount(self) -> float:
        return self.line_total

    @property
    def cgst_rate(self) -> float:
        return self.gst_rate / 2

    @property
    def sgst_rate(self) -> float:
        return self.gst_rate / 2

    @property
    def igst_rate(self) -> float:
        return float(self.gst_rate)

    def tax_amounts(self, is_interstate: bool) -> dict:
        """Compute CGST+SGST (intra-state) or IGST (inter-state)."""
        base = self.taxable_amount
        if is_interstate:
            igst = round(base * self.igst_rate / 100, 2)
            return {"CGST": 0.0, "SGST": 0.0, "IGST": igst, "total_tax": igst}
        else:
            cgst = round(base * self.cgst_rate / 100, 2)
            sgst = round(base * self.sgst_rate / 100, 2)
            return {"CGST": cgst, "SGST": sgst, "IGST": 0.0, "total_tax": round(cgst + sgst, 2)}

    def to_dict(self) -> dict:
        return asdict(self)


# ── Master document pair ──────────────────────────────────────────────────────
@dataclass
class PurchaseOrder:
    doc_index: int
    po_number: str
    po_date: str
    buyer_name: str
    buyer_address: dict
    buyer_gstin: str
    seller_name: str
    seller_address: dict
    seller_gstin: str
    shipping_address: dict
    line_items: list[LineItem]
    payment_terms: str
    # Floating terminology choices (English ground-truth strings — see note
    # above _BUYER_TERMS et al. for why these stay English regardless of
    # --language; layout_engine.py uses the *_term_idx fields below to look
    # up a translated equivalent for display)
    buyer_term: str
    seller_term: str
    ship_term: str
    po_num_term: str
    date_term: str
    sign_off: str
    # Layout variant seed
    layout_variant: int
    # Pool indices for the terminology choices above, so the renderer can
    # resolve a translated label without re-deriving which pool entry was
    # picked. Defaulted so existing keyword-constructed callers don't break.
    buyer_term_idx: int = 0
    seller_term_idx: int = 0
    ship_term_idx: int = 0
    po_num_term_idx: int = 0
    date_term_idx: int = 0
    sign_off_idx: int = 0
    payment_terms_idx: int = 0

    @property
    def subtotal(self) -> float:
        return round(sum(i.line_total for i in self.line_items), 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["subtotal"] = self.subtotal
        return d


@dataclass
class TaxInvoice:
    doc_index: int
    invoice_number: str
    invoice_date: str
    po_number: str          # foreign key → PurchaseOrder
    buyer_name: str
    buyer_address: dict
    buyer_gstin: str
    seller_name: str
    seller_address: dict
    seller_gstin: str
    shipping_address: dict
    line_items: list[LineItem]
    is_interstate: bool
    payment_terms: str
    due_date: str
    # Floating terminology
    inv_num_term: str
    inv_date_term: str
    buyer_term: str
    seller_term: str
    ship_term: str
    sign_off: str
    # Layout variant seed
    layout_variant: int
    # Pool indices — see PurchaseOrder for why these exist
    inv_num_term_idx: int = 0
    inv_date_term_idx: int = 0
    buyer_term_idx: int = 0
    seller_term_idx: int = 0
    ship_term_idx: int = 0
    sign_off_idx: int = 0
    payment_terms_idx: int = 0
    # Discrepancy ground truth — see DISCREPANCY_KINDS / _apply_discrepancy
    # above. has_discrepancy is what assembler.py checks to route a copy of
    # the rendered output into output/.../discrepant/ alongside its normal
    # tier folder. discrepancy_kind/notes are stored in the DB json_payload
    # as ground truth for training a "does this invoice match its PO" model.
    has_discrepancy: bool = False
    discrepancy_kind: Optional[str] = None
    discrepancy_notes: list[str] = field(default_factory=list)

    @property
    def subtotal(self) -> float:
        return round(sum(i.line_total for i in self.line_items), 2)

    @property
    def total_cgst(self) -> float:
        if self.is_interstate:
            return 0.0
        return round(sum(i.tax_amounts(False)["CGST"] for i in self.line_items), 2)

    @property
    def total_sgst(self) -> float:
        if self.is_interstate:
            return 0.0
        return round(sum(i.tax_amounts(False)["SGST"] for i in self.line_items), 2)

    @property
    def total_igst(self) -> float:
        if not self.is_interstate:
            return 0.0
        return round(sum(i.tax_amounts(True)["IGST"] for i in self.line_items), 2)

    @property
    def total_tax(self) -> float:
        return round(self.total_cgst + self.total_sgst + self.total_igst, 2)

    @property
    def grand_total(self) -> float:
        return round(self.subtotal + self.total_tax, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["subtotal"] = self.subtotal
        d["total_cgst"] = self.total_cgst
        d["total_sgst"] = self.total_sgst
        d["total_igst"] = self.total_igst
        d["total_tax"] = self.total_tax
        d["grand_total"] = self.grand_total
        return d


# ── Document pair factory ─────────────────────────────────────────────────────
def generate_document_pair(
    idx: int,
    catalog: list[dict],
) -> tuple[PurchaseOrder, TaxInvoice]:
    """
    Deterministically generate a linked PO + Invoice pair for index `idx`.
    Seeded so regeneration is idempotent.
    """
    rng = random.Random(idx * 31337 + 7)
    fk = get_faker(idx)

    # ── Entity details ────────────────────────────────────────────────────────
    buyer_name = fk.company()
    seller_name = fk.company()
    buyer_addr = _generate_address(fk)
    seller_addr = _generate_address(fk)
    # Ship-to: sometimes same as buyer, sometimes different
    ship_addr = buyer_addr if rng.random() < 0.6 else _generate_address(fk)

    buyer_gstin = _random_gstin(rng)
    seller_gstin = _random_gstin(rng)

    # Interstate if buyer_state ≠ seller_state
    is_interstate = buyer_addr["state"] != seller_addr["state"]

    # ── Dates ─────────────────────────────────────────────────────────────────
    base_date = date(2022, 1, 1) + timedelta(days=rng.randint(0, 730))
    po_date = base_date
    inv_date = po_date + timedelta(days=rng.randint(1, 14))
    payment_days = rng.choice([15, 30, 45, 60, 90])
    due_date = inv_date + timedelta(days=payment_days)

    # ── Line items ────────────────────────────────────────────────────────────
    # Real B2B invoices have 10–50+ line items; 1–5 was unrealistically thin.
    # Distribution: 60% chance of 10–25 items, 25% chance of 26–50, 15% of 5–9
    # (the tail keeps some variety without every doc being exactly long).
    roll = rng.random()
    if roll < 0.15:
        num_items = rng.randint(5, 9)
    elif roll < 0.75:
        num_items = rng.randint(10, 25)
    else:
        num_items = rng.randint(26, 50)
    catalog_sample = rng.choices(catalog, k=min(num_items, len(catalog)))
    line_items = []
    for cat_item in catalog_sample:
        qty = rng.randint(1, 200)
        li = LineItem(
            description=cat_item["description"],
            hsn_code=cat_item["hsn_code"],
            unit=cat_item["unit"],
            quantity=qty,
            unit_cost_inr=cat_item["unit_cost_inr"],
            gst_rate=cat_item["gst_rate"],
            category=cat_item["category"],
        )
        line_items.append(li)

    # ── Reference numbers ─────────────────────────────────────────────────────
    po_num = _random_po_number(rng, idx)
    inv_num = _random_invoice_number(rng, idx, po_num)
    payment_terms_idx = rng.randrange(len(PAYMENT_TERMS_POOL))
    payment_terms = PAYMENT_TERMS_POOL[payment_terms_idx]
    layout_variant = rng.randint(0, 7)

    # ── Discrepancy injection ─────────────────────────────────────────────────
    # The invoice gets its OWN deep copy of line_items here — critically,
    # BEFORE any discrepancy mutation. The PO below is assembled from the
    # original `line_items` list; the invoice is assembled from
    # `inv_line_items`. If these were the same list/objects (as in the
    # previous version of this function), mutating one for discrepancy
    # injection would corrupt the PO's ground truth too.
    inv_line_items = deepcopy(line_items)
    has_discrepancy = _assign_discrepancy(idx)
    discrepancy_kind: Optional[str] = None
    discrepancy_notes: list[str] = []
    if has_discrepancy:
        inv_line_items, discrepancy_kind, discrepancy_notes = _apply_discrepancy(
            inv_line_items, rng, catalog
        )

    # ── Terminology picks ─────────────────────────────────────────────────────
    # Indices are captured alongside each choice so layout_engine.py can look
    # up a translated equivalent (lang_cfg.labels) for the SAME pool entry,
    # instead of rendering this English string directly regardless of
    # --language (see the note above the pool definitions for why the
    # strings here stay English: they're the OCR ground-truth value).
    buyer_term_idx = rng.randrange(len(BUYER_TERMS))
    seller_term_idx = rng.randrange(len(SELLER_TERMS))
    ship_term_idx = rng.randrange(len(SHIP_TERMS))
    po_num_term_idx = rng.randrange(len(PO_TERMS))
    date_term_idx = rng.randrange(len(DATE_TERMS))
    inv_num_term_idx = rng.randrange(len(INV_TERMS))
    inv_date_term_idx = rng.randrange(len(INVDATE_TERMS))
    sign_off_idx = rng.randrange(len(SIGN_OFFS))

    buyer_term = BUYER_TERMS[buyer_term_idx]
    seller_term = SELLER_TERMS[seller_term_idx]
    ship_term = SHIP_TERMS[ship_term_idx]
    po_num_term = PO_TERMS[po_num_term_idx]
    date_term = DATE_TERMS[date_term_idx]
    inv_num_term = INV_TERMS[inv_num_term_idx]
    inv_date_term = INVDATE_TERMS[inv_date_term_idx]
    sign_off = SIGN_OFFS[sign_off_idx]

    # ── Assemble PO ───────────────────────────────────────────────────────────
    po = PurchaseOrder(
        doc_index=idx,
        po_number=po_num,
        po_date=po_date.strftime("%d/%m/%Y"),
        buyer_name=buyer_name,
        buyer_address=buyer_addr,
        buyer_gstin=buyer_gstin,
        seller_name=seller_name,
        seller_address=seller_addr,
        seller_gstin=seller_gstin,
        shipping_address=ship_addr,
        line_items=line_items,
        payment_terms=payment_terms,
        buyer_term=buyer_term,
        seller_term=seller_term,
        ship_term=ship_term,
        po_num_term=po_num_term,
        date_term=date_term,
        sign_off=sign_off,
        layout_variant=layout_variant,
        buyer_term_idx=buyer_term_idx,
        seller_term_idx=seller_term_idx,
        ship_term_idx=ship_term_idx,
        po_num_term_idx=po_num_term_idx,
        date_term_idx=date_term_idx,
        sign_off_idx=sign_off_idx,
        payment_terms_idx=payment_terms_idx,
    )

    # ── Assemble Invoice ──────────────────────────────────────────────────────
    inv = TaxInvoice(
        doc_index=idx,
        invoice_number=inv_num,
        invoice_date=inv_date.strftime("%d/%m/%Y"),
        po_number=po_num,
        buyer_name=buyer_name,
        buyer_address=buyer_addr,
        buyer_gstin=buyer_gstin,
        seller_name=seller_name,
        seller_address=seller_addr,
        seller_gstin=seller_gstin,
        shipping_address=ship_addr,
        line_items=inv_line_items,
        is_interstate=is_interstate,
        payment_terms=payment_terms,
        due_date=due_date.strftime("%d/%m/%Y"),
        inv_num_term=inv_num_term,
        inv_date_term=inv_date_term,
        buyer_term=buyer_term,
        seller_term=seller_term,
        ship_term=ship_term,
        sign_off=sign_off,
        layout_variant=layout_variant,
        inv_num_term_idx=inv_num_term_idx,
        inv_date_term_idx=inv_date_term_idx,
        buyer_term_idx=buyer_term_idx,
        seller_term_idx=seller_term_idx,
        ship_term_idx=ship_term_idx,
        sign_off_idx=sign_off_idx,
        payment_terms_idx=payment_terms_idx,
        has_discrepancy=has_discrepancy,
        discrepancy_kind=discrepancy_kind,
        discrepancy_notes=discrepancy_notes,
    )

    return po, inv