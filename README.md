# B2B Synthetic Document Dataset Pipeline

Generates paired Purchase Orders and Tax Invoices as PNG images and multi-page PDFs,
with a SQLite ground-truth log. Designed for OCR / document-understanding model training.

---

## Quick Start

```bash
# One-time setup: build the HSN/SAC TF-IDF search index
python pipeline/build_hsn_index.py --xlsx /path/to/HSN_SAC.xlsx

# Smoke test (no Playwright needed, validates imports + math)
python test_pipeline.py

# Fresh run — 3 pairs, 1 worker, PDF+PNG, all tiers, degradation ON by default
python main.py --industry "Automotive Fasteners" --count 3 --workers 1 --output-format both

# Full 20k run
python main.py --industry "Automotive Fasteners" --workers 4
```

### Test command for 3 multi-page PDFs (clean / degraded / heavy), fresh mode

```bash
python main.py \
  --industry "Industrial Equipment" \
  --count 3 \
  --workers 1 \
  --output-format pdf \
  --skip-bootstrap
```

> **Why `--skip-bootstrap`?** If `industry_catalog.json` already exists from a previous run,
> this skips the LLM/TF-IDF catalog generation step and reuses it. Remove the flag on a
> first run or when you want a fresh catalog.

> **Why only 3 docs hit all 3 tiers?** By default `assign_tier()` is probabilistic per
> doc_index (50/30/20 weighted, deterministic per index). With 3 docs you'll likely see
> 2 tiers; with ~10 you'll reliably see all 3 — or use `--tier-counts` (below) to guarantee
> an exact split regardless of count.

```bash
# Guaranteed hit of all 3 tiers + multi-page PDFs, exact split
python main.py \
  --industry "Industrial Equipment" \
  --count 10 \
  --workers 1 \
  --output-format pdf \
  --tier-counts clean=3,degraded=3,heavy=4 \
  --skip-bootstrap
```

---

## Exact Tier Counts (`--tier-counts`)

By default, tier assignment is **probabilistic**: `assign_tier(doc_index)` deterministically
maps each index to clean/degraded/heavy via a seeded RNG, converging to a 50/30/20 split over
a large run. This is unchanged from the original design and is still what happens if you don't
pass `--tier-counts`.

For small or exact-count runs — e.g. "generate exactly 10 docs: 3 clean, 3 degraded, 4 heavy" —
pass `--tier-counts`:

```bash
python main.py --industry "Fasteners" --count 10 --workers 1 \
    --tier-counts clean=3,degraded=3,heavy=4
```

Rules:
- The counts **must sum exactly to `--count`** — if they don't, `main.py` exits immediately
  with a clear error before any bootstrap/LLM work or output wiping happens.
- Tier assignment within exact-count mode is still deterministic and reproducible: indices are
  bucketed by their "natural" `assign_tier()` result first, and only reassigned where a tier's
  natural count over/undershoots its requested quota — so re-running the same `--count` +
  `--tier-counts` always produces the same plan.
- Each tier still randomly picks its visual degradation **profile** (worn_laser, tea_stained,
  etc.) the same way as before — `--tier-counts` only controls which tier each doc_index lands
  in, not how that tier degrades.
- Discrepancy (see below) is independent of tier and unaffected by this flag — a discrepant
  doc still gets whatever tier the plan assigns it (so e.g. a discrepant+heavy doc gets both
  the discrepancy mutation and heavy degradation).
- `--no-degradation` + `--tier-counts` together logs a warning and ignores `--tier-counts`
  (everything is `clean` when degradation is off).

---

## How Multi-Page PDFs Work

Multi-page output is **automatic and random** — no flag needed.

### What controls page count

`data_models.py` draws line-item counts from this distribution per document:

| Probability | Line items | Typical pages |
|-------------|------------|---------------|
| 15 %        | 5 – 9      | 1             |
| 60 %        | 10 – 25    | 1 – 2         |
| 25 %        | 26 – 50    | 2 – 4         |

Documents with 26–50 items reliably spill across 2–4 A4 pages.

### Page layout (what goes where)

- **Page 1** — coloured header bar, buyer/seller/shipping/meta blocks, and the first
  N line items that fit.
- **Page 2+ (overflow)** — continuation of the items table.
- **Last page** — remaining items, then the summary block (subtotal / tax / grand total)
  and footer (payment terms, signature). These are kept together via
  `page-break-inside: avoid` in the `@media print` CSS block in `layout_engine.py`.

### How it's implemented

`layout_engine.py` has a `@media print` CSS block that:
- Removes the fixed `794px` width and `box-shadow` so the document fills A4 width.
- Applies `page-break-inside: avoid` to every `<tr>` in the items table (no row splits
  across pages) and to `.summary-block` / `.footer-block` (they stay together on the
  last page).

`renderer.py`'s `render_html_to_pdf()` calls Playwright's `page.pdf(format="A4",
print_background=True)` with zero margins, which activates those print rules and lets
the browser's layout engine handle pagination automatically.

The PDF bytes are written to `output/purchase_orders/<tier>/po_NNNNNN.pdf` and
`output/tax_invoices/<tier>/inv_NNNNNN.pdf`, and are degraded the same as PNGs (see
**PDF Degradation** below) — both formats reflect the assigned tier visually.

---

## PDF Degradation

PDF output is **degraded with the same tiered pipeline as PNGs**, not left clean.

`pdf_degradation.py` closes the gap between `degradation.py` (which is pixel-array/OpenCV
and can't touch a vector PDF directly) and the PDF output path:

1. **Rasterize** each PDF page to a PNG at 200 DPI via PyMuPDF.
2. **Degrade** each page image using the exact same `degrade_image()` used for the PNG
   output — same tier, same profile selection logic — so a "heavy" tier PDF and its PNG
   counterpart look visually consistent.
3. **Rebuild** a new PDF from the degraded page images, preserving A4 page dimensions.

Multi-page documents get a distinct-but-derived seed per page (`doc_index * 1000 +
page_number`) so page 2 of a degraded multi-page invoice doesn't look like a clone of
page 1 — real multi-page scans vary stain/crumple per page.

Requires `pymupdf` (already in `requirements.txt`). If unavailable, PDF degradation is
skipped and a warning is logged — the PDF is still produced, just undegraded.

---

## Discrepancy Injection

A configurable fraction of PO/Invoice pairs get a deliberate, labeled mismatch — useful
for training "does this invoice match its PO" detection models.

- **Rate:** 15% of pairs by default (`DISCREPANCY_RATE` in `data_models.py`), assigned
  deterministically per `doc_index` via its own seeded RNG stream (separate from tier
  assignment and from document content generation, so none of these interfere).
- **Five discrepancy kinds**, one applied per discrepant pair: `quantity_mismatch`,
  `price_mismatch`, `extra_line_item`, `missing_line_item`, `gst_rate_mismatch`.
- The PO and invoice get **independent deep copies** of `line_items` before any mutation
  — an earlier bug shared the same list object between PO and invoice, which meant
  mutating one for a discrepancy silently mutated the other too (see `learning.md` §11
  for the full story).
- Discrepant pairs are written to both their assigned tier folder **and**
  `discrepant/`, with matching visual degradation for that tier.
- `has_discrepancy`, `discrepancy_kind` are real queryable columns on the `documents`
  table (not buried in the JSON payload):
  ```sql
  SELECT COUNT(*) FROM documents WHERE has_discrepancy = 1;
  SELECT discrepancy_kind, COUNT(*) FROM documents WHERE has_discrepancy = 1 GROUP BY discrepancy_kind;
  ```

Exact discrepant counts (e.g. "exactly 4 of 10 must be discrepant," mirroring
`--tier-counts`) is not yet wired into the CLI — `tier_planner.plan_discrepancy_indices()`
implements the same exact-count pattern and is ready to use, but `data_models.
generate_document_pair()` needs a small change (accept an optional `force_discrepancy: bool`
instead of always calling `_assign_discrepancy(idx)` internally) to plug it in.

---

## Output Structure

```
output/
├── master_ground_truth.db          ← SQLite: one row per doc, one row per pair
├── purchase_orders/
│   ├── clean/      po_000000.png  po_000000.pdf  ...
│   ├── degraded/   po_000001.png  po_000001.pdf  ...
│   ├── heavy/      po_000002.png  po_000002.pdf  ...
│   └── discrepant/ po_000005.png  po_000005.pdf  ...  (deliberate PO↔invoice mismatches)
└── tax_invoices/
    ├── clean/      inv_000000.png  inv_000000.pdf  ...
    ├── degraded/   inv_000001.png  inv_000001.pdf  ...
    ├── heavy/      inv_000002.png  inv_000002.pdf  ...
    └── discrepant/ inv_000005.png  inv_000005.pdf  ...
```

---

## Run Modes

### Fresh run (default)
Wipes `output/purchase_orders/`, `output/tax_invoices/`, and `master_ground_truth.db`,
then generates from doc_index 0.

```bash
python main.py --industry "Textile Machinery" --count 20000 --workers 4
```

### Append run
Keeps existing files and DB. Auto-continues from `max(doc_index) + 1` so new documents
never overwrite existing ones.

```bash
python main.py --industry "Textile Machinery" --count 5000 --workers 4 --append
```

### Reuse catalog
Skip Pass 1 (LLM bootstrap) if `industry_catalog.json` already exists:

```bash
python main.py --industry "Textile Machinery" --count 1000 --workers 4 --skip-bootstrap
```

---

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--industry` | `"Automotive Fasteners"` | Industry name sent to the LLM for catalog generation |
| `--count` | `20000` | Number of PO/Invoice pairs to generate |
| `--workers` | `4` | Parallel subprocess workers (one Chromium per worker) |
| `--output-format` | `both` | `png` / `pdf` / `both` |
| `--tier-counts` | off | Exact tier counts, e.g. `clean=3,degraded=3,heavy=4`. Must sum to `--count`. Default: probabilistic 50/30/20 split |
| `--append` | off | Keep existing output; continue numbering from last doc_index |
| `--skip-bootstrap` | off | Reuse `industry_catalog.json` if it exists |
| `--no-degradation` | off | Skip degradation; all docs go to `clean/` (PNG and PDF) |
| `--language` | `en` | Language code: `en`, `hi`, `ur`, `fr`, or any LibreTranslate code |
| `--handwriting` | off | `font` or `synthesis` (synthesis is a documented stub) |
| `--llm-provider` | `ollama` | `ollama` / `openai` / `anthropic` |
| `--llm-model` | `qwen2.5:7b` | Model string passed to the provider |
| `--llm-api-key` | — | API key (OpenAI / Anthropic) |
| `--llm-base-url` | — | Base URL override (e.g. OpenAI-compatible self-hosted endpoint) |
| `--start-index` | auto | Override starting doc_index (auto-resolved on `--append`) |
| `--list-languages` | — | Print all supported language codes and exit |

---

## Degradation Tiers

Assigned per `doc_index` — probabilistically by default (50% clean / 30% degraded / 20%
heavy), or exactly via `--tier-counts`:

| Tier | Profiles | Effects |
|------|----------|---------|
| `clean` | pristine, clean_laser, high_res_clean | Near-zero noise, no stains, no rotation |
| `degraded` | worn_laser, inkjet_old, low_ink, archive_scan, office_copy, streaky_toner | Visible noise, light stains, mild crumple, low-ink streaking, slight rotation |
| `heavy` | fax_quality, tea_stained, crumpled_scan, low_dpi_old, ink_bleed_worn, dying_cartridge | Heavy stains, strong crumple/warp, resolution loss, aggressive fading, rotation up to ±2.5° |

Applies identically to PNG and PDF output (see **PDF Degradation** above).

---

## Per-Step Timing

Every worker process times each major step (`generate_document_pair`, `render_html`,
`handwriting_overlay`, `render_html_to_png`, `degrade_image`, `render_html_to_pdf`,
`degrade_pdf`, `log_document_pair`) via `pipeline/timing.py`. A breakdown is logged to
`logs/pipeline.log` every 100 docs per worker, and once more at the end of each worker's
shard:

```
PID 48213 — Timing breakdown (total tracked time: 812.4s):
    render_html_to_pdf            512.3s  ( 63.1%)    100 calls  avg 5123.0ms
    degrade_pdf                   180.1s  ( 22.2%)    100 calls  avg 1801.0ms
    render_html_to_png             68.2s  (  8.4%)    100 calls  avg  682.0ms
    degrade_image                  31.5s  (  3.9%)    100 calls  avg  315.0ms
    generate_document_pair         12.1s  (  1.5%)    100 calls  avg  121.0ms
    log_document_pair               8.2s  (  1.0%)    100 calls  avg   82.0ms
```

`main.py` also logs the Pass 1 (bootstrap) vs Pass 2 (assembly) wall-clock split at the
end of a run. Timing is per-process — each worker has independent counters, which tells
you whether a slow step is systemic (every worker) or isolated (one worker, likely
resource contention).

In practice `render_html_to_pdf` (Playwright's print pipeline) is typically the single
largest cost — if you don't need PDFs for a given run, `--output-format png` skips it
entirely.

---

## Architecture Overview

```
Pass 1 — bootstrap.py (run once per industry)
  LLM → product descriptions
  → hsn_lookup.py (TF-IDF over official HSN/SAC master)
  → industry_catalog.json  (~1,000 items with real HSN codes + GST rates)

Pass 2 — assembler.py (parallel workers, one per CPU)
  for each doc_index:
    tier_planner.py         → resolves tier (probabilistic default, or exact --tier-counts plan)
    data_models.py           → PurchaseOrder + TaxInvoice dataclasses (+ discrepancy injection)
    layout_engine.py         → HTML strings (8 layout variants × 8 themes)
    [handwriting.py]         → optional font-based handwriting overlay
    renderer.py               → PNG bytes (Playwright/Chromium screenshot)
                              → PDF bytes (Playwright page.pdf(), multi-page)
    degradation.py            → tiered OpenCV degradation (PNG)
    pdf_degradation.py        → tiered degradation for PDF (rasterize → degrade → rebuild)
    database.py                → SQLite insert (documents + document_pairs)
    timing.py                  → per-step duration tracking, logged periodically
```

---

## Module Responsibilities

| File | What it does |
|------|-------------|
| `data_models.py` | Dataclasses + deterministic field generators (Faker, seeded RNG); discrepancy injection |
| `layout_engine.py` | HTML template rendering; 8 layout variants, 8 colour themes, RTL support |
| `renderer.py` | Playwright pool; `render_html_to_png()` + `render_html_to_pdf()` |
| `degradation.py` | OpenCV pipeline; 15 profiles across 3 tiers; `assign_tier()` |
| `pdf_degradation.py` | Rasterize → degrade → rebuild pipeline so PDF output matches PNG degradation |
| `tier_planner.py` | Resolves probabilistic default or exact `--tier-counts` tier assignment per doc_index |
| `timing.py` | Lightweight per-step timing accumulator + periodic log breakdown |
| `database.py` | SQLite schema + insert helpers; WAL mode for parallel writes |
| `bootstrap.py` | Pass 1 orchestration; LLM path + TF-IDF fallback |
| `assembler.py` | Pass 2 orchestration; `ProcessPoolExecutor` sharding |
| `main.py` | CLI entry point; fresh/append logic; logging setup |
| `hsn_lookup.py` | Runtime TF-IDF search over HSN/SAC master (lazy singleton) |
| `build_hsn_index.py` | One-time index builder from official HSN_SAC.xlsx |
| `gst_rate_schedule.py` | Chapter/heading/SAC rate tables; `lookup_rate()`; `guess_unit()` |
| `languages.py` | `LanguageConfig` dataclass; built-in EN/HI/UR/FR labels; LibreTranslate bridge |
| `handwriting.py` | Font-mode full-document handwriting overlay; synthesis stub |
| `llm_providers.py` | Provider abstraction: Ollama / OpenAI-compatible / Anthropic |

---

## Dependencies

```
playwright        # HTML → PNG/PDF rendering
opencv-python     # Image degradation
pymupdf           # PDF rasterize/rebuild for pdf_degradation.py
Pillow            # Fallback renderer + degradation helpers
numpy             # Array ops in degradation
faker             # Synthetic names/addresses (en_IN locale)
scikit-learn      # TF-IDF vectorizer for HSN lookup
joblib            # Serialise/load TF-IDF index
openpyxl          # Parse HSN_SAC.xlsx in build_hsn_index.py
aiohttp           # Async HTTP to LLM providers
```

Install Playwright browsers after `pip install playwright`:
```bash
playwright install chromium
```

---

## Known Limitations / Planned Extensions

- **Exact discrepant counts** — `--tier-counts` gives exact control over tier
  distribution, but discrepancy rate is still probabilistic only (`DISCREPANCY_RATE =
  0.15` in `data_models.py`). `tier_planner.plan_discrepancy_indices()` implements the
  same exact-count pattern and is ready; wiring it in requires a small change to
  `generate_document_pair()` to accept an optional `force_discrepancy: bool`.

- **Handwriting synthesis mode** — `handwriting.py`'s `mode="synthesis"` raises
  `NotImplementedError`. Font mode (`mode="font"`) is fully working. A real synthesis
  backend would need an IAM-trained stroke-sequence model; see the stub's docstring for
  the exact integration contract.

- **Non-Latin handwriting synthesis** — font mode works for any Google Font (including
  Devanagari/Nastaliq via `languages.py`), but synthesis mode is Latin-only pending an
  IAM-equivalent model for those scripts.

---

## Project Log (session history)

### What was built and why

**Two-pass architecture** — Pass 1 (bootstrap) runs once per industry and is expensive
(LLM calls). Pass 2 (assembly) runs 20,000 times and is cheap (template + render). This
1:1,000 reuse ratio is the reason the split exists.

**HTML + Playwright instead of reportlab/WeasyPrint/PIL** — A real browser rendering
engine handles fonts, RTL text, table layout, and multi-page flow correctly by
construction. PIL-based rendering would require hand-coding every layout detail.
ReportLab has a steep API surface. WeasyPrint is close but Playwright gives a real
Chromium engine and a clean screenshot API for PNG output on the same path as PDF.

**TF-IDF for HSN lookup** — HSN/SAC descriptions are dense legal jargon where exact
term overlap is the strongest match signal, not semantic paraphrase. TF-IDF needs no
model download, no GPU, runs in <10ms per query, and builds in <5 seconds. Sentence
transformers would add inference cost with no benefit for this specific retrieval task.

**SQLite over PostgreSQL** — single-machine pipeline, one portable output file, no
concurrent multi-user access needed. `PRAGMA journal_mode=WAL` handles the one real
concurrency concern (multiple worker processes writing simultaneously).

**Provider abstraction in `llm_providers.py`** — Ollama was originally hardcoded in
`bootstrap.py`. The refactor defines a `complete()` / `healthcheck()` interface so
switching to OpenAI or Anthropic is a config change, not a code change.

**PDF degradation as a parallel pipeline (`pdf_degradation.py`)** — `degradation.py` is
pixel-array/OpenCV-based and can't act on a vector PDF directly. Rather than building a
second, separate degradation system for PDFs, `pdf_degradation.py` rasterizes each PDF
page, runs the exact same `degrade_image()` used for PNGs, then rebuilds the PDF —
reusing all 15 existing profiles instead of duplicating logic, and keeping PNG/PDF
output visually consistent for the same doc_index/tier.

**Discrepancy injection (`data_models.py`)** — built once the shared-list-object bug
(see Bugs Fixed below) was identified and fixed; five discrepancy kinds, deterministic
15% rate, independent of tier assignment.

**`tier_planner.py` — exact tier counts without disturbing default behaviour** — added
so small/demo runs (`--count 10`) can guarantee an exact tier split instead of relying
on probability to "probably" hit all three tiers. Designed so omitting `--tier-counts`
is byte-for-byte identical to the original probabilistic behaviour — no new code path
activates unless explicitly opted into.

**`timing.py` — per-step duration logging** — added because there was previously zero
visibility into *which* pipeline stage was the bottleneck, only an aggregate docs/s
rate. In practice this surfaced `render_html_to_pdf` (Playwright's print pipeline) as
the dominant cost in PDF-emitting runs.

### Bugs fixed during development

- `_PRICE_BY_RATE` was being imported from `gst_rate_schedule` (where it doesn't exist)
  inside `bootstrap.py`'s fallback path. Fixed by removing the phantom import — the
  dict was already defined at module level in `bootstrap.py`.

- `assign_tier()` was not exported from `degradation.py`'s public surface initially,
  causing an ImportError in `assembler.py`. Fixed by ensuring it is defined at module
  level (not inside a function).

- `--append` originally only prevented the output folder from being wiped, but did not
  prevent `doc_index` from restarting at 0 — causing silent overwrites on resume.
  Fixed by adding `get_max_doc_index()` to `database.py` and auto-resolving
  `start_index = max_existing + 1` in `main.py`.

- `get_pair_count()` and `get_max_doc_index()` used bare `except` clauses that silently
  returned 0/−1 for any failure, making a locked/corrupt DB indistinguishable from an
  empty one. Fixed by adding `log.warning` / `log.error` with explicit messages
  explaining what main.py will infer from the returned value.

- `_jitter_css()` in `handwriting.py` originally set `display: inline-block` on `<td>`
  elements, which collapsed table column widths. Fixed by applying only `transform`
  (rotate/translate) to table cells and reserving `display` overrides for paragraph
  elements only.

- `lookup_rate()` in `gst_rate_schedule.py` originally inferred `is_service=True` from
  codes starting with `"99"` — a landmine because Indian customs chapters 98/99 also
  cover physical goods. Fixed by requiring callers to pass `is_service` explicitly and
  never inferring it from the code string.

- **PDF output had zero degradation** — `degradation.py`'s pixel-array pipeline was
  never called for PDFs (they're vector content, not pixels), so `degraded/` and
  `heavy/` tier PDFs looked identical to `clean/`. Fixed by `pdf_degradation.py`
  (rasterize → degrade_image() → rebuild). See `learning.md` §10 for the full story.

- **PO and invoice shared the same `line_items` list object** — mutating one for
  discrepancy injection silently mutated the other too, making "discrepant" pairs
  actually identical. Fixed by deep-copying `line_items` for the invoice before any
  discrepancy mutation. See `learning.md` §11.

- **`.gitignore` didn't retroactively untrack already-committed files** — `__pycache__`,
  `output/`, and several local-only scripts kept showing up in `git status` and on
  GitHub despite being listed in `.gitignore`, because `.gitignore` only prevents
  *future* additions, not files already in git's index from earlier commits. Fixed in
  two stages: (1) `git rm -r --cached <path>` to untrack without deleting from disk,
  committed and pushed normally; (2) `git filter-repo --path-glob '...' --invert-paths
  --force` to strip those paths from every commit in history (not just HEAD), followed
  by a force-push. Order mattered: normal sync to GitHub had to land *before* the
  history rewrite, or the rewrite would purge a stale snapshot and orphan newer work.