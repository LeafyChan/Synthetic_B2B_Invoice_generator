"""
pipeline/gst_rate_schedule.py
==============================
Best-effort GST rate lookup keyed by HSN/SAC code.

Structure
---------
1. CHAPTER_RATES  — default GST rate for each 2-digit HSN chapter (covers ~95%
   of cases — within a chapter most items have one dominant rate).
2. HEADING_RATES  — heading-level (4-digit) overrides for split-rate chapters
   (e.g. chapter 39 has both 12% and 18% items depending on heading).
3. lookup_rate()  — public function that checks code length and walks
   8→6→4→2 digits until it finds an override, then falls back to chapter.
4. lookup_rate() for SAC codes (starting with 99) always returns 18 unless
   the well-known 5%/12% SAC ranges apply.
5. guess_unit()   — heuristic unit-of-measure guesser from description text.

IMPORTANT: This is a best-effort approximation derived from the GST council
rate schedule as of FY 2024-25. Rates change via council notifications.
For any real tax filing, always verify against the official CBIC schedule.
For synthetic OCR/document-understanding training data (this project's use
case), the approximation is accurate enough that the patterns are learnable.
"""

from __future__ import annotations

# ── Chapter-level defaults (2-digit HSN prefix → GST %) ──────────────────────
# Where a chapter has a dominant rate, that rate is used for any code in the
# chapter unless a heading/subheading override applies.
CHAPTER_RATES: dict[str, int] = {
    # Section I — Live animals, animal products
    "01": 0,   "02": 0,   "03": 5,   "04": 5,   "05": 0,
    # Section II — Vegetable products
    "06": 5,   "07": 0,   "08": 0,   "09": 5,   "10": 0,
    "11": 0,   "12": 5,   "13": 5,   "14": 5,
    # Section III — Animal/vegetable fats
    "15": 5,
    # Section IV — Food preparations
    "16": 12,  "17": 5,   "18": 18,  "19": 18,  "20": 12,
    "21": 18,  "22": 18,  "23": 5,   "24": 28,
    # Section V — Mineral products
    "25": 5,   "26": 5,   "27": 18,
    # Section VI — Chemical products
    "28": 18,  "29": 18,  "30": 12,  "31": 5,   "32": 18,
    "33": 18,  "34": 18,  "35": 18,  "36": 18,  "37": 18,
    "38": 18,
    # Section VII — Plastics, rubber
    "39": 18,  "40": 18,
    # Section VIII — Hides, skins, leather
    "41": 5,   "42": 18,  "43": 28,
    # Section IX — Wood, cork, straw
    "44": 12,  "45": 12,  "46": 12,
    # Section X — Pulp, paper
    "47": 12,  "48": 12,  "49": 5,
    # Section XI — Textiles
    "50": 5,   "51": 5,   "52": 5,   "53": 5,   "54": 5,
    "55": 5,   "56": 12,  "57": 12,  "58": 12,  "59": 12,
    "60": 5,   "61": 5,   "62": 5,   "63": 5,
    # Section XII — Footwear, headgear
    "64": 18,  "65": 18,  "66": 18,  "67": 18,
    # Section XIII — Stone, plaster, cement, ceramic
    "68": 18,  "69": 18,  "70": 18,  "71": 3,
    # Section XIV — Base metals
    "72": 18,  "73": 18,  "74": 18,  "75": 18,  "76": 18,
    "77": 18,  "78": 18,  "79": 18,  "80": 18,  "81": 18,
    "82": 18,  "83": 18,
    # Section XVI — Machinery, electrical
    "84": 18,  "85": 18,
    # Section XVII — Vehicles, aircraft, ships
    "86": 12,  "87": 28,  "88": 5,   "89": 5,
    # Section XVIII — Instruments
    "90": 18,  "91": 18,  "92": 28,
    # Section XIX — Arms
    "93": 12,
    # Section XX — Miscellaneous manufactured
    "94": 18,  "95": 18,  "96": 18,
    # Section XXI — Works of art
    "97": 12,  "98": 18,  "99": 18,
}

# ── Heading-level overrides (4-digit → GST %) ─────────────────────────────────
# Only the headings where the rate differs from the chapter default.
HEADING_RATES: dict[str, int] = {
    # Chapter 2 — Meat: some fresh = 0, processed = 12
    "0201": 0, "0202": 0, "0203": 0, "0204": 0,
    "0210": 12,
    # Chapter 4 — Dairy: milk/eggs/honey exempt or 5%, cheese 12%, ghee 12%
    "0401": 0, "0402": 5, "0403": 5, "0404": 5,
    "0405": 12, "0406": 12,
    # Chapter 9 — Tea/coffee 5%, pepper/spices 5%, processed 12%
    "0902": 5, "0901": 5, "0904": 5, "0907": 5,
    # Chapter 12 — Seeds 0%, others 5%
    "1201": 0, "1202": 0, "1205": 0, "1206": 0,
    # Chapter 15 — Edible oils 5%, industrial fats 18%
    "1507": 5, "1508": 5, "1509": 5, "1510": 5,
    "1511": 5, "1512": 5, "1513": 5, "1514": 5,
    "1515": 18, "1516": 5, "1517": 5,
    # Chapter 17 — Sugar 5%, molasses 28%, confectionery 18%
    "1701": 5, "1702": 5, "1703": 5, "1704": 18,
    # Chapter 18 — Cocoa/chocolate: cocoa 18%, chocolate 28%
    "1806": 28,
    # Chapter 21 — Extracts/essences 18%, pan masala 28%
    "2106": 18,
    # Chapter 22 — Water 18%, aerated drinks 28%
    "2201": 18, "2202": 28, "2203": 28, "2204": 18,
    # Chapter 24 — Tobacco: cigarettes 28%, other tobacco 28%
    "2401": 5, "2402": 28, "2403": 28,
    # Chapter 27 — Petroleum: crude 0%, petrol/diesel 0% (under state VAT),
    # LPG 5%, coal 5%, other petroleum 18%
    "2701": 5, "2702": 5, "2709": 0, "2710": 0,
    "2711": 5, "2716": 18,
    # Chapter 30 — Pharma: most 12%, ayurvedic 12%, vaccines 5%
    "3001": 5, "3002": 5, "3003": 12, "3004": 12,
    "3005": 12, "3006": 12,
    # Chapter 31 — Fertilisers: most 5%
    "3101": 5, "3102": 5, "3103": 5, "3104": 5, "3105": 5,
    # Chapter 39 — Plastics: pipes 18%, packaging 18%, floor covering 12%
    "3918": 12, "3919": 18, "3920": 18, "3921": 18,
    "3922": 18, "3923": 18, "3924": 18, "3925": 18,
    # Chapter 40 — Rubber: tyres 28%, tubes 18%, condoms 12%
    "4011": 28, "4012": 28, "4013": 18, "4014": 12,
    # Chapter 44 — Wood: plywood/boards 12%, articles 12%, charcoal 5%
    "4401": 5, "4402": 5, "4411": 12, "4412": 12,
    "4418": 12, "4421": 12,
    # Chapter 48 — Paper: newsprint 5%, coated paper 12%, paperboard 12%
    "4801": 5, "4802": 12, "4803": 12, "4804": 12,
    # Chapter 49 — Printed books 0%, newspapers 0%, other printed 5%
    "4901": 0, "4902": 0, "4903": 0, "4904": 0,
    "4905": 0, "4911": 5,
    # Chapter 63 — Used clothing 5%
    "6309": 5,
    # Chapter 71 — Diamonds/precious stones 0.25%, gold 3%, silver 3%
    "7101": 3, "7102": 0, "7103": 0, "7104": 0,
    "7106": 3, "7107": 3, "7108": 3, "7109": 3,
    "7110": 3, "7111": 3, "7112": 18, "7113": 3,
    "7114": 3, "7116": 3, "7117": 3,
    # Chapter 86 — Railway locomotives 12%, track 12%
    "8601": 12, "8602": 12, "8603": 12, "8604": 12,
    # Chapter 87 — Vehicles: tractors 12%, electric vehicles 5%, ambulances 12%
    "8701": 12, "8703": 28, "8704": 28, "8705": 28,
    "8706": 28, "8708": 28, "8711": 28, "8712": 12,
    "8713": 5,  "8714": 28, "8715": 28,
    # Chapter 88 — Aircraft 5%
    "8802": 5, "8803": 5,
    # Chapter 89 — Ships/boats 5%
    "8901": 5, "8902": 5, "8903": 5,
    # Chapter 90 — Medical instruments 12%, optical 18%
    "9018": 12, "9019": 12, "9020": 12, "9021": 12,
    # Chapter 94 — Furniture: seats 18%, medical furniture 12%
    "9401": 18, "9402": 12, "9403": 18, "9404": 18,
    "9405": 12,
    # Chapter 95 — Toys 12%, video games 18%
    "9503": 12, "9504": 18,
    # Chapter 96 — Pens/pencils 18%, lighters 28%, diapers 18%
    "9613": 28, "9619": 18,
}

# ── SAC service rate overrides (6-digit prefix) ────────────────────────────────
# Most services: 18%. Exceptions:
SAC_RATES: dict[str, int] = {
    # Construction services
    "995411": 12, "995412": 12, "995413": 12, "995414": 12,
    "995415": 12, "995416": 12, "995419": 12,
    # Renting of immovable property
    "997211": 18, "997212": 18,
    # GTA (Goods Transport Agency)
    "996511": 5,  "996512": 5,  "996513": 5,
    # Passenger transport
    "996411": 5,  "996412": 5,
    # Healthcare
    "999311": 0,  "999312": 0,  "999313": 0,  "999314": 0,
    # Education
    "999210": 0,  "999220": 0,  "999230": 0,
    # Financial services (exempt or 18%)
    "997111": 0,  "997113": 0,
    # Restaurant / catering
    "996331": 5,  "996332": 5,
    # Hotel accommodation
    "996311": 12, "996312": 18,
    # Insurance
    "997132": 18, "997133": 18,
}


def lookup_rate(code: str, is_service: bool = False) -> int:
    """
    Return the best-estimate GST rate for a given HSN or SAC code string.
    Walk from most-specific (8-digit) to least-specific (2-digit chapter).
    """
    code = str(code).strip().zfill(8 if not is_service else 6)

    if is_service or code.startswith("99"):
        # SAC: check 6-digit exact, then 4-digit prefix, else 18%
        if code in SAC_RATES:
            return SAC_RATES[code]
        if code[:4] in SAC_RATES:
            return SAC_RATES[code[:4]]
        return 18

    # HSN: check heading override, then chapter default
    heading = code[:4]
    chapter = code[:2]
    if heading in HEADING_RATES:
        return HEADING_RATES[heading]
    return CHAPTER_RATES.get(chapter, 18)


# ── Unit-of-measure guesser ───────────────────────────────────────────────────
_KG_WORDS  = ("bulk", "powder", "granule", "pellet", "grain", "ore", "coal",
               "sand", "gravel", "flour", "meal", "scrap", "metal", "alloy",
               "chemical", "compound", "acid", "salt", "fertiliser", "resin",
               "fiber", "fibre", "wool", "cotton", "jute", "aggregate",
               "cement", "slag", "ash", "catalyst", "pigment", "dye",
               "solvent", "oil cake", "feed", "fodder")
_MTR_WORDS = ("wire", "cable", "pipe", "tube", "rod", "bar", "strip",
               "sheet", "film", "foil", "fabric", "cloth", "textile",
               "yarn", "thread", "rope", "belt", "hose", "duct", "rail",
               "profile", "section", "angle", "channel", "rolled")
_LTR_WORDS = ("liquid", "oil", "lubricant", "fuel", "solvent", "acid",
               "paint", "varnish", "resin", "adhesive", "ink", "dye",
               "beverage", "drink", "juice", "milk", "cream", "beer",
               "wine", "spirits", "water", "coolant", "hydraulic",
               "reagent", "solution", "emulsion", "concentrate")
_SQM_WORDS = ("floor", "flooring", "tile", "carpet", "mat", "membrane",
               "board", "panel", "laminate", "plywood", "veneer",
               "tarpaulin", "cover", "geotextile")
_BOX_WORDS = ("medicine", "tablet", "capsule", "vial", "ampoule",
               "syringe", "strip", "blister", "pharma", "drug",
               "cosmetic", "cream", "lotion")
_SET_WORDS = ("assembly", "kit", "system", "unit", "machine", "equipment",
               "apparatus", "instrument", "device", "installation",
               "pump", "compressor", "generator", "transformer",
               "motor", "engine", "turbine", "reactor")
_ROLL_WORDS= ("roll", "coil", "reel", "spool", "bobbin")


def guess_unit(description: str) -> str:
    desc_lower = description.lower()
    if any(w in desc_lower for w in _ROLL_WORDS):
        return "ROLL"
    if any(w in desc_lower for w in _SQM_WORDS):
        return "SQM"
    if any(w in desc_lower for w in _SET_WORDS):
        return "SET"
    if any(w in desc_lower for w in _LTR_WORDS):
        return "LTR"
    if any(w in desc_lower for w in _MTR_WORDS):
        return "MTR"
    if any(w in desc_lower for w in _KG_WORDS):
        return "KG"
    if any(w in desc_lower for w in _BOX_WORDS):
        return "BOX"
    return "PCS"