"""
pipeline/assembler.py
=====================
Pass 2 — High-Throughput Assembly

Output layout
-------------
output/
    purchase_orders/
        clean/            po_000000.png  po_000000.pdf
        degraded/         po_000003.png  po_000003.pdf
        heavy/            po_000007.png  po_000007.pdf
        discrepant/       po_000012.png  po_000012.pdf
    tax_invoices/
        clean/            inv_000000.png  inv_000000.pdf
        degraded/         inv_000003.png  inv_000003.pdf
        heavy/            inv_000007.png  inv_000007.pdf
        discrepant/       inv_000012.png  inv_000012.pdf

Multi-page PDFs: documents with many line items (26-50) naturally overflow
one A4 page. Playwright's page.pdf() flows content across pages automatically
using the @media print CSS rules in layout_engine.py. No manual splitting.
"""

import asyncio
import json
import logging
import os
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

log = logging.getLogger("assembler")

PROGRESS_INTERVAL = 100
TIER_DIRS = ("clean", "degraded", "heavy", "discrepant")


def _worker_process(
    shard_indices: list[int],
    catalog: list[dict],
    output_dir: str,
    db_path: str,
    apply_degradation: bool,
    language: str,
    libretranslate_url: str,
    handwriting_mode: Optional[str],
    output_format: str,          # "png" | "pdf" | "both"
    tier_plan: Optional[dict] = None,   # {doc_index: tier} — None = default probabilistic assign_tier()
) -> dict:
    """Worker entry point — runs in a subprocess."""
    from pipeline.data_models import generate_document_pair
    from pipeline.layout_engine import render_purchase_order_html, render_tax_invoice_html
    from pipeline.degradation import degrade_image, get_degradation_metadata, assign_tier
    from pipeline.pdf_degradation import degrade_pdf
    from pipeline.database import log_document_pair
    from pipeline.renderer import render_html_to_png, render_html_to_pdf, close_browser
    from pipeline.timing import timed, log_timing_summary

    emit_png = output_format in ("png", "both")
    emit_pdf = output_format in ("pdf", "both")

    # Set up language config once per worker
    lang_cfg = None
    try:
        from pipeline.languages import get_language_config
        lang_cfg = get_language_config(
            language,
            libretranslate_url=libretranslate_url if language not in ("en", "fr", "hi", "ur") else None
        )
        logging.getLogger("worker").info(f"Language: {lang_cfg.name} (Tier {lang_cfg.tier})")
    except Exception as e:
        logging.getLogger("worker").warning(f"Could not load language '{language}': {e} — using English")

    po_base  = Path(output_dir) / "purchase_orders"
    inv_base = Path(output_dir) / "tax_invoices"
    for tier_name in TIER_DIRS:
        (po_base  / tier_name).mkdir(parents=True, exist_ok=True)
        (inv_base / tier_name).mkdir(parents=True, exist_ok=True)

    success = 0
    failed  = 0
    tier_counts = Counter()
    discrepant_count = 0
    t0 = time.time()

    async def process_one(idx: int):
        nonlocal success, failed, discrepant_count
        try:
            if not apply_degradation:
                tier = "clean"
            elif tier_plan is not None:
                # Exact-count mode — tier_plan was pre-resolved by
                # tier_planner.resolve_tier_plan() in run_assembly() and
                # already covers every idx in shard_indices.
                tier = tier_plan[idx]
            else:
                tier = assign_tier(idx)

            with timed("generate_document_pair"):
                po, inv = generate_document_pair(idx, catalog)

            # has_discrepancy: detect meaningful PO/invoice mismatches
            # (grand totals diverge more than rounding when a line item
            # quantity or price was patched by data_models — this is the
            # hook for future discrepancy injection; for now it's always False)
            has_disc = getattr(inv, "has_discrepancy", False)
            if has_disc:
                discrepant_count += 1

            # Render HTML (language-aware if lang_cfg is available)
            with timed("render_html"):
                if lang_cfg is not None:
                    po_html  = render_purchase_order_html(po,  lang_cfg=lang_cfg)
                    inv_html = render_tax_invoice_html(inv, lang_cfg=lang_cfg)
                else:
                    po_html  = render_purchase_order_html(po)
                    inv_html = render_tax_invoice_html(inv)

            # Optional full-document handwriting overlay
            if handwriting_mode:
                from pipeline.handwriting import render_handwritten_document
                intensity = {"clean": 0.4, "degraded": 0.7, "heavy": 1.0}.get(tier, 0.7)
                with timed("handwriting_overlay"):
                    po_html  = render_handwritten_document(po_html,  mode=handwriting_mode,
                                                            seed=idx * 2,     intensity=intensity)
                    inv_html = render_handwritten_document(inv_html, mode=handwriting_mode,
                                                            seed=idx * 2 + 1, intensity=intensity)

            po_filename  = f"po_{idx:06d}.png"
            inv_filename = f"inv_{idx:06d}.png"
            po_pdf_filename  = f"po_{idx:06d}.pdf"
            inv_pdf_filename = f"inv_{idx:06d}.pdf"

            # ── PNG output ────────────────────────────────────────────────────
            if emit_png:
                async with timed("render_html_to_png"):
                    po_png_raw  = await render_html_to_png(po_html)
                    inv_png_raw = await render_html_to_png(inv_html)
                with timed("degrade_image"):
                    po_png  = degrade_image(po_png_raw,  idx * 2,     tier=tier, apply_degradation=apply_degradation)
                    inv_png = degrade_image(inv_png_raw, idx * 2 + 1, tier=tier, apply_degradation=apply_degradation)
                (po_base  / tier / po_filename).write_bytes(po_png)
                (inv_base / tier / inv_filename).write_bytes(inv_png)
                if has_disc:
                    (po_base  / "discrepant" / po_filename).write_bytes(po_png)
                    (inv_base / "discrepant" / inv_filename).write_bytes(inv_png)

            # ── PDF output ────────────────────────────────────────────────────
            # Multi-page is automatic: Playwright's page.pdf() flows content
            # across A4 pages via the @media print CSS in layout_engine.py.
            # Documents with many line items (26-50) will span 2-4 pages.
            #
            # Degradation: degrade_pdf() rasterizes each page, runs the SAME
            # degrade_image() pipeline used for PNGs (degradation.py), then
            # rebuilds a PDF from the degraded pages. Uses the identical
            # idx*2 / idx*2+1 seeds as the PNG branch above so a "heavy" tier
            # PDF and its PNG counterpart get the same profile/parameters —
            # they're visually consistent, just one is raster-in-pdf.
            if emit_pdf:
                async with timed("render_html_to_pdf"):
                    po_pdf_raw  = await render_html_to_pdf(po_html)
                    inv_pdf_raw = await render_html_to_pdf(inv_html)
                with timed("degrade_pdf"):
                    po_pdf_bytes  = degrade_pdf(po_pdf_raw,  idx * 2,     tier=tier, apply_degradation=apply_degradation) if po_pdf_raw else b""
                    inv_pdf_bytes = degrade_pdf(inv_pdf_raw, idx * 2 + 1, tier=tier, apply_degradation=apply_degradation) if inv_pdf_raw else b""
                if po_pdf_bytes:
                    (po_base  / tier / po_pdf_filename).write_bytes(po_pdf_bytes)
                if inv_pdf_bytes:
                    (inv_base / tier / inv_pdf_filename).write_bytes(inv_pdf_bytes)
                if has_disc:
                    if po_pdf_bytes:
                        (po_base  / "discrepant" / po_pdf_filename).write_bytes(po_pdf_bytes)
                    if inv_pdf_bytes:
                        (inv_base / "discrepant" / inv_pdf_filename).write_bytes(inv_pdf_bytes)

            # ── Degradation metadata (needed for DB even if PNG not emitted) ─
            deg_meta = get_degradation_metadata(idx * 2, tier=tier)

            # ── Database log ──────────────────────────────────────────────────
            po_dict  = po.to_dict()
            inv_dict = inv.to_dict()
            with timed("log_document_pair"):
                log_document_pair(
                    db_path=Path(db_path),
                    po_data=po_dict,
                    inv_data=inv_dict,
                    po_filename=f"{tier}/{po_filename}",
                    inv_filename=f"{tier}/{inv_filename}",
                    degradation_profile=deg_meta["profile_name"],
                    tier=tier,
                )
            success += 1
            tier_counts[tier] += 1

        except Exception as e:
            failed += 1
            logging.getLogger("worker").error(f"[idx={idx}] Failed: {e}", exc_info=True)

    async def run_shard():
        for i, idx in enumerate(shard_indices):
            await process_one(idx)
            if (i + 1) % PROGRESS_INTERVAL == 0:
                elapsed = time.time() - t0
                rate = (success + failed) / max(elapsed, 0.001)
                logging.getLogger("worker").info(
                    f"PID {os.getpid()} — {success + failed}/{len(shard_indices)} "
                    f"({rate:.1f} docs/s, {failed} failed, {discrepant_count} discrepant) "
                    f"| tiers: {dict(tier_counts)}"
                )
                log_timing_summary(prefix=f"PID {os.getpid()} — ")
        log_timing_summary(prefix=f"PID {os.getpid()} FINAL — ")
        await close_browser()

    asyncio.run(run_shard())

    return {
        "pid": os.getpid(),
        "shard_size": len(shard_indices),
        "success": success,
        "failed": failed,
        "discrepant": discrepant_count,
        "tier_counts": dict(tier_counts),
        "elapsed_s": round(time.time() - t0, 2),
    }


async def run_assembly(
    catalog_path: Path,
    output_dir: Path,
    db_path: Path,
    count: int,
    workers: int,
    apply_degradation: bool,
    language: str = "en",
    libretranslate_url: str = "http://localhost:5000",
    handwriting_mode: Optional[str] = None,
    start_index: int = 0,
    output_format: str = "both",
    tier_counts: Optional[dict] = None,
) -> None:
    """
    start_index : first doc_index to generate (default 0).
    output_format : "png" | "pdf" | "both" (default "both")
    tier_counts : optional {"clean": n, "degraded": n, "heavy": n} — exact
        counts that MUST sum to `count`. If None (default), tier assignment
        falls back to the original probabilistic assign_tier(doc_index)
        behaviour (~50/30/20 split), unchanged from before.
    """
    catalog_path = Path(catalog_path)
    output_dir   = Path(output_dir)
    db_path      = Path(db_path)

    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog not found: {catalog_path}. Run Pass 1 first.")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    log.info(f"Catalog loaded: {len(catalog)} items from {catalog_path}")

    import numpy as np
    all_indices = list(range(start_index, start_index + count))
    shards = [arr.tolist() for arr in np.array_split(all_indices, workers)]
    shards = [s for s in shards if s]

    # ── Resolve tier plan (exact-count mode or default probabilistic) ───────
    tier_plan: Optional[dict] = None
    if apply_degradation and tier_counts is not None:
        from pipeline.tier_planner import resolve_tier_plan
        tier_plan = resolve_tier_plan(all_indices, tier_counts)
        log.info(f"Exact tier counts requested: {tier_counts} (verified to sum to {count})")
    elif not apply_degradation:
        if tier_counts is not None:
            log.warning("--tier-counts given but --no-degradation is set — "
                        "all docs will be 'clean' regardless of --tier-counts")
    else:
        log.info("No --tier-counts given — using default probabilistic "
                 "tier split (clean 50% / degraded 30% / heavy 20%)")

    log.info(f"Sharding: {count} docs (indices {start_index}–{start_index + count - 1}) → "
             f"{len(shards)} shards across {workers} workers")
    if apply_degradation and tier_plan is None:
        log.info("Realism tiers: clean 50% / degraded 30% / heavy 20% (deterministic per doc_index)")
    elif not apply_degradation:
        log.info("Degradation disabled — all docs in 'clean/' subfolder")
    log.info(f"Output format: {output_format}")
    log.info("Multi-page PDFs: documents with 26-50 line items will span 2-4 A4 pages automatically")
    if language != "en":
        log.info(f"Language: {language}")
    if handwriting_mode:
        log.info(f"Handwriting mode: {handwriting_mode}")

    t0 = time.time()
    total_success     = 0
    total_failed      = 0
    total_discrepant  = 0
    total_tier_counts = Counter()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _worker_process,
                shard, catalog, str(output_dir), str(db_path),
                apply_degradation, language, libretranslate_url,
                handwriting_mode, output_format,
                # Each worker only needs the slice of the plan covering its
                # own shard — pass the full dict (cheap: count entries max)
                # and let the worker look up by idx; no need to pre-slice.
                tier_plan,
            ): i
            for i, shard in enumerate(shards)
        }

        for future in as_completed(futures):
            shard_id = futures[future]
            try:
                result = future.result()
                total_success    += result["success"]
                total_failed     += result["failed"]
                total_discrepant += result.get("discrepant", 0)
                total_tier_counts.update(result["tier_counts"])
                elapsed = time.time() - t0
                overall_rate = (total_success + total_failed) / max(elapsed, 0.001)
                log.info(
                    f"Shard {shard_id:02d} done | PID {result['pid']} | "
                    f"{result['success']}/{result['shard_size']} ok | "
                    f"{result['failed']} failed | {result['elapsed_s']}s | "
                    f"Overall: {total_success + total_failed}/{count} ({overall_rate:.1f}/s)"
                )
            except Exception as e:
                log.error(f"Shard {shard_id} raised exception: {e}", exc_info=True)

    elapsed = time.time() - t0
    log.info(
        f"Assembly complete: {total_success:,} ok / {total_failed:,} failed "
        f"in {elapsed:.1f}s ({(total_success + total_failed)/max(elapsed,0.001):.1f} docs/s)"
    )
    log.info(f"Tier breakdown: {dict(total_tier_counts)}")
    log.info(f"Discrepant pairs: {total_discrepant:,}")
    if tier_counts is not None:
        mismatches = {
            t: (tier_counts.get(t, 0), total_tier_counts.get(t, 0))
            for t in ("clean", "degraded", "heavy")
            if tier_counts.get(t, 0) != total_tier_counts.get(t, 0)
        }
        if mismatches:
            log.warning(f"Requested tier counts did not match actual output (requested, actual): {mismatches}")
        else:
            log.info("Exact tier counts matched the request precisely.")
    if total_failed > 0:
        log.warning(f"{total_failed} documents failed — check logs/pipeline.log")