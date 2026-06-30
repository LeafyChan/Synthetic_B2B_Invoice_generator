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

# Fresh run — 3 pairs, 1 worker, PDF+PNG, all tiers, no degradation flag off
# (degradation is ON by default; --no-degradation disables it)
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

> **Why only 3 docs hit all 3 tiers?** `assign_tier()` is deterministic per doc_index.
> doc_index 0 → clean, doc_index 1 → degraded, doc_index 2 → heavy (based on the
> 50/30/20 weighted random with a fixed prime salt). With 3 docs you are likely to see
> at least 2 tiers; with ~10 docs you will reliably see all 3. Use `--count 10` if you
> want to guarantee one of each tier in the output.

```bash
# Guaranteed hit of all 3 tiers + multi-page PDFs
python main.py \
  --industry "Industrial Equipment" \
  --count 10 \
  --workers 1 \
  --output-format pdf \
  --skip-bootstrap
```

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
`output/tax_invoices/<tier>/inv_NNNNNN.pdf`.

---

## Output Structure

```
output/
├── master_ground_truth.db          ← SQLite: one row per doc, one row per pair
├── purchase_orders/
│   ├── clean/      po_000000.png  po_000000.pdf  ...
│   ├── degraded/   po_000001.png  po_000001.pdf  ...
│   ├── heavy/      po_000002.png  po_000002.pdf  ...
│   └── discrepant/ (future: pairs with deliberate PO↔invoice mismatches)
└── tax_invoices/
    ├── clean/      inv_000000.png  inv_000000.pdf  ...
    ├── degraded/   inv_000001.png  inv_000001.pdf  ...
    ├── heavy/      inv_000002.png  inv_000002.pdf  ...
    └── discrepant/
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
| `--append` | off | Keep existing output; continue numbering from last doc_index |
| `--skip-bootstrap` | off | Reuse `industry_catalog.json` if it exists |
| `--no-degradation` | off | Skip OpenCV degradation; all docs go to `clean/` |
| `--language` | `en` | Language code: `en`, `hi`, `ur`, `fr`, or any LibreTranslate code |
| `--handwriting` | off | `font` or `synthesis` (synthesis is a documented stub) |
| `--llm-provider` | `ollama` | `ollama` / `openai` / `anthropic` |
| `--llm-model` | `qwen2.5:7b` | Model string passed to the provider |
| `--llm-api-key` | — | API key (OpenAI / Anthropic) |
| `--start-index` | auto | Override starting doc_index (auto-resolved on `--append`) |
| `--list-languages` | — | Print all supported language codes and exit |

---

## Degradation Tiers

Assigned deterministically per `doc_index` (50 % clean / 30 % degraded / 20 % heavy):

| Tier | Profiles | Effects |
|------|----------|---------|
| `clean` | pristine, clean_laser, high_res_clean | Near-zero noise, no stains, no rotation |
| `degraded` | worn_laser, inkjet_old, low_ink, archive_scan, office_copy, streaky_toner | Visible noise, light stains, mild crumple, low-ink streaking, slight rotation |
| `heavy` | fax_quality, tea_stained, crumpled_scan, low_dpi_old, ink_bleed_worn, dying_cartridge | Heavy stains, strong crumple/warp, resolution loss, aggressive fading, rotation up to ±2.5° |

---

## Architecture Overview

```
Pass 1 — bootstrap.py (run once per industry)
  LLM → product descriptions
  → hsn_lookup.py (TF-IDF over official HSN/SAC master)
  → industry_catalog.json  (~1,000 items with real HSN codes + GST rates)

Pass 2 — assembler.py (parallel workers, one per CPU)
  for each doc_index:
    data_models.py          → PurchaseOrder + TaxInvoice dataclasses
    layout_engine.py        → HTML strings (8 layout variants × 8 themes)
    [handwriting.py]        → optional font-based handwriting overlay
    renderer.py             → PNG bytes (Playwright/Chromium screenshot)
                            → PDF bytes (Playwright page.pdf(), multi-page)
    degradation.py          → tiered OpenCV degradation (PNG only)
    database.py             → SQLite insert (documents + document_pairs)
```

---

## Module Responsibilities

| File | What it does |
|------|-------------|
| `data_models.py` | Dataclasses + deterministic field generators (Faker, seeded RNG) |
| `layout_engine.py` | HTML template rendering; 8 layout variants, 8 colour themes, RTL support |
| `renderer.py` | Playwright pool; `render_html_to_png()` + `render_html_to_pdf()` |
| `degradation.py` | OpenCV pipeline; 15 profiles across 3 tiers; `assign_tier()` |
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

- **Discrepancy injection** — `has_discrepancy` flag exists on `TaxInvoice` and the
  `discrepant/` output folders are created, but intentional PO↔invoice mismatches are
  not yet generated. Planned: randomly patch a line-item quantity or unit price on the
  invoice side after copying from the PO.

- **Handwriting synthesis mode** — `handwriting.py`'s `mode="synthesis"` raises
  `NotImplementedError`. Font mode (`mode="font"`) is fully working. A real synthesis
  backend would need an IAM-trained stroke-sequence model; see the stub's docstring for
  the exact integration contract.

- **PDF degradation** — degradation (`degradation.py`) is applied to PNG output only.
  PDF files are stored as clean Playwright output regardless of tier. Degrading PDFs
  would require rasterising each page then re-encoding, which is a separate step.

- **Non-Latin handwriting synthesis** — `handwriting.py` font mode works for any Google
  Font (including Devanagari/Nastaliq via `languages.py`), but synthesis mode is Latin
  only pending an IAM-equivalent model for those scripts.

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