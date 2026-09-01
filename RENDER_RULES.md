# RENDER RULES & VALIDATION SPECIFICATION

This document defines the strict rendering constraints and visual quality standards for the Vianet Catalogue PDF generation pipeline.

---

## SECTION A — Build-Time Rules (Automated Checks in `src/4_build.py`)

These rules are programmatically enforced during every execution of `4_build.py`. The build fails immediately with a loud exception if any check fails before HTML rendering or PDF compilation begins.

1. **Required Fields Integrity:**
   - Every product row in the dataset must have non-empty values for:
     - `Product_ID`
     - `Brand`
     - `Model_Name`
     - `Subtitle`
     - `Bullets` (at least 1–4 feature bullets)
     - `Price` / `MRP`
     - `Local_Image_Path`
   - *Failure behavior:* `ValueError` raised specifying the offending Product ID and missing field.

2. **Asset Presence & File Verification:**
   - Every product's `Local_Image_Path` must resolve to an existing, non-zero-byte file on disk before rendering.
   - Master logo (`images/vianet-logo.png`) and cover asset (`images/cover.png`, if present) must be verified.
   - *Failure behavior:* `FileNotFoundError` raised with full resolved file path.

3. **Whitespace & Typography Integrity (No Collapsed or Doubled Spaces):**
   - No double or consecutive spaces (`"  "`) allowed anywhere within product names or formatted HTML.
   - No leading or trailing whitespace on model names or subtitles.
   - The space between the two-digit index number and the product name (e.g. `01 click 10`) must always be a single, natural space.
   - Multi-word product names with styled trailing words (e.g. `click <em>10</em>`, `major <em>ultra</em>`) must preserve exact single-space separation without collapsing or doubling.
   - *Failure behavior:* `ValueError` raised specifying the formatting violation.

4. **No Broken Letter-Spacing / Tracking Artifacts:**
   - No broken manual letter-spacing (e.g. `"S T U F F C O O L"` or `"P U N E"`) in data fields or CSS rules that fragment words. Tracking must be applied strictly via standard CSS `letter-spacing` on unified word elements.
   - *Failure behavior:* `ValueError` raised if manual letter-spaced patterns are detected.

5. **Rendered Title Width & Overflow Integrity (No Auto-Shrink):**
   - Every product title must fit completely within the details panel's available width (`~346px`) at its natural font size.
   - The engine does not auto-shrink product titles. If any title's rendered text width exceeds the container's available width, the build must fail immediately and identify the offending product row.
   - *Failure behavior:* `ValueError` raised specifying the overflowing Product ID, model name, text width, and container width.

6. **No Stray Decorative Elements:**
   - Legacy eyebrow lines (e.g. `"01 — STUFFCOOL SERIES"`), orphaned rule dividers, or abandoned decorative elements must never appear in template output.
   - *Failure behavior:* `ValueError` raised if obsolete eyebrow or decorative tokens are detected in rendered HTML.

7. **Image Background Consistency:**
   - Corner pixels (top-left, top-right, bottom-left, bottom-right) of all product images within each brand group are sampled and compared against the brand's median corner background color.
   - If any image corner pixels deviate beyond an acceptable Euclidean color distance tolerance, a loud warning is emitted listing the offending filenames and sampled hex colors.
   - *Failure behavior:* Warning logged listing inconsistent image assets and sampled RGB values.

8. **Image Aspect Ratio (Strict Square 1:1 Framing):**
   - Every product image must have a square aspect ratio (`width == height`, tolerance < 1%), matching the square `290px × 290px` ivory media tile. Non-square images will crop or distort inside the square container.
   - *Failure behavior:* Warning/error logged specifying non-square image dimensions and offending filenames.

9. **URL-Title Numeric Consistency Warning:**
   - Emits a non-blocking `WARN` when numeric tokens in the URL slug conflict with numeric tokens in the scraped title or Model_Name (e.g. slug `pebble-electra10` matching title `Rapid Electra20`), detecting reused URL handles across product generations.
   - *Failure behavior:* Non-blocking warning logged in review report.

---

## SECTION B — Human Visual Checks (Post-Build Review Checklist)

These visual quality standards must be verified by a human reviewer inspecting the compiled PDF before client distribution:

1. **Card & Panel Uniformity:**
   - Image tile and details panel heights must be strictly identical (`235px`) across all products, regardless of subtitle or bullet copy length.

2. **Image Scaling & Framing Consistency:**
   - Product cutouts must fill approximately 80% of their square ivory tile (`235px × 235px`) with comfortable, balanced padding.
   - No product should appear noticeably smaller, oversized, or clipped.

3. **DP Badge Positioning & Clearance:**
   - The gold **DP** badge (`₹X,XXX incl. GST`) must sit cleanly inside the ivory panel with comfortable bottom padding (never flush against or touching the card's bottom edge).

4. **Bullet Column Alignment:**
   - In the 2-column feature bullet grid, each row track must start at the exact same vertical baseline across both left and right columns.

5. **Full-Bleed Canvas & Page Edges:**
   - Teal background (`#1B3A47`) must reach the very edge of every A4 page with zero white border lines or margin slivers along the top, bottom, or sides.

6. **Razor-Sharp Image Resolution:**
   - Raster images and product cutouts must remain crisp and sharp at fit-to-page zoom, rendered from high-resolution source masters (`1200×1200px+`) with `deviceScaleFactor: 2`.

7. **Brand Divider Page Impact:**
   - Brand divider pages must feature the brand name in massive, bold white uppercase typography filling nearly the full width of the page, centered both horizontally and vertically on the full A4 canvas.
