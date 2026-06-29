"""
B2B Synthetic Document Dataset Pipeline
========================================
Generates 40,001 documents: 20k Purchase Orders + 20k Tax Invoices + 1 SQLite Master Log
Two-pass async pipeline with block-based floating layout engine.

Usage:
    # Fresh run — clears ALL previous output + DB, then generates
    python main.py --industry "Automotive Fasteners" --workers 4

    # Append run — keeps existing output, appends to existing DB
    python main.py --industry "Automotive Fasteners" --workers 4 --append

    # Quick test, no degradation
    python main.py --industry "Test" --count 10 --workers 1 --no-degradation

    # Multi-language
    python main.py --industry "Fasteners" --language hi --workers 4

    # Paid LLM backend
    python main.py --industry "Fasteners" --llm-provider openai \\
        --llm-model gpt-4o-mini --llm-api-key sk-... --workers 4

    # Full-document handwriting mode
    python main.py --industry "Fasteners" --handwriting font --workers 4
"""

import argparse
import asyncio
import logging
import shutil
import sys
import time
from pathlib import Path

from pipeline.bootstrap import run_bootstrap
from pipeline.assembler import run_assembly
from pipeline.database import init_database

# ── Logging ───────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/pipeline.log"),
    ],
)
log = logging.getLogger("main")

BASE_DIR    = Path(__file__).parent
CATALOG_PATH = BASE_DIR / "industry_catalog.json"
DB_PATH      = BASE_DIR / "output" / "master_ground_truth.db"
OUTPUT_DIR   = BASE_DIR / "output"


def parse_args():
    parser = argparse.ArgumentParser(description="B2B Synthetic Document Pipeline")

    # Core
    parser.add_argument("--industry", type=str, default="Automotive Fasteners")
    parser.add_argument("--workers",  type=int, default=4)
    parser.add_argument("--count",    type=int, default=20000,
                        help="Number of PO/Invoice pairs to generate")

    # Run mode
    parser.add_argument("--append", action="store_true",
                        help="Append to existing output + DB instead of wiping first")
    parser.add_argument("--skip-bootstrap", action="store_true",
                        help="Reuse existing industry_catalog.json")

    # LLM backend
    parser.add_argument("--llm-provider", type=str, default="ollama",
                        choices=["ollama", "openai", "anthropic"])
    parser.add_argument("--llm-model",    type=str, default="qwen2.5:7b")
    parser.add_argument("--llm-api-key",  type=str, default=None)
    parser.add_argument("--llm-base-url", type=str, default=None)
    # Legacy aliases kept for backward compat
    parser.add_argument("--ollama-model", type=str, default=None)
    parser.add_argument("--ollama-url",   type=str, default="http://localhost:11434")

    # Visual
    parser.add_argument("--no-degradation", action="store_true")
    parser.add_argument("--language", type=str, default="en",
                        help="Language code: en, fr, hi, ur, or any LibreTranslate code")
    parser.add_argument("--libretranslate-url", type=str,
                        default="http://localhost:5000",
                        help="LibreTranslate server URL (for non-built-in languages)")
    parser.add_argument("--handwriting", type=str, default=None,
                        choices=["font", "synthesis"],
                        help="Apply full-document handwriting mode to all docs")

    return parser.parse_args()


def _wipe_output(output_dir: Path, db_path: Path):
    """Delete all previous PNG output and the ground-truth database."""
    log.info("Fresh run mode — wiping previous output …")

    for subdir in ["purchase_orders", "tax_invoices"]:
        p = output_dir / subdir
        if p.exists():
            shutil.rmtree(p)
            log.info(f"  Removed {p}")

    # WAL files too
    for suffix in ["", "-wal", "-shm"]:
        f = Path(str(db_path) + suffix)
        if f.exists():
            f.unlink()
            log.info(f"  Removed {f}")


async def main():
    args = parse_args()

    # Resolve legacy --ollama-model / --ollama-url into the new unified flags
    if args.ollama_model and args.llm_provider == "ollama":
        args.llm_model = args.ollama_model
    if args.llm_provider == "ollama" and args.llm_base_url is None:
        args.llm_base_url = args.ollama_url

    t0 = time.time()

    log.info("=" * 60)
    log.info("  B2B SYNTHETIC DOCUMENT PIPELINE")
    log.info(f"  Industry    : {args.industry}")
    log.info(f"  Doc Pairs   : {args.count:,}")
    log.info(f"  Workers     : {args.workers}")
    log.info(f"  Language    : {args.language}")
    log.info(f"  LLM backend : {args.llm_provider} / {args.llm_model}")
    log.info(f"  Handwriting : {args.handwriting or 'off'}")
    log.info(f"  Mode        : {'APPEND' if args.append else 'FRESH (previous output will be wiped)'}")
    log.info("=" * 60)

    # ── Fresh vs append ───────────────────────────────────────────────────────
    if not args.append:
        _wipe_output(OUTPUT_DIR, DB_PATH)

    # ── Initialise SQLite ─────────────────────────────────────────────────────
    log.info("Initialising SQLite master ground-truth database …")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_database(DB_PATH)

    # ── Pass 1: Dynamic Catalog Bootstrap ────────────────────────────────────
    if args.skip_bootstrap and CATALOG_PATH.exists():
        log.info(f"[PASS 1] Skipped — using existing {CATALOG_PATH}")
    else:
        log.info(f"[PASS 1] Bootstrapping industry catalog ({args.llm_provider}/{args.llm_model}) …")
        await run_bootstrap(
            industry=args.industry,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            llm_api_key=args.llm_api_key,
            llm_base_url=args.llm_base_url,
            output_path=CATALOG_PATH,
        )
        log.info(f"[PASS 1] Catalog saved → {CATALOG_PATH}")

    # ── Pass 2: High-Throughput Assembly ─────────────────────────────────────
    log.info(f"[PASS 2] Assembling {args.count:,} PO/Invoice pairs …")
    await run_assembly(
        catalog_path=CATALOG_PATH,
        output_dir=OUTPUT_DIR,
        db_path=DB_PATH,
        count=args.count,
        workers=args.workers,
        apply_degradation=not args.no_degradation,
        language=args.language,
        libretranslate_url=args.libretranslate_url,
        handwriting_mode=args.handwriting,
    )

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"  Pipeline complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    log.info(f"  Output    : {OUTPUT_DIR}")
    log.info(f"  Database  : {DB_PATH}")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())