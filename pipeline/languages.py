"""
pipeline/languages.py
=====================
Language configuration for the B2B document pipeline.

Provides LanguageConfig for every language LibreTranslate supports (133 total),
plus a get_language_config() factory used by assembler.py.

TIER SYSTEM
-----------
Tier 1 (hand-authored, verified)     : en, fr
Tier 2 (hand-authored, unverified)   : hi, ur, ar, bn, zh, ja, ko, de, es,
                                        pt, ru, ta, te, tr, vi, pa, gu, mr,
                                        ml, kn, or, as, ne, si, my, th, id,
                                        ms, sw, fa, ps, sd, ug
Tier 3 (font + metadata only, labels auto-translated at runtime via LibreTranslate)
      : everything else

HOW TIER 3 WORKS
-----------------
- A LanguageConfig is pre-registered for every known LT language with correct
  font, direction, and name — so the pipeline can always *start* generating.
- Labels are translated lazily the first time that language is requested.
- If LibreTranslate is not running, Tier 3 labels fall back to English text
  but the correct font and RTL direction are still applied.
- You don't need to download all language model pairs upfront. Only the pairs
  you actually use need to be loaded in LibreTranslate.

LIBRETRANSLATE VENV RECOMMENDATION (see below)
-----------------------------------------------
Keep it, move it, or merge it — options explained at bottom of this file.

ADDING A NEW LANGUAGE
---------------------
1. Add its code to _METADATA with direction + font info.
2. If you want Tier 2 quality, add a hand-authored _LABELS_xx dict and register
   it in _BUILTIN_LABELS.
3. Otherwise it auto-translates at runtime — nothing else needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("languages")


# ─────────────────────────────────────────────────────────────────────────────
#  LanguageConfig dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LanguageConfig:
    code: str            # ISO 639-1 (or BCP-47) code, e.g. "ur", "zh", "zh-TW"
    name: str            # Human-readable English name
    tier: int            # 1=verified, 2=hand-authored-unverified, 3=auto-translated
    direction: str       # "ltr" or "rtl"
    font_family: str     # CSS font-family string (already has fallbacks)
    font_import_url: str # Google Fonts @import URL; empty = use system fonts
    currency_symbol: str # ₹ for India-focused pipeline; override per deployment
    labels: dict         # All localised UI strings


# ─────────────────────────────────────────────────────────────────────────────
#  RTL language codes
# ─────────────────────────────────────────────────────────────────────────────

_RTL = {
    "ar", "dv", "fa", "he", "iw", "ku", "ckb", "ps", "sd",
    "ug", "ur", "yi",
}

# ─────────────────────────────────────────────────────────────────────────────
#  Google Font mapping  →  (css_family, import_url)
#  Noto covers almost everything; specific families for major scripts.
# ─────────────────────────────────────────────────────────────────────────────

_GF_BASE = "https://fonts.googleapis.com/css2?family="
_GF_SUFFIX = ":wght@400;700&display=swap"

def _gf(family: str) -> tuple[str, str]:
    slug = family.replace(" ", "+")
    return (
        f"'{family}', sans-serif",
        f"{_GF_BASE}{slug}{_GF_SUFFIX}",
    )

def _gf2(f1: str, f2: str) -> tuple[str, str]:
    s1 = f1.replace(" ", "+")
    s2 = f2.replace(" ", "+")
    return (
        f"'{f1}', '{f2}', sans-serif",
        f"{_GF_BASE}{s1}{_GF_SUFFIX}&family={s2}{_GF_SUFFIX}",
    )

# Script → (font_family_css, import_url)
_FONT: dict[str, tuple[str, str]] = {
    # Arabic-script
    "ar":      _gf("Noto Sans Arabic"),
    "fa":      _gf("Noto Sans Arabic"),
    "ps":      _gf("Noto Sans Arabic"),
    "sd":      _gf("Noto Sans Arabic"),
    "ug":      _gf("Noto Sans Arabic"),
    "ur":      ("'Noto Nastaliq Urdu', 'Noto Sans Arabic', serif",
                f"{_GF_BASE}Noto+Nastaliq+Urdu{_GF_SUFFIX}&family=Noto+Sans+Arabic{_GF_SUFFIX}"),
    "ku":      _gf("Noto Sans Arabic"),
    "ckb":     _gf("Noto Sans Arabic"),
    # Hebrew-script
    "he":      _gf("Noto Sans Hebrew"),
    "iw":      _gf("Noto Sans Hebrew"),
    "yi":      _gf("Noto Sans Hebrew"),
    # Devanagari
    "hi":      _gf("Noto Sans Devanagari"),
    "mr":      _gf("Noto Sans Devanagari"),
    "ne":      _gf("Noto Sans Devanagari"),
    "mai":     _gf("Noto Sans Devanagari"),
    "bho":     _gf("Noto Sans Devanagari"),
    "doi":     _gf("Noto Sans Devanagari"),
    "gom":     _gf("Noto Sans Devanagari"),
    "sa":      _gf("Noto Sans Devanagari"),
    # Bengali
    "bn":      _gf("Noto Sans Bengali"),
    "as":      _gf("Noto Sans Bengali"),
    # Chinese
    "zh":      _gf("Noto Sans SC"),
    "zh-TW":   _gf("Noto Sans TC"),
    # Japanese
    "ja":      _gf("Noto Sans JP"),
    # Korean
    "ko":      _gf("Noto Sans KR"),
    # Tamil
    "ta":      _gf("Noto Sans Tamil"),
    # Telugu
    "te":      _gf("Noto Sans Telugu"),
    # Kannada
    "kn":      _gf("Noto Sans Kannada"),
    # Malayalam
    "ml":      _gf("Noto Sans Malayalam"),
    # Gujarati
    "gu":      _gf("Noto Sans Gujarati"),
    # Punjabi / Gurmukhi
    "pa":      _gf("Noto Sans Gurmukhi"),
    # Odia
    "or":      _gf("Noto Sans Oriya"),
    # Sinhala
    "si":      _gf("Noto Sans Sinhala"),
    # Myanmar
    "my":      _gf("Noto Sans Myanmar"),
    # Thai
    "th":      _gf("Noto Sans Thai"),
    # Khmer
    "km":      _gf("Noto Sans Khmer"),
    # Lao
    "lo":      _gf("Noto Sans Lao"),
    # Georgian
    "ka":      _gf("Noto Sans Georgian"),
    # Armenian
    "hy":      _gf("Noto Sans Armenian"),
    # Ethiopic (Amharic, Tigrinya, Oromo)
    "am":      _gf("Noto Sans Ethiopic"),
    "ti":      _gf("Noto Sans Ethiopic"),
    # Dhivehi (Thaana script — RTL)
    "dv":      _gf("Noto Sans Thaana"),
    # Mongolian
    "mn":      _gf("Noto Sans Mongolian"),
}

# Default for Latin/Cyrillic/Greek scripts — covers most of the rest
_LATIN_FONT = _gf("Noto Sans")


def _font_for(code: str) -> tuple[str, str]:
    return _FONT.get(code, _LATIN_FONT)


# ─────────────────────────────────────────────────────────────────────────────
#  Full language metadata table  (code, name, direction)
#  133 languages from LibreTranslate's supported set
# ─────────────────────────────────────────────────────────────────────────────

_METADATA: list[tuple[str, str]] = [
    ("af",       "Afrikaans"),
    ("ak",       "Twi (Akan)"),
    ("am",       "Amharic"),
    ("ar",       "Arabic"),
    ("as",       "Assamese"),
    ("ay",       "Aymara"),
    ("az",       "Azerbaijani"),
    ("ba",       "Bashkir"),
    ("be",       "Belarusian"),
    ("bg",       "Bulgarian"),
    ("bho",      "Bhojpuri"),
    ("bm",       "Bambara"),
    ("bn",       "Bengali"),
    ("bs",       "Bosnian"),
    ("ca",       "Catalan"),
    ("ceb",      "Cebuano"),
    ("ckb",      "Sorani Kurdish"),
    ("co",       "Corsican"),
    ("cs",       "Czech"),
    ("cy",       "Welsh"),
    ("da",       "Danish"),
    ("de",       "German"),
    ("doi",      "Dogri"),
    ("dv",       "Dhivehi"),
    ("ee",       "Ewe"),
    ("el",       "Greek"),
    ("en",       "English"),
    ("eo",       "Esperanto"),
    ("es",       "Spanish"),
    ("et",       "Estonian"),
    ("eu",       "Basque"),
    ("fa",       "Persian"),
    ("fi",       "Finnish"),
    ("fr",       "French"),
    ("fy",       "Frisian"),
    ("ga",       "Irish"),
    ("gd",       "Scots Gaelic"),
    ("gl",       "Galician"),
    ("gom",      "Konkani"),
    ("gu",       "Gujarati"),
    ("ha",       "Hausa"),
    ("haw",      "Hawaiian"),
    ("hi",       "Hindi"),
    ("hmn",      "Hmong"),
    ("hr",       "Croatian"),
    ("ht",       "Haitian Creole"),
    ("hu",       "Hungarian"),
    ("hy",       "Armenian"),
    ("id",       "Indonesian"),
    ("ig",       "Igbo"),
    ("ilo",      "Ilocano"),
    ("is",       "Icelandic"),
    ("it",       "Italian"),
    ("iw",       "Hebrew"),
    ("ja",       "Japanese"),
    ("jw",       "Javanese"),
    ("ka",       "Georgian"),
    ("kk",       "Kazakh"),
    ("km",       "Khmer"),
    ("kn",       "Kannada"),
    ("ko",       "Korean"),
    ("kri",      "Krio"),
    ("ku",       "Kurdish (Kurmanji)"),
    ("ky",       "Kyrgyz"),
    ("la",       "Latin"),
    ("lb",       "Luxembourgish"),
    ("lg",       "Luganda"),
    ("ln",       "Lingala"),
    ("lo",       "Lao"),
    ("lt",       "Lithuanian"),
    ("lus",      "Mizo"),
    ("lv",       "Latvian"),
    ("mai",      "Maithili"),
    ("mg",       "Malagasy"),
    ("mi",       "Maori"),
    ("mk",       "Macedonian"),
    ("ml",       "Malayalam"),
    ("mn",       "Mongolian"),
    ("mni-Mtei", "Meitei (Manipuri)"),
    ("mr",       "Marathi"),
    ("ms",       "Malay"),
    ("mt",       "Maltese"),
    ("my",       "Myanmar (Burmese)"),
    ("ne",       "Nepali"),
    ("nl",       "Dutch"),
    ("no",       "Norwegian"),
    ("nso",      "Sepedi"),
    ("ny",       "Chichewa"),
    ("om",       "Oromo"),
    ("or",       "Odia (Oriya)"),
    ("pa",       "Punjabi"),
    ("pl",       "Polish"),
    ("ps",       "Pashto"),
    ("pt",       "Portuguese"),
    ("qu",       "Quechua"),
    ("ro",       "Romanian"),
    ("ru",       "Russian"),
    ("rw",       "Kinyarwanda"),
    ("sa",       "Sanskrit"),
    ("sd",       "Sindhi"),
    ("si",       "Sinhala"),
    ("sk",       "Slovak"),
    ("sl",       "Slovenian"),
    ("sm",       "Samoan"),
    ("sn",       "Shona"),
    ("so",       "Somali"),
    ("sq",       "Albanian"),
    ("sr",       "Serbian"),
    ("st",       "Sesotho"),
    ("su",       "Sundanese"),
    ("sv",       "Swedish"),
    ("sw",       "Swahili"),
    ("ta",       "Tamil"),
    ("te",       "Telugu"),
    ("tg",       "Tajik"),
    ("th",       "Thai"),
    ("ti",       "Tigrinya"),
    ("tk",       "Turkmen"),
    ("tl",       "Filipino"),
    ("tr",       "Turkish"),
    ("ts",       "Tsonga"),
    ("tt",       "Tatar"),
    ("ug",       "Uyghur"),
    ("uk",       "Ukrainian"),
    ("ur",       "Urdu"),
    ("uz",       "Uzbek"),
    ("vi",       "Vietnamese"),
    ("xh",       "Xhosa"),
    ("yi",       "Yiddish"),
    ("yo",       "Yoruba"),
    ("zh",       "Chinese (Simplified)"),
    ("zh-TW",    "Chinese (Traditional)"),
    ("zu",       "Zulu"),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Hand-authored label sets  (Tier 1 and Tier 2)
# ─────────────────────────────────────────────────────────────────────────────

# English defaults for every label layout_engine.py looks up. Any hand-authored
# language dict that omits a key falls back to this (via _base_labels' **extra
# merge below) rather than silently falling back all the way to hardcoded
# English inside layout_engine.py itself — that hidden second fallback layer
# was the actual root cause of "only some of the invoice is translated": new
# label keys kept getting added straight into layout_engine.py as literal
# English strings instead of going through lang_cfg.labels at all, so no
# amount of translating _BUILTIN_LABELS could ever reach them. Every string
# layout_engine.py prints now has a key here AND is looked up via _L()/_LP().
_EXTRA_DEFAULTS: dict[str, str] = {
    "meta_title_po":        "Order Details",
    "meta_title_invoice":   "Invoice Details",
    "payment_terms":        "Payment Terms",
    "against_po":           "Against PO",
    "due_date":             "Due Date",
    "supply_type":          "Supply Type",
    "inter_state":          "Inter-State",
    "intra_state":          "Intra-State",
    "col_tax_header":       "Tax",
    "po_value_excl_gst":    "PO Value (excl. GST)",
    "gst_note":             "GST applicable as per Tax Invoice",
    "footer_po_notice":     "This is a computer-generated Purchase Order.",
    "footer_po_subject_to": "Subject to terms and conditions of",
    "footer_inv_notice":    "This is a computer-generated Tax Invoice under GST regulations.",
    "footer_inv_no_sig":    "No signature required if generated electronically.",
    "po_ref":               "PO Ref",
    # "Floating terminology" pools — MUST stay same length/order as the
    # English pools in data_models.py (BUYER_TERMS, SELLER_TERMS, etc.) so
    # the *_term_idx fields on PurchaseOrder/TaxInvoice index correctly into
    # whichever language pool is active. layout_engine.py falls back to the
    # English pool value (via data_models' own list) if a language's pool
    # list is missing or too short for a given index.
    "buyer_terms":       ["Bill To", "Buyer", "Purchaser", "Client", "Consignee", "Billed Party"],
    "seller_terms":      ["Sold By", "Vendor", "Supplier", "From", "Issued By", "Seller"],
    "ship_terms":        ["Ship To", "Delivery Address", "Dispatch To", "Consign To", "Deliver At"],
    "po_num_terms":      ["Purchase Order No.", "PO Number", "Order Reference", "PO Ref.", "Requisition No."],
    "date_terms":        ["PO Date", "Order Date", "Issue Date", "Date of Order"],
    "inv_num_terms":     ["Tax Invoice No.", "Invoice Number", "Invoice Ref.", "Bill No."],
    "invdate_terms":     ["Invoice Date", "Bill Date", "Date of Invoice", "Issue Date"],
    "sign_offs":         ["Authorised Signatory", "For & on behalf of", "Approved by",
                           "Authorised by", "Signed", "Finance Head"],
    "payment_terms_pool": ["Net 30", "Net 45", "Net 60", "Due on Receipt", "2/10 Net 30",
                            "Net 15", "COD", "30 Days EOM", "Immediate", "Net 90"],
}


def _base_labels(
    po, inv, desc, unit, qty, price, total, gst_rate, tax_amt,
    subtotal, total_tax, grand_total, stamps, **extra
) -> dict:
    """
    Helper to build a labels dict — keeps hand-authored blocks DRY.

    `extra` lets a language override any of _EXTRA_DEFAULTS' English defaults
    with a real translation (see e.g. "hi"/"ur"/"fr" below for examples).
    Languages that don't pass `extra` simply get the English default for
    those keys — better than the old behaviour (literal English baked into
    layout_engine.py with NO key at all, so it could never be overridden).
    """
    labels = {
        "doc_title_po":      po,
        "doc_title_invoice": inv,
        "col_description":   desc,
        "col_hsn_sac":       "HSN/SAC",
        "col_unit":          unit,
        "col_qty":           qty,
        "col_unit_price":    price,
        "col_total":         total,
        "col_gst_rate":      gst_rate,
        "col_tax_amount":    tax_amt,
        "subtotal":          subtotal,
        "total_tax":         total_tax,
        "grand_total":       grand_total,
        "cgst": "CGST", "sgst": "SGST", "igst": "IGST",
        "stamp_texts":       stamps,
    }
    labels.update(_EXTRA_DEFAULTS)
    labels.update(extra)
    return labels


_BUILTIN_LABELS: dict[str, dict] = {

    "en": _base_labels(
        "PURCHASE ORDER", "TAX INVOICE",
        "Description", "Unit", "Qty", "Unit Price", "Total",
        "GST Rate", "Tax Amount", "Sub-Total", "Total Tax", "GRAND TOTAL",
        ["ORIGINAL", "VERIFIED", "APPROVED", "TAX INVOICE", "PAID", "PROCESSED"],
        meta_title_po="Order Details", meta_title_invoice="Invoice Details",
        payment_terms="Payment Terms", against_po="Against PO", due_date="Due Date",
        supply_type="Supply Type", inter_state="Inter-State", intra_state="Intra-State",
        col_tax_header="Tax", po_value_excl_gst="PO Value (excl. GST)",
        gst_note="GST applicable as per Tax Invoice",
        footer_po_notice="This is a computer-generated Purchase Order.",
        footer_po_subject_to="Subject to terms and conditions of",
        footer_inv_notice="This is a computer-generated Tax Invoice under GST regulations.",
        footer_inv_no_sig="No signature required if generated electronically.",
        po_ref="PO Ref",
    ),

    "fr": _base_labels(
        "BON DE COMMANDE", "FACTURE FISCALE",
        "Description", "Unité", "Qté", "Prix Unitaire", "Total",
        "Taux GST", "Montant Taxe", "Sous-Total", "Total Taxes", "TOTAL GÉNÉRAL",
        ["ORIGINAL", "VÉRIFIÉ", "APPROUVÉ", "FACTURE FISCALE", "PAYÉ", "TRAITÉ"],
        meta_title_po="Détails de la Commande", meta_title_invoice="Détails de la Facture",
        payment_terms="Conditions de Paiement", against_po="Réf. Commande", due_date="Date d'Échéance",
        supply_type="Type de Livraison", inter_state="Inter-État", intra_state="Intra-État",
        col_tax_header="Taxe", po_value_excl_gst="Valeur Commande (hors GST)",
        gst_note="GST applicable selon la Facture Fiscale",
        footer_po_notice="Ceci est un Bon de Commande généré par ordinateur.",
        footer_po_subject_to="Soumis aux conditions générales de",
        footer_inv_notice="Ceci est une Facture Fiscale générée par ordinateur selon la réglementation GST.",
        footer_inv_no_sig="Aucune signature requise si générée électroniquement.",
        po_ref="Réf. Commande",
        buyer_terms=["Facturé À", "Acheteur", "Acquéreur", "Client", "Destinataire", "Partie Facturée"],
        seller_terms=["Vendu Par", "Vendeur", "Fournisseur", "De", "Émis Par", "Vendeur"],
        ship_terms=["Livré À", "Adresse de Livraison", "Expédié À", "Consigné À", "Livraison Chez"],
        po_num_terms=["N° Bon de Commande", "Numéro BC", "Référence Commande", "Réf. BC", "N° Réquisition"],
        date_terms=["Date BC", "Date Commande", "Date d'Émission", "Date de la Commande"],
        inv_num_terms=["N° Facture Fiscale", "Numéro de Facture", "Réf. Facture", "N° de Note"],
        invdate_terms=["Date de Facture", "Date de Note", "Date de la Facture", "Date d'Émission"],
        sign_offs=["Signataire Autorisé", "Pour le compte de", "Approuvé par",
                   "Autorisé par", "Signé", "Directeur Financier"],
        payment_terms_pool=["Net 30", "Net 45", "Net 60", "Paiement à Réception", "2/10 Net 30",
                            "Net 15", "Contre Remboursement", "30 Jours Fin de Mois", "Immédiat", "Net 90"],
    ),

    "hi": _base_labels(
        "क्रय आदेश", "कर चालान",
        "विवरण", "इकाई", "मात्रा", "इकाई मूल्य", "कुल",
        "GST दर", "कर राशि", "उप-योग", "कुल कर", "कुल योग",
        ["मूल", "सत्यापित", "अनुमोदित", "कर चालान", "भुगतान", "संसाधित"],
        meta_title_po="आदेश विवरण", meta_title_invoice="चालान विवरण",
        payment_terms="भुगतान शर्तें", against_po="आदेश संदर्भ", due_date="देय तिथि",
        supply_type="सप्लाई प्रकार", inter_state="अंतर-राज्य", intra_state="अंतः-राज्य",
        col_tax_header="कर", po_value_excl_gst="आदेश मूल्य (GST रहित)",
        gst_note="कर चालान के अनुसार GST लागू",
        footer_po_notice="यह एक कंप्यूटर-जनित क्रय आदेश है।",
        footer_po_subject_to="नियम एवं शर्तों के अधीन",
        footer_inv_notice="यह GST नियमों के अंतर्गत एक कंप्यूटर-जनित कर चालान है।",
        footer_inv_no_sig="इलेक्ट्रॉनिक रूप से जनित होने पर हस्ताक्षर आवश्यक नहीं है।",
        po_ref="आदेश संदर्भ",
        buyer_terms=["बिल टू", "खरीदार", "क्रेता", "ग्राहक", "प्रापक", "बिल प्राप्तकर्ता"],
        seller_terms=["विक्रेता द्वारा", "विक्रेता", "सप्लायर", "प्रेषक", "जारीकर्ता", "विक्रेता"],
        ship_terms=["शिप टू", "डिलीवरी पता", "प्रेषण स्थान", "कंसाइन टू", "डिलीवरी स्थल"],
        po_num_terms=["क्रय आदेश संख्या", "PO संख्या", "आदेश संदर्भ", "PO रेफ.", "अधियाचन संख्या"],
        date_terms=["PO तिथि", "आदेश तिथि", "जारी तिथि", "आदेश की तिथि"],
        inv_num_terms=["कर चालान संख्या", "चालान संख्या", "चालान संदर्भ", "बिल संख्या"],
        invdate_terms=["चालान तिथि", "बिल तिथि", "चालान की तिथि", "जारी तिथि"],
        sign_offs=["अधिकृत हस्ताक्षरकर्ता", "की ओर से", "द्वारा अनुमोदित",
                   "अधिकृत द्वारा", "हस्ताक्षरित", "वित्त प्रमुख"],
        payment_terms_pool=["नेट 30", "नेट 45", "नेट 60", "प्राप्ति पर देय", "2/10 नेट 30",
                            "नेट 15", "COD", "30 दिन माह अंत", "तुरंत", "नेट 90"],
    ),

    "ur": _base_labels(
        "خریداری آرڈر", "ٹیکس انوائس",
        "تفصیل", "اکائی", "مقدار", "فی اکائی قیمت", "کل",
        "GST شرح", "ٹیکس رقم", "ذیلی کل", "کل ٹیکس", "مجموعی کل",
        ["اصل", "تصدیق شدہ", "منظور شدہ", "ٹیکس انوائس", "ادا شدہ", "کارروائی شدہ"],
        meta_title_po="آرڈر کی تفصیلات", meta_title_invoice="انوائس کی تفصیلات",
        payment_terms="ادائیگی کی شرائط", against_po="آرڈر کے خلاف", due_date="آخری تاریخ",
        supply_type="سپلائی کی قسم", inter_state="بین الریاستی", intra_state="اندرون ریاست",
        col_tax_header="ٹیکس", po_value_excl_gst="آرڈر کی مالیت (بغیر GST)",
        gst_note="ٹیکس انوائس کے مطابق GST لاگو",
        footer_po_notice="یہ ایک کمپیوٹر سے تیار کردہ خریداری آرڈر ہے۔",
        footer_po_subject_to="شرائط و ضوابط سے مشروط",
        footer_inv_notice="یہ GST قوانین کے تحت کمپیوٹر سے تیار کردہ ٹیکس انوائس ہے۔",
        footer_inv_no_sig="الیکٹرانک طور پر تیار ہونے کی صورت میں دستخط ضروری نہیں۔",
        po_ref="آرڈر کے خلاف",
        buyer_terms=["بل ٹو", "خریدار", "خریدنے والا", "کلائنٹ", "وصول کنندہ", "بل وصول کنندہ"],
        seller_terms=["فروخت کنندہ", "وینڈر", "سپلائر", "بھیجنے والا", "جاری کنندہ", "بیچنے والا"],
        ship_terms=["شپ ٹو", "ترسیل کا پتہ", "روانگی کی جگہ", "کنسائن ٹو", "ترسیل کی جگہ"],
        po_num_terms=["خریداری آرڈر نمبر", "PO نمبر", "آرڈر حوالہ", "PO ریف", "درخواست نمبر"],
        date_terms=["PO تاریخ", "آرڈر کی تاریخ", "اجراء کی تاریخ", "آرڈر کی تاریخ"],
        inv_num_terms=["ٹیکس انوائس نمبر", "انوائس نمبر", "انوائس حوالہ", "بل نمبر"],
        invdate_terms=["انوائس کی تاریخ", "بل کی تاریخ", "انوائس کی تاریخ", "اجراء کی تاریخ"],
        sign_offs=["مجاز دستخط کنندہ", "کی جانب سے", "منظور کردہ از",
                   "مجاز کردہ از", "دستخط شدہ", "فنانس ہیڈ"],
        payment_terms_pool=["نیٹ 30", "نیٹ 45", "نیٹ 60", "وصولی پر ادائیگی", "2/10 نیٹ 30",
                            "نیٹ 15", "ادائیگی بوقت ترسیل", "30 دن ماہ کے اختتام پر", "فوری", "نیٹ 90"],
    ),

    "ar": _base_labels(
        "أمر شراء", "فاتورة ضريبية",
        "الوصف", "الوحدة", "الكمية", "سعر الوحدة", "الإجمالي",
        "معدل ضريبة السلع والخدمات", "مبلغ الضريبة",
        "المجموع الفرعي", "إجمالي الضريبة", "المجموع الكلي",
        ["أصل", "تم التحقق", "موافق عليه", "فاتورة ضريبية", "مدفوع", "تمت المعالجة"],
        meta_title_po="تفاصيل الطلب", meta_title_invoice="تفاصيل الفاتورة",
        payment_terms="شروط الدفع", against_po="مقابل أمر الشراء", due_date="تاريخ الاستحقاق",
        supply_type="نوع التوريد", inter_state="بين الولايات", intra_state="داخل الولاية",
        col_tax_header="الضريبة", po_value_excl_gst="قيمة أمر الشراء (بدون GST)",
        gst_note="ضريبة السلع والخدمات مطبقة وفق الفاتورة الضريبية",
        footer_po_notice="هذا أمر شراء تم إنشاؤه بواسطة الكمبيوتر.",
        footer_po_subject_to="خاضع لشروط وأحكام",
        footer_inv_notice="هذه فاتورة ضريبية تم إنشاؤها بواسطة الكمبيوتر وفقًا لأنظمة GST.",
        footer_inv_no_sig="لا حاجة لتوقيع إذا تم إنشاؤها إلكترونيًا.",
        po_ref="مرجع أمر الشراء",
        buyer_terms=["فاتورة إلى", "المشتري", "الشاري", "العميل", "المرسل إليه", "الجهة المفوترة"],
        seller_terms=["مباع من قبل", "البائع", "المورد", "من", "صادر من", "البائع"],
        ship_terms=["الشحن إلى", "عنوان التسليم", "إرسال إلى", "تسليم لـ", "مكان التسليم"],
        po_num_terms=["رقم أمر الشراء", "رقم PO", "مرجع الطلب", "مرجع PO", "رقم طلب الشراء"],
        date_terms=["تاريخ أمر الشراء", "تاريخ الطلب", "تاريخ الإصدار", "تاريخ الطلب"],
        inv_num_terms=["رقم الفاتورة الضريبية", "رقم الفاتورة", "مرجع الفاتورة", "رقم الفاتورة"],
        invdate_terms=["تاريخ الفاتورة", "تاريخ الفاتورة", "تاريخ إصدار الفاتورة", "تاريخ الإصدار"],
        sign_offs=["موقع مفوض", "نيابة عن", "موافق عليه من قبل",
                   "مفوض من قبل", "موقّع", "رئيس الشؤون المالية"],
        payment_terms_pool=["30 يومًا", "45 يومًا", "60 يومًا", "الدفع عند الاستلام", "2/10 خلال 30 يومًا",
                            "15 يومًا", "الدفع عند التسليم", "30 يومًا نهاية الشهر", "فوري", "90 يومًا"],
    ),

    "bn": _base_labels(
        "ক্রয় আদেশ", "কর চালান",
        "বিবরণ", "একক", "পরিমাণ", "একক মূল্য", "মোট",
        "GST হার", "করের পরিমাণ", "উপ-মোট", "মোট কর", "সর্বমোট",
        ["মূল", "যাচাইকৃত", "অনুমোদিত", "কর চালান", "পরিশোধিত", "প্রক্রিয়াকৃত"],
    ),

    "de": _base_labels(
        "BESTELLUNG", "STEUERRECHNUNG",
        "Beschreibung", "Einheit", "Menge", "Stückpreis", "Gesamt",
        "GST-Satz", "Steuerbetrag", "Zwischensumme", "Gesamtsteuer", "GESAMTSUMME",
        ["ORIGINAL", "GEPRÜFT", "GENEHMIGT", "STEUERRECHNUNG", "BEZAHLT", "VERARBEITET"],
    ),

    "es": _base_labels(
        "ORDEN DE COMPRA", "FACTURA FISCAL",
        "Descripción", "Unidad", "Cant.", "Precio Unitario", "Total",
        "Tasa GST", "Monto Impuesto", "Subtotal", "Total Impuesto", "TOTAL GENERAL",
        ["ORIGINAL", "VERIFICADO", "APROBADO", "FACTURA FISCAL", "PAGADO", "PROCESADO"],
    ),

    "pt": _base_labels(
        "ORDEM DE COMPRA", "FATURA FISCAL",
        "Descrição", "Unidade", "Qtd", "Preço Unitário", "Total",
        "Taxa GST", "Valor Imposto", "Subtotal", "Total Imposto", "TOTAL GERAL",
        ["ORIGINAL", "VERIFICADO", "APROVADO", "FATURA FISCAL", "PAGO", "PROCESSADO"],
    ),

    "ru": _base_labels(
        "ЗАКАЗ НА ПОКУПКУ", "НАЛОГОВЫЙ СЧЁТ",
        "Описание", "Единица", "Кол-во", "Цена за единицу", "Итого",
        "Ставка GST", "Сумма налога", "Подытог", "Итого налог", "ОБЩИЙ ИТОГ",
        ["ОРИГИНАЛ", "ПРОВЕРЕНО", "УТВЕРЖДЕНО", "НАЛОГОВЫЙ СЧЁТ", "ОПЛАЧЕНО", "ОБРАБОТАНО"],
    ),

    "zh": _base_labels(
        "采购订单", "税务发票",
        "描述", "单位", "数量", "单价", "总计",
        "GST税率", "税额", "小计", "总税", "总金额",
        ["原件", "已验证", "已批准", "税务发票", "已付款", "已处理"],
    ),

    "zh-TW": _base_labels(
        "採購訂單", "稅務發票",
        "描述", "單位", "數量", "單價", "總計",
        "GST稅率", "稅額", "小計", "總稅", "總金額",
        ["原件", "已驗證", "已批准", "稅務發票", "已付款", "已處理"],
    ),

    "ja": _base_labels(
        "発注書", "税務請求書",
        "説明", "単位", "数量", "単価", "合計",
        "GST税率", "税額", "小計", "合計税", "総合計",
        ["原本", "確認済み", "承認済み", "税務請求書", "支払済み", "処理済み"],
    ),

    "ko": _base_labels(
        "구매 주문서", "세금 계산서",
        "설명", "단위", "수량", "단가", "합계",
        "GST 세율", "세액", "소계", "총 세금", "총 합계",
        ["원본", "확인됨", "승인됨", "세금 계산서", "결제됨", "처리됨"],
    ),

    "ta": _base_labels(
        "கொள்முதல் ஆணை", "வரி விலைப்பட்டியல்",
        "விளக்கம்", "அலகு", "அளவு", "அலகு விலை", "மொத்தம்",
        "GST விகிதம்", "வரி தொகை", "உப மொத்தம்", "மொத்த வரி", "பொது மொத்தம்",
        ["அசல்", "சரிபார்க்கப்பட்டது", "அங்கீகரிக்கப்பட்டது",
         "வரி விலைப்பட்டியல்", "செலுத்தப்பட்டது", "செயலாக்கப்பட்டது"],
    ),

    "te": _base_labels(
        "కొనుగోలు ఆర్డర్", "పన్ను ఇన్వాయిస్",
        "వివరణ", "యూనిట్", "పరిమాణం", "యూనిట్ ధర", "మొత్తం",
        "GST రేటు", "పన్ను మొత్తం", "ఉప-మొత్తం", "మొత్తం పన్ను", "గ్రాండ్ టోటల్",
        ["అసలు", "ధృవీకరించబడింది", "ఆమోదించబడింది",
         "పన్ను ఇన్వాయిస్", "చెల్లించబడింది", "ప్రాసెస్ చేయబడింది"],
    ),

    "gu": _base_labels(
        "ખરીદ આદેશ", "કર ભરપાઈ",
        "વર્ણન", "એકમ", "જથ્થો", "એકમ કિંમત", "કુલ",
        "GST દર", "કર રકમ", "પેટા-કુલ", "કુલ કર", "ગ્રાન્ડ ટોટલ",
        ["મૂળ", "ચકાસાયેલ", "મંજૂર", "કર ભરપાઈ", "ચૂકવેલ", "પ્રક્રિયા કરેલ"],
    ),

    "mr": _base_labels(
        "खरेदी आदेश", "कर चलन",
        "वर्णन", "एकक", "प्रमाण", "एकक किंमत", "एकूण",
        "GST दर", "कर रक्कम", "उप-एकूण", "एकूण कर", "ग्रँड टोटल",
        ["मूळ", "सत्यापित", "मंजूर", "कर चलन", "भरलेले", "प्रक्रिया केलेले"],
    ),

    "ml": _base_labels(
        "വാങ്ങൽ ഓർഡർ", "നികുതി ഇൻവോയ്സ്",
        "വിവരണം", "യൂണിറ്റ്", "അളവ്", "യൂണിറ്റ് വില", "ആകെ",
        "GST നിരക്ക്", "നികുതി തുക", "ഉപ-ആകെ", "ആകെ നികുതി", "ഗ്രാൻഡ് ടോട്ടൽ",
        ["യഥാർഥം", "പരിശോധിച്ചത്", "അംഗീകരിച്ചത്",
         "നികുതി ഇൻവോയ്സ്", "അടച്ചത്", "പ്രോസസ്സ് ചെയ്തത്"],
    ),

    "kn": _base_labels(
        "ಖರೀದಿ ಆದೇಶ", "ತೆರಿಗೆ ಇನ್ವಾಯ್ಸ್",
        "ವಿವರಣೆ", "ಘಟಕ", "ಪ್ರಮಾಣ", "ಘಟಕ ಬೆಲೆ", "ಒಟ್ಟು",
        "GST ದರ", "ತೆರಿಗೆ ಮೊತ್ತ", "ಉಪ-ಒಟ್ಟು", "ಒಟ್ಟು ತೆರಿಗೆ", "ಗ್ರ್ಯಾಂಡ್ ಟೋಟಲ್",
        ["ಮೂಲ", "ಪರಿಶೀಲಿಸಲಾಗಿದೆ", "ಅನುಮೋದಿಸಲಾಗಿದೆ",
         "ತೆರಿಗೆ ಇನ್ವಾಯ್ಸ್", "ಪಾವತಿಸಲಾಗಿದೆ", "ಪ್ರಕ್ರಿಯೆಗೊಳಿಸಲಾಗಿದೆ"],
    ),

    "pa": _base_labels(
        "ਖਰੀਦ ਆਦੇਸ਼", "ਟੈਕਸ ਇਨਵੌਇਸ",
        "ਵੇਰਵਾ", "ਇਕਾਈ", "ਮਾਤਰਾ", "ਇਕਾਈ ਕੀਮਤ", "ਕੁੱਲ",
        "GST ਦਰ", "ਟੈਕਸ ਰਕਮ", "ਉਪ-ਕੁੱਲ", "ਕੁੱਲ ਟੈਕਸ", "ਗ੍ਰੈਂਡ ਟੋਟਲ",
        ["ਅਸਲ", "ਤਸਦੀਕਸ਼ੁਦਾ", "ਮਨਜ਼ੂਰ", "ਟੈਕਸ ਇਨਵੌਇਸ", "ਅਦਾਇਗੀ ਕੀਤੀ", "ਪ੍ਰੋਸੈਸ ਕੀਤਾ"],
    ),

    "or": _base_labels(
        "କ୍ରୟ ଆଦେଶ", "ଟ୍ୟାକ୍ସ ଇନ୍‌ଭୟ୍ସ",
        "ବିବରଣ", "ଏକକ", "ପରିମାଣ", "ଏକକ ମୂଲ୍ୟ", "ମୋଟ",
        "GST ହାର", "କର ରାଶି", "ଉପ-ମୋଟ", "ମୋଟ କର", "ଗ୍ରାଣ୍ଡ ଟୋଟାଲ",
        ["ମୂଳ", "ଯାଞ୍ଚ ହୋଇଛି", "ଅନୁମୋଦିତ", "ଟ୍ୟାକ୍ସ ଇନ୍‌ଭୟ୍ସ", "ଦେୟ ଦିଆ ହୋଇଛି", "ପ୍ରକ୍ରିୟା ହୋଇଛି"],
    ),

    "ne": _base_labels(
        "खरीद आदेश", "कर चलान",
        "विवरण", "एकाइ", "मात्रा", "एकाइ मूल्य", "जम्मा",
        "GST दर", "कर रकम", "उप-जम्मा", "जम्मा कर", "ग्र्यान्ड टोटल",
        ["मूल", "प्रमाणित", "स्वीकृत", "कर चलान", "भुक्तानी", "प्रशोधित"],
    ),

    "si": _base_labels(
        "මිලදී ගැනීමේ නියෝගය", "බදු ඉන්වොයිසිය",
        "විස්තරය", "ඒකකය", "ප්‍රමාණය", "ඒකක මිල", "එකතුව",
        "GST අනුපාතය", "බදු ප්‍රමාණය", "උප-එකතුව", "මුළු බදු", "ග්‍රෑන්ඩ් ටෝටල්",
        ["මූලික", "තහවුරු කළ", "අනුමත", "බදු ඉන්වොයිසිය", "ගෙවා ඇත", "සකස් කළ"],
    ),

    "my": _base_labels(
        "ဝယ်ယူမှုအမိန့်", "အခွန်ကားချာ",
        "ဖော်ပြချက်", "ယူနစ်", "အရေအတွက်", "ယူနစ်စျေး", "စုစုပေါင်း",
        "GST နှုန်း", "အခွန်ပမာဏ", "ပင်မစုစုပေါင်း", "အခွန်စုစုပေါင်း", "ကြီးမားသောစုစုပေါင်း",
        ["မူလ", "အတည်ပြုပြီး", "ခွင့်ပြုပြီး", "အခွန်ကားချာ", "ပေးချေပြီး", "လုပ်ဆောင်ပြီး"],
    ),

    "th": _base_labels(
        "ใบสั่งซื้อ", "ใบกำกับภาษี",
        "รายละเอียด", "หน่วย", "จำนวน", "ราคาต่อหน่วย", "รวม",
        "อัตรา GST", "จำนวนภาษี", "ยอดรวมย่อย", "ภาษีรวม", "ยอดรวมทั้งหมด",
        ["ต้นฉบับ", "ตรวจสอบแล้ว", "อนุมัติแล้ว", "ใบกำกับภาษี", "ชำระแล้ว", "ดำเนินการแล้ว"],
    ),

    "id": _base_labels(
        "PESANAN PEMBELIAN", "FAKTUR PAJAK",
        "Deskripsi", "Satuan", "Qty", "Harga Satuan", "Total",
        "Tarif GST", "Jumlah Pajak", "Sub-Total", "Total Pajak", "TOTAL KESELURUHAN",
        ["ASLI", "TERVERIFIKASI", "DISETUJUI", "FAKTUR PAJAK", "DIBAYAR", "DIPROSES"],
    ),

    "ms": _base_labels(
        "PESANAN BELIAN", "INVOIS CUKAI",
        "Penerangan", "Unit", "Kuantiti", "Harga Seunit", "Jumlah",
        "Kadar GST", "Amaun Cukai", "Sub-Jumlah", "Jumlah Cukai", "JUMLAH KESELURUHAN",
        ["ASAL", "DISAHKAN", "DILULUSKAN", "INVOIS CUKAI", "DIBAYAR", "DIPROSES"],
    ),

    "sw": _base_labels(
        "AGIZO LA UNUNUZI", "ANKARA YA KODI",
        "Maelezo", "Kitengo", "Idadi", "Bei kwa Kitengo", "Jumla",
        "Kiwango cha GST", "Kiasi cha Kodi", "Jumla Ndogo", "Jumla ya Kodi", "JUMLA KUU",
        ["ASILI", "IMETHIBITISHWA", "IMEIDHINISHWA", "ANKARA YA KODI", "IMELIPWA", "IMECHAKATWA"],
    ),

    "fa": _base_labels(
        "سفارش خرید", "فاکتور مالیاتی",
        "شرح", "واحد", "تعداد", "قیمت واحد", "جمع",
        "نرخ GST", "مبلغ مالیات", "جمع فرعی", "جمع مالیات", "جمع کل",
        ["اصل", "تأیید شده", "تأییدیه", "فاکتور مالیاتی", "پرداخت شده", "پردازش شده"],
    ),

    "tr": _base_labels(
        "SATIN ALMA SİPARİŞİ", "VERGİ FATURASI",
        "Açıklama", "Birim", "Miktar", "Birim Fiyat", "Toplam",
        "GST Oranı", "Vergi Tutarı", "Ara Toplam", "Toplam Vergi", "GENEL TOPLAM",
        ["ASIL", "DOĞRULANDI", "ONAYLANDI", "VERGİ FATURASI", "ÖDENDİ", "İŞLENDİ"],
    ),

    "vi": _base_labels(
        "ĐƠN ĐẶT HÀNG", "HÓA ĐƠN THUẾ",
        "Mô tả", "Đơn vị", "Số lượng", "Đơn giá", "Thành tiền",
        "Thuế suất GST", "Số tiền thuế", "Tổng phụ", "Tổng thuế", "TỔNG CỘNG",
        ["BẢN GỐC", "ĐÃ XÁC MINH", "ĐÃ PHÊ DUYỆT", "HÓA ĐƠN THUẾ", "ĐÃ THANH TOÁN", "ĐÃ XỬ LÝ"],
    ),
}

# Tier assignment: 1 for en/fr, 2 for the rest of _BUILTIN_LABELS, 3 for all others
_TIER1 = {"en", "fr"}
_TIER2 = set(_BUILTIN_LABELS.keys()) - _TIER1


# ─────────────────────────────────────────────────────────────────────────────
#  Label cache for Tier 3 (populated lazily at runtime)
# ─────────────────────────────────────────────────────────────────────────────

_tier3_cache: dict[str, dict] = {}

_STRINGS_TO_TRANSLATE = [
    "PURCHASE ORDER", "TAX INVOICE",
    "Description", "Unit", "Qty", "Unit Price", "Total",
    "GST Rate", "Tax Amount", "Sub-Total", "Total Tax", "GRAND TOTAL",
    "ORIGINAL", "VERIFIED", "APPROVED", "TAX INVOICE", "PAID", "PROCESSED",
    "Order Details", "Invoice Details", "Payment Terms", "Against PO", "Due Date",
    "Supply Type", "Inter-State", "Intra-State", "Tax", "PO Value (excl. GST)",
    "GST applicable as per Tax Invoice",
    "This is a computer-generated Purchase Order.",
    "Subject to terms and conditions of",
    "This is a computer-generated Tax Invoice under GST regulations.",
    "No signature required if generated electronically.",
    "PO Ref",
]
_STRING_KEYS = [
    "doc_title_po", "doc_title_invoice",
    "col_description", "col_unit", "col_qty", "col_unit_price", "col_total",
    "col_gst_rate", "col_tax_amount", "subtotal", "total_tax", "grand_total",
    "stamp_0", "stamp_1", "stamp_2", "stamp_3", "stamp_4", "stamp_5",
    "meta_title_po", "meta_title_invoice", "payment_terms", "against_po", "due_date",
    "supply_type", "inter_state", "intra_state", "col_tax_header", "po_value_excl_gst",
    "gst_note",
    "footer_po_notice",
    "footer_po_subject_to",
    "footer_inv_notice",
    "footer_inv_no_sig",
    "po_ref",
]


def _translate_one(text: str, target: str, lt_url: str) -> str:
    import urllib.request, json
    payload = json.dumps({"q": text, "source": "en", "target": target, "format": "text"}).encode()
    req = urllib.request.Request(
        lt_url.rstrip("/") + "/translate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("translatedText", text)


def _build_tier3_labels(code: str, lt_url: str) -> dict:
    """Translate all UI strings once and cache them."""
    if code in _tier3_cache:
        return _tier3_cache[code]

    log.info(f"Tier 3: translating labels → '{code}' via LibreTranslate (one-time per run)")
    translated = {}
    for key, src in zip(_STRING_KEYS, _STRINGS_TO_TRANSLATE):
        try:
            translated[key] = _translate_one(src, code, lt_url)
        except Exception as e:
            log.warning(f"  [{code}] Could not translate '{src}': {e} — using English")
            translated[key] = src

    labels = {
        "doc_title_po":      translated["doc_title_po"],
        "doc_title_invoice": translated["doc_title_invoice"],
        "col_description":   translated["col_description"],
        "col_hsn_sac":       "HSN/SAC",
        "col_unit":          translated["col_unit"],
        "col_qty":           translated["col_qty"],
        "col_unit_price":    translated["col_unit_price"],
        "col_total":         translated["col_total"],
        "col_gst_rate":      translated["col_gst_rate"],
        "col_tax_amount":    translated["col_tax_amount"],
        "subtotal":          translated["subtotal"],
        "total_tax":         translated["total_tax"],
        "grand_total":       translated["grand_total"],
        "cgst": "CGST", "sgst": "SGST", "igst": "IGST",
        "stamp_texts": [
            translated["stamp_0"], translated["stamp_1"], translated["stamp_2"],
            translated["stamp_3"], translated["stamp_4"], translated["stamp_5"],
        ],
        "meta_title_po":        translated["meta_title_po"],
        "meta_title_invoice":   translated["meta_title_invoice"],
        "payment_terms":        translated["payment_terms"],
        "against_po":           translated["against_po"],
        "due_date":             translated["due_date"],
        "supply_type":          translated["supply_type"],
        "inter_state":          translated["inter_state"],
        "intra_state":          translated["intra_state"],
        "col_tax_header":       translated["col_tax_header"],
        "po_value_excl_gst":    translated["po_value_excl_gst"],
        "gst_note":             translated["gst_note"],
        "footer_po_notice":     translated["footer_po_notice"],
        "footer_po_subject_to": translated["footer_po_subject_to"],
        "footer_inv_notice":    translated["footer_inv_notice"],
        "footer_inv_no_sig":    translated["footer_inv_no_sig"],
        "po_ref":               translated["po_ref"],
    }
    _tier3_cache[code] = labels
    return labels


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_language_config(
    code: str,
    libretranslate_url: Optional[str] = None,
) -> LanguageConfig:
    """
    Return a LanguageConfig for the given ISO language code.

    - Tier 1/2 codes: returns hand-authored config instantly, no network needed.
    - Tier 3 codes: font/direction/name always available; labels translated via
      LibreTranslate on first use and cached for the rest of the run.
      If LT is unreachable, falls back to English labels with correct font/dir.
    - Unknown codes: falls back to English with a warning.

    Parameters
    ----------
    code               : ISO 639-1 or BCP-47 code ("ur", "zh-TW", "de", …)
    libretranslate_url : Base URL of running LibreTranslate (for Tier 3 only)
    """
    code = code.strip().lower()

    # Find metadata (name, direction, font)
    meta = {c.lower(): (c, name) for c, name in _METADATA}
    if code not in meta:
        log.warning(f"Language '{code}' not in known list — using English")
        code = "en"

    orig_code, name = meta[code]
    font_family, font_import_url = _font_for(orig_code)
    direction = "rtl" if orig_code in _RTL else "ltr"

    # Tier 1/2: hand-authored labels
    if orig_code in _BUILTIN_LABELS:
        tier = 1 if orig_code in _TIER1 else 2
        return LanguageConfig(
            code=orig_code, name=name, tier=tier,
            direction=direction, font_family=font_family,
            font_import_url=font_import_url, currency_symbol="₹",
            labels=_BUILTIN_LABELS[orig_code],
        )

    # Tier 3: auto-translate via LibreTranslate, fall back to English labels
    tier = 3
    if libretranslate_url:
        try:
            labels = _build_tier3_labels(orig_code, libretranslate_url)
        except Exception as e:
            log.warning(f"LibreTranslate failed for '{orig_code}': {e} — using English labels")
            labels = _BUILTIN_LABELS["en"]
    else:
        log.warning(
            f"Language '{orig_code}' is Tier 3 but no LibreTranslate URL given — "
            f"using English labels with correct font/direction"
        )
        labels = _BUILTIN_LABELS["en"]

    return LanguageConfig(
        code=orig_code, name=name, tier=tier,
        direction=direction, font_family=font_family,
        font_import_url=font_import_url, currency_symbol="₹",
        labels=labels,
    )


def list_supported_languages() -> list[dict]:
    """
    Return info on all 133 supported languages — useful for --list-languages CLI flag.
    """
    meta = {c.lower(): (c, name) for c, name in _METADATA}
    results = []
    for code_lower, (orig_code, name) in sorted(meta.items()):
        if orig_code in _TIER1:
            tier = 1
        elif orig_code in _TIER2:
            tier = 2
        else:
            tier = 3
        results.append({
            "code": orig_code,
            "name": name,
            "tier": tier,
            "direction": "rtl" if orig_code in _RTL else "ltr",
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
"""
LIBRETRANSLATE VENV RECOMMENDATION
====================================

You have two venvs:
  ~/b2b_synthetic_gen/venv        ← pipeline venv (Playwright, Faker, sklearn…)
  ~/libretranslate_venv           ← LibreTranslate server venv

RECOMMENDATION: keep them separate. Here's why and what to do:

WHY SEPARATE IS BETTER
-----------------------
LibreTranslate has heavy dependencies (Flask, Werkzeug, argostranslate, CTranslate2,
and the language model packages themselves). These can conflict with scikit-learn
and Playwright's pinned dependency versions. Separate venvs = no conflict risk.

The pipeline only communicates with LibreTranslate over HTTP (localhost:5000),
so it doesn't matter that they're in different venvs — they're not imported
together, just networked together.

WHAT TO DO WITH ~/libretranslate_venv
---------------------------------------
Option A (recommended) — leave it where it is, add a start script:

    cat > ~/b2b_synthetic_gen/start_libretranslate.sh << 'EOF'
    #!/bin/bash
    # Start LibreTranslate with only the language pairs you need
    # Add more codes to --load-only as you need them (each pair = ~300-600MB download)
    source ~/libretranslate_venv/bin/activate
    nohup libretranslate \\
        --host 127.0.0.1 --port 5000 \\
        --load-only en,ur,hi,bn,ta,te,de,es,fr,zh,ar,ru,pt,tr,vi,id,ms,sw,ko,ja \\
        > ~/libretranslate.log 2>&1 &
    disown
    echo "LibreTranslate starting... tail -f ~/libretranslate.log to watch"
    EOF
    chmod +x ~/b2b_synthetic_gen/start_libretranslate.sh

Option B — move it inside the project (cosmetic, same behaviour):

    mv ~/libretranslate_venv ~/b2b_synthetic_gen/lt_venv
    # Update start_libretranslate.sh to source ~/b2b_synthetic_gen/lt_venv/bin/activate

Option C — install into the pipeline venv (not recommended, conflict risk):
    source ~/b2b_synthetic_gen/venv/bin/activate
    pip install libretranslate   # may break sklearn/playwright deps

WHICH LANGUAGE PAIRS TO DOWNLOAD
----------------------------------
You don't need all 133 pairs upfront. Download only what you're generating:

    # Minimal: just Urdu (already downloaded from your earlier session)
    --load-only en,ur

    # Indian languages for this project:
    --load-only en,ur,hi,bn,ta,te,gu,mr,ml,kn,pa,or,ne,si,as

    # Adding major world languages:
    --load-only en,ur,hi,bn,ta,te,gu,mr,ml,kn,pa,or,ne,si,as,ar,zh,ja,ko,de,es,fr,pt,ru,tr,vi,id,ms,sw

    # Each new pair = ~300-600MB download on first --update-models run.
    # Check what's already downloaded: du -sh ~/.local/share/argos-translate

CHECKING IF LIBRETRANSLATE IS RUNNING
--------------------------------------
    curl -s http://127.0.0.1:5000/languages | python3 -m json.tool

STOPPING IT
-----------
    pkill -f libretranslate
"""