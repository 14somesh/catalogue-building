# Regression Harness & Golden Dataset Documentation

A deterministic, offline regression harness designed to safeguard against historical regressions and preserve pipeline invariants without modifying pipeline source code (`src/`) or production data assets (`data/catalogue_data.xlsx`, `config/brand_defaults.yaml`, `images/`, `dist/`).

---

## 1. Running the Harness

Execute the entire test suite with one command:

```bash
python tests/harness.py
```

### CLI Options

| Command | Description |
| :--- | :--- |
| `python tests/harness.py` | Runs both Level 1 unit tests and Level 2 golden baseline diff. |
| `python tests/harness.py --unit-only` | Runs only Level 1 unit tests (< 0.6s execution). |
| `python tests/harness.py --golden-only` | Runs only Level 2 golden dataset replay and diffing. |
| `python tests/harness.py --update-baseline` | Deliberately updates the golden baseline from the current catalogue. |
| `python tests/harness.py --record-brand <Brand>` | Re-records offline HTTP fixtures for a specific brand without touching other brands. |

---

## 2. Maintaining the Harness

### A. Baseline Location and Format
- **Path**: `tests/fixtures/golden_baseline.json`
- **Format**: JSON dictionary mapping `Product_ID` to its verified baseline state:
  ```json
  {
    "PB-POR-006": {
      "product_id": "PB-POR-006",
      "brand": "Portronics",
      "category": "Powerbank",
      "model_name": "Chyro",
      "display_name": "Chyro",
      "source_url": "https://www.portronics.com/products/chyro",
      "tier": "Tier 1: Official Brand Store",
      "spec_count": 4,
      "status": "Approved",
      "image_status": "Saved",
      "image_path": "images/powerbank/portronics/chyro.png"
    }
  }
  ```
- **Git Tracking**: **Yes, this file must be committed to Git.** It acts as the immutable contract of truth for the entire product catalogue.

### B. Adding a New Brand or Category
When you legitimately onboard a new brand or category (e.g. via `src/onboard_brand.py`):
1. Review and approve the newly onboarded product rows in the UI or catalogue sheet.
2. Run fixture recording for that brand:
   ```bash
   python tests/harness.py --record-brand "NewBrand"
   ```
3. Update the golden baseline to include the new products:
   ```bash
   python tests/harness.py --update-baseline
   ```
4. Run `python tests/harness.py` to verify that all unit tests and the new baseline pass.
5. Commit `tests/fixtures/golden_baseline.json` and `tests/fixtures/http_fixtures.json` to Git.

### C. Accepting a Changed Baseline (Intentional Improvement)
If a pipeline change legitimately improves data resolution (for example, extracting 4 specs where 2 were previously extracted, or finding a cleaner official URL):
1. The harness will report the difference by name:
   ```
   ! DIFF FINDINGS (1 mismatch detected):
     • Portronics 'Chyro' (PB-POR-006) found 4 specs, baseline was 2
   ```
2. If this change is **verified and intended**, accept it using the explicit command:
   ```bash
   python tests/harness.py --update-baseline
   ```
3. Re-run `python tests/harness.py` to confirm zero diffs.
4. Baseline changes are **never updated automatically**; they require this explicit command.

### D. Updating Stale Fixtures for a Single Brand
When a brand's website updates its layout or product URLs change:
1. Re-run collection for that brand to populate the disk cache.
2. Re-record fixtures for that brand only:
   ```bash
   python tests/harness.py --record-brand "Pebble"
   ```
3. This refreshes only the targeted brand's fixtures in `tests/fixtures/http_fixtures.json`, keeping all other brand fixtures intact.

---

## 3. Structural Constraint Guards Inventory

The codebase contains several proactive **structural constraint guards** that prevent invalid actions before they occur:

| Guard Name | File & Location | What It Prevents | Level 1 Test Coverage |
| :--- | :--- | :--- | :--- |
| **Write Guard** | `src/utils/excel_handler.py`<br>`validate_write_guard` (L115) | Prevents writing any `Raw_` field to Excel without a verified `Source_` URL. Human `Override_` fields are explicitly exempt. | `TestFabricationGuard.test_raw_field_without_source_raises_invariant_violation`<br>`TestFabricationGuard.test_override_fields_are_strictly_exempt` |
| **Missing Sheet Guard** | `src/utils/excel_handler.py`<br>`_verify_sheet_exists` (L144) | Prevents corruption caused by scripts writing raw `df.to_excel()` and clobbering the `CatalogueData` worksheet name to `Sheet1`. | `TestSilentFailureGuards.test_missing_catalogue_sheet_raises_named_error` |
| **Approved-Only Build Gate** | `src/4_build.py`<br>`build_catalogue_pdf` (L303) | Prevents draft, unreviewed, or rejected rows (`Ready_For_Review`, `Pending`, `Skipped`, `Blocked`, `Deferred`) from ever appearing in the final PDF catalogue. | `TestPipelineInvariants.test_only_approved_rows_build_ready_for_review_must_not` |
| **Image Presence Build Gate** | `src/4_build.py`<br>`build_catalogue_pdf` (L308-323) | Halts compilation before rendering if any `Approved` row lacks a physical image file on disk. | `TestSilentFailureGuards.test_no_approved_row_may_reach_build_without_image_on_disk` |
| **Review Loop Status Gate** | `src/run_brand.py`<br>`process_row_loop` (L163) | Prevents overwriting already `Approved` or `Skipped` rows during bulk batch runs unless an explicit single-row re-run is requested. | `TestDataResolution.test_override_survives_rerun` |
| **Brand Root Config Lock** | `src/utils/scraper.py`<br>`save_brand_domain_default` (L365) | Prevents saving category-scoped keys (`collection_url`, `qualifier_tokens`) at the brand root in `config/brand_defaults.yaml`. | `TestAdditionalPastFixInvariants.test_saving_collection_url_without_category_is_prohibited` |
| **Title Width Guard** | `src/title_measurer.py`<br>`measure_title` & `src/4_build.py` (L232-237) | Measures real rendered pixel width in Playwright against the 291px card heading container; refuses rendering double spaces, broken letter-spacing, or collapsed HTML tags. | `TestDataResolution.test_title_width_overflow_rejection` |
| **Max+1 ID Sequence Guard** | `src/onboard_brand.py`<br>`append_products_to_catalogue` (L659) | Uses `max(existing_seqs) + 1` rather than `count + 1` to guarantee deleted rows never cause `Product_ID` collisions. | `TestPipelineInvariants.test_product_id_generated_as_max_plus_one_never_count_plus_one` |
| **Warranty Prohibition Guard** | `src/utils/validators.py`<br>`validate_row_deterministic` (L229, L236) | Enforces removal of warranty/guarantee claims from subtitles and bullet points across all products. | `TestSilentFailureGuards.test_subtitle_and_bullets_must_not_contain_warranty_or_guarantee` |
