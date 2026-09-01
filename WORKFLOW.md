# Brand Onboarding Workflow (`WORKFLOW.md`)

This document defines the standard, repeatable procedure for onboarding a new brand into the Vianet Product Catalogue from a raw dealer price sheet in any format.

```
Dealer Price Sheet (Screenshot / PDF / Excel / CSV / Pasted Text)
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 1: Ingestion & Pure Product Row Extraction                 │
│         - Detects format automatically                          │
│         - Strips headers, blanks, merged cells, stray columns   │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 2: Autonomous Inference & Inference Summary Table          │
│         - Infer Brand, DP vs MRP, Domain, Platform              │
│         - Strip SKUs/colors -> Model_Name                       │
│         - Generate Shortest Clean Name -> Display_Name          │
│         - Derive & justify brand-specific Qualifier Tokens      │
│         - Identify Duplicates                                   │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 3: Brochure Tier 0 Prompt                                  │
│         - Single check for brand catalog PDF in brochures/{brand}│
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 4: Autonomous Configuration Update                         │
│         - Populates config/brand_defaults.yaml with tokens      │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 5: Unattended Pipeline Run                                 │
│         - python src/run_brand.py --brand <Brand>               │
│         - Tiers 0-4 -> Image Resolver -> Review Loop            │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│ STEP 6: Human Approval & PDF Compilation Routing                │
│         - Standalone PDF (dist/<brand>/) vs Combined (dist/)    │
│         - Brand order in config.yaml                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Step 1: Read the Price Sheet (Any Format)

The ingestion engine accepts dealer price sheets in any format and normalizes them into structured product records:
- **Image (`.png`, `.jpg`, `.jpeg`, `.webp`):** Uses Gemini Vision to perform structured OCR on screenshot sheets, photo price lists, and scanned infographics.
- **PDF (`.pdf`):** Extracts text layers with `pdfplumber`. If the PDF is scanned or image-heavy, renders high-resolution page bitmaps with `pypdfium2` and evaluates via Gemini Vision.
- **Excel / CSV (`.xlsx`, `.xls`, `.csv`):** Reads tabular structures via `pandas`, resolving merged header blocks, multi-row banners, blank lines, and stray summary columns.
- **Pasted Text:** Ingests unstructured, tab-separated, comma-separated, or line-delimited price text blocks.

**Extraction Invariant:** Filters out dealer terms, freight clauses, header rows, category banners, and non-product line items.

---

## Step 2: Autonomous Inference & Summary (State, Do Not Ask)

The engine evaluates product rows deterministically and via LLM, inferring all metadata autonomously without asking clarification questions:

1. **Brand Name:** Detected from sheet titles, product code prefixes, or web domain lookups.
2. **Price Disambiguation:**
   - **Two price columns:** Lower price $\rightarrow$ Dealer Price (`MRP_Input` / DP); Higher price $\rightarrow$ Maximum Retail Price (`MRP_Display` / MRP).
   - **Single price column:** Treated as Dealer Price (`MRP_Input` / DP).
3. **Official Domain & Platform:** Locates the brand's official consumer website and detects the store platform (e.g. Shopify JSON API vs Custom HTML).
4. **Model Name Cleaning (`Model_Name`):** Strips internal SKU prefixes, brand prefixes, and color variant suffixes while retaining model descriptors and capacities (e.g. `"PB-SC-001 Stuffcool Mega 20K Black"` $\rightarrow$ `"Mega 20000mAh"`).
5. **Display Name (`Display_Name`):** Generates the **shortest clean name only** to guarantee it fits the product card title without wrapping or overflowing (e.g. `"Mega"` — *not* `"Mega 20000mAh Powerbank"`).
6. **Brand-Specific Qualifier Tokens:** Evaluates the brand's catalog naming schema to select true variant/modifier suffixes (e.g. `Plus`, `Pro`, `Max`, `Mini`, `Ultra`, `Lite`, `Go`).
   - *Strict Rule:* Never add actual model names (e.g. `Fuel`, `Boost`, `Nova`, `Electra`, `Giga`, `Major`) to the qualifier list.
7. **Deduplication:** Flags duplicate entries (identical brand, model name, and DP).

### Inference Summary Presentation
Before proceeding, the engine presents an **Inferences Summary Table** with rationales:

```markdown
### 📋 Onboarding Inference Summary for Brand '{Brand}'
- **Brand Inferred:** {Brand} (source: sheet header / model prefixes)
- **Price Mapping:** Column '{Col1}' read as DP (₹{X}); Column '{Col2}' read as MRP (₹{Y})
- **Official Domain & Platform:** {domain} (Platform: {shopify|custom})
- **Selected Qualifier Tokens & Justification:**
  - `Plus`: Sibling variant suffix (e.g. 'Roam' vs 'Roam Plus')
  - `Pro`: High-wattage variant suffix
  - `Mini`: Form-factor variant suffix
- **Duplicates Identified:** None

| Product_ID | Model_Name | Display_Name | DP (₹) | MRP (₹) | Inferred Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| PB-XXX-001 | Mega 20000mAh | Mega | 1,499 | 2,999 | Stripped color suffix 'Black' |
| PB-XXX-002 | Roam Plus 20000mAh | Roam Plus | 1,299 | 2,499 | Shortest clean display title |
```
*The human reviewer may correct any inference. If no corrections are given, the pipeline proceeds.*

---

## Step 3: Brochure Tier 0 Prompt (Single Question)

The agent prompts once:
> *"Do you have a brand brochure PDF for **{Brand}**?"*
- **If Yes:** Execution pauses until you place the PDF into `brochures/{brand_slug}/`.
- **If No / None:** The pipeline advances immediately; **Tier 0** skips silently and starts at Tier 1.

---

## Step 4: Autonomous Configuration Setup

The agent automatically registers the brand in `config/brand_defaults.yaml`:
```yaml
{brand_slug}:
  brand: "{Brand}"
  domain: "{domain}"
  platform: "{shopify|custom}"
  qualifier_tokens: ["Plus", "Pro", "Max", "Mini", "Ultra", "Lite", "Go"]
```

---

## Step 5: Unattended Pipeline Run

The engine appends the prepared rows to `data/catalogue_data.xlsx` and launches the full pipeline:
```bash
python src/run_brand.py --brand {Brand}
```
**Execution Chain:**
1. **`1_collect.py`:** Tier 0 Brochure $\rightarrow$ Tier 1 Brand Page (HTML/JSON + Vision) $\rightarrow$ Tier 2 Collection $\rightarrow$ Tier 3 Retail Endpoints $\rightarrow$ Tier 4 Auto-Skip.
2. **`2_images.py`:** Resolves high-resolution square assets (1200×1200px+), runs aspect ratio and background consistency gates.
3. **`3_review.py`:** Self-correction loop, deterministic validation, partial specs verification ($\ge 3$ of 5 specs), and comprehensive run report generation.

**Run Report Outputs:**
- Per-row breakdown: `Product_ID`, `Model_Name`, `Display_Name`, `Status`, `Source_URL`, `Tier`, `Capacity`, `MRP`, `Attempts`, `Fix_Log`.
- Metric summary: Total runtime, Zero-Touch pass count, Skipped count, Blocked count.

---

## Step 6: Human Review & PDF Build Gate

Before any PDF compilation occurs, the agent pauses for explicit approval:
1. **Row Sign-off:** Human reviews the run report, checks images, resolves any `Blocked` rows via `Override_*` columns in `catalogue_data.xlsx`, and marks `Status = "Approved"`.
2. **Compilation Target Question:**
   > *"Would you like to build **{Brand}** as a standalone PDF in `dist/{brand_slug}/`, or append it to the combined catalogue in `dist/combined/`?"*
   > *(If Combined: "Where should **{Brand}** sit in `brand_order` in `config.yaml`?")*
3. **Compilation:** Executes `python src/4_build.py --brand {Brand}` (or combined build) only after human confirmation.

---

## Unchanged Core Invariants
- **Zero Fabrication:** No field is written without a verified `Source_` URL or brochure page reference.
- **Human Approval Gate:** Catalogue PDFs are compiled exclusively from `Status = "Approved"` rows.
- **Data Protection:** Previously committed brand data, approved images, and PDFs remain locked.
