"""
pipeline/assembler.py
=====================
Pass 2 — High-Throughput Assembly (40,001 Documents, 3 Realism Tiers)

Architecture
------------
Main process          : coordinator; dispatches batches to workers
Worker processes      : each runs a synchronous batch loop that:
                          1. Generates PO + Invoice data models
                          2. Renders HTML → PNG via Playwright (async within worker)
                          3. Determines the realism tier for this doc_index
                             (clean / degraded / heavy — deterministic split,
                             default 50/30/20)
                          4. Applies OpenCV degradation matching that tier
                          5. Writes PNG files to the tier-specific subfolder
                          6. Logs to SQLite (WAL mode for concurrent writes),
                             including the tier in the row

Output layout
-------------
output/
    purchase_orders/
        clean/        po_000000.png ...
        degraded/     po_000003.png ...
        heavy/        po_000007.png ...
    tax_invoices/
        clean/        inv_000000.png ...
        degraded/     inv_000003.png ...
        heavy/        inv_000007.png ...

Each doc_index is assigned to exactly ONE tier (no duplication across tiers).
Tier assignment is deterministic — see pipeline.degradation.assign_tier().

Concurrency model
-----------------
concurrent.futures.ProcessPoolExecutor for CPU-bound degradation.
asyncio event loop per worker process handles Playwright async I/O.
SQLite WAL journal mode prevents write contention.

Memory management
-----------------
Images are processed and written in streaming fashion; no in-memory
accumulation. Each worker handles a shard of indices then exits cleanly.
"""

import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

log = logging.getLogger("assembler")

# ── Progress tracking ──────────────────────────────────────────────────────────
PROGRESS_INTERVAL = 100   # log progress every N pairs

TIER_DIRS = ("clean", "degraded", "heavy")


def _worker_process(
    shard_indices: list[int],
    catalog: list[dict],
    output_dir: str,
    db_path: str,
    apply_degradation: bool,
) -> dict:
    """
    Worker entry point (runs in a subprocess).
    Processes a shard of document indices end-to-end.
    Returns a summary dict with counts, tier breakdown, and timing.
    """
    # Re-import inside worker (fresh process)
    from pipeline.data_models import generate_document_pair
    from pipeline.layout_engine import render_purchase_order_html, render_tax_invoice_html
    from pipeline.degradation import degrade_image, get_degradation_metadata, assign_tier
    from pipeline.database import log_document_pair
    from pipeline.renderer import render_html_to_png, close_browser

    po_base = Path(output_dir) / "purchase_orders"
    inv_base = Path(output_dir) / "tax_invoices"
    for tier_name in TIER_DIRS:
        (po_base / tier_name).mkdir(parents=True, exist_ok=True)
        (inv_base / tier_name).mkdir(parents=True, exist_ok=True)

    success = 0
    failed = 0
    tier_counts = Counter()
    t0 = time.time()

    async def process_one(idx: int):
        nonlocal success, failed
        try:
            # ── Determine realism tier for this document ─────────────────────
            tier = assign_tier(idx) if apply_degradation else "clean"

            # ── Generate data models ──────────────────────────────────────────
            po, inv = generate_document_pair(idx, catalog)

            # ── Render HTML → PNG ─────────────────────────────────────────────
            po_html = render_purchase_order_html(po)
            inv_html = render_tax_invoice_html(inv)

            po_png_raw = await render_html_to_png(po_html)
            inv_png_raw = await render_html_to_png(inv_html)

            # ── Degrade images (tier-aware) ───────────────────────────────────
            po_png = degrade_image(po_png_raw, idx * 2, tier=tier, apply_degradation=apply_degradation)
            inv_png = degrade_image(inv_png_raw, idx * 2 + 1, tier=tier, apply_degradation=apply_degradation)
            deg_meta = get_degradation_metadata(idx * 2, tier=tier)

            # ── Write to disk (tier subfolder) ────────────────────────────────
            po_filename = f"po_{idx:06d}.png"
            inv_filename = f"inv_{idx:06d}.png"

            (po_base / tier / po_filename).write_bytes(po_png)
            (inv_base / tier / inv_filename).write_bytes(inv_png)

            # ── Log to SQLite ─────────────────────────────────────────────────
            po_dict = po.to_dict()
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
        for idx in shard_indices:
            await process_one(idx)
            if (shard_indices.index(idx) + 1) % PROGRESS_INTERVAL == 0:
                elapsed = time.time() - t0
                rate = (success + failed) / max(elapsed, 0.001)
                logging.getLogger("worker").info(
                    f"PID {os.getpid()} — {success + failed}/{len(shard_indices)} "
                    f"({rate:.1f} docs/s, {failed} failed) | tiers so far: {dict(tier_counts)}"
                )
        await close_browser()

    asyncio.run(run_shard())

    return {
        "pid": os.getpid(),
        "shard_size": len(shard_indices),
        "success": success,
        "failed": failed,
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
) -> None:
    """
    Orchestrate Pass 2: shard `count` indices across `workers` processes.
    """
    catalog_path = Path(catalog_path)
    output_dir = Path(output_dir)
    db_path = Path(db_path)

    # Load catalog
    if not catalog_path.exists():
        raise FileNotFoundError(f"Catalog not found: {catalog_path}. Run Pass 1 first.")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    log.info(f"Catalog loaded: {len(catalog)} items from {catalog_path}")

    # Shard indices
    all_indices = list(range(count))
    shard_size = max(1, count // workers)
    shards = [
        all_indices[i : i + shard_size]
        for i in range(0, count, shard_size)
    ]
    # Assign any remainder to the last shard
    if len(shards) > workers:
        shards[-2].extend(shards[-1])
        shards = shards[:-1]

    log.info(f"Sharding: {count} docs → {len(shards)} shards across {workers} workers")
    if apply_degradation:
        log.info("Realism tiers: clean 50% / degraded 30% / heavy 20% (deterministic per doc_index)")
    else:
        log.info("Degradation disabled (--no-degradation) — all docs written to 'clean/' subfolder")

    t0 = time.time()
    total_success = 0
    total_failed = 0
    total_tier_counts = Counter()

    # Run workers in a process pool
    # We use run_in_executor so the main asyncio loop stays responsive
    loop = asyncio.get_event_loop()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _worker_process,
                shard,
                catalog,
                str(output_dir),
                str(db_path),
                apply_degradation,
            ): i
            for i, shard in enumerate(shards)
        }

        for future in as_completed(futures):
            shard_id = futures[future]
            try:
                result = future.result()
                total_success += result["success"]
                total_failed += result["failed"]
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
        f"in {elapsed:.1f}s ({(total_success + total_failed)/elapsed:.1f} docs/s)"
    )
    log.info(f"Tier breakdown: {dict(total_tier_counts)}")
    if total_failed > 0:
        log.warning(f"{total_failed} documents failed — check logs/pipeline.log")