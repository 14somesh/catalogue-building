# Catalog builder — frontend spec

Build a React + Vite app against the existing FastAPI backend. Screenshots accompany this spec — where a screenshot and this document disagree, ask rather than guessing.

Do not change anything in `src/`. The backend is complete and verified. This is frontend only.

---

## 1. Design tokens

Use these exact values. No other colours.

| Token | Hex | Used for |
|---|---|---|
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
- Amber is the site's identity. Teal is the action colour — every "go forward" button is teal.
- Text on a coloured background uses the darkest shade of that same colour family. Never black or grey.
- Two font weights only: 400 and 500. Never 600 or 700.
- Sentence case everywhere. Never Title Case, never ALL CAPS.
- Border radius: 8px on panels and buttons, 12px on the outer page container.
- No gradients, no drop shadows, no blur.
- Font sizes: 22px page heading, 15px card title, 14px body, 13px labels, 12px table text, 11px meta. Nothing below 11px.

---

## 2. Shell

A persistent top nav on every screen, amber background:
- Left: a small powerbank glyph plus the text `Catalog builder`. Clicking it returns to the landing page.
- Right: two links only — `Build catalogue` and `How to use`. The active one gets a cream pill background.

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

Full amber background. Centred:
- Three powerbank glyphs, the middle one teal and taller, the outer two smaller and pale.
- Heading: `Welcome to catalogue builder`
- Subline: `Hand over a price sheet. Get back a print-ready catalogue.`
- One teal button: `Start building` → routes to `/build`
- Below, three cream stat tiles: brands live, products, pages built. Pull real numbers from `GET /brands` and `GET /builds`.

---

## 5. Stage stepper

Sits at the top of every build screen. Five items: Ingest, Brochure, Collect, Approve, Build.
- Completed stages: teal text with a check icon, no pill.
- Current stage: amber pill, deep amber text, weight 500.
- Future stages: plain muted text.
- Chevron separators between items.

---

## 6. Stage 1 — Ingest

Shows nothing but the upload. No results, no preview.

- A dashed amber border box on cream, centred: upload icon, `Drop your price sheet here`, subline `Screenshot, PDF, Excel, CSV, or pasted text`, and a `Choose file` button in amber pale.
- Drag and drop must work, not just the button.
- Bottom right: teal `Read the sheet`.

Endpoints: `POST /uploads` then `POST /ingest`. Ingest returns a job id — poll `GET /jobs/{id}` or stream. Advance to stage 2 when the job completes.

---

## 7. Stage 2 — Brochure

Three blocks, in this order.

**Amber bar.** Brand dropdown on the left (editable — the inferred brand may be wrong). On the right, two numbers: rows read, duplicates.

**Parsed rows table.** Columns: Model, Display name, DP, MRP, and a trailing delete icon. Every cell is inline-editable on click. Header right: `Click any cell to edit`.
Duplicate rows get a cream row background, an amber warning triangle before the model name, and the text `same as row N` in the display name column.

**Brochure strip.** A dashed amber box on cream, single row: PDF icon, `Brand brochure`, `Optional`, and a `Choose PDF` button. Not a blocking question.

Footer: `Back` on the left, teal `Start collecting N rows` on the right, where N excludes deleted rows.

Endpoints: `POST /brands/{brand}/confirm` on continue, `POST /brands/{brand}/brochure` if a PDF is attached.

Do not show: column mapping, platform detection, powerbank illustrations, or a stats band. All were removed as noise.

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

Length limits — enforce client-side and show the count as the user types:
- Title: 20 characters
- Subtitle: 80 characters
- Each bullet: 60 characters

The 20-character title limit is real and tight. Show remaining characters live so it is never a surprise on submit. The server rejects overruns with the limit and given length; surface that message verbatim.

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

Static page. A one-line intro, then five numbered steps with a coloured circle each — amber for stages you act on (1, 2, 4), grey for automatic ones (3, 5).

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
