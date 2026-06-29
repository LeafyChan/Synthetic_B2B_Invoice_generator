"""
pipeline/bootstrap.py
=====================
Pass 1 — Dynamic Catalog Bootstrap

Now routes through pipeline.llm_providers so Ollama, OpenAI-compatible APIs,
and Anthropic all work through the same interface — no more hardcoded Ollama HTTP.

Two paths, both grounded in the REAL official HSN/SAC master:

Ollama/LLM path  (when provider is reachable)
----------------------------------------------
1. Ask the LLM to generate product DESCRIPTIONS only — no HSN digits.
2. Run each description through TF-IDF semantic search → real candidate codes.
3. Pass candidates back to the LLM for final code selection.
4. LLM cannot invent codes; it only chooses from real pre-filtered options.
5. Rate is always overridden from the real schedule.

Fallback path  (provider unreachable)
---------------------------------------
1. TF-IDF search over the real master with rotating keyword queries.
2. Fully offline, deterministic, reproducible.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import Optional

import aiohttp

log = logging.getLogger("bootstrap")

BATCH_SIZE   = 100
TARGET_ITEMS = 1000
SERVICES_FRACTION = 0.12

_PRICE_BY_RATE: dict[float, tuple[float, float]] = {
    0:    (5,     500),
    0.1:  (5,     200),
    0.25: (500,   50000),   # diamonds/precious stones
    1:    (10,    1000),
    1.5:  (10,    1000),
    3:    (500,   200000),  # precious metals
    5:    (10,    2000),
    6:    (10,    2000),
    7.5:  (20,    5000),
    12:   (50,    15000),
    18:   (20,    50000),
    28:   (200,   200000),
    40:   (500,   500000),
}

_GOODS_QUERY_WORDS = [
    "bolt nut screw fastener", "steel plate sheet coil", "pipe tube fitting valve",
    "motor pump compressor", "cable wire connector", "plastic film container",
    "chemical solvent lubricant", "bearing gear drive", "filter membrane seal",
    "sensor meter gauge instrument", "fabric textile fibre", "wood panel board",
    "rubber gasket seal", "paint coating adhesive", "battery cell power",
    "glass ceramic tile", "alloy metal casting forging", "conveyor belt system",
    "hydraulic pneumatic cylinder", "transformer switch relay", "pump impeller shaft",
    "spring washer clip bracket", "nozzle spray atomiser", "chain sprocket pulley",
    "cutting tool drill bit", "abrasive grinding wheel", "heat exchanger coil",
    "safety helmet glove protective", "packaging carton box crate",
    "label tag marking print", "weighing scale balance load cell",
    "valve regulator actuator", "coupling flange adapter fitting",
    "insulation foam sheet", "wire mesh screen filter", "roller bearing housing",
]

_SERVICE_QUERY_WORDS = [
    "road freight transport goods", "cargo handling warehousing storage",
    "machinery installation commissioning", "annual maintenance contract repair",
    "engineering consultancy technical service", "security manpower supply",
    "housekeeping facility management", "testing inspection certification",
    "accounting audit compliance", "legal advisory service",
    "cleaning sanitation service", "IT software support service",
]


def _descriptions_prompt(industry: str, batch_index: int, batch_size: int) -> str:
    n_goods    = round(batch_size * (1 - SERVICES_FRACTION))
    n_services = batch_size - n_goods
    return f"""You are a B2B procurement specialist for the {industry} industry in India.

List EXACTLY {n_goods} distinct physical goods AND {n_services} services that a
{industry} business would buy or sell. This is batch {batch_index + 1}.

RULES:
1. Each item is a SPECIFIC product or service (include grade/spec/size where meaningful).
2. No duplicate items. No brands.
3. Services: freight, installation, AMC, testing, consulting, etc.
4. Return ONLY a JSON array of strings — one description per element.

No markdown, no explanation. Just the JSON array of {batch_size} strings.
"""


def _selection_prompt(description: str, candidates: list[dict]) -> str:
    cand_text = "\n".join(
        f'{i+1}. code={c["code"]} | GST={c["gst_rate"]}% | {c["description"][:90]}'
        for i, c in enumerate(candidates)
    )
    return f"""Product: "{description}"

Choose the best-matching HSN/SAC code from this list of REAL official Indian codes.
Return ONLY: {{"index": <1-based>, "unit": "<PCS|KGS|MTR|LTR|BOX|SET|NOS|SQM|PAC|ROL>", "unit_cost_inr": <float>}}

Candidates:
{cand_text}

No markdown. No explanation. Just the JSON object.
"""


async def _generate_batch_with_provider(provider, industry, batch_index, hsn_index, session=None):
    """Generate one catalog batch using the given LLM provider."""
    desc_prompt = _descriptions_prompt(industry, batch_index, BATCH_SIZE)
    try:
        raw = await provider.complete(desc_prompt, temperature=0.0, session=session)
        descriptions = json.loads(raw)
        if not isinstance(descriptions, list):
            raise ValueError("not a list")
        descriptions = [str(d).strip() for d in descriptions if str(d).strip()]
    except Exception as e:
        log.warning(f"Batch {batch_index}: description parse failed — {e}")
        return []

    items = []
    for desc in descriptions[:BATCH_SIZE]:
        try:
            is_svc = any(kw in desc.lower() for kw in
                         ("service", "maintenance", "freight", "transport", "consulting",
                          "installation", "testing", "amc", "audit", "repair", "handling"))
            candidates = hsn_index.search_services(desc, top_k=5) if is_svc \
                         else hsn_index.search_goods(desc, top_k=5)
            if not candidates:
                continue
            sel_raw = await provider.complete(
                _selection_prompt(desc, candidates),
                temperature=0.0, session=session
            )
            sel = json.loads(sel_raw)
            idx1 = int(sel.get("index", 1))
            chosen = candidates[min(idx1 - 1, len(candidates) - 1)]
            unit = str(sel.get("unit", chosen.get("unit", "PCS")))
            from .gst_rate_schedule import VALID_UNIT_CODES
            if unit not in VALID_UNIT_CODES:
                unit = chosen.get("unit", "PCS")
            cost = max(0.01, float(sel.get("unit_cost_inr", 100.0)))
            items.append({
                "description": desc,
                "hsn_code": chosen["code"],
                "unit": unit,
                "unit_cost_inr": round(cost, 2),
                "gst_rate": chosen["gst_rate"],
                "category": chosen["description"].split(" — ")[0][:50].title(),
            })
        except Exception as e:
            log.debug(f"Item '{desc[:40]}' selection failed: {e}")
    return items


def _make_description(industry, rec, rng):
    leaf = rec.get("leaf_description", "").strip(" :;,.")
    if not leaf or leaf.upper() in ("OTHER", "OTHERS", "UNSPECIFIED"):
        leaf = rec["description"].split(" — ")[-1].strip(" :;,.")
    specs = ["Grade A", "ISO 9001", "BIS Certified", "IS Grade", "Export Quality",
             "Industrial Grade", "Commercial Grade", "Standard Grade"]
    return f"{leaf[:80]}, {rng.choice(specs)} — {industry}"


def _fallback_from_index(hsn_index, industry, count):
    # _PRICE_BY_RATE is already defined at module level in this file (see top
    # of bootstrap.py) — it does NOT exist in gst_rate_schedule.py. The old
    # local import here always raised ImportError and crashed every call to
    # the TF-IDF fallback path.
    rng = random.Random(hash(industry) & 0xFFFFFFFF)
    num_services = max(1, round(count * SERVICES_FRACTION))
    num_goods    = count - num_services
    items = []
    seen_codes: set[str] = set()

    query_words = list(_GOODS_QUERY_WORDS)
    rng.shuffle(query_words)
    qc = 0
    for _ in range(num_goods * 4):
        if len(items) >= num_goods:
            break
        q = f"{industry} {query_words[qc % len(query_words)]}"
        qc += 1
        for rec in hsn_index.search_goods(q, top_k=8):
            if rec["code"] in seen_codes or len(items) >= num_goods:
                break
            seen_codes.add(rec["code"])
            rate = rec["gst_rate"]
            lo, hi = _PRICE_BY_RATE.get(rate, (20, 10000))
            items.append({
                "description": _make_description(industry, rec, rng),
                "hsn_code": rec["code"],
                "unit": rec.get("unit", "PCS"),
                "unit_cost_inr": round(rng.uniform(lo, hi), 2),
                "gst_rate": rate,
                "category": rec["description"].split(" — ")[0][:50].title(),
            })

    if len(items) < num_goods:
        for rec in hsn_index.random_goods(rng, num_goods - len(items) + 50):
            if rec["code"] in seen_codes or len(items) >= num_goods:
                break
            seen_codes.add(rec["code"])
            rate = rec["gst_rate"]
            lo, hi = _PRICE_BY_RATE.get(rate, (20, 10000))
            items.append({
                "description": _make_description(industry, rec, rng),
                "hsn_code": rec["code"],
                "unit": rec.get("unit", "PCS"),
                "unit_cost_inr": round(rng.uniform(lo, hi), 2),
                "gst_rate": rate,
                "category": rec["description"].split(" — ")[0][:50].title(),
            })

    svc_words = list(_SERVICE_QUERY_WORDS)
    rng.shuffle(svc_words)
    seen_sac: set[str] = set()
    sc = 0
    for _ in range(num_services * 3):
        if len(items) >= count:
            break
        sq = f"{industry} {svc_words[sc % len(svc_words)]}"
        sc += 1
        for rec in hsn_index.search_services(sq, top_k=5):
            if rec["code"] in seen_sac or len(items) >= count:
                break
            seen_sac.add(rec["code"])
            items.append({
                "description": f"{rec['leaf_description'].title()} — {industry}",
                "hsn_code": rec["code"],
                "unit": "NOS",
                "unit_cost_inr": round(rng.uniform(500, 50000), 2),
                "gst_rate": rec["gst_rate"],
                "category": rec["description"].split(" — ")[0][:50].title(),
            })

    rng.shuffle(items)
    return items[:count]


def _stub_catalog(industry, count):
    rng = random.Random(42)
    POOL = [
        ("73181500", 18, "PCS", "Fasteners"), ("84818090", 18, "PCS", "Valves"),
        ("85044000", 18, "PCS", "Electronics"), ("39201000", 18, "KGS", "Plastics"),
        ("48191000", 12, "BOX", "Packaging"), ("52111000",  5, "MTR", "Textiles"),
        ("30042011", 12, "BOX", "Pharma"),    ("94032000", 18, "PCS", "Furniture"),
        ("99651100", 12, "NOS", "Transport"), ("99871200", 18, "NOS", "Maintenance"),
    ]
    items = []
    for i in range(count):
        code, rate, unit, cat = rng.choice(POOL)
        lo, hi = _PRICE_BY_RATE.get(rate, (20, 5000))
        items.append({
            "description": f"{industry} — {cat} Component (Item {i+1:04d})",
            "hsn_code": code, "unit": unit,
            "unit_cost_inr": round(rng.uniform(lo, hi), 2),
            "gst_rate": rate, "category": cat,
        })
    return items


def _generate_fallback_catalog(industry: str, count: int = TARGET_ITEMS) -> list[dict]:
    try:
        from pipeline.hsn_lookup import HSNIndex
        idx = HSNIndex()
        idx._ensure_loaded()
        return _fallback_from_index(idx, industry, count)
    except FileNotFoundError:
        log.warning("HSN index not built — using stub catalog. Run build_hsn_index.py first.")
        return _stub_catalog(industry, count)


async def run_bootstrap(
    industry: str,
    llm_provider: str = "ollama",
    llm_model: str = "qwen2.5:7b",
    llm_api_key: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    output_path: Path = Path("industry_catalog.json"),
    # Legacy kwargs kept for backward compat
    model: Optional[str] = None,
    ollama_url: Optional[str] = None,
):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolve legacy args
    if model and not llm_model:
        llm_model = model
    if ollama_url and llm_provider == "ollama" and not llm_base_url:
        llm_base_url = ollama_url

    # Build the LLM provider
    from pipeline.llm_providers import get_provider
    provider_kwargs = {"model": llm_model}
    if llm_api_key:
        provider_kwargs["api_key"] = llm_api_key
    if llm_base_url:
        if llm_provider == "ollama":
            provider_kwargs["base_url"] = llm_base_url
        else:
            provider_kwargs["base_url"] = llm_base_url
    try:
        provider = get_provider(llm_provider, **provider_kwargs)
    except Exception as e:
        log.warning(f"Could not create LLM provider: {e} — falling back to TF-IDF only")
        provider = None

    # Load HSN index
    try:
        from pipeline.hsn_lookup import HSNIndex
        hsn_index = HSNIndex()
        hsn_index._ensure_loaded()
        index_available = True
        log.info(f"HSN index: {hsn_index.goods_count} goods + {hsn_index.service_count} services")
    except FileNotFoundError as e:
        log.warning(f"HSN index missing: {e}")
        index_available = False
        hsn_index = None

    # Check provider reachability
    provider_available = False
    if provider and index_available:
        try:
            connector = aiohttp.TCPConnector(limit=2)
            async with aiohttp.ClientSession(connector=connector) as session:
                provider_available = await provider.healthcheck(session=session)
            if provider_available:
                log.info(f"LLM provider '{llm_provider}' is reachable")
            else:
                log.warning(f"LLM provider '{llm_provider}' not reachable — TF-IDF fallback")
        except Exception as e:
            log.warning(f"Provider health check failed ({e}) — TF-IDF fallback")

    if provider_available and index_available:
        num_batches = TARGET_ITEMS // BATCH_SIZE
        all_items: list[dict] = []
        connector = aiohttp.TCPConnector(limit=2)
        async with aiohttp.ClientSession(connector=connector) as session:
            for bi in range(num_batches):
                log.info(f"  Batch {bi+1}/{num_batches} …")
                batch = await _generate_batch_with_provider(
                    provider, industry, bi, hsn_index, session=session)
                log.info(f"  → {len(batch)} items")
                all_items.extend(batch)
                await asyncio.sleep(0.3)

        seen: set[str] = set()
        unique = []
        for item in all_items:
            k = item["description"].lower()[:60]
            if k not in seen:
                seen.add(k)
                unique.append(item)

        if len(unique) < TARGET_ITEMS:
            extra = _fallback_from_index(hsn_index, industry, TARGET_ITEMS - len(unique) + 50)
            unique.extend(extra)

        catalog = unique[:TARGET_ITEMS]
    else:
        log.info("Generating catalog via TF-IDF search over official HSN master …")
        catalog = _generate_fallback_catalog(industry, TARGET_ITEMS)

    output_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False))
    log.info(f"Catalog saved: {len(catalog)} items → {output_path}")