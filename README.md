# B2B Synthetic Document Dataset Pipeline — Project Log
**Last updated:** 28 June 2026

---

## At a Glance

| Component | Status | Notes |
|---|---|---|
| Core 2-pass pipeline (catalog + assembly) | ✅ **Working** | Tested end-to-end |
| Real HSN/SAC grounding (14,709 + 649 codes) | ✅ **Done** | TF-IDF index, no invented codes |
| 3-tier visual realism (clean/degraded/heavy) | ✅ **Done** | 50/30/20 split |
| Low-ink / streaky printer fade effect | ✅ **Done** | Layered into tiers 2 & 3 |
| **Critical bug fixes #1–3** (screenshot truncation, shared line items, GSTIN correlation) | ✅ **Fixed** | See §14 |
| **Unit code fix** (KG→KGS, PKT→PAC, ROLL→ROL) | ✅ **Fixed** | All units now official CBIC codes |
| **GST rate fix** (added 0.25%, 3%, 0.1%, 40%, etc.) | ✅ **Fixed** | Full official rate set |
| **Fresh vs append run mode** | ✅ **Done** | Default = fresh (wipes output+DB); `--append` to add |
| **Discrepant pair separation** | ✅ **Done** | ~20% of pairs → also written to `discrepant/` subfolder |
| **Multi-language architecture** (languages.py) | ✅ **Done + INTEGRATED** | `--language` flag wired into main.py + assembler + layout_engine |
| **LLM provider abstraction** (llm_providers.py) | ✅ **Done + INTEGRATED** | `--llm-provider` flag; replaces `--ollama-model` / `--ollama-url` |
| **Full-document handwriting** (handwriting.py) | ✅ **Done + INTEGRATED** | `--handwriting font` flag wired in |
| 20,000-pair production run | ⏳ **Not started** | All fixes landed — ready after a fresh smoke test |

---

## Table of Contents

1. [The Original Idea](#1-the-original-idea)
2. [Architecture Overview](#2-architecture-overview)
3. [HSN/SAC Brief](#3-hsnsac-brief)
4. [Three-Tier Visual Realism + Low-Ink Fade](#4-three-tier-visual-realism--low-ink-fade)
5. [Multi-Language Architecture](#5-multi-language-architecture)
6. [Handwriting (Font Mode + Synthesis Stub)](#6-handwriting-font-mode--synthesis-stub)
7. [LLM Provider Abstraction](#7-llm-provider-abstraction--beyond-ollama)
8. [Resolved Issue — Empty Database](#8-resolved-issue--empty-database)
9. [File Manifest](#9-file-manifest--what-to-drop-where)
10. [Command Reference — Every Command Used So Far](#10-command-reference--every-command-used-so-far)
11. [Proposed Final Outcome](#11-proposed-final-outcome)
12. [Suggested Additions Not Yet Built](#12-suggested-additions-not-yet-built)
13. [On Using This Dataset](#13-on-using-this-dataset)
14. [Known Issues — Code Review Findings](#14-known-issues-found-in-code-review)

---

## 1. The Original Idea

Build a synthetic dataset generator producing large volumes of realistic Indian B2B business document pairs — Purchase Orders and matching Tax Invoices — complete with ground-truth structured data for every field on every document. Intended use: training and evaluating document-understanding / OCR models.

Three things layered together make this realistic:
- **Structurally varied documents** — different layouts, terminology, fonts, formats.
- **Tax-correct content** — real HSN/SAC product codes with the GST rates that actually apply.
- **Visually degraded renders** — real-world scanned/photographed documents are never pixel-perfect.

Original target: 20,000 Purchase Orders + 20,000 Tax Invoices + one master SQLite database.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     main.py (Coordinator)                   │
│  asyncio event loop • arg parsing • logging • DB init       │
│  fresh/append mode • language • handwriting • LLM backend   │
└────────────┬───────────────────────┬────────────────────────┘
             │                       │
      ┌──────▼──────┐        ┌───────▼──────────────────────┐
      │   PASS 1    │        │          PASS 2               │
      │  bootstrap  │        │       assembler               │
      │             │        │                               │
      │ llm_provid- │        │ ProcessPoolExecutor           │
      │ ers.py      │        │ N worker shards               │
      │ (ollama /   │        │ ↓                             │
      │  openai /   │        │ data_models.py  (discrepancy) │
      │  anthropic) │        │ layout_engine.py (lang_cfg)   │
      │ ↓           │        │ handwriting.py  (optional)    │
      │ industry_   │        │ renderer.py     (no-truncate) │
      │ catalog.json│        │ degradation.py  (3 tiers)     │
      └─────────────┘        │ database.py                   │
                             │ languages.py                  │
                             └───────────────────────────────┘
```

### Output layout

```
output/
├── purchase_orders/
│   ├── clean/          po_000000.png  (50% of pairs)
│   ├── degraded/       po_000003.png  (30% of pairs)
│   ├── heavy/          po_000007.png  (20% of pairs)
│   └── discrepant/     po_000012.png  (~20% of pairs — PO/Invoice mismatch)
├── tax_invoices/
│   ├── clean/          inv_000000.png
│   ├── degraded/       inv_000003.png
│   ├── heavy/          inv_000007.png
│   └── discrepant/     inv_000012.png
└── master_ground_truth.db
```

**Important:** discrepant pairs are also in their tier folder — `discrepant/` is an additive overlay for easy filtering, not a replacement for the tier structure.

---

## 3. HSN/SAC Brief

HSN (Harmonized System of Nomenclature) codes: 2-digit chapter → 4-digit heading → 6-digit subheading → 8-digit tariff item. The pipeline uses all 14,709 leaf-level 8-digit codes + 649 SAC codes from the official CBIC `HSN_SAC.xlsx` master.

**Unit code fix applied:** previously `guess_unit()` returned `KG`, `PKT`, `ROLL` — invalid codes not in the official CBIC unit master. These are now `KGS`, `PAC`, `ROL` respectively, matching the official master exactly.

**GST rate fix applied:** previously only rates {5, 12, 18, 28} were used. The full official set is now: `{0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28, 40}` — covering exempt goods, diamonds (0.25%), precious metals (3%), and luxury goods (40%).

---

## 4. Three-Tier Visual Realism + Low-Ink Fade

Three deterministic tiers (50% clean / 30% degraded / 20% heavy), assigned per `doc_index`. Output in separate subfolders. See earlier sections for full detail.

---

## 5. Multi-Language Architecture

**Now fully integrated.** `--language` flag wired from `main.py` → `assembler.py` → `layout_engine.py`.

- **Tier A** (verified): `en`, `fr`
- **Tier B** (evidence-based, unverified): `hi` (Hindi/Devanagari), `ur` (Urdu/Nastaliq RTL)
- **Tier C** (auto-translated via LibreTranslate): any other code LibreTranslate supports

RTL layout fully handled: `dir="rtl"` on body, `<span dir="ltr">` wrapping on all numeric/code fields.

### LibreTranslate setup (WSL + venv)

```bash
# In your project venv:
pip install libretranslate --break-system-packages

# Then start the server (keep this terminal open, or use a tmux/screen session):
libretranslate --load-only en,hi,ur,bn,ta,te,fr,de,es,zh &

# Or Docker if you prefer (easier — avoids any venv conflicts):
docker run -ti --rm -p 5000:5000 libretranslate/libretranslate

# Verify it's running:
curl http://localhost:5000/languages
```

**Notes for WSL:**
- LibreTranslate downloads ~50MB of Argos Translate model files on first run. This can take a few minutes.
- The `&` above backgrounds it; to stop it: `kill %1` or `pkill libretranslate`.
- If running Docker on WSL, make sure Docker Desktop WSL integration is enabled in Docker Desktop → Settings → Resources → WSL Integration.
- Cached Tier C configs live in `pipeline/language_cache/{code}.json` — translation only runs once per language ever, not per document.

---

## 6. Handwriting (Font Mode + Synthesis Stub)

**Now fully integrated.** `--handwriting font` wired from `main.py` → `assembler.py` → per-document HTML post-processing via `pipeline.handwriting.render_handwritten_document()`.

Intensity is tier-aware: 0.4 for clean, 0.7 for degraded, 1.0 for heavy.

`--handwriting synthesis` raises `NotImplementedError` with a clear integration contract message.

---

## 7. LLM Provider Abstraction — Beyond Ollama

**Now fully integrated.** `--llm-provider` / `--llm-model` / `--llm-api-key` / `--llm-base-url` wired from `main.py` → `bootstrap.py` via `pipeline.llm_providers.get_provider()`.

Old flags `--ollama-model` and `--ollama-url` still work for backward compatibility.

---

## 8. Resolved Issue — Empty Database

Confirmed fixed. A second run produced 100 rows in `documents` (50 POs + 50 invoices), 50 rows in `document_pairs`, correct schema including `tier` column. See earlier sections for diagnostic steps.

---

## 9. File Manifest — What to Drop Where

All paths relative to project root (`~/b2b_synthetic_gen/`):

| File | Destination | Notes |
|---|---|---|
| `main.py` | `main.py` | **Replace** — fresh/append mode, language, LLM, handwriting flags |
| `gst_rate_schedule.py` | `pipeline/gst_rate_schedule.py` | **Replace** — full rate set, CBIC unit codes |
| `bootstrap.py` | `pipeline/bootstrap.py` | **Replace** — uses llm_providers, all backends |
| `assembler.py` | `pipeline/assembler.py` | **Replace** — language, handwriting, discrepant folder |
| `layout_engine.py` | `pipeline/layout_engine.py` | **Replace** — language-integrated, RTL support |
| `data_models.py` | `pipeline/data_models.py` | **Replace** — fixes #2 (shared items) + #3 (GSTIN) |
| `renderer.py` | `pipeline/renderer.py` | **Replace** — fixes #1 (screenshot truncation) |
| `languages.py` | `pipeline/languages.py` | **New file** |
| `handwriting.py` | `pipeline/handwriting.py` | **New file** |
| `llm_providers.py` | `pipeline/llm_providers.py` | **New file** |
| `degradation.py` | `pipeline/degradation.py` | **Replace** (already done in previous session) |
| `database.py` | `pipeline/database.py` | **Replace** (already done — adds tier column) |

---

## 10. Command Reference — Every Command Used So Far

### 10.1 — One-Time Environment Setup

```bash
cd ~/b2b_synthetic_gen

# Confirm Python 3.10+
python3 --version

# Create venv inside project folder
python3 -m venv venv
source venv/bin/activate

# Install all dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Playwright headless Chromium
python3 -m playwright install chromium
sudo python3 -m playwright install-deps chromium
```

### 10.2 — WSL Memory Configuration

```bash
# Edit from within WSL (file lives on Windows side)
cat > /mnt/c/Users/<your_windows_username>/.wslconfig << 'EOF'
[wsl2]
memory=10GB
processors=4
swap=4GB
EOF
```

Then from **PowerShell** (not WSL):
```powershell
wsl --shutdown
```

Reopen WSL and confirm:
```bash
free -h
```

To revert:
```bash
rm /mnt/c/Users/<your_windows_username>/.wslconfig
```
```powershell
wsl --shutdown
```

### 10.3 — Ollama Setup

```bash
# Install Ollama (system-level, outside venv)
curl -fsSL https://ollama.com/install.sh | sh

# Check systemd availability
cat /proc/1/comm
# "systemd" → use systemctl:
sudo systemctl enable ollama
sudo systemctl start ollama
sudo systemctl status ollama

# "init" → start manually each session:
ollama serve

# Pull the model (in a separate terminal)
ollama pull qwen2.5:7b

# Verify server
curl http://localhost:11434/api/tags

# Verify GPU usage
nvidia-smi
ollama ps
```

### 10.4 — LibreTranslate Setup (WSL, inside venv)

```bash
source venv/bin/activate

# Option A: pip install (inside venv)
pip install libretranslate

# Start server — load the languages you plan to use
# Keep this running in a separate terminal or tmux session
libretranslate --load-only en,fr,hi,ur,bn,ta,te,kn,ml,gu,pa,de,es,zh &

# Verify it's up
curl http://localhost:5000/languages | python3 -m json.tool | head -20

# Option B: Docker (if Docker Desktop with WSL integration is enabled)
docker run -d --rm -p 5000:5000 libretranslate/libretranslate

# Stop the Docker container
docker ps                          # find container ID
docker stop <container_id>

# First-time language model download can take 2–5 minutes
# Subsequent starts are instant (models cached on disk)
```

**Language codes you can pass to --language:**

| Code | Language | Tier | Notes |
|------|----------|------|-------|
| `en` | English | A ✅ | Default, no LibreTranslate needed |
| `fr` | French | A ✅ | No LibreTranslate needed |
| `hi` | Hindi | B ⚠️ | No LibreTranslate needed, Noto Sans Devanagari font auto-loaded |
| `ur` | Urdu | B ⚠️ | No LibreTranslate needed, RTL layout, Noto Nastaliq Urdu font |
| `bn`, `ta`, `te`, `de`, `es`, etc. | Others | C 🤖 | LibreTranslate must be running |

### 10.5 — Build HSN/SAC Index (One-Time)

```bash
source venv/bin/activate

# Place HSN_SAC.xlsx in project root first, then:
python3 pipeline/build_hsn_index.py --xlsx HSN_SAC.xlsx

# Commit pipeline/hsn_data/ to your repo — never needs re-running
# unless you refresh the official xlsx from the GST portal
```

### 10.6 — Smoke Test (Always Run Before a Real Batch)

```bash
source venv/bin/activate
python3 test_pipeline.py
```

All checks should pass before generating at scale.

### 10.7 — Fresh Run (DEFAULT — Wipes Previous Output)

```bash
source venv/bin/activate

# Smallest possible test — single worker, no degradation, no Ollama
python3 main.py --industry "Automotive Fasteners" --count 10 --workers 1 --no-degradation

# Slightly larger — tiers enabled
python3 main.py --industry "Automotive Fasteners" --count 50 --workers 2

# Reuse existing catalog
python3 main.py --industry "Automotive Fasteners" --count 50 --workers 2 --skip-bootstrap

# Full production run with Ollama
python3 main.py --industry "Pharmaceutical Equipment" --workers 4

# Full run without Ollama (TF-IDF fallback catalog)
python3 main.py --industry "Commercial Furniture" --workers 4
```

### 10.8 — Append Run (Keeps Existing Output)

```bash
# Add more documents to an existing run without wiping what's already there
python3 main.py --industry "Automotive Fasteners" --count 5000 --workers 4 --append --skip-bootstrap
```

### 10.9 — Multi-Language Runs

```bash
# Built-in languages — no LibreTranslate needed
python3 main.py --industry "Automotive Fasteners" --language fr --count 100 --workers 2
python3 main.py --industry "Automotive Fasteners" --language hi --count 100 --workers 2
python3 main.py --industry "Automotive Fasteners" --language ur --count 100 --workers 2

# Tier C — LibreTranslate must be running on port 5000
python3 main.py --industry "Automotive Fasteners" --language de --count 50 --workers 2
python3 main.py --industry "Automotive Fasteners" --language ta --count 50 --workers 2

# Custom LibreTranslate URL
python3 main.py --industry "Fasteners" --language es \
    --libretranslate-url http://localhost:5000 --count 50 --workers 2
```

### 10.10 — Paid LLM Provider Runs

```bash
# OpenAI
python3 main.py --industry "Automotive Fasteners" \
    --llm-provider openai --llm-model gpt-4o-mini \
    --llm-api-key sk-... \
    --workers 4

# OpenAI-compatible (Together, Groq, Fireworks, etc.)
python3 main.py --industry "Automotive Fasteners" \
    --llm-provider openai \
    --llm-model meta-llama/Llama-3-70b-chat-hf \
    --llm-api-key <together_key> \
    --llm-base-url https://api.together.xyz/v1 \
    --workers 4

# Anthropic Claude
python3 main.py --industry "Automotive Fasteners" \
    --llm-provider anthropic --llm-model claude-sonnet-4-6 \
    --llm-api-key sk-ant-... \
    --workers 4
```

### 10.11 — Handwriting Mode

```bash
# Full-document handwriting font overlay (working)
python3 main.py --industry "Automotive Fasteners" \
    --handwriting font --count 50 --workers 2 --no-degradation

# Synthesis mode — raises NotImplementedError (documented stub)
python3 main.py --industry "Automotive Fasteners" --handwriting synthesis --count 1 --workers 1
```

### 10.12 — Combined Flags

```bash
# Hindi + handwriting + no degradation — good for visual inspection
python3 main.py --industry "Industrial Safety" \
    --language hi --handwriting font \
    --count 20 --workers 1 --no-degradation

# French + paid API + full degradation
python3 main.py --industry "Commercial HVAC" \
    --language fr \
    --llm-provider openai --llm-model gpt-4o-mini --llm-api-key sk-... \
    --count 200 --workers 4
```

### 10.13 — Cleaning Between Runs

```bash
# The pipeline wipes output automatically on a fresh run (default).
# But if you need to do it manually:

# Delete all output images and DB
rm -rf output/purchase_orders/ output/tax_invoices/
rm -f output/master_ground_truth.db output/master_ground_truth.db-wal output/master_ground_truth.db-shm

# Clear stale Python bytecode (do this after replacing any .py file)
find ~/b2b_synthetic_gen -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find ~/b2b_synthetic_gen -name "*.pyc" -delete

# Confirm no duplicate pipeline files exist (was a suspected cause of the original empty-DB issue)
find ~/b2b_synthetic_gen -name "database.py"
find ~/b2b_synthetic_gen -name "assembler.py"
find ~/b2b_synthetic_gen -name "degradation.py"
```

### 10.14 — Inspecting the Output Database

```bash
# Quick schema + row counts
python3 -c "
import sqlite3
conn = sqlite3.connect('output/master_ground_truth.db')
print(conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall())
print('documents:', conn.execute('SELECT COUNT(*) FROM documents').fetchone()[0])
print('pairs:',     conn.execute('SELECT COUNT(*) FROM document_pairs').fetchone()[0])
conn.close()
"

# Tier distribution
python3 -c "
import sqlite3
conn = sqlite3.connect('output/master_ground_truth.db')
print(conn.execute('SELECT tier, COUNT(*) FROM document_pairs GROUP BY tier').fetchall())
conn.close()
"

# Discrepant pair count
python3 -c "
import sqlite3, json
conn = sqlite3.connect('output/master_ground_truth.db')
rows = conn.execute(\"SELECT json_payload FROM documents WHERE doc_type='tax_invoice' LIMIT 500\").fetchall()
disc = sum(1 for (r,) in rows if json.loads(r).get('has_discrepancy'))
print(f'Discrepant invoices in sample: {disc}/500')
conn.close()
"

# High-value pairs
sqlite3 output/master_ground_truth.db \
    "SELECT po_number, invoice_number, grand_total FROM document_pairs
     WHERE grand_total > 100000 ORDER BY grand_total DESC LIMIT 10;"

# Full JSON for a specific document
sqlite3 output/master_ground_truth.db \
    "SELECT json_payload FROM documents WHERE filename LIKE '%po_000042%';"
```

### 10.15 — Expected Output Folder Structure

```
output/
├── purchase_orders/
│   ├── clean/          po_000000.png  po_000001.png  ...
│   ├── degraded/       po_000002.png  ...
│   ├── heavy/          po_000004.png  ...
│   └── discrepant/     po_000005.png  ...   (~20% of total)
├── tax_invoices/
│   ├── clean/          inv_000000.png ...
│   ├── degraded/       inv_000002.png ...
│   ├── heavy/          inv_000004.png ...
│   └── discrepant/     inv_000005.png ...
└── master_ground_truth.db
logs/
└── pipeline.log
industry_catalog.json
```

---

## 11. Proposed Final Outcome

A self-contained, reproducible dataset generator that, for any chosen industry, produces:

- 20,000 Purchase Orders + 20,000 matching Tax Invoices as high-resolution PNG images, organised into clean/degraded/heavy realism tiers in a 50/30/20 split
- ~20% of pairs with deliberate, realistic PO↔Invoice discrepancies (partial shipment, price change, item substitution), labelled in the SQLite ground truth and written to a separate `discrepant/` subfolder for easy reconciliation-dataset extraction
- One SQLite master ground-truth database with full structured JSON, every financial figure, PO↔Invoice linkage, discrepancy labels, and realism tier
- Official CBIC unit codes (KGS, PAC, ROL, etc.) and full GST rate set (0% through 40%)
- Optional multi-language output (4 hand-curated + auto-translate fallback for dozens more)
- Optional full-document handwriting rendering
- Choice of LLM backend for catalog bootstrap (Ollama / OpenAI-compatible / Anthropic)

---

## 12. Suggested Additions Not Yet Built

- **Multi-page documents** — real invoices with 15+ line items should spill onto a second page
- **Bounding-box / token-level ground truth** — Playwright can report each field's bounding box before degradation; currently discarded
- **Document tampering / fraud variants** — mismatched GST math, altered totals (distinct from the current PO↔Invoice discrepancy which is about content mismatch between the two documents in a pair, not arithmetic errors within one document)
- **Other Indian regulatory document types** — e-way bills, delivery challans, credit/debit notes
- **Resume support** — `--resume` flag to skip already-completed indices after a crash

---

## 13. On Using This Dataset

Strongest-fit use cases:

1. **Document-understanding / OCR model fine-tuning** — evaluate field-extraction accuracy across the three realism tiers; "accuracy degrades by X% from clean to heavy" is a concrete, presentable result
2. **PO↔Invoice reconciliation / discrepancy detection** — the `discrepant/` folder with labelled mismatches makes this a clean binary classification or entity-extraction task
3. **Synthetic-to-real domain transfer** — train on synthetic, evaluate on real invoices
4. **Dataset release / benchmark** — "controllable-difficulty benchmark for Indian B2B document OCR" with three realism tiers as the headline feature

---

## 14. Known Issues Found in Code Review

### Fixed ✅

| # | Issue | Fix applied |
|---|---|---|
| 1 | Screenshot clip truncated long documents silently | renderer.py: measure real content height, resize viewport before clip |
| 2 | PO and Invoice shared the same Python list/objects | data_models.py: deep copy + ~20% deliberate discrepancies |
| 3 | GSTIN state code unrelated to address state | data_models.py: STATE_NAME_TO_GST_CODE lookup table |
| — | Unit codes not in CBIC official master (KG, PKT, ROLL) | gst_rate_schedule.py: returns KGS, PAC, ROL |
| — | GST rates limited to {5,12,18,28} only | gst_rate_schedule.py: full official rate set |

### Open (real tech debt, non-blocking)

| # | Issue | File |
|---|---|---|
| 4 | Resume support half-built — no `--resume` flag, no uniqueness constraint | database.py, assembler.py |
| 5 | `loop = asyncio.get_event_loop()` dead code; async framing misleading | assembler.py |
| 6 | New browser context per render (should reuse one context per worker) | renderer.py |
| 7 | No spatial/bounding-box ground truth captured | layout_engine.py, renderer.py |
| 8 | `lookup_rate()` SAC branch: `is_service` flag was previously ignored | gst_rate_schedule.py ✅ fixed |
| 9 | `guess_unit()` ran on full ancestor-chain description, not leaf only | gst_rate_schedule.py ✅ fixed |
| 10 | Shard split was a patch, not correct for all count/workers combos | assembler.py ✅ fixed (numpy array_split) |
| 11 | `get_pair_count()` / `get_tier_counts()` swallow all exceptions silently | database.py |
| 12 | Handwritten signatory name has no ground-truth backing field | layout_engine.py (by design, documented) |