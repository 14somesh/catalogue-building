# Architecture Document: Print-Ready Catalogue Generation Pipeline

## 1. System Overview & Core Principles

The Catalogue Generation Pipeline is a modular, automated, and count-agnostic system designed for corporate gifting electronics distributors. It processes structured and unstructured product data from brochures, websites, and marketplace listings to produce print-ready, pixel-perfect A4 PDF catalogues.

The pipeline is completely **count-agnostic**—it operates dynamically on whatever rows and brands are present in the Excel sheet without any hardcoded product or brand limits. It is reusable across product categories (powerbanks, cables, audio, chargers) by updating configuration settings, design tokens, templates, and the master data sheet.

```
+-----------------------------------------------------------------------------------+
|                            SINGLE SOURCE OF TRUTH                                 |
|                         (data/catalogue_data.xlsx)                                |
+-----------------------+-----------------------+-------------------+---------------+
                        |                       |                   |
                        v                       v                   v
                +---------------+       +---------------+   +---------------+
                | 1_collect.py  |       |  2_images.py  |   | 3_review.py   |
                | (LLM Agent)   | ----> | (Script)      | ->| (Agent+Rules) |
                +---------------+       +---------------+   +---------------+
                        |                       |                   |
                        +-----------------------+-------------------+
                                                |
                                                v
                                    +-----------------------+
                                    |   HUMAN REVIEW GATE   |
                                    |  (Excel Manual Edits) |
                                    +-----------------------+
                                                |
                                                v
                                    +-----------------------+
                                    |     4_build.py        |
                                    | (Jinja2 + Chromium)   |
                                    +-----------------------+
                                                |
                                                v
                                    +-----------------------+
                                    | Print-Ready PDF Output|
                                    | (dist/catalogue.pdf)  |
                                    +-----------------------+
```

### Core Architecture Principles
1. **Excel as Single Source of Truth:** No JSON databases or external state files. All pipeline steps read from and write directly to `data/catalogue_data.xlsx`.
2. **Flat Schema (Zero JSON-in-cell):** Every data point lives in its own dedicated Excel column. No JSON strings or complex objects are stored within cells.
3. **Authoritative MRP Model:** The `MRP_Input` column supplied in the input sheet is the primary source of truth. Scraped prices are stored in `Raw_MRP_Scraped` solely for cross-verification. The collector never overwrites `MRP_Input`.
4. **Convention-First Image Resolution:** Images are located by convention at `images/{brand_slug}/{model_slug}.png`. If a file exists locally, `2_images.py` validates its resolution and skips downloading. Downloading is only attempted if no local asset exists. `Image_Local_Path` is eliminated from the schema.
5. **Non-Destructive Overrides:** Scripts only write to raw/scraped and status columns. Scripts **never** overwrite human-edited override columns.
6. **Required Single-Product Page Layout:** Every brand starts on a fresh page. When a brand has an odd product count, the final page contains one product. This single-product layout is required behavior (not an edge case), and `4_build.py` dynamically selects the 1-up layout for odd remaining cards.
7. **Deterministic PDF Build:** `4_build.py` uses Jinja2 HTML rendering and Playwright headless Chromium to produce pixel-identical PDF output with zero LLM variance.

---

## 2. Directory Structure

```
Vianet Catalogue/
├── ARCHITECTURE.md            # System architecture reference document (this file)
├── PHASES.md                  # Phased build plan and definitions of done
├── config.yaml                # Central system configuration (category, brand order, rules)
├── .env                       # Environment secrets (API keys)
├── .env.example               # Template for environment variables
├── requirements.txt           # Python package dependencies
├── data/
│   └── catalogue_data.xlsx    # Master Excel file (Single Source of Truth)
├── brochures/                 # Storage for local brand PDF brochures
│   └── {brand}/
│       └── {model_slug}.pdf
├── images/                    # Local image asset store (convention: {brand_slug}/{model_slug}.png)
│   └── {brand_slug}/
│       └── {model_slug}.png
├── src/
│   ├── utils/
│   │   ├── excel_handler.py   # File lock detector & safe Excel read/write engine
│   │   ├── llm_client.py      # LLM provider interface wrapper (Gemini API)
│   │   ├── scraper.py         # Web page scraper & brochure PDF extractor
│   │   └── logger.py          # Structured logging and audit utility
│   ├── 1_collect.py           # Pipeline Step 1: Agent Data Collector
│   ├── 2_images.py            # Pipeline Step 2: Convention-First Image Analyzer & Downloader
│   ├── 3_review.py            # Pipeline Step 3: Review & Validation Agent
│   └── 4_build.py             # Pipeline Step 4: Jinja2 & Headless Chromium PDF Generator
├── templates/
│   ├── base.html              # Base Jinja2 page structure (header, footer, page numbering)
│   ├── components/            # Reusable Jinja2 template partials
│   │   ├── brand_header.html  # Brand section divider header
│   │   ├── product_card.html  # Standard 2-up product card partial
│   │   ├── single_product_card.html # 1-up layout partial for odd product final slot
│   │   ├── cover_hook.html    # Structural hook for Cover Page
│   │   └── back_hook.html     # Structural hook for Back Cover / Contact Page
│   └── catalogue.html         # Main template assembling catalogue layout
├── styles/
│   ├── tokens.css             # Design Tokens (CSS Variables: palette, typography, spacing)
│   └── layout.css             # Print CSS layout rules (A4 page breaks, 2-up grid, page numbers)
└── dist/
    └── catalogue.pdf          # Final print-ready PDF deliverable
```

---

## 3. Master Data Schema (`catalogue_data.xlsx`)

The Excel sheet uses a single main worksheet named `CatalogueData`. Columns are strictly flat (no JSON in cells) and explicitly categorized into **Initial/Human Inputs**, **Script Raw Outputs**, **Human Overrides**, and **System Status & Flags**.

| Column Name | Data Type | Ownership | Description & Validation Constraints |
|---|---|---|---|
| `Product_ID` | String | Human (Init) | Unique SKU / Item Code (e.g. `PB-ANK-001`) |
| `Brand` | String | Human (Init) | Brand name (e.g. `Anker`, `Ambrane`, `Portronics`, `Stuffcool`) |
| `Model_Name` | String | Human (Init) | Exact marketing model name |
| `Product_URL` | String | Human (Init) | Optional manual override URL to force scraping a specific product page |
| `Brochure_PDF` | String | Human (Init) | Optional relative path to local brand brochure PDF |
| `Marketplace_URL` | String | Human (Init) | Optional manual override marketplace fallback URL |
| `MRP_Input` | Numeric | Human (Init) | Authoritative MRP in INR (Primary Source of Truth) |
| `Source_URL` | String | Script (`1_collect`) | Discovered web page URL used for data collection |
| `Raw_Title` | String | Script (`1_collect`) | Title parsed by agent |
| `Raw_Subtitle` | String | Script (`1_collect`) | Subtitle / key phrase parsed by agent |
| `Raw_MRP_Scraped` | Numeric | Script (`1_collect`) | Scraped MRP from website/brochure for cross-check |
| `Raw_Spec_Capacity` | String | Script (`1_collect`) | e.g. "10,000 mAh" |
| `Raw_Spec_Output` | String | Script (`1_collect`) | e.g. "22.5W Fast Charging" |
| `Raw_Spec_Ports` | String | Script (`1_collect`) | e.g. "2 x USB-A, 1 x Type-C" |
| `Raw_Spec_Weight` | String | Script (`1_collect`) | e.g. "225g" |
| `Raw_Spec_Warranty` | String | Script (`1_collect`) | e.g. "1 Year Manufacturer Warranty" |
| `Raw_Bullet_1` | String | Script (`1_collect`) | Marketing bullet 1 (max 60 chars) |
| `Raw_Bullet_2` | String | Script (`1_collect`) | Marketing bullet 2 (max 60 chars) |
| `Raw_Bullet_3` | String | Script (`1_collect`) | Marketing bullet 3 (max 60 chars) |
| `Raw_Bullet_4` | String | Script (`1_collect`) | Marketing bullet 4 (max 60 chars) |
| `Source_Audit` | String | Script (`1_collect`) | Readable source mapping e.g. `specs: brand-site | mrp: input-sheet` |
| `Image_URL` | String | Script (`1_collect`) | Discovered web image URL for download fallback |
| `Image_Status` | Enum | Script (`2_images`) | `ok` \| `low-res` \| `missing` |
| `Override_Title` | String | Human Reviewer | Manual override for Title |
| `Override_Subtitle` | String | Human Reviewer | Manual override for Subtitle |
| `Override_MRP` | Numeric | Human Reviewer | Manual override for MRP |
| `Override_Spec_Capacity` | String | Human Reviewer | Manual override for Capacity spec |
| `Override_Spec_Output` | String | Human Reviewer | Manual override for Output spec |
| `Override_Spec_Ports` | String | Human Reviewer | Manual override for Ports spec |
| `Override_Spec_Weight` | String | Human Reviewer | Manual override for Weight spec |
| `Override_Spec_Warranty` | String | Human Reviewer | Manual override for Warranty spec |
| `Override_Bullet_1` | String | Human Reviewer | Manual override for Bullet 1 |
| `Override_Bullet_2` | String | Human Reviewer | Manual override for Bullet 2 |
| `Override_Bullet_3` | String | Human Reviewer | Manual override for Bullet 3 |
| `Override_Bullet_4` | String | Human Reviewer | Manual override for Bullet 4 |
| `Override_Image_Path` | String | Human Reviewer | Escape hatch path for non-standard image files |
| `Flags` | String | Script (`3_review`) | Comma/Newline separated error & warning flags |
| `Status` | Enum | System / Human | `Pending` \| `Collected` \| `Flagged` \| `Ready_For_Review` \| `Approved` |

### Effective Value Selection Rules (`4_build.py`):
1. **MRP Selection:** `Override_MRP` > `MRP_Input` > `Raw_MRP_Scraped` (if `MRP_Input` is empty, `Raw_MRP_Scraped` is used but flagged as unverified).
2. **Text / Spec Fields:** `Override_{Field}` > `Raw_{Field}`.
3. **Image Path Resolution:**
   - If `Override_Image_Path` is populated $\rightarrow$ use `Override_Image_Path`.
   - Else $\rightarrow$ derive path via convention: `images/{brand_slug}/{model_slug}.png`.

---

## 4. Sequential Data Flow & Pipeline Architecture

### Step 1: `1_collect.py` (Agent Data Collector with Autonomous Search)
- **Objective:** Take brand + model name only, autonomously locate the product, extract raw specs, discover image URL, and write 4 sales bullet points ($\le 60$ chars each).
- **Source Priority Hierarchy:**
  1. `Brochure_PDF`: If a local brochure path is present in the row, parse PDF first.
  2. `Product_URL`: If a URL is manually provided in the sheet, use it as a forced manual override.
  3. `Automated Web Search`: If no URL is provided, search the official brand website first (e.g. `site:{brand_domain} {model}` or `{brand} {model} official`). If no official site match is found, fallback to marketplace search (Amazon.in / Flipkart listing).
- **Source URL Logging:** Writes the discovered and scraped web page URL directly to the `Source_URL` column.
- **MRP Safeguard:** The collector extracts scraped pricing into `Raw_MRP_Scraped`. It **never** writes to or overwrites `MRP_Input`.
- **Output:** Populates `Source_URL`, `Raw_*` columns, and writes plain readable text into `Source_Audit` (e.g. `specs: brand-site (stuffcool.com) | mrp: input-sheet`).
- **Safety:** Never overwrites any non-empty `Override_*` columns.

### Step 2: `2_images.py` (Script - Convention-First Image Analyzer)
- **Objective:** Resolve and validate product image assets.
- **Convention-First Logic:**
  1. Derives convention path: `images/{brand_slug}/{model_slug}.png`.
  2. Checks if file already exists at this path.
  3. **If file exists:** Validates resolution (min $800 \times 800\text{ px}$). If valid, sets `Image_Status = "ok"` and **skips downloading entirely**. This allows manual images to be dropped directly into the folder with zero sheet edits.
  4. **If file does not exist:** Downloads image from `Image_URL` to `images/{brand_slug}/{model_slug}.png`, then validates resolution.
  5. If `Override_Image_Path` is set, uses that path as an escape hatch.
  6. Updates `Image_Status` (`ok`, `low-res`, or `missing`). `Image_Local_Path` is not written to Excel since the path is derived.

### Step 3: `3_review.py` (Agent + Deterministic Validator Engine)
- **Objective:** Comprehensive quality audit without auto-correcting data.
- **Deterministic Rules:**
  - Mandatory fields present (Title, Subtitle, MRP, Specs, 4 Bullets, Image `ok`).
  - Bullet character limit: **$\le 60$ characters** per bullet (for two-column card layout).
  - MRP Cross-Check:
    - Compare `MRP_Input` vs `Raw_MRP_Scraped`. If they differ, raise a flag (`"MRP mismatch: Input X vs Scraped Y"`). Never overwrite `MRP_Input`.
    - If `MRP_Input` is empty, use `Raw_MRP_Scraped` and raise a warning flag (`"MRP_Input empty: using unverified scraped value"`).
- **LLM Semantic Audit (Agent):**
  - Subtitle vs spec consistency (e.g., subtitle claiming 20,000mAh vs spec 10,000mAh).
  - Tone and grammar across bullets.
- **Status Assignment:**
  - Sets `Status` to `Flagged` if issues are found.
  - Sets `Status` to `Ready_For_Review` if all validation checks pass cleanly.

### Step 4: `4_build.py` (Script - PDF Compiler)
- **Objective:** Deterministic, count-agnostic PDF rendering.
- **Execution Flow:**
  1. Checks Excel file lock. Reads dataset using `get_effective_value`.
  2. Reads `brand_order` from `config.yaml` to order brand sections.
  3. Groups products by brand.
  4. **Page Split & Odd Product Handling:**
     - A new brand always starts a new page (`break-before: page`).
     - Product sequence numbering restarts at `01` for each brand.
     - Products are rendered 2 per page stacked vertically.
     - **Odd Product Count Behavior:** If a brand has an odd number of products (e.g., 3 products), the last page contains 1 product. `4_build.py` detects this and renders the single-product card layout (`single_product_card.html`) for the final slot, ensuring valid, beautiful rendering without empty placeholder errors.
  5. Passes category label (`config.yaml`) and page numbering variables to Jinja2 context.
  6. Renders HTML with `styles/tokens.css` and `styles/layout.css`.
  7. Uses Playwright headless Chromium to compile PDF with running headers, footers, and page numbers.

---

## 5. Design System, Layout Architecture & Hooks

### Approved Design Aesthetics & Tokens (`styles/tokens.css`)
> [!NOTE]
> The exact hex values, fonts, and spacing below are **placeholders** and will be extracted directly from the approved design reference image during Phase 1 template implementation.

```css
:root {
  /* Approved Palette */
  --accent-color: #4F46E5;       /* Deep Indigo / Violet accent */
  --accent-hover: #4338CA;
  --heading-dark: #0F172A;       /* Near-black for price badges & titles */
  --bg-page: #FAFAFA;            /* Off-white page background */
  --bg-card-image: #F3F0FF;      /* Soft lavender-tinted image container */
  --text-body: #334155;
  --text-muted: #64748B;
  --border-subtle: #E2E8F0;

  /* Typography */
  --font-heading: 'Outfit', sans-serif;
  --font-body: 'Inter', sans-serif;

  /* Layout Spacing */
  --page-padding: 12mm;
  --card-gap: 6mm;
  --bullet-max-chars: 60;
}
```

### Page Layout & Running Footer
- **A4 Portrait Page:** $210\text{ mm} \times 297\text{ mm}$.
- **Vertical 2-Up Stack:** 2 product cards per page.
- **Page Numbers:** Rendered dynamically in footer (`Page X of Y` or running brand footer) using CSS Paged Media `@bottom-right` / `@bottom-center` rules in Playwright Chromium.

### Jinja2 Structural Hooks
- `templates/base.html`: Base frame with CSS links and page container.
- `templates/components/product_card.html`: Standard 2-up card template.
- `templates/components/single_product_card.html`: Required 1-up card variant for odd final product slots.
- `templates/components/cover_hook.html`: Structural hook for future Cover Page.
- `templates/components/back_hook.html`: Structural hook for future Back Cover / Contact Page.

---

## 6. System Configuration (`config.yaml`)

```yaml
category:
  name: "POWERBANK"
  display_title: "Powerbanks & Portable Chargers"

# Explicit brand rendering order in catalogue
brand_order:
  # The real brand list will be supplied when the product sheet is populated

page_setup:
  paper_size: "A4"
  orientation: "portrait"
  products_per_page: 2
  show_page_numbers: true

image_validation:
  min_width_px: 800
  min_height_px: 800
  recommended_dpi: 300
  allowed_formats: ["png", "jpg", "jpeg", "webp"]

validation_rules:
  title_max_chars: 40
  subtitle_max_chars: 80
  bullet_max_chars: 60       # Matched to two-column card layout
  required_bullets: 4

llm:
  model: "gemini-2.5-flash"
  temperature: 0.2

search:
  provider: "gemini_grounding"   # Options: "gemini_grounding", "duckduckgo", "serper"
  max_results: 3
  timeout_secs: 15
  official_domains:
    stuffcool: "stuffcool.com"
    anker: "anker.com"
    ambrane: "ambraneindia.com"
    portronics: "portronics.com"
    urbrn: "urbnworld.com"
    duracell: "duracell.in"

paths:
  excel_file: "data/catalogue_data.xlsx"
  images_dir: "images/"
  brochures_dir: "brochures/"
  output_pdf: "dist/catalogue.pdf"
```

---

## 7. Error Handling, Safety & Logging Approach

1. **Excel File Lock Check:** `utils/excel_handler.py` detects if `catalogue_data.xlsx` is open in Microsoft Excel and halts execution with a clear notification.
2. **MRP Safeguard:** Scraped prices never overwrite `MRP_Input`. Discrepancies raise flags in `3_review.py`.
3. **Non-Destructive Overrides:** Human `Override_*` columns are strictly preserved.
4. **Convention Image Fallback:** Manually placed images at `images/{brand_slug}/{model_slug}.png` bypass web downloading automatically.
5. **Logging:** Structured logs write to console and `logs/pipeline.log`.

---

## 8. Open Decisions & Confirmation Items

1. **Approved Design Reference Image:** Provide the design reference image for Phase 1 HTML/CSS extraction.
2. **Brand Sequence:** Confirm initial brand list in `brand_order` within `config.yaml`.
