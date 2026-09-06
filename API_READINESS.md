# API Readiness & Backend Integration Audit (`API_READINESS.md`)
**Date:** September 2, 2026  
**Audited Codebase:** Vianet Product Catalogue Engine  
**Objective:** Evaluate backend readiness for Web API / Frontend integration while strictly preserving existing CLI behavior, Excel invariants, and validation rules.

---

## Executive Summary

The existing backend is modular and decoupled from any specific UI. Core capabilities (OCR, LLM inference, tiered scraping, AI image quality auditing, deterministic validation, Jinja2 rendering, and Playwright PDF compilation) are implemented in standalone Python functions.

To allow a web API (FastAPI / Flask) and frontend to operate against this codebase without breaking or altering existing CLI runs:
1. **No business logic needs to be rewritten.**
2. **Crash recovery and Excel state safety already work out of the box.**
3. **Four small, non-breaking enhancements** are proposed (via optional arguments and return types) to support headless API execution, progress streaming, structured responses, and endpoint formalization for the two chat-driven stages.

---

## Detailed Findings Across the 6 Audit Areas

---

### 1. Terminal I/O & Stdout Audit (Where Logic Prints or Prompts)

#### Audit Findings
* **Terminal Input (`input()`, `sys.stdin`):**
  * **Result: Clean (Zero instances).**
  * There is **no `input()` or `sys.stdin.read()` anywhere in `src/`**.
  * User prompts ("Do you have a brochure?", "Approve these rows?") currently occur exclusively through conversational chat turns with the AI assistant, not via blocking Python terminal prompts.
* **Stdout Printing (`print()`):**
  * **Result: 3 instances in `src/run_brand.py`:**
    1. Line 561: `print(f"\n[HALT] {e}\n")` (prints LLM pre-flight quota exhaustion).
    2. Line 578: `print(f"\n[HALT] {e}\n")` (prints pipeline LLM quota exhaustion).
    3. Line 604: `print(table_output)` (prints the markdown presentation review table and sign-off prompt).
  * In all other places, output is emitted through standard Python `logging` (`logger.info`, `logger.warning`, `logger.error`).
* **Return Value Limitation:**
  * `run_brand(brand_name, ...)` returns only a `report_path: str` (the disk path to `RUN_REPORT_{brand}_{timestamp}.md`), while the actual review table data is sent solely to stdout via `print()`.

#### Assessment & Smallest Proposed Change
* **Status:** **Needs Change (Minor).**
* **Proposed Non-Breaking Change:**
  * In `src/run_brand.py`, add an optional parameter `return_summary: bool = False` (defaulting to `False`).
  * When `return_summary=False` (CLI runs): Existing behavior is 100% preserved (`print()` is called, returns `report_path: str`).
  * When `return_summary=True` (Web API runs): Suppress `print()`, log via `logger`, and return a structured dictionary or dataclass:
    ```python
    {
        "brand": brand_name,
        "report_path": report_path,
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "skipped_count": skipped_count,
        "deferred_count": deferred_count,
        "rows": brand_rows,              # Full product row dicts
        "table_markdown": table_output,   # Rendered markdown table
        "halt_reason": halt_reason        # None if success
    }
    ```

---

### 2. Hardcoded File Paths & Manual File Dependencies

#### Audit Findings

| Location | Path | Usage | Hardcoded vs. Configurable |
| :--- | :--- | :--- | :--- |
| **Excel Database** | `data/catalogue_data.xlsx` | Master product dataset (Sheet: `CatalogueData`) | Configurable via `config.yaml` (`paths.excel_file`), with default fallback `"data/catalogue_data.xlsx"`. |
| **Brand Defaults** | `config/brand_defaults.yaml` | Per-brand domains, platforms, qualifier tokens, warranty | Hardcoded default argument in `onboard_brand.py` and `run_brand.py`. |
| **Global Config** | `config.yaml` | Category metadata, brand ordering, paths, company info | Configurable via CLI `--config`, defaults to `"config.yaml"`. |
| **Product Images** | `images/{category}/{brand}/{model}.png` | Studio packshots (1:1 square on `#ffffff`) | Dynamically derived from category, brand, and model slugs. Base directory configurable via `paths.images_dir`. |
| **Output Deliverables** | `dist/{category}/{brand}/*.pdf`<br>`dist/{category}/combined/*.pdf` | Standalone and combined PDFs, HTML previews, run reports | Dynamically derived from category, brand, and timestamp. Base directory configurable via `paths.output_dir`. |
| **Brochure PDFs** | `brochures/{brand_slug}/*.pdf` | Tier 0 spec extraction | Checked dynamically by `find_brochure_pdfs()`. Also accepts explicit path from Excel column `Brochure_PDF`. |
| **Cover & Logo Assets** | `images/cover.png`<br>`assets/vianet-logo.png` | Global corporate cover background and logo mark | Paths checked with fallback in `src/4_build.py`. |
| **Templates & Fonts** | `templates/catalogue.html`<br>`styles/tokens.css`<br>`styles/layout.css`<br>`assets/fonts/` | Jinja2 template, CSS tokens, self-hosted WOFF2 fonts | Relative paths from project root. |

#### Manual File Placement Dependencies
1. **Price Sheet Input (Stage 1):** Currently, the operator must save the uploaded file (or image) to a path on disk before passing `file_path` to `extract_text_from_file(file_path)`.
2. **Brochure PDF (Stage 2):** If a brochure exists, it must be saved to `brochures/{brand_slug}/{filename}.pdf` (or its path written to `Brochure_PDF` in Excel).

#### Assessment & Smallest Proposed Change
* **Status:** **Partially Works / Minor Change Needed.**
* **Proposed Non-Breaking Change:**
  * Add a dedicated upload directory (e.g. `uploads/` or `data/uploads/`).
  * In the API upload endpoint for Stage 1, save the incoming `UploadFile` (multipart/form-data) to `uploads/{timestamp}_{filename}`, then pass that path to `extract_text_from_file(file_path)`.
  * In the API upload endpoint for Stage 2, save the uploaded brochure to `brochures/{brand_slug}/{filename}.pdf`, which `find_brochure_pdfs()` automatically discovers without altering any collection code.

---

### 3. Return Signatures of Callable Functions

#### Detailed Function Audit

| Function | Current Return Type | Current Behavior | API Suitability |
| :--- | :--- | :--- | :--- |
| `extract_text_from_file(file_path, llm_config)`<br>*(src/onboard_brand.py)* | `Tuple[str, str]` | Returns `(extracted_text, format_type)`. | **Ready.** Pure function, returns clean text and format identifier. |
| `analyze_price_sheet(raw_content, category, llm_config)`<br>*(src/onboard_brand.py)* | `BrandInferenceSchema` | Returns a fully typed Pydantic `BaseModel` instance. | **Ready.** Can be returned directly as JSON via `.model_dump()` in FastAPI. |
| `generate_onboarding_summary(inference)`<br>*(src/onboard_brand.py)* | `str` | Returns formatted markdown table with live bot-probe results. | **Ready.** Pure function, returns string for markdown rendering in frontend. |
| `register_brand_config(inference, config_path)`<br>*(src/onboard_brand.py)* | `None` | Writes brand block to `config/brand_defaults.yaml`. | **Ready.** Performs file update. |
| `append_products_to_catalogue(inference, excel_path)`<br>*(src/onboard_brand.py)* | `int` | Appends rows to Excel; returns count of rows added. | **Needs Minor Enhancement.** Returns only integer count. Propose returning `Tuple[int, List[Dict[str, Any]]]` (count + list of generated rows with `Product_ID`s). |
| `run_brand(brand_name, config_path, ...)`<br>*(src/run_brand.py)* | `str` | Runs collection/review; prints table to stdout; returns `report_path: str`. | **Needs Enhancement.** Returns only file path. Needs optional `return_summary=True` to return structured row data. |
| `build_catalogue(config_path, brand)`<br>*(src/4_build.py)* | `str` | Validates data; compiles PDF via Playwright; returns `output_pdf: str`. | **Ready.** Returns path to compiled PDF deliverable. |

#### Assessment & Smallest Proposed Change
* **Status:** **Mixed (3 functions ready, 2 benefit from minor return additions).**
* **Proposed Non-Breaking Change:**
  * For `append_products_to_catalogue`: Return `Tuple[int, List[Dict[str, Any]]]` so the API can immediately send the newly created rows back to the frontend without a separate Excel read.
  * For `run_brand`: Support `return_summary=True` returning dict with row states, metrics, and markdown table (as described in Section 1).

---

### 4. Progress Signals During Long-Running Execution

#### Audit Findings
* **Current Execution Duration:**
  * `run_brand()`: Takes **60 to 300 seconds** (1–5 minutes) depending on product count, tier escalations, Playwright page visits, and Gemini Vision image review calls.
  * `build_catalogue()`: Takes **10 to 25 seconds** (launches headless Chromium, loads HTML with Base64 assets, verifies DOM element bounding boxes, and prints PDF).
* **Current Progress Mechanism:**
  * **Zero programmatic progress signals.**
  * Progress is strictly logged to Python `logger` (`setup_logger`) which prints colorized text to console stdout and appends to task logs.
  * There is no callback, event hook, status file, or yield generator.

#### Assessment & Smallest Proposed Change
* **Status:** **Needs Change.**
* **Proposed Non-Breaking Change:**
  * Add an optional callback parameter to `run_brand` and `process_row_loop`:
    ```python
    def run_brand(
        brand_name: str,
        config_path: str = "config.yaml",
        enable_semantic_audit: bool = False,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> Union[str, Dict[str, Any]]:
    ```
  * Inside `run_brand`, at each major milestone (pre-flight, each product row start/finish, image resolution, review loop attempt, post-run LLM audit, report generation), emit an event dict:
    ```python
    if progress_callback:
        progress_callback({
            "stage": "collection",
            "brand": brand_name,
            "product_id": pid,
            "model_name": model,
            "current_index": i + 1,
            "total_count": total_rows,
            "status": "processing",
            "message": f"Auditing image for {model} via Visual AI Gate..."
        })
    ```
  * **When `progress_callback is None` (CLI runs):** Nothing changes. Zero overhead.
  * **When provided by Web API (FastAPI):** The callback pushes progress events into a WebSocket connection, an SSE stream, or updates an in-memory job status dictionary for frontend polling.

---

### 5. Crash State & Mid-Run Failure Recovery

#### Audit Findings

#### 1. What happens in Excel (`data/catalogue_data.xlsx`) if the process is killed mid-run?
* In `src/run_brand.py`:
  * Products are processed in memory inside a local pandas DataFrame (`df`).
  * `save_catalogue_data(df, excel_path)` is called **only once at line 587, at the very end of the run**, after all rows have finished `process_row_loop`, URL collisions are resolved, and the post-run review is complete.
* **Result if process dies mid-run:**
  * **Excel on disk is completely untouched.**
  * All rows remain in their original `Pending` state with `Attempts = 0`.
  * **There are zero half-written, corrupt, or partially updated rows in the spreadsheet.**

#### 2. What happens on disk (`images/` and `cache/`)?
* **Images:**
  * Images downloaded and verified before the crash remain on disk at `images/{category}/{brand}/{model}.png`.
  * On a re-run:
    * `execute_image_tier_escalation` calls `validate_image_file(dest_path)`.
    * If the image is complete, square, and uncorrupted, it logs:
      `[images] Preserving existing local image at {dest_path} -> Status: ok`
      and **reuses it immediately without re-downloading or re-auditing**.
    * If the image was mid-write and corrupted (0 bytes or partial write), `validate_image_file` returns `"missing"`, and the image is cleanly re-fetched and re-audited.
* **Scraping Cache:**
  * `cache/{hash}.json` files are written atomically. Valid cached responses are reused; corrupt files are caught and refreshed.
* **PDF Deliverables (`dist/`):**
  * In `src/4_build.py`, Playwright writes the PDF file upon full completion. A partial PDF is never left in place.

#### Assessment & Smallest Proposed Change
* **Status:** **Already Works (100% Robust).**
* No code changes are required for crash recovery. The re-run semantics are safe, deterministic, and idempotent.

---

### 6. Formalizing Stage 2 (Brochure Ask) and Stage 4 (Approval)

Stages 2 and 4 currently exist as conversational chat turns. Here is the exact specification of what occurs today in each stage so they can be implemented as clean API endpoints:

---

#### Stage 2: Brochure Gate Specification

* **Current Reality:**
  * The operator asks: *"Do you have a brand brochure PDF for {Brand}?"*
  * If the user provides a PDF:
    1. The PDF is saved to `brochures/{brand_slug}/{filename}.pdf`.
    2. In `data/catalogue_data.xlsx`, for all rows matching `{Brand}`, the column `Brochure_PDF` is set to `brochures/{brand_slug}/{filename}.pdf`.
    3. During Stage 3, `parse_brochure_for_model()` scans the brochure for specs in Tier 0.
  * If the user says "No" / "no proceed":
    1. No file is saved.
    2. Column `Brochure_PDF` in Excel remains empty (`None`).
    3. During Stage 3, `find_brochure_pdfs()` finds no files, logs a debug message, and silently skips Tier 0, starting directly with Tier 1 (web storefront).
* **Exact Files & Fields Touched:**
  * File created (if provided): `brochures/{brand_slug}/{filename}.pdf`
  * Excel column updated (if provided): `Brochure_PDF` for brand rows.
* **Proposed API Endpoint:**
  ```http
  POST /api/brands/{brand_slug}/brochure
  ```
  * **Payload:** Multipart form-data with optional file `file: UploadFile` and flag `skip: bool = False`.
  * **Logic:**
    * If `skip=True` or `file is None`: Ensure `Brochure_PDF` is empty; return `{"status": "skipped", "tier_0": False}`.
    * If `file` provided: Save to `brochures/{brand_slug}/{filename}`, update `Brochure_PDF` in `catalogue_data.xlsx`, return `{"status": "uploaded", "path": saved_path, "tier_0": True}`.

---

#### Stage 4: Approval Gate Specification

* **Current Reality:**
  * The operator presents the review table and asks:
    1. *"Approve these rows?"*
    2. *"Standalone PDF in `dist/{category}/{brand}/`, or append to the combined PDF and where in `brand_order`?"*
  * User Actions:
    * **Approval Decision:**
      * User confirms approval ("approved") for the rows.
      * (Optional) User specifies any manual field corrections (e.g. override subtitle, price, or bullet points).
    * **Build Mode Decision:**
      * Choice A: Standalone PDF only.
      * Choice B: Append to Master Combined Catalogue PDF.
  * Backend State Changes:
    1. **Excel Updates:**
       * For all approved `Product_ID`s: `Status` column is set to `"Approved"`.
       * If user provided field overrides: Values are written to `Override_Title`, `Override_Subtitle`, `Override_MRP`, `Override_Spec_*`, `Override_Bullet_*`, or `Override_Image_Path`.
    2. **Config Updates (If Appending):**
       * In `config.yaml`, the brand is added to the `brand_order` list at the user's requested position (default: appended to the end).
    3. **Build Triggering:**
       * Standalone build: `build_catalogue(config_path="config.yaml", brand=brand_name)`
       * Combined build: `build_catalogue(config_path="config.yaml", brand=None)`
* **Exact Files & Fields Touched:**
  * `data/catalogue_data.xlsx`: Column `Status` $\rightarrow$ `"Approved"`, plus any `Override_*` columns.
  * `config.yaml`: List `brand_order` $\rightarrow$ appended with brand name.
* **Proposed API Endpoint:**
  ```http
  POST /api/brands/{brand_slug}/approve
  ```
  * **Payload:**
    ```json
    {
        "brand": "EVM",
        "product_ids": ["PB-EVM-001", "PB-EVM-002", "PB-EVM-003", "PB-EVM-004", "PB-EVM-005"],
        "overrides": {
            "PB-EVM-001": {
                "Override_Subtitle": "Compact emergency power backup."
            }
        },
        "build_target": "both", // "standalone" | "combined" | "both"
        "brand_order_position": "end" // "end" | index number | null
    }
    ```
  * **Logic:**
    1. Load `data/catalogue_data.xlsx`.
    2. Update `Status = "Approved"` for specified `product_ids`.
    3. Apply any provided `overrides` to the respective `Override_*` columns.
    4. Save Excel via `save_catalogue_data(df, excel_path)`.
    5. If `build_target` in `("combined", "both")`: Update `config.yaml` `brand_order` if brand not already present.
    6. Return confirmation: `{"status": "approved", "approved_count": len(product_ids), "ready_for_build": True}`.

---

## 7. Summary Matrix: What Works vs. What Needs a Change

| Area | Current Status | Change Required? | Proposed Minimal Change |
| :--- | :--- | :--- | :--- |
| **Stage 1 (Ingest)** | Python functions in `onboard_brand.py` | **Minor** | Expose a clean wrapper function that accepts an uploaded file path, runs analysis, and returns `BrandInferenceSchema.model_dump()`. |
| **Stage 2 (Brochure)** | Conversational chat turn | **Minor (New Endpoint)** | Create `POST /api/brands/{brand}/brochure` to save PDF to `brochures/{brand}/` or flag skip. |
| **Stage 3 (Collection)** | `run_brand.py` CLI | **Minor** | Add optional `progress_callback` and `return_summary=True` to `run_brand()`. Preserves existing CLI behavior when omitted. |
| **Stage 4 (Approval)** | Conversational chat turn | **Minor (New Endpoint)** | Create `POST /api/brands/{brand}/approve` to update Excel `Status="Approved"`, apply overrides, and update `brand_order` in `config.yaml`. |
| **Stage 5 (Output)** | `build_catalogue()` function in `4_build.py` | **Works (Ready)** | Wrap in `POST /api/build` (takes `brand: Optional[str] = None` and returns compiled PDF path). |
| **Crash Recovery** | Excel written only at end; images validated | **Works (100% Ready)** | No changes needed. Re-runs pick up cleanly and reuse intact assets. |
| **File Locking** | `check_file_lock()` protects Excel | **Works (100% Ready)** | API background worker simply queues write jobs to ensure single-writer safety. |
| **Static File Delivery** | Files in `images/` and `dist/` | **Works (100% Ready)** | Mount `images/` and `dist/` as static routes in the web API framework. |
