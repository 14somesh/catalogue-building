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
   - Every product title must fit completely within the details panel's available width (`291px` inner container width inside the 331px panel) at its natural font size (`32px 700 Space Grotesk`, `letter-spacing: -0.02em`, `white-space: nowrap`).
   - The engine does not auto-shrink product titles. If any title's rendered text width (including the sequence prefix `01.`) exceeds the container's available width (`textWidth > containerWidth + 1.0px`), the build must fail immediately and identify the offending product row.
   - **Critical Architecture Rule:** The title limit is strictly a **physical rendered width limit (291px)**, NOT a character count. Wide glyphs (e.g. `WMWMWMWMW`) overflow at 9 characters, while narrower titles (e.g. `Rapid Electra 20`) fit at 16 characters. Never reintroduce a fixed character number.
   - *Failure behavior:* `ValueError` raised specifying the overflowing Product ID, model name, rendered text width, and container width.

6. **Card Height Integrity & Text Wrap Ceilings (Fixed 290px Card Height):**
   - The product card details panel has a fixed height of `290px` (`--prod-h: 290px; padding: 16px 20px;`, available interior height: `258px`). Content flows vertically: Title -> Subtitle -> Gold Divider -> 2-Column Bullets Grid -> Gold DP Badge.
   - **Subtitle Limit (80 chars max):** Subtitles wrap to 2 lines up to ~80 chars (height: 33px). At 81+ chars, subtitle wraps to a 3rd line (height: 49px), pushing down the stack and causing vertical card overflow (`scrollHeight > clientHeight`). 80 is a **vertical 2-line wrap ceiling**, not a width limit.
   - **Bullet Limit (60 chars max each):** Feature bullets render in a 2-column CSS Grid with 125.5px column width. Bullets wrap to 2 or 3 lines up to ~58 chars (height: 52px). At 60+ chars, a bullet wraps to a 4th line (height: 69px), collapsing bottom clearance to 0.8px or negative. 60 is a **vertical 3-line wrap ceiling per grid column**, not a width limit.
   - *Failure behavior:* Violations push the DP badge flush or out of bounds, violating Section B Rule 3.

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
