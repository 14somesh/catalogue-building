# Architecture Document: Print-Ready Catalogue Generation Pipeline

## 1. Why This Is Changing

In the Stuffcool production run, every critical error was caught by the human reviewer reading the raw output, not by the pipeline's automated checks:
- The collector encountered three HTTP 404 pages and fabricated four confident marketing bullets and specifications for each.
- `3_review.py` then evaluated those fabricated rows and passed all three as clean—an LLM approved copy that another LLM had invented from thin air.
- The root cause was not a missing pipeline step. It was a **missing foundational constraint**: the agent was permitted to populate a data field for which it had no verified, live source URL.

This architecture redesign replaces the linear, open-loop process with a **constrained, self-healing loop** where data-layer invariants strictly prohibit hallucinations, source escalation is mandatory, and clear boundaries govern the scripts, the LLM, and the human reviewer.

---

## 2. Core Architecture Principles

```
+-----------------------------------------------------------------------------------------------+
|                                    SINGLE SOURCE OF TRUTH                                     |
|                                  (data/catalogue_data.xlsx)                                   |
+-----------------------------------------------+-----------------------------------------------+
                                                |
                                                v
             +=====================================================================+
             |                     AUTONOMOUS SELF-HEALING LOOP                    |
             |                                                                     |
             |   +------------------+         +------------------+                 |
             |   |   1_collect.py   | ------> |   2_images.py    |                 |
             |   | (Tiered Scraper) |         | (Image Fallback) |                 |
             |   +------------------+         +------------------+                 |
             |            ^                             |                          |
             |            | (Retry <= 3 attempts)       v                          |
             |   +------------------+         +------------------+                 |
             |   |   Fix / Retry    | <------ |   3_validate.py  |                 |
             |   |    (Auto-Fix)    |         | (Hard Invariant) |                 |
             |   +------------------+         +------------------+                 |
             |            |                             |                          |
             |            | (If Tier Exhausted / Failed)| (If Clean Pass)          |
             |            v                             v                          |
             |      [BLOCKED ROW]                 [READY ROW]                      |
             +=====================================================================+
                                                |
                                                v
                                    +-----------------------+
                                    |   HUMAN REVIEW GATE   |
                                    | (1 Review per Brand)  |
                                    +-----------------------+
                                                | (Approved)
                                                v
                                    +-----------------------+
                                    |      4_build.py       |
                                    |  (Jinja2 + Chromium)  |
                                    +-----------------------+
                                                |
                                                v
                                    +-----------------------+
                                    | Print-Ready PDF Output|
                                    | (dist/catalogue.pdf)  |
                                    +-----------------------+
```

### Principle 1: The Core Constraint (Data-Layer Invariant)
**A field that has no verified source URL cannot be written.**
- Enforced strictly in the data layer (Python runtime validator and Excel handler), not via soft LLM system prompt instructions.
- If a source fetch returns a non-200 status, a soft-404, or lacks verified technical specs, the script writes **NOTHING** to `Raw_*` columns and immediately moves to the next source tier.
- **Human Exemption:** Human-supplied `Override_*` columns are strictly exempt from this check—the human reviewer is the authoritative source of truth.

### Principle 2: Shape — Loop, Not Line
- **Old Architecture:** `collect → images → review → build` in a straight line with no feedback mechanism, requiring human intervention and debugging at every stage.
- **New Architecture:** `collect → images → validate → fix or block → (retry loop up to 3 attempts per row) → run report → human review gate → build`.
- **The loop is the agent.** The LLM is merely a stateless tool called by the loop to draft copy from verified text blocks.

### Principle 3: Clear Division of Ownership
1. **Script (Deterministic Engine):** Orchestrates pipeline execution, fetches HTML/PDFs, parses DOM/JSON, executes deterministic validation, enforces fix/block rules, manages image quality gates, formats run reports, and compiles the final PDF.
2. **LLM (Single Scoped Job):** Given a clean block of product text already fetched by a script from a verified URL, drafts 4 concise bullet points ($\le 60$ chars each) and a subtitle. The LLM **never** discovers URLs, never judges whether a web page is authentic, and never signs off on row approval.
3. **Human (Once per Brand):** Reviews the comprehensive run report, resolves blocked rows via `Override_*` columns, eyeballs the final image grid, and marks `Status = "Approved"`.

---

## 3. Tiered Source Escalation Models

### A. Spec & Bullet Source Tiers
A 404 or missing product page on the brand's primary site does not mean the product is unknowable; it simply means *that specific page* is delisted. The collector must autonomously escalate through the defined tiers before giving up:

```
+-----------------------------------------------------------------------------------+
| Tier 1: Official Brand Product Page (e.g. stuffcool.com/products/aura-10000mah)   |
+-----------------------------------------------------------------------------------+
                                         | (If 404 / Blocked / Missing Specs)
                                         v
+-----------------------------------------------------------------------------------+
| Tier 2: Official Brand Collection Page (e.g. stuffcool.com/collections/powerbanks)|
+-----------------------------------------------------------------------------------+
                                         | (If Delisted / Missing Specs)
                                         v
+-----------------------------------------------------------------------------------+
| Tier 3: Major Retail Listings (Croma, Reliance Digital, Tata CLiQ, Vijay Sales)   |
+-----------------------------------------------------------------------------------+
                                         | (If Retail Missing)
                                         v
+-----------------------------------------------------------------------------------+
| Tier 4: Authoritative Tech Press / Product Launch Coverage / Specification Portals|
+-----------------------------------------------------------------------------------+
                                         | (If All Tiers Exhausted)
                                         v
                                   [HARD BLOCK]
```

> [!IMPORTANT]
> **Amazon Policy for Specs:** Amazon.in is **never** used as a source for specifications or bullet copy because of aggressive anti-scraping bot barriers and inconsistent product descriptions.

---

### B. Image Source Tiers (Independent Chain)
Images are binary assets—they do not require cross-corroboration, but they must satisfy strict visual quality gates before acceptance:

```
+-----------------------------------------------------------------------------------+
| Tier 1: Official Brand Product Page (High-Res Master Asset, 1200×1200px+)         |
+-----------------------------------------------------------------------------------+
                                         | (If Missing / Low-Res)
                                         v
+-----------------------------------------------------------------------------------+
| Tier 2: Major Retail Product Listings (Croma, Reliance Digital, Tata CLiQ)        |
+-----------------------------------------------------------------------------------+
                                         | (If Retail Missing)
                                         v
+-----------------------------------------------------------------------------------+
| Tier 3: Amazon India Product Listings (High-Res Media CDN via Playwright)         |
+-----------------------------------------------------------------------------------+
                                         | (If Direct Listing Missing)
                                         v
+-----------------------------------------------------------------------------------+
| Tier 4: Official Brand Collection Page Thumbnail / Catalog Master Asset           |
+-----------------------------------------------------------------------------------+
```

#### Image Quality Gates:
1. **Resolution Gate:** Minimum $800 \times 800\text{ px}$ (Target $1200 \times 1200\text{ px}+$ master resolution).
2. **Aspect Ratio Gate:** Strictly 1:1 square ($\le 1\%$ variance).
3. **Unique Hash Gate:** Perceptual/MD5 image hash check across the brand to prevent duplicate images assigned to different sibling SKUs.
4. **Background Consistency Gate:** Corner pixel sampling across brand images to identify background tone divergence.

---

## 4. Fix vs. Block Taxonomy

When validation detects an anomaly, the agent categorizes it strictly into either an **Auto-Fix** (autonomous retry) or a **Hard Block** (immediate halt on that row):

| Category | Issue Type | Autonomous Action |
| :--- | :--- | :--- |
| **Auto-Fix** | Sibling SKU Mismatch *(e.g. Giga vs Giga Max)* | Clear raw fields, refine search query to exclude sibling tokens, re-fetch. |
| **Auto-Fix** | Capacity / Spec Mismatch | Reject candidate URL, search next source tier for exact model match. |
| **Auto-Fix** | Boilerplate Bullets *(Site footer text)* | Reject LLM output, extract clean accordion/spec text block, re-prompt LLM. |
| **Auto-Fix** | Low-Res Image ($< 800\text{ px}$) | Query CDN with max resolution parameter (`width=2048` or `_SL1500_`) or escalate to next image tier. |
| **Auto-Fix** | Filename Slug Mismatch | Re-slugify filename according to canonical convention `images/{brand_slug}/{model_slug}.png`. |
| **Auto-Fix** | Missing Warranty Spec | Check brand-wide policy in collection/footer schema and apply brand baseline. |
| **Hard Block** | All Spec Tiers Exhausted | Stop. Set `Status = "Blocked"`, log diagnostic in `Flags`, write NOTHING to `Raw_*`. |
| **Hard Block** | No Image Found in Any Tier | Stop. Set `Image_Status = "missing"`, `Status = "Blocked"`. |
| **Hard Block** | Most Specs Empty / No Tech Specs | Stop. Refuse LLM drafting, write NOTHING to `Raw_*`. |
| **Hard Block** | Field Lacks Verified Source URL | Data-layer invariant violation. Immediate block. |
| **Hard Block** | Duplicate Image Hash Detected | Stop. Block both rows until human confirms distinct product images. |
| **Hard Block** | Max Retry Budget Exhausted ($\ge 3$ tries)| Stop. Mark row `Blocked` and present diagnostic log in the run report. |

> [!CAUTION]
> A blocked row **always waits for the human reviewer**. It is never populated with hallucinated data, fallback boilerplate, or unverified approximations.

---

## 5. Master Data Schema (`catalogue_data.xlsx`)

The master workbook contains a single worksheet named `CatalogueData` with a strictly flat 39-column schema:

| Column Name | Data Type | Ownership | Description & Constraints |
|---|---|---|---|
| `Product_ID` | String | Human (Init) | Unique SKU code (e.g. `PB-SC-001`) |
| `Brand` | String | Human (Init) | Canonical brand name (e.g. `Stuffcool`) |
| `Model_Name` | String | Human (Init) | Exact marketing model name |
| `Product_URL` | String | Human (Init) | Forced manual URL override |
| `Brochure_PDF` | String | Human (Init) | Relative path to local brochure PDF |
| `Marketplace_URL` | String | Human (Init) | Forced manual marketplace fallback URL |
| `MRP_Input` | Numeric | Human (Init) | Authoritative Dealer Price (DP) in INR |
| `Source_URL` | String | Script (`1_collect`) | Verified web page URL where raw data was extracted |
| `Raw_Title` | String | Script (`1_collect`) | Extracted model title |
| `Raw_Subtitle` | String | LLM Tool | Drafted subtitle with key spec highlighted |
| `Raw_MRP_Scraped` | Numeric | Script (`1_collect`) | Scraped price (unused if DP authoritative) |
| `Raw_Spec_Capacity` | String | Script (`1_collect`) | e.g. `10000 mAh` |
| `Raw_Spec_Output` | String | Script (`1_collect`) | e.g. `20W PD Fast Charging` |
| `Raw_Spec_Ports` | String | Script (`1_collect`) | e.g. `1 x Type-C, 1 x Type-A` |
| `Raw_Spec_Weight` | String | Script (`1_collect`) | e.g. `185g` |
| `Raw_Spec_Warranty` | String | Script (`1_collect`) | e.g. `6 Months (Extendable to 1 Year)` |
| `Raw_Bullet_1` | String | LLM Tool | Concise sales bullet 1 ($\le 60$ chars) |
| `Raw_Bullet_2` | String | LLM Tool | Concise sales bullet 2 ($\le 60$ chars) |
| `Raw_Bullet_3` | String | LLM Tool | Concise sales bullet 3 ($\le 60$ chars) |
| `Raw_Bullet_4` | String | LLM Tool | Concise sales bullet 4 ($\le 60$ chars) |
| `Source_Audit` | String | Script (`1_collect`) | Human-readable provenance (e.g. `tier-1: stuffcool.com`) |
| `Image_URL` | String | Script (`2_images`) | Direct CDN URL of downloaded product image |
| `Image_Status` | Enum | Script (`2_images`) | `ok` \| `low-res` \| `missing` |
| `Image_Source` | String | Script (`2_images`) | Origin label (e.g. `brand-site`, `amazon.in/dp/B0DMDZF5SV`) |
| `Override_Title` | String | Human Reviewer | Manual override for Title |
| `Override_Subtitle` | String | Human Reviewer | Manual override for Subtitle |
| `Override_MRP` | Numeric | Human Reviewer | Manual override for Price (DP) |
| `Override_Spec_Capacity` | String | Human Reviewer | Manual override for Capacity spec |
| `Override_Spec_Output` | String | Human Reviewer | Manual override for Output spec |
| `Override_Spec_Ports` | String | Human Reviewer | Manual override for Ports spec |
| `Override_Spec_Weight` | String | Human Reviewer | Manual override for Weight spec |
| `Override_Spec_Warranty` | String | Human Reviewer | Manual override for Warranty spec |
| `Override_Bullet_1` | String | Human Reviewer | Manual override for Bullet 1 |
| `Override_Bullet_2` | String | Human Reviewer | Manual override for Bullet 2 |
| `Override_Bullet_3` | String | Human Reviewer | Manual override for Bullet 3 |
| `Override_Bullet_4` | String | Human Reviewer | Manual override for Bullet 4 |
| `Override_Image_Path` | String | Human Reviewer | Escape hatch path for non-standard local image |
| `Flags` | String | Script (`3_validate`) | Comma-separated diagnostic and validation flags |
| `Status` | Enum | System / Human | `Pending` \| `Collected` \| `Blocked` \| `Ready_For_Review` \| `Approved` |

---

## 6. Directory Structure

```
Vianet Catalogue/
├── ARCHITECTURE.md            # System architecture reference document (this file)
├── PHASES.md                  # Implementation phases and migration records
├── RENDER_RULES.md            # Build-time automated rules & visual review checklist
├── config.yaml                # Central system configuration (category, rules, paths)
├── .env                       # Environment secrets (GEMINI_API_KEY)
├── requirements.txt           # Python dependencies
├── data/
│   └── catalogue_data.xlsx    # Master Excel Single Source of Truth
├── images/                    # Master image repository (convention: {brand_slug}/{model_slug}.png)
│   └── {brand_slug}/
│       └── {model_slug}.png
├── src/
│   ├── utils/
│   │   ├── excel_handler.py   # Data layer invariants & safe Excel I/O
│   │   ├── llm_client.py      # LLM drafting interface (bullets/subtitles only)
│   │   ├── scraper.py         # Tiered HTML/PDF scraper & accordion parser
│   │   └── logger.py          # Structured logging utility
│   ├── 1_collect.py           # Pipeline Step 1: Tiered Data Collector
│   ├── 2_images.py            # Pipeline Step 2: Tiered Image Resolver & Quality Validator
│   ├── 3_review.py            # Pipeline Step 3: Loop Validator & Diagnostic Reporter
│   └── 4_build.py             # Pipeline Step 4: Jinja2 & Playwright PDF Compiler
├── templates/
│   ├── base.html              # Base HTML frame with self-hosted font loader
│   ├── components/            # Reusable design partials
│   │   ├── brand_header.html  # Top brand header panel
│   │   ├── product_card.html  # 2-up standard product block
│   │   ├── single_product_card.html # 1-up odd product block
│   │   ├── cover_hook.html    # Full-bleed cover page component
│   │   └── brand_divider.html # Dynamic brand divider page
│   └── catalogue.html         # Master catalogue template
├── styles/
│   ├── tokens.css             # Approved design tokens (palette, typography, geometry)
│   └── layout.css             # Pixel-perfect A4 print layout rules
└── dist/
    └── catalogue.pdf          # Final compiled PDF deliverable
```

---

## 7. Build-Time Safety & PDF Compilation (`4_build.py`)

1. **Excel File Lock Gate:** Detects if `catalogue_data.xlsx` is open in Microsoft Excel and halts execution immediately.
2. **Automated Section A Checks:**
   - All required fields non-empty.
   - All image assets exist on disk.
   - Whitespace and single-space typography integrity verified.
   - No broken manual letter-spacing.
   - Rendered title width verification (no auto-shrink; fails on text overflow).
   - No stray or legacy decorative elements.
   - Image aspect ratio (1:1 square verification).
   - Brand image background tone consistency check.
3. **Deterministic Page Flow:**
   - Page 1: Full-bleed Cover Page.
   - Page 2: Massive Bold White Brand Divider Page.
   - Pages 3+: 2-up product stacks with dynamic 1-up odd card handling.
   - Compiles via Playwright headless Chromium with `device_scale_factor: 2` and `@page` margin `0mm`.
