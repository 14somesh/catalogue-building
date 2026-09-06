# Backend Architecture Audit for Frontend Integration (`FRONTEND_AUDIT.md`)
**Date:** September 2, 2026  
**Audited Codebase:** Vianet Product Catalogue Engine  
**Environment:** Python 3.14.5 (Windows)

---

## Executive Summary

This document provides a factual, ground-truth audit of the existing backend codebase. It details how the pipeline executes today across each of the 5 workflow stages, specifies the exact data schemas and transitions, assesses function vs. CLI invocation, and identifies necessary prerequisites before frontend implementation can commence.

---

## 1. Five-Stage Pipeline Mapping

### Stage 1: Ingest (Price List Given)

* **What Script / Function Runs It Right Now:**
  * Module: `src/onboard_brand.py`
  * Functions:
    1. `extract_text_from_file(file_path: str, llm_config: dict) -> Tuple[str, str]`: Detects file format (`.xlsx`, `.xls`, `.csv`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.txt`, `.md`). For images or scanned PDFs, renders page bitmaps via `pypdfium2` and runs OCR via `_ocr_images_via_gemini` (`gemini-3.6-flash`).
    2. `analyze_price_sheet(raw_content: str, category_name: str, llm_config: dict) -> BrandInferenceSchema`: Calls Gemini (`gemini-3.6-flash`) with strict Pydantic JSON schema (`BrandInferenceSchema`) to infer brand name, brand code, domain, platform, DP vs. MRP column mapping, qualifier tokens, and clean product rows.
    3. `generate_onboarding_summary(inference: BrandInferenceSchema) -> str`: Performs live HTTP probe on the inferred domain (`detect_ecommerce_platform`), detects bot challenge status (403/Cloudflare), identifies duplicate models, and formats the markdown summary table.
    4. `register_brand_config(inference: BrandInferenceSchema, config_path: str = "config/brand_defaults.yaml")`: Registers the domain, platform, and qualifier tokens into YAML.
    5. `append_products_to_catalogue(inference: BrandInferenceSchema, excel_path: str = "data/catalogue_data.xlsx") -> int`: Appends rows to Excel with status `Pending`.
* **How It Is Triggered Today:**
  * Interactive Chat Prompt / Terminal script: The user uploads a price list image/document in chat. The agent calls these functions in Python to perform OCR, inference, summary generation, YAML registration, and Excel row appending.
  * Note: `src/onboard_brand.py` does not currently possess an `if __name__ == "__main__":` CLI block; its functions are called directly via Python.
* **Inputs & Outputs:**
  * **Input:** Raw file (image/PDF/spreadsheet/text) + `config.yaml` (`category.name`).
  * **Output:**
    * In-memory Pydantic object `BrandInferenceSchema`.
    * Formatted markdown summary table.
    * Appended block in `config/brand_defaults.yaml`.
    * New rows appended to `data/catalogue_data.xlsx` with:
      * `Product_ID` (e.g. `PB-EVM-001`)
      * `Category` (e.g. `Powerbank`)
      * `Brand` (e.g. `EVM`)
      * `Model_Name` (e.g. `Encharge`)
      * `Display_Name` (e.g. `Encharge`)
      * `MRP_Input` (Dealer Price DP)
      * `MRP_Display` (Maximum Retail Price MRP)
      * `Status` = `Pending`
      * `Attempts` = 0
* **Automatic vs. Manual:**
  * **Automatic:** File format detection, OCR, text extraction, brand identification, DP vs. MRP disambiguation, model name cleaning, display name derivation, qualifier token selection, live bot-block probing, duplicate identification, Excel row creation.
  * **Manual Today:** User providing the file/screenshot into chat; user reviewing the inference summary table and confirming no corrections are needed before proceeding.

---

### Stage 2: Brochure Ask (Pipeline Reports Inferences, Asks for Brochure/Catalog)

* **What Script / Function Runs It Right Now:**
  * Script: No standalone Python script.
  * Workflow Reference: `WORKFLOW.md` Step 3.
  * Mechanism: Conversational gate executed by the AI pair programmer / operator in chat:
    > *"Do you have a brand brochure PDF for {Brand}?"*
* **How It Is Triggered Today:**
  * Chat prompt immediately following Stage 1 inference summary.
* **Inputs & Outputs:**
  * **Input:** User response ("Yes" + PDF file, or "No" / "no proceed").
  * **Output:**
    * If Yes: PDF file placed at `brochures/{brand_slug}/{filename}.pdf`, and column `Brochure_PDF` in `data/catalogue_data.xlsx` populated with that relative path.
    * If No: `Brochure_PDF` remains empty. Tier 0 is bypassed automatically during Stage 3.
* **Automatic vs. Manual:**
  * **Automatic:** Silent bypass of Tier 0 when `Brochure_PDF` is empty.
  * **Manual Today:** User typing "yes" or "no" in chat, and manually placing the PDF file into `brochures/{brand_slug}/` if available.

---

### Stage 3: Collection (Info/Images Gathered, Results Shown)

* **What Script / Function Runs It Right Now:**
  * Orchestrator Script: `src/run_brand.py`
  * Function: `run_brand(brand_name: str, config_path: str = "config.yaml", enable_semantic_audit: bool = False) -> str`
  * Sub-components invoked sequentially:
    1. Pre-flight checks: Excel file lock detection (`check_file_lock`), intra-brand duplicate SKU detection, multi-provider LLM quota probe (`preflight_llm_chain_check`).
    2. Data Collection: `src/1_collect.py` (`collect_data_for_row`):
       * Tier 0: Brochure parsing (`src/parsers/brochure.py`) — text/specs only (Zero Brochure Images policy).
       * Tier 1: Brand official product page (`src/parsers/shopify.py` / `src/parsers/generic.py`) + Gemini Vision infographic extraction.
       * Tier 2: Brand collection page crawl.
       * Tier 3: Marketplace / Retail search (Reliance Digital, Croma, Tata CLiQ, Amazon).
       * Tier 4: Auto-skip on data exhaustion.
       * Copy Drafting: LLM drafts Title, Subtitle ($\le 80$ chars), and 4 Bullets ($\le 58-60$ chars).
    3. Image Resolution & Visual AI Gate: `src/2_images.py` (`execute_image_tier_escalation`):
       * Fetches brand gallery candidates (Tier 1) or Amazon master listing (Tier 3).
       * Centers candidate on pure white (`#ffffff`) 1:1 square canvas ($\ge 1200\times 1200$px) via `normalize_and_pad_image_square`.
       * Evaluates candidate with **Visual AI Image Review Gate** (`audit_collected_image_quality` via `gemini-3.5-flash-lite`). Rejects lifestyle shots, hands, and promotional banners (scores $<8$ rejected; $\ge 8$ approved).
       * Saves approved packshot to `images/{category_slug}/{brand_slug}/{model_slug}.png`.
    4. Deterministic Review & Fix Loop: `src/3_review.py` (`execute_review_loop_for_row`):
       * Evaluates 12 hard checks and 4 warn checks via `src/utils/validators.py`.
       * Executes self-correction loop (up to 3 attempts: warranty fallback, bullet word-boundary truncation, boilerplate rewrite, tier escalation).
       * Resolves URL collisions (`resolve_brand_url_collisions`).
    5. Step 5.5 Automatic Post-Run LLM Review: `execute_automatic_llm_post_run_review`:
       * Cross-checks specs vs copy, capacity vs model name, copy veracity, and display name cleanliness.
       * Auto-triggers re-collection (max 2 review reruns) if contradictions are detected.
    6. Reporting & Presentation:
       * Generates markdown run report: `dist/{category_slug}/{brand_slug}/RUN_REPORT_{brand_slug}_{timestamp}.md`.
       * Prints formatted markdown table and dual build questions to console stdout via `format_final_presentation_table`.
* **How It Is Triggered Today:**
  * CLI command:
    ```powershell
    python src/run_brand.py --brand <Brand>
    ```
* **Inputs & Outputs:**
  * **Inputs:**
    * `--brand` argument string.
    * `data/catalogue_data.xlsx` (rows with `Status == "Pending"`).
    * `config.yaml` and `config/brand_defaults.yaml`.
    * Environment variables: `GEMINI_API_KEY` (required), `GROQ_API_KEY` (optional).
  * **Outputs:**
    * Updated `data/catalogue_data.xlsx`: Populated with scraped specs, drafted copy, image metrics, attempt counts, fix logs, flags, and updated `Status` (`Ready_For_Review`, `Blocked`, `Skipped`, `Deferred`).
    * Image files: `images/{category_slug}/{brand_slug}/{model_slug}.png` (1:1 square, verified $\ge 1200\times 1200$px on `#ffffff`).
    * Run report: `dist/{category_slug}/{brand_slug}/RUN_REPORT_{brand_slug}_{timestamp}.md`.
    * Console stdout: Full review table + sign-off prompt.
* **Automatic vs. Manual:**
  * **Automatic:** Entire collection chain, image resolution, image quality audit, 3-attempt fix loop, collision resolution, post-run LLM audit, and report generation execute unattended.
  * **Manual Today:** Triggering the CLI command.

---

### Stage 4: Approval (User Approves, Build Triggered)

* **What Script / Function Runs It Right Now:**
  * Script: No standalone Python script.
  * Workflow Reference: `WORKFLOW.md` Step 6.
  * Mechanism: Conversational sign-off in chat:
    1. User reviews the markdown presentation table and types "approved".
    2. User specifies destination: "standalone" (single-brand PDF) or "append" (master combined catalogue).
    3. The assistant updates `Status = "Approved"` in `data/catalogue_data.xlsx` for those rows via Python.
    4. If appending, the assistant updates `brand_order` in `config.yaml`.
* **How It Is Triggered Today:**
  * Human text input in chat interface.
* **Inputs & Outputs:**
  * **Input:** Human confirmation text ("approved", "append" / "standalone", order preference).
  * **Output:**
    * `data/catalogue_data.xlsx`: Row `Status` transitioned from `Ready_For_Review` to `Approved`.
    * `config.yaml`: `brand_order` list updated if appending.
* **Automatic vs. Manual:**
  * **Automatic:** None.
  * **Manual Today:** Human reviewing the table, writing approval in chat, and agent/user updating Excel and YAML.

---

### Stage 5: Output (Final Catalog Delivered)

* **What Script / Function Runs It Right Now:**
  * Script: `src/4_build.py`
  * Function: `build_catalogue(config_path: str = "config.yaml", brand: Optional[str] = None) -> str`
  * Execution Steps:
    1. Verifies Excel file lock (`check_file_lock`).
    2. Filters rows strictly to `Status == "Approved"`.
    3. Groups and orders products by `brand_order` from `config.yaml`.
    4. Validates Section A constraints (1:1 square image aspect ratio, text width overflow, typography integrity).
    5. Converts logo and product images to Base64 data URIs.
    6. Renders Jinja2 HTML template (`templates/catalogue.html`) with tokens (`styles/tokens.css`) and print layout (`styles/layout.css`).
    7. Saves standalone HTML preview (`catalogue_preview.html`).
    8. Launches Playwright headless Chromium, loads rendered HTML, verifies DOM card heights, and prints PDF with `@page { size: A4 portrait; margin: 0; }`.
* **How It Is Triggered Today:**
  * CLI command:
    * Standalone brand PDF:
      ```powershell
      python src/4_build.py --brand <Brand>
      ```
    * Master combined PDF:
      ```powershell
      python src/4_build.py
      ```
* **Inputs & Outputs:**
  * **Inputs:**
    * `data/catalogue_data.xlsx` (`Status == "Approved"` rows).
    * `config.yaml` (`category`, `brand_order`, `company`, `paths`).
    * Image assets in `images/{category_slug}/{brand_slug}/*.png`.
    * Assets in `assets/fonts/` (self-hosted WOFF2 fonts) and `assets/vianet-logo.png`.
  * **Outputs:**
    * Final compiled PDF deliverable:
      * Standalone: `dist/{category_slug}/{brand_slug}/{category}_catalogue_{timestamp}.pdf`
      * Combined: `dist/{category_slug}/combined/{category}_catalogue_{timestamp}.pdf`
    * HTML preview: `dist/{category_slug}/{brand_slug}/catalogue_preview.html` or `dist/{category_slug}/combined/catalogue_preview.html`.
* **Automatic vs. Manual:**
  * **Automatic:** Jinja2 rendering, Base64 embedding, Playwright DOM validation, PDF generation.
  * **Manual Today:** Launching the CLI build command.

---

## 2. Excel Data Layer Schema & State Machine

Master file location: `data/catalogue_data.xlsx` (Sheet name: `CatalogueData`).

### Complete Column Inventory (65 Columns)

| Column Index | Column Name | Type | Purpose / Source | Who Writes It |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `Product_ID` | String | Unique SKU identifier (e.g. `PB-EVM-001`) | Stage 1 (`onboard_brand.py`) |
| 2 | `Category` | String | Category namespace (e.g. `Powerbank`) | Stage 1 (`onboard_brand.py`) |
| 3 | `Brand` | String | Brand name (e.g. `EVM`, `Stuffcool`) | Stage 1 (`onboard_brand.py`) |
| 4 | `Model_Name` | String | Cleaned model name with capacity | Stage 1 (`onboard_brand.py`) |
| 5 | `Display_Name` | String | Shortest clean name for product card | Stage 1 / Stage 3 LLM review |
| 6 | `Product_URL` | String | Official product web URL | Stage 1 / Stage 3 collection |
| 7 | `Brochure_PDF` | String | Relative path to brochure PDF | Stage 2 (Manual placement) |
| 8 | `Marketplace_URL` | String | Amazon / Retail fallback URL | Stage 3 collection |
| 9 | `MRP_Input` | Numeric | Dealer Price (DP) from price sheet | Stage 1 (`onboard_brand.py`) |
| 10 | `MRP_Display` | Numeric | MRP from sheet or human override | Stage 1 / Stage 4 |
| 11 | `Source_URL` | String | Verified web URL where specs were found | Stage 3 (`1_collect.py`) |
| 12 | `Source_Audit` | String | Provenance audit log string | Stage 3 (`1_collect.py`) |
| 13–15 | `Raw_Title`, `Source_Title`, `Tier_Title` | String, String, Int | Scraped/drafted title, provenance, tier | Stage 3 (`1_collect.py`) |
| 16–18 | `Raw_Subtitle`, `Source_Subtitle`, `Tier_Subtitle` | String, String, Int | Scraped/drafted subtitle, provenance, tier | Stage 3 (`1_collect.py`) |
| 19–21 | `Raw_MRP_Scraped`, `Source_MRP_Scraped`, `Tier_MRP_Scraped` | Numeric, String, Int | Scraped retail price, provenance, tier | Stage 3 (`1_collect.py`) |
| 22–24 | `Raw_Spec_Capacity`, `Source_Spec_Capacity`, `Tier_Spec_Capacity` | String, String, Int | Battery capacity (e.g. `10000 mAh`) | Stage 3 (`1_collect.py`) |
| 25–27 | `Raw_Spec_Output`, `Source_Spec_Output`, `Tier_Spec_Output` | String, String, Int | Output wattage (e.g. `22.5W Fast Charging`) | Stage 3 (`1_collect.py`) |
| 28–30 | `Raw_Spec_Ports`, `Source_Spec_Ports`, `Tier_Spec_Ports` | String, String, Int | Port configuration (e.g. `Type-C, USB-A`) | Stage 3 (`1_collect.py`) |
| 31–33 | `Raw_Spec_Weight`, `Source_Spec_Weight`, `Tier_Spec_Weight` | String, String, Int | Weight (e.g. `225g`) | Stage 3 (`1_collect.py`) |
| 34–36 | `Raw_Spec_Warranty`, `Source_Spec_Warranty`, `Tier_Spec_Warranty` | String, String, Int | Warranty term (e.g. `2 Years`) | Stage 3 (`1_collect.py`) |
| 37–39 | `Raw_Bullet_1`, `Source_Bullet_1`, `Tier_Bullet_1` | String, String, Int | Feature bullet 1 ($\le 58-60$ chars) | Stage 3 (`1_collect.py`) |
| 40–42 | `Raw_Bullet_2`, `Source_Bullet_2`, `Tier_Bullet_2` | String, String, Int | Feature bullet 2 ($\le 58-60$ chars) | Stage 3 (`1_collect.py`) |
| 43–45 | `Raw_Bullet_3`, `Source_Bullet_3`, `Tier_Bullet_3` | String, String, Int | Feature bullet 3 ($\le 58-60$ chars) | Stage 3 (`1_collect.py`) |
| 46–48 | `Raw_Bullet_4`, `Source_Bullet_4`, `Tier_Bullet_4` | String, String, Int | Feature bullet 4 ($\le 58-60$ chars) | Stage 3 (`1_collect.py`) |
| 49 | `Image_URL` | String | Source URL of candidate image | Stage 3 (`2_images.py`) |
| 50 | `Image_Status` | String | `ok` \| `missing` \| `low-res` | Stage 3 (`2_images.py`) |
| 51 | `Image_Source` | String | `brand-product-page` \| `amazon-search` | Stage 3 (`2_images.py`) |
| 52 | `Image_Tier` | Int | Image tier (1 or 3) | Stage 3 (`2_images.py`) |
| 53–62 | `Override_Title`, `Override_Subtitle`, `Override_MRP`, `Override_Spec_Capacity`, `Override_Spec_Output`, `Override_Spec_Ports`, `Override_Spec_Weight`, `Override_Spec_Warranty`, `Override_Bullet_1`–`4`, `Override_Image_Path` | Various | Human override columns (take strict precedence over `Raw_` values) | Stage 4 (Human/Operator) |
| 63 | `Attempts` | Numeric | Collection / fix attempts count (0–3) | Stage 3 (`3_review.py`) |
| 64 | `Fix_Log` | String | Multi-line diagnostic log of auto-fixes | Stage 3 (`3_review.py`) |
| 65 | `Flags` | String | Validation flags or warnings | Stage 3 (`3_review.py`) |
| 66 | `LLM_Provider` | String | Provider used (e.g. `gemini`, `groq`) | Stage 3 (`llm_client.py`) |
| 67 | `Status` | String | Row lifecycle state (see below) | Stage 1, 3, 4 |

### Lifecycle Status Values & State Transitions

1. **`Pending`**: Newly ingested row, awaiting collection. Also the state a row is reset to if a human corrects a URL or forces re-collection.
   * *Valid Transitions To:* `Ready_For_Review`, `Blocked`, `Skipped`, `Deferred`.
2. **`Ready_For_Review`**: Collection complete, deterministic validation passed (zero hard flags, partial spec policy satisfied $\ge 3/5$), studio packshot approved by AI review gate ($\ge 8/10$), and Step 5.5 LLM review satisfied.
   * *Valid Transitions To:* `Approved` (via human approval), `Pending` (if LLM post-run review forces re-collection), `Blocked` (if persistent contradiction after 2 review reruns).
3. **`Approved`**: Human has explicitly signed off on the row. **Only rows in `Approved` status are read and compiled into the PDF by `src/4_build.py`**.
   * *Valid Transitions To:* Terminal state for build; may be manually reverted to `Pending` if data changes.
4. **`Blocked`**: Unfixable contradiction detected (e.g. qualifier mismatch, duplicate image hash to another active SKU, filename mismatch, or URL collision loser). Raw fields are cleared to protect the data layer. Halts auto-retry for that row and requires human attention.
   * *Valid Transitions To:* `Pending` (after human fixes `Product_URL` or supplies override).
5. **`Skipped`**: Technical data exhausted (e.g. all collection tiers failed, fewer than 3 specs found). Raw fields are cleared. Does *not* halt the pipeline run; omitted from PDF compilation.
   * *Valid Transitions To:* `Pending` (if user provides a valid `Product_URL` or overrides).
6. **`Deferred`**: Temporary infrastructure block (e.g. API quota exhaustion, transient 429). Preserves raw fields and attempt counts so processing can resume when quotas reset.
   * *Valid Transitions To:* `Pending` (when re-run after quota window).

---

## 3. Project Directory Structure

```
Vianet Catalogue/
├── assets/
│   ├── fonts/                         # Self-hosted WOFF2 fonts (Outfit, Inter)
│   └── vianet-logo.png                # High-resolution corporate logo mark
├── brochures/                         # Tier 0 Manual brand brochure PDFs
│   ├── pebble/
│   ├── portronics/
│   ├── stuffcool/
│   └── urbn/
├── config/
│   └── brand_defaults.yaml            # Per-brand domain, platform, qualifier tokens, warranty
├── data/
│   └── catalogue_data.xlsx            # Master Excel database (Sheet: 'CatalogueData')
├── dist/                              # Output deliverables (Category-scoped)
│   └── powerbank/
│       ├── combined/
│       │   ├── catalogue_preview.html # Browser preview of combined catalog
│       │   └── powerbank_catalogue_2026-09-02_1219.pdf # Master combined PDF (27 pages)
│       ├── evm/
│       │   ├── catalogue_preview.html
│       │   ├── RUN_REPORT_evm_*.md    # Markdown diagnostic run report
│       │   └── powerbank_catalogue_2026-09-02_1218.pdf # Standalone brand PDF (5 pages)
│       ├── pebble/
│       ├── portronics/
│       ├── stuffcool/
│       └── urbn/
├── images/                            # Master product imagery (Category-scoped)
│   ├── cover.png                      # Global corporate cover background
│   ├── vianet-logo.png                # Brand logo asset
│   └── powerbank/                     # Category image root
│       ├── evm/                       # 1:1 square studio packshots on #ffffff
│       ├── pebble/
│       ├── portronics/
│       ├── stuffcool/
│       └── urbn/
├── src/                               # Backend Python codebase
│   ├── parsers/                       # Tier-specific extraction modules
│   │   ├── amazon.py                  # Amazon image CDN extractor
│   │   ├── base.py                    # BaseParser & ParserResult dataclass
│   │   ├── brochure.py                # Tier 0 PDF text/spec extractor
│   │   ├── croma.py                   # Tier 3 Croma search parser
│   │   ├── generic.py                 # Platform detector, JSON-LD & sitemap parser
│   │   ├── reliance.py                # Tier 3 Reliance Digital parser
│   │   ├── shopify.py                 # Tier 1/2 Shopify JSON & Magento/HTML parser
│   │   └── tatacliq.py                # Tier 3 Tata CLiQ parser
│   ├── utils/                         # Core utility modules
│   │   ├── excel_handler.py           # Safe openpyxl/pandas I/O & field precedence
│   │   ├── llm_client.py              # Multi-provider LLM chain & Visual AI review gate
│   │   ├── logger.py                  # Standardized color/file logger
│   │   ├── scraper.py                 # HTTP/Playwright fetcher & qualifier matcher
│   │   └── validators.py              # Deterministic validation rules (Hard/Warn checks)
│   ├── 1_collect.py                   # Step 1: Data collection & copy drafting
│   ├── 2_images.py                    # Step 2: Image resolution, 1:1 auto-pad, AI audit
│   ├── 3_review.py                    # Step 3: Review loop, auto-fix, collision resolution
│   ├── 4_build.py                     # Step 4: Jinja2 template render & Playwright PDF compiler
│   ├── onboard_brand.py               # Step 1/2 Ingestion & autonomous brand inference
│   └── run_brand.py                   # Step 5/5.5 Master orchestrator & post-run review
├── styles/
│   ├── layout.css                     # A4 portrait print layout rules
│   └── tokens.css                     # Palette, geometry, and typography tokens
├── templates/
│   ├── base.html                      # HTML5 frame with self-hosted font loader
│   ├── catalogue.html                 # Master Jinja2 catalogue template
│   └── components/                    # Partials (product_card, single_product_card, etc.)
├── tests/                             # Regression test suites
├── config.yaml                        # Global catalogue configuration
├── ARCHITECTURE.md                    # System architecture documentation
├── WORKFLOW.md                        # Brand onboarding workflow specification
└── FRONTEND_AUDIT.md                  # This audit document
```

---

## 4. Invocation Assessment: Function vs. CLI

| Pipeline Stage | Current Invocation Method | Can It Run as a Plain Function with Clear I/O? | Details / Modifications Needed |
| :--- | :--- | :--- | :--- |
| **Stage 1 (Ingest)** | Function calls via agent | **Yes (Directly callable)** | `extract_text_from_file(path)` + `analyze_price_sheet(content)` + `generate_onboarding_summary(inference)` + `append_products_to_catalogue(inference)` exist as Python functions. No CLI wrapper currently exists. |
| **Stage 2 (Brochure)** | Conversational chat prompt | **N/A (State check)** | Purely an interactive gate. Functionally, it is just saving an uploaded file to `brochures/{brand_slug}/` or setting `Brochure_PDF = None`. Easily wrapped as an endpoint. |
| **Stage 3 (Collection)** | CLI: `python src/run_brand.py --brand <Brand>` | **Yes (Callable function)** | `run_brand(brand_name: str, config_path: str = "config.yaml") -> str` in `src/run_brand.py` is a top-level callable function that returns the generated report path. **Note:** It is synchronous and long-running (2–5 minutes). |
| **Stage 4 (Approval)** | Conversational chat prompt | **N/A (State transition)** | Functionally an Excel update (`Status = "Approved"` for selected PIDs) and a YAML append (`brand_order`). Readily implemented as a backend endpoint. |
| **Stage 5 (Output)** | CLI: `python src/4_build.py [--brand <Brand>]` | **Yes (Callable function)** | `build_catalogue(config_path: str = "config.yaml", brand: Optional[str] = None) -> str` in `src/4_build.py` is a top-level callable function that returns the generated PDF path. |

---

## 5. Technical Environment & Web API Feasibility

### Current Environment
* **Python Runtime:** `3.14.5` (64-bit on Windows)
* **Core Libraries Installed:**
  * `google-genai` (2.19.0) — Gemini API SDK
  * `groq` (1.7.0) — Groq LLM fallback SDK
  * `playwright` (1.62.0) — Headless Chromium for dynamic scraping and PDF export
  * `pydantic` (2.13.4) & `pydantic_core` (2.46.4) — Data modeling and schema validation
  * `pandas` (3.0.5) & `openpyxl` (3.1.5) — Excel database reading and writing
  * `pillow` (12.3.0) — Image processing, auto-padding, dimensions validation
  * `jinja2` (3.1.6) — HTML catalogue template rendering
  * `beautifulsoup4` (4.15.0) & `requests` (2.34.2) — HTML parsing and scraping
  * `pypdfium2` (5.13.0) & `pdfplumber` (0.11.10) — PDF text and bitmap extraction
  * `pyyaml` (6.0.3) — Configuration management

### Web API Feasibility (FastAPI / Flask)
**Yes, the backend can sit behind a FastAPI or Flask web service without rewriting core logic.**
The core extraction, validation, and build engines are cleanly decoupled from the UI/chat layer. However, three operational realities must be accommodated by any API wrapper:

1. **Execution Duration & Asynchronous Workers:**
   * Stage 3 (`run_brand`) and Stage 5 (`build_catalogue`) are compute- and I/O-intensive. Stage 3 performs multiple external HTTP calls, browser automations, Gemini Vision audits, and LLM drafting, taking between 60 and 300 seconds per brand.
   * *Requirement:* These cannot be handled as synchronous HTTP request/response cycles. They must run in background worker tasks (e.g. FastAPI `BackgroundTasks`, Celery, or `asyncio.to_thread`), exposing job IDs with a status/polling endpoint (`GET /api/jobs/{job_id}`) or WebSocket/SSE log stream.
2. **Excel Concurrency & File Locking:**
   * The backend relies on `src/utils/excel_handler.py` which enforces `check_file_lock()` on `data/catalogue_data.xlsx`.
   * *Requirement:* Two simultaneous brand runs cannot write to the same Excel file at the same instant. Background tasks must be queued or serialized.
3. **Static File Serving:**
   * The frontend must display candidate packshots, render HTML previews, and serve compiled PDFs.
   * *Requirement:* The API service must mount `images/` and `dist/` as static directories (e.g. `app.mount("/dist", StaticFiles(directory="dist"), name="dist")`).

---

## 6. Items Requiring User Decision Before Frontend Work Starts

The following items cannot be determined from the codebase alone and require your decision before frontend development begins:

1. **Frontend Technology & Framework:**
   * What framework should be used for the frontend interface (e.g. React / Vite, Next.js, Vue / Nuxt, or Vanilla HTML/JS with Tailwind/CSS)?
2. **API Framework Choice:**
   * What Python API framework is preferred to wrap the backend: **FastAPI** (natively aligns with existing `pydantic` schemas and provides auto-generated OpenAPI docs) or **Flask**?
3. **Deployment / Hosting Target:**
   * Is this frontend intended to run exclusively as a **local single-user desktop web tool** on your machine (e.g. `localhost:8000`), an **Electron app**, or a **multi-user cloud/server application**?
4. **Data Persistence Strategy:**
   * Should `data/catalogue_data.xlsx` remain the permanent database of record, or should a relational database (e.g. SQLite / PostgreSQL) hold the operational state and sync/export to Excel on demand?
5. **Level of Real-Time Granularity in Stage 3:**
   * During Stage 3 collection, do you want the frontend to stream real-time terminal-style execution logs (via WebSockets or SSE), or is a high-level step-by-step progress bar (e.g., *Ingesting $\rightarrow$ Scraping Specs $\rightarrow$ Auditing Images $\rightarrow$ Validating*) sufficient?
6. **Interactivity During Stage 4 (Approval):**
   * Do you want an interactive editable data grid in Stage 4 where you can manually modify individual fields (title, bullets, specs, or upload alternative image assets) before clicking "Approve", or strictly a review-and-accept summary table?
