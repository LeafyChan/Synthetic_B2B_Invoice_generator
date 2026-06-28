"""
pipeline/data_models.py
=======================
Deterministic field generators for Purchase Orders and Tax Invoices.

All randomisation is seeded per document index to ensure reproducibility.
Uses Faker (Indian locale) for addresses, company names, and dates.
"""

import random
import string
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

# ── GSTIN generation ──────────────────────────────────────────────────────────
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
    # Floating terminology choices
    buyer_term: str
    seller_term: str
    ship_term: str
    po_num_term: str
    date_term: str
    sign_off: str
    # Layout variant seed
    layout_variant: int

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

    # ── Line items ─────────────────────────────────────────────────────────────
    num_items = rng.randint(1, 5)
    catalog_sample = rng.sample(catalog, min(num_items, len(catalog)))
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
    payment_terms = rng.choice(PAYMENT_TERMS_POOL)
    layout_variant = rng.randint(0, 7)

    # ── Terminology picks ─────────────────────────────────────────────────────
    buyer_term = rng.choice(BUYER_TERMS)
    seller_term = rng.choice(SELLER_TERMS)
    ship_term = rng.choice(SHIP_TERMS)
    po_num_term = rng.choice(PO_TERMS)
    date_term = rng.choice(DATE_TERMS)
    inv_num_term = rng.choice(INV_TERMS)
    inv_date_term = rng.choice(INVDATE_TERMS)
    sign_off = rng.choice(SIGN_OFFS)

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
        line_items=line_items,
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
    )

    return po, inv
