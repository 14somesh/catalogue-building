# Phased Build Plan: Catalogue Generation Pipeline

This document outlines the step-by-step implementation order for building the catalogue generation pipeline. The phases are structured to establish an end-to-end testable PDF layout early as possible before implementing data collection and LLM agents. The pipeline is count-agnostic and processes whatever data is supplied in the master Excel sheet.

---

## Overview of Build Phases

```
+--------------------------------------------------------------------------------+
| Phase 1: Environment, Excel Schema & PDF Renderer Skeleton (End-to-End Testable)|
+--------------------------------------------------------------------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
| Phase 2: Convention-First Image Pipeline & Deterministic Validation Engine     |
+--------------------------------------------------------------------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
| Phase 3: Agent Data Collection & PDF Brochure Scraper                          |
+--------------------------------------------------------------------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
| Phase 4: LLM Audit Agent & Human Override Integration                          |
+--------------------------------------------------------------------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
| Phase 5: Full Production Run                                                   |
+--------------------------------------------------------------------------------+
```

---

## Phase 1: Environment, Excel Schema & PDF Renderer Skeleton

### Objective
Set up the environment, define the flat Excel schema, translate the approved catalogue design reference image into HTML/CSS layout templates, and build `src/4_build.py` to produce a testable, multi-page print PDF.

### Scope
- Create Python project environment (`requirements.txt`, `.env.example`, `config.yaml`).
- Add `brand_order` list and `bullet_max_chars: 60` setting to `config.yaml`.
- Create master sheet `data/catalogue_data.xlsx` populated with flat schema columns and mock rows across 2 brands (including 1 brand with an odd product count to test single-product page layout).
- Build `src/utils/excel_handler.py` with file lock detection.
- **Design Reference Translation Step:** Translate the approved catalogue design reference (supplied as an image reference) into HTML and CSS, treating the approved design as the visual specification.
  - Create CSS Design Tokens (`styles/tokens.css`) using indigo/violet accent, near-black headings/badges, off-white page background, and soft lavender image card container (placeholder values until extracted from design image).
  - Create Layout Rules (`styles/layout.css`) for A4 portrait 2-up vertical cards and dynamic page numbering footers.
- Build Jinja2 templates (`templates/base.html`, `templates/catalogue.html`, `templates/components/product_card.html`, `templates/components/single_product_card.html`, and structural hooks for cover/back pages).
- Implement `src/4_build.py` using Playwright headless Chromium:
  - Supports dynamic brand ordering via `brand_order`.
  - Detects odd product counts per brand and applies `single_product_card.html` layout for the final slot.
  - Renders dynamic page numbers in page footers.

### Deliverables
1. `config.yaml`, `.env.example`, `requirements.txt`.
2. `data/catalogue_data.xlsx` with flat schema columns and test rows.
3. `styles/tokens.css` and `styles/layout.css` implementing the approved design layout and page footers.
4. Jinja2 templates (`templates/base.html`, `templates/catalogue.html`, `templates/components/*`).
5. `src/4_build.py` renderer script.
6. Sample output: `dist/catalogue.pdf`.

### Definition of Done (DoD)
- [ ] Running `python src/4_build.py` reads `data/catalogue_data.xlsx` and generates `dist/catalogue.pdf`.
- [ ] Visual layout faithfully matches the approved catalogue design reference image.
- [ ] PDF is A4 portrait with 2 products per page stacked vertically, plus dynamic page numbers in footer.
- [ ] Forced page breaks occur on brand changes, respecting `brand_order` from `config.yaml`.
- [ ] Product sequence numbering restarts at `01` for each brand.
- [ ] Single-product page variant layout is correctly rendered for brands with an odd product count.
- [ ] Script halts with a user-friendly error if `catalogue_data.xlsx` is open in Excel.

---

## Phase 2: Convention-First Image Pipeline & Rule Validation Engine

### Objective
Implement convention-first image resolution/downloads and deterministic rule validation.

### Scope
- Implement `src/2_images.py`:
  - Derives file path by convention: `images/{brand_slug}/{model_slug}.png`.
  - Checks if file already exists locally. If present, validates resolution (min $800 \times 800\text{ px}$) and sets `Image_Status = "ok"`, **skipping download entirely**.
  - If missing locally, attempts download from `Image_URL` to `images/{brand_slug}/{model_slug}.png`.
  - Supports `Override_Image_Path` as an escape hatch for non-standard image locations.
  - Updates `Image_Status` (`ok`, `low-res`, `missing`).
- Implement deterministic validation rules in `src/3_review.py`:
  - Rule 1: All required fields present.
  - Rule 2: `MRP_Input` numeric and > 0. If `MRP_Input` is empty, uses `Raw_MRP_Scraped` and flags as unverified.
  - Rule 3: Bullet count equals exactly 4.
  - Rule 4: Bullet length $\le 60$ characters.
  - Rule 5: Image file exists on disk and `Image_Status == "ok"`.
  - Rule 6: MRP Cross-Check — flag if `Raw_MRP_Scraped` differs from `MRP_Input`.
  - Sets row `Status` to `Flagged` if issues exist, or `Ready_For_Review` if clean.

### Deliverables
1. `src/2_images.py` script.
2. Rule validator engine in `src/3_review.py`.
3. Updated test sheet containing local images, missing images, and deliberate price discrepancies.

### Definition of Done (DoD)
- [ ] Running `python src/2_images.py` skips downloading when local image exists at `images/{brand_slug}/{model_slug}.png`.
- [ ] Running `python src/3_review.py` flags MRP discrepancies (`MRP_Input` vs `Raw_MRP_Scraped`) without modifying `MRP_Input`.
- [ ] Bullets exceeding 60 characters are correctly flagged.
- [ ] Clean products are assigned `Status = "Ready_For_Review"`; flawed rows are set to `"Flagged"`.

---

## Phase 3: Agent Data Collection & PDF Brochure Scraper

### Objective
Build `src/1_collect.py` to autonomously search and discover product pages from brand + model name only, scrape specs/brochures, extract flat specs into dedicated columns, and generate 4 high-converting sales bullet points ($\le 60$ chars each) using Gemini LLM logic.

### Scope
- Build `src/utils/scraper.py`:
  - Autonomous product page search: Searches official brand domain first (e.g. `stuffcool.com`), then falls back to marketplace (Amazon.in / Flipkart).
  - Web scraper with User-Agent rotation, HTML clean extraction, and brochure PDF extractor (`pdfplumber`).
- Build `src/utils/llm_client.py`:
  - Gemini API client with retries, structured JSON schema parsing, and prompt engineering for specs and 4 bullet points ($\le 60$ chars each).
- Implement `src/1_collect.py`:
  - **Source Priority Hierarchy:** Local `Brochure_PDF` $\rightarrow$ Manual `Product_URL` override $\rightarrow$ Automated Search (Brand Website $\rightarrow$ Marketplace).
  - Populates `Source_URL` with the discovered/used web page URL.
  - Parses specs into flat columns (`Raw_Spec_Capacity`, `Raw_Spec_Output`, `Raw_Spec_Ports`, `Raw_Spec_Weight`, `Raw_Spec_Warranty`) and 4 sales bullets.
  - Extracts image URL to `Image_URL` for download fallback.
  - Writes scraped pricing to `Raw_MRP_Scraped` (never modifies `MRP_Input`).
  - Writes readable text source provenance to `Source_Audit` (e.g. `specs: brand-site (stuffcool.com) | mrp: input-sheet`).
  - Preserves all human override columns intact.

### Deliverables
1. `src/utils/scraper.py` and `src/utils/llm_client.py`.
2. `src/1_collect.py` script.
3. Test suite verifying automated search, scraping, and LLM extraction on a real product by name only.

### Definition of Done (DoD)
- [ ] Running `python src/1_collect.py` takes brand + model name, autonomously discovers the product URL, and populates `Source_URL`.
- [ ] Populates all `Raw_*` columns and `Source_Audit` in readable plain text format.
- [ ] Scraped prices are written to `Raw_MRP_Scraped` without altering `MRP_Input`.
- [ ] Bullet points strictly adhere to the 60-character limit.
- [ ] Human override columns (`Override_*`) remain untouched.

---

## Phase 4: LLM Semantic Audit Agent & Human Override Integration

### Objective
Complete `src/3_review.py` with LLM semantic auditing and integrate human override resolution rules.

### Scope
- Implement LLM Semantic Audit in `src/3_review.py`:
  - Checks if `Raw_Subtitle` or `Override_Subtitle` contradicts specs.
  - Checks bullet tone consistency and grammar.
- Implement effective value resolver logic in `src/3_review.py` and `src/4_build.py` (`Override_*` > `Raw_*`, `MRP_Input` authoritative).

### Deliverables
1. Completed `src/3_review.py` (Rule Engine + LLM Semantic Auditor).
2. Test dataset with deliberate semantic contradictions and human overrides.

### Definition of Done (DoD)
- [ ] Semantic contradictions (e.g., subtitle claiming 20,000mAh vs spec 10,000mAh) are caught and logged in `Flags`.
- [ ] Human overrides take precedence during validation and rendering.
- [ ] Clean rows reach `Status = "Ready_For_Review"`. The `Status = "Approved"` state is set manually by the human reviewer at the review gate, not by any script.

---

## Phase 5: Full Production Run

### Objective
Execute the full pipeline end-to-end on the master dataset, perform Human Review Gate, and compile the final print-ready PDF catalogue.

### Scope
- Populate `data/catalogue_data.xlsx` with complete product list across all configured brands.
- Execute sequential pipeline steps:
  1. `python src/1_collect.py`
  2. `python src/2_images.py`
  3. `python src/3_review.py`
- Human Review Gate: Reviewer opens Excel, checks `Flags`, resolves items, fills `Override_*` columns if needed, and marks `Status = "Approved"`.
- Execute `python src/4_build.py`.

### Deliverables
1. Fully populated `data/catalogue_data.xlsx` with approved records.
2. Complete local image asset store under `images/`.
3. Final print-ready PDF catalogue deliverable: `dist/catalogue.pdf`.

### Definition of Done (DoD)
- [x] Pipeline runs end-to-end without errors.
- [x] Output PDF `dist/catalogue.pdf` contains all products cleanly rendered under brand headers in `brand_order`.
- [x] Odd product count pages cleanly use single-product card layout without overflow or blank card errors.
- [x] Visuals match approved design reference image with exact typography, color palette, image resolution, and dynamic page numbers.

---

## Architecture Transition & Modernization Log (Date: August 31, 2026)

### Reason for Architectural Change
During the Stuffcool production run, every critical error was caught manually by human inspection of the raw outputs rather than by automated pipeline validation:
- The collector encountered three HTTP 404 pages and fabricated four confident marketing bullets and specifications for each.
- `3_review.py` then evaluated those fabricated rows and passed all three as clean—an LLM approved copy that another LLM had made up.
- The root cause was not a missing pipeline step. It was a **missing foundational constraint**: the agent was permitted to populate a data field for which it had no verified, live source URL.

### New Architecture Principles & Phase Updates
1. **Core Data-Layer Constraint:** A field that has no verified source URL cannot be written. Enforced programmatically in the data layer (Python runtime/Excel handler), not via soft prompt instructions. Human-supplied `Override_*` columns are strictly exempt.
2. **Pipeline Shape (Loop, Not Line):**
   - **Old Shape:** `collect → images → review → build` (linear, open-loop, requiring manual step-by-step intervention).
   - **New Shape:** `collect → images → validate → fix or block → (retry loop max 3 attempts per row) → report → human approves once → build`.
   - The loop is the agent. The LLM is a stateless drafting tool called by the loop.
3. **Clear Ownership Boundaries:**
   - **Script (Deterministic):** Orchestration, fetching, DOM parsing, validation, fix/block decisions, image gates, reporting, PDF compilation.
   - **LLM (Single Scoped Job):** Given a block of product text already fetched by a script from a verified URL, draft 4 sales bullets ($\le 60$ chars) and a subtitle. The LLM never selects URLs, never judges page authenticity, and never approves rows.
   - **Human Reviewer:** Reads the structured run report once per brand, resolves blocked rows via overrides, eyeballs image sets, and signs off.
4. **Mandatory Spec Source Tiers:** Tier 1 (Brand product page) $\rightarrow$ Tier 2 (Brand collection page) $\rightarrow$ Tier 3 (Retail: Croma, Reliance, Tata CLiQ) $\rightarrow$ Tier 4 (Tech press). 404 triggers escalation, not hallucination. Amazon is never used for specs.
5. **Separate Image Source Tiers:** Tier 1 (Brand product page) $\rightarrow$ Tier 2 (Retail) $\rightarrow$ Tier 3 (Amazon CDN via Playwright) $\rightarrow$ Tier 4 (Collection thumbnail). Enforces quality gates (min $800\text{ px}$, 1:1 square, unique hash, background tone consistency).
6. **Fix vs. Block Taxonomy:** Auto-fixes retry autonomously (sibling SKU mismatch, capacity mismatch, boilerplate text, low-res image, missing warranty). Hard blocks write nothing, set `Status = "Blocked"`, and wait for human review (all tiers exhausted, missing image, missing specs, unverified source URL, duplicate image hash, 3 attempts spent).
