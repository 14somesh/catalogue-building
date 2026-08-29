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

---

## SECTION B — Human Visual Checks (Post-Build Review Checklist)

These visual quality standards must be verified by a human reviewer inspecting the compiled PDF before client distribution:

1. **Card & Panel Uniformity:**
   - Image tile and details panel heights must be strictly identical (`235px`) across all products (`01 click 10`, `02 aura`, `03 giga`, `04 lucid`, `05 major ultra`), regardless of subtitle or bullet copy length.

2. **Image Scaling & Framing Consistency:**
   - Product cutouts must fill approximately 80% of their square ivory tile (`235px × 235px`) with comfortable, balanced padding.
   - No product should appear noticeably smaller, oversized, or clipped.

3. **No Overflow or Text Clipping:**
   - Product images must never overflow or bleed outside their ivory tile.
   - Subtitles, feature bullets, and product names must never overlap or clip.

4. **DP Badge Positioning & Clearance:**
   - The gold **DP** badge (`₹X,XXX incl. GST`) must sit cleanly inside the ivory panel with comfortable bottom padding (never flush against or touching the card's bottom edge).

5. **Bullet Column Alignment:**
   - In the 2-column feature bullet grid, each row track must start at the exact same vertical baseline across both left and right columns.

6. **Full-Bleed Canvas & Page Edges:**
   - Teal background (`#1B3A47`) must reach the very edge of every A4 page with zero white border lines or margin slivers along the top, bottom, or sides.

7. **Razor-Sharp Image Resolution:**
   - Raster images and product cutouts must remain crisp and sharp at fit-to-page zoom, rendered from high-resolution source masters (`1200×1200px+`) with `deviceScaleFactor: 2`.

8. **Brand Divider Page Impact:**
   - Brand divider pages must feature the brand name in massive, bold white uppercase typography filling nearly the full width of the page, vertically centered and left-aligned to the card margin.
