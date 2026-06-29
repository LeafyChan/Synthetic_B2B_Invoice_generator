"""
pipeline/assembler.py
=====================
Pass 2 — High-Throughput Assembly

New in this version
--------------------
- Language support: passes --language + libretranslate_url down to layout_engine
- Handwriting mode: optionally post-processes HTML through pipeline.handwriting
- Discrepancy separation: pairs where has_discrepancy=True are written to
  a separate subfolder (purchase_orders/discrepant/ + tax_invoices/discrepant/)
  so they can be used as a standalone reconciliation/fraud-detection dataset
  without mixing into the clean training split.

Output layout
-------------
output/
    purchase_orders/
        clean/            po_000000.png
        degraded/          po_000003.png
        heavy/             po_000007.png
        discrepant/        po_000012.png   ← pairs with PO/Invoice mismatch
    tax_invoices/
        clean/            inv_000000.png
        degraded/          inv_000003.png
        heavy/             inv_000007.png
        discrepant/        inv_000012.png  ← matching invoice for above

Note: a discrepant document is ALSO placed in its tier subfolder AND in
discrepant/ — so discrepant/ is an additive overlay for easy filtering,
not a replacement for the tier structure.
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
) -> dict:
    """Worker entry point — runs in a subprocess."""
    from pipeline.data_models import generate_document_pair
    from pipeline.layout_engine import render_purchase_order_html, render_tax_invoice_html
    from pipeline.degradation import degrade_image, get_degradation_metadata, assign_tier
    from pipeline.database import log_document_pair
    from pipeline.renderer import render_html_to_png, close_browser

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
            tier = assign_tier(idx) if apply_degradation else "clean"

            po, inv = generate_document_pair(idx, catalog)

            # Render HTML (language-aware if lang_cfg is available)
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
                po_html  = render_handwritten_document(po_html,  mode=handwriting_mode,
                                                        seed=idx * 2,     intensity=intensity)
                inv_html = render_handwritten_document(inv_html, mode=handwriting_mode,
                                                        seed=idx * 2 + 1, intensity=intensity)

            po_png_raw  = await render_html_to_png(po_html)
            inv_png_raw = await render_html_to_png(inv_html)

            po_png  = degrade_image(po_png_raw,  idx * 2,     tier=tier, apply_degradation=apply_degradation)
            inv_png = degrade_image(inv_png_raw, idx * 2 + 1, tier=tier, apply_degradation=apply_degradation)
            deg_meta = get_degradation_metadata(idx * 2, tier=tier)

            po_filename  = f"po_{idx:06d}.png"
            inv_filename = f"inv_{idx:06d}.png"

            # Write to tier subfolder
            (po_base  / tier / po_filename).write_bytes(po_png)
            (inv_base / tier / inv_filename).write_bytes(inv_png)

            # Also write to discrepant/ if this pair has a PO<->Invoice mismatch
            has_disc = getattr(inv, "has_discrepancy", False)
            if has_disc:
                (po_base  / "discrepant" / po_filename).write_bytes(po_png)
                (inv_base / "discrepant" / inv_filename).write_bytes(inv_png)
                discrepant_count += 1

            po_dict  = po.to_dict()
            inv_dict = inv.to_dict()
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
            logging.getLogger("worker").error(f"[idx={idx}] Failed: {e}", exc_info=False)

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
) -> None:
    catalog_path = Path(catalog_path)
    output_dir   = Path(output_dir)
    db_path      = Path(db_path)

    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog not found: {catalog_path}. Run Pass 1 first.")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    log.info(f"Catalog loaded: {len(catalog)} items from {catalog_path}")

    # Even shard split — correct for all count/workers combinations
    import numpy as np
    all_indices = list(range(count))
    shards = [arr.tolist() for arr in np.array_split(all_indices, workers)]
    shards = [s for s in shards if s]  # drop empty shards if workers > count

    log.info(f"Sharding: {count} docs → {len(shards)} shards across {workers} workers")
    if apply_degradation:
        log.info("Realism tiers: clean 50% / degraded 30% / heavy 20% (deterministic per doc_index)")
    else:
        log.info("Degradation disabled — all docs in 'clean/' subfolder")
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
                apply_degradation, language, libretranslate_url, handwriting_mode,
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
    log.info(f"Discrepant pairs (PO/Invoice mismatch): {total_discrepant:,} "
             f"(written to discrepant/ subfolder)")
    if total_failed > 0:
        log.warning(f"{total_failed} documents failed — check logs/pipeline.log")