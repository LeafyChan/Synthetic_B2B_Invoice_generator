"""
B2B Synthetic Document Dataset Pipeline
========================================
Generates 40,001 documents: 20k Purchase Orders + 20k Tax Invoices + 1 SQLite Master Log
Two-pass async pipeline with block-based floating layout engine.

Usage:
    python main.py --industry "Automotive Fasteners" --workers 4
    python main.py --industry "Commercial Furniture" --workers 8 --skip-bootstrap
"""

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from pipeline.bootstrap import run_bootstrap
from pipeline.assembler import run_assembly
from pipeline.database import init_database

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/pipeline.log"),
    ],
)
log = logging.getLogger("main")

BASE_DIR = Path(__file__).parent
CATALOG_PATH = BASE_DIR / "industry_catalog.json"
DB_PATH = BASE_DIR / "output" / "master_ground_truth.db"
OUTPUT_DIR = BASE_DIR / "output"


def parse_args():
    parser = argparse.ArgumentParser(description="B2B Synthetic Document Pipeline")
    parser.add_argument(
        "--industry",
        type=str,
        default="Automotive Fasteners",
        help="Business industry for catalog generation (Pass 1)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel worker processes for Pass 2",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=20000,
        help="Number of PO/Invoice pairs to generate (default: 20000)",
    )
    parser.add_argument(
        "--skip-bootstrap",
        action="store_true",
        help="Skip Pass 1 if industry_catalog.json already exists",
    )
    parser.add_argument(
        "--ollama-model",
        type=str,
        default="qwen2.5:7b",
        help="Ollama model for catalog bootstrap",
    )
    parser.add_argument(
        "--ollama-url",
        type=str,
        default="http://localhost:11434",
        help="Ollama server URL",
    )
    parser.add_argument(
        "--no-degradation",
        action="store_true",
        help="Skip OpenCV visual degradation (faster, cleaner output)",
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    t0 = time.time()

    log.info("=" * 60)
    log.info("  B2B SYNTHETIC DOCUMENT PIPELINE")
    log.info(f"  Industry  : {args.industry}")
    log.info(f"  Doc Pairs : {args.count:,}")
    log.info(f"  Workers   : {args.workers}")
    log.info("=" * 60)

    # ── Initialise SQLite ─────────────────────────────────────────────────────
    log.info("Initialising SQLite master ground-truth database …")
    init_database(DB_PATH)

    # ── Pass 1: Dynamic Catalog Bootstrap ────────────────────────────────────
    if args.skip_bootstrap and CATALOG_PATH.exists():
        log.info(f"[PASS 1] Skipped — using existing {CATALOG_PATH}")
    else:
        log.info(f"[PASS 1] Bootstrapping industry catalog via Ollama ({args.ollama_model}) …")
        await run_bootstrap(
            industry=args.industry,
            model=args.ollama_model,
            ollama_url=args.ollama_url,
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
    )

    elapsed = time.time() - t0
    log.info("=" * 60)
    log.info(f"  Pipeline complete in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    log.info(f"  Output    : {OUTPUT_DIR}")
    log.info(f"  Database  : {DB_PATH}")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
