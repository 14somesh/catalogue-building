# Catalog builder — frontend spec

Build a React + Vite app against the existing FastAPI backend. Screenshots accompany this spec — where a screenshot and this document disagree, ask rather than guessing.

Do not change anything in `src/`. The backend is complete and verified. This is frontend only.

---

## 1. Design tokens

Use these exact values. No other colours.

| Token | Hex | Used for |
|---|---|---|
| Page background | `#FDFBF7` | Warm off-white filling entire viewport |
| Amber | `#EF9F27` | Top nav, stage banners, primary surface |
| Amber dark | `#BA7517` | Icons and borders on amber |
| Amber deep | `#412402` | Text on amber |
| Amber mid | `#633806` | Secondary text on amber |
| Amber pale | `#FAC775` | Secondary buttons on amber |
| Cream | `#FAEEDA` | Panels, warning rows, upload zones |
| Teal | `#0F6E56` | Every primary action button |
| Teal light | `#E1F5EE` | Text on teal, approved badge background |
| Teal pale | `#9FE1CB` | Muted text on teal |
| Red | `#A32D2D` | Failure states only |
| Grey 50 | `#F1EFE8` | Image placeholder backgrounds |
| Grey 400 | `#888780` | Muted text |

Rules:
- The page background is `#FDFBF7` (warm off-white), filling the entire viewport. Never dark. App is light only.
- Amber is only the top nav and the landing hero. Nowhere else. No page-level amber anywhere else.
- Amber is the site's identity. Teal is the action colour — every "go forward" button is teal.
- Text on a coloured background uses the darkest shade of that same colour family. Never black or grey.
- Two font weights only: 400 and 500. Never 600 or 700.
- Sentence case everywhere. Never Title Case, never ALL CAPS.
- Border radius: 8px on panels, buttons, tables, and cards. There is no rounded outer page container.
- No gradients, no drop shadows, no blur.
- Desktop type scale:
  - Landing heading: 34px, weight 500
  - Landing subline: 17px
  - Landing stat tile number: 30px, its label 13px
  - Page heading on inner pages: 22px
  - Section heading: 17px
  - Body text: 15px
  - Table text and labels: 13px
  - Meta text: 12px
  - Buttons: 14px
  - Nothing below 12px anywhere.

---

## 2. Shell

- The app fills the browser window edge to edge. No outer floating card, no outer border radius, no margins.
- Persistent top nav on every screen, amber background, spanning the full window width edge to edge with no rounded corners and no gap above or beside it:
  - Left: a small powerbank glyph plus the text `Catalog builder`. Clicking it returns to the landing page.
  - Right: two links only — `Build catalogue` and `How to use`. The active one gets a cream pill background.
- Inside the nav and inside each page, centre the content in a container with max-width 1100px and 32px horizontal padding. The background stays full width; only the content is constrained.
- Content starts 28px below the nav. Pages do not leave a large empty void below the content — the `#FDFBF7` page background fills it naturally.
- Default route: opening the app lands on `/` (the landing page), not `/build`.

There is no "Welcome" nav link. The landing page is reached via the logo.

---

## 3. Routes

| Route | Screen |
|---|---|
| `/` | Landing |
| `/build` | Build flow — hosts stages 1 to 5 |
| `/how-to-use` | Static instructions |

Within `/build`, the stage is app state, not a URL. Refreshing returns to the current stage based on the brand's row statuses.

---

## 4. Landing page

- The amber hero fills the remaining viewport height — `min-height: calc(100vh - navHeight)` — with its content vertically centred. On a short window it grows with the content instead of clipping. Directly below the nav, edge-to-edge. No void below the hero.
- Centred within the 1100px container:
  - Three powerbank glyphs, the middle one teal and taller, the outer two smaller and pale.
  - Heading: `Welcome to catalogue builder` (34px, weight 500)
  - Subline: `Hand over a price sheet. Get back a print-ready catalogue.` (17px)
  - One teal button: `Start building` (14px) → routes to `/build`
  - Below, three cream stat tiles: brands live, products, pages built. Pull real numbers from `GET /brands` and `GET /builds`. Stat tiles have 20px padding, 16px gap, 30px numbers, and 13px labels.

---

## 5. Stage stepper

Sits at the top of every build screen. Five items: Ingest, Brochure, Collect, Approve, Build.
- Completed stages: teal text with a check icon, no pill.
- Current stage: amber pill, deep amber text, weight 500.
- Future stages: plain muted text.
- Chevron separators between items.

---

## 6. Stage 1 — Ingest

Pre-upload metadata and sheet upload zone.

1. **Brand and Category Inputs (Above Drop Zone):**
   - Two required fields side-by-side above the drop zone:
     - **Brand:** Free text, required. Autocompletes against existing brands in the sheet (`GET /brands`) so a re-run matches an existing brand exactly rather than introducing a spelling duplicate.
     - **Category:** Dropdown of existing categories in the sheet (`GET /categories`), plus an `+ Add new category` option which reveals an inline text input. Required.
   - Both Brand and Category must be filled and a file uploaded before `Read the sheet` is enabled.
   - Brand and Category are passed directly to `POST /ingest` in the payload. The LLM extracts rows without overriding the typed brand or category.

2. **Upload Zone:**
   - A dashed amber border box on cream, centred: upload icon, `Drop your price sheet here`, subline `Screenshot, PDF, Excel, CSV, or pasted text`, and a `Choose file` button in amber pale.
   - Drag and drop works across `.xlsx`, `.xls`, `.csv`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.txt`.
   - Bottom right: teal `Read the sheet`.

3. **Validation of Extracted Rows:**
   - Evaluates row completeness (each row must have a clean product name, a DP, and an MRP):
     - **All complete:** Advances directly to Stage 2.
     - **Some incomplete:** Advances to Stage 2, marking incomplete rows and highlighting missing fields in amber so they can be filled in inline.
     - **Low confidence / Unusable (< 50% usable or 0 rows):** Stays on Stage 1. Explains plainly what was found and what is needed (products with both a dealer price and an MRP). Renders a review table of whatever rows were found so the user can see what went wrong, with an option to choose a different file or retry.
   - Incomplete rows are never silently dropped.

Endpoints: `POST /uploads`, `POST /ingest` (with `brand` and `category`). Ingest returns a job id — poll `GET /jobs/{id}` or stream `/jobs/{id}/stream`.

---

## 7. Stage 2 — Brochure & Ingest Review

Three blocks, reflecting Stage 1 inputs:

**Amber bar.**
- Brand control on the left (pre-filled from what was entered in Stage 1, still editable).
- Category label displayed beside the brand (e.g. `Category: Powerbank`).
- On the right, two numbers: rows read, duplicates.

**Parsed rows table.**
- Columns: Model, Display name, DP, MRP, and a trailing delete icon. Every cell is inline-editable on click.
- Missing DP or MRP cells get an amber cell treatment (`#FAEEDA` background, `#EF9F27` border) and can be clicked to fill in the missing price.
- Incomplete rows cannot be included until their required DP and MRP values are supplied.
- Duplicate rows get a cream row background, an amber warning triangle before the model name, and the text `same as row N` beneath the model name.
- Table data is plain text: `#2C2C2A` at 13px, weight 400.

**Brochure strip.**
- A dashed amber box on cream, single row: PDF icon, `Brand brochure`, `Optional`, and a `Choose PDF` button.

Footer: `Back` on the left, teal `Continue with N rows` on the right, where N counts **only complete rows**. Disabled if complete count is 0.

Endpoints: `POST /brands/{brand}/confirm` on continue (passing `category`), `POST /brands/{brand}/brochure` if a PDF is attached.

---

## 8. Stage 3 — Collect

Two states.

**Running.** An amber panel with the brand name, `N of M`, a progress bar (cream track, teal fill), and one line of current activity. Below it, a table: Product, Result, Source. Rows fill in as they complete — Ready in teal with a check, failures in red with a cross, the current row on cream with `Running`, and the rest muted as `Waiting`. Footer: `Stop` on the left; the forward button is present but visually disabled until the run ends.

**Finished.** The amber panel becomes a summary: runtime, brand, and two numbers — found, not found.

If anything failed, a cream block comes next, before the success table:
- Heading `N not found` with `Retry, or leave them out` on the right
- One line per failure: product name left, plain-language reason right
- Two buttons: `Retry these N` in amber pale, and `Add a URL myself`

Then the found table: Product, Source, Image. Image quality shows `Good` in teal or `Low quality` in amber.

Footer: `Back`, and teal `Continue with N rows`. There is no separate skip button — continuing simply drops the failures.

Endpoints: `POST /brands/{brand}/collect`, `GET /jobs/{id}/stream` for live progress, `POST /jobs/{id}/cancel` for Stop, `POST /brands/{brand}/retry`, `POST /products/{id}/source` for the manual URL.

Use SSE. Fall back to polling `GET /jobs/{id}` if the stream fails.

---

## 9. Stage 4 — Approve

The most important screen. Cards, not a spreadsheet grid — you are judging whether a product card reads right, so it should resemble the printed card.

**Amber bar.** `N rows to check` on the left. On the right, the approved count and an `Approve all` button in amber pale.

**One card per product.** Layout: square image thumbnail on the left with a `Replace` button beneath it; content on the right.

Content, in order:
- Product title, weight 500, with a status badge on the far right — `Approved` in teal on teal-light, or `Needs a look` in deep amber on amber pale
- Subtitle in muted text
- The four bullets, one per line
- Meta line: `DP 1,331 | MRP 3,999 · portronics.com` in muted 11px
- Action buttons on the same line, right-aligned: `Edit`, `Skip`, plus `Re-run` only on problem rows

Clean rows get a hairline border. Problem rows get a 1px amber border, an amber-pale `Replace` button, and a cream callout above the meta line stating the problem in plain language.

**Do not show specs.** They are not rendered on the printed card. Editing them would do nothing.

Editing is inline within the card. Editable fields: title, subtitle, and the four bullets, plus DP and MRP. Nothing else.

Card layout limits:
- **Title: Physical WIDTH limit (291px container), NOT a character count.** Single-line nowrap (`32px 700 Space Grotesk`, `-0.02em` tracking) including sequence prefix `01.`. A fixed number is false — wide glyphs (`WMWMWMWMW`) overflow at 9 chars, while narrower titles (`Rapid Electra 20`) fit at 16 chars. Never reintroduce a character count. The UI renders a live proportional fit indicator (e.g. `82% width`) with a visual capacity bar that turns red on overflow (`102% (exceeds by 5px)`), disables Save, and surfaces the server's rejection message verbatim.
- **Subtitle: 80 characters max.** This is a vertical 2-line wrap ceiling against the fixed 290px card height, not a width limit. At 81+ chars, subtitle wraps to a 3rd line (49px), vertically overflowing the card and pushing the DP badge out of bounds.
- **Each bullet: 60 characters max.** This is a vertical 3-line wrap ceiling per column in the 2-column grid (125.5px column width). At 60+ chars, a bullet wraps to a 4th line (69px), vertically overflowing the card.

Footer: `Back`, and teal `Build N products` counting only approved rows.

Endpoints: `GET /brands/{brand}/review` to populate, `PATCH /products/{id}` for edits, `POST /products/{id}/image` and `DELETE` for images, `POST /products/{id}/approve`, `POST /products/{id}/skip`, `POST /brands/{brand}/approve` for Approve all.

Sending `null` for a field clears the override and restores the collected value. Offer this as a `Reset` control on any field marked overridden — `GET /brands/{brand}/review` returns an `is_overridden` map per row.

---

## 10. Stage 5 — Build

Deliberately sparse. Two blocks.

**Amber bar.** `Your catalogue is ready` with a meta line: `27 pages · 5 brands · 40 products · 9.4 MB`. On the right, a teal `Download PDF` button.

**Cream strip.** `Add another brand to this catalogue?` with an amber-pale `Start a new brand` button that returns to stage 1 with the existing catalogue intact.

Footer: `Back to approve` and `Rebuild`.

Do not show page thumbnails. They were considered and cut.

Endpoints: `POST /build`, `GET /builds` for the download URL and history.

---

## 11. How to use

Static page sitting directly on the `#FDFBF7` page background. A one-line intro, then five numbered steps with a coloured circle each — amber for stages you act on (1, 2, 4), grey for automatic ones (3, 5). The steps sit directly on the background, with no coloured panel behind them.

Below, a cream callout: `One brand at a time. If someone else is running a brand, you'll be told who.`

---

## 12. Behaviour rules

**Long jobs.** Ingest, collect and build all return a job id. Never block the UI. Poll or stream, and show progress.

**Brand lock.** `POST` to a brand already running returns HTTP 409. Show the message plainly — name the conflicting job — and do not retry automatically.

**Errors.** The API returns clear JSON messages. Show them as written. Never show a stack trace, never replace a specific message with a generic one.

**Empty states.** Write an invitation, not an apology. No "Nothing here yet."

**Copy.** Verb-first buttons. No "please", no exclamation marks, no "successfully". Say what happened, then what to do.

**One primary action per screen**, bottom right, in teal. Everything else is secondary.

---

## 13. Out of scope

No authentication. No user accounts. No dark mode. No mobile layout — this is a desktop tool. No spec editing. No page thumbnails. No live log stream.
