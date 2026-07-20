---
name: simplify-tracker-sync
description: Syncs the user's Simplify.jobs application tracker into a local applied.md. Drives the already-open Chromium tab via the Tab Share extension (port 8766) to scroll-load all rows, extracts the rendered list, parses every application (title, company, location, applied/saved dates), and append-merges new rows into applied.md (never overwrites existing rows/comments; promotes saved→applied). Use when the user wants to sync/update/import what they applied to from Simplify, refresh applied.md, or pull their Simplify tracker.
metadata:
  author: yoav
  version: "1.0"
compatibility: Requires Chromium running with the Tab Share extension HTTP API on port 8766, Python 3, and the user logged into simplify.jobs.
---

# Simplify Tracker Sync

Pulls the full application list from the user's Simplify.jobs tracker and rebuilds a local `applied.md`. Simplify's tracker list is client-side rendered and **lazy-loads on scroll**, so the bare page fetch only returns the first ~25 rows — this skill scrolls the list container to load everything before extracting.

## When to Use

- "Sync what I applied to from Simplify" / "update applied.md from Simplify"
- "Import my Simplify tracker" / "pull my applications"
- Any routine job-search pass that needs an up-to-date `applied.md` for dedup.

## Prerequisites

- Chromium open with the **Tab Share extension** (job-search pref: Chromium = port 8766).
- The user logged into `https://simplify.jobs/tracker`.
- Probe the API first:
  ```bash
  for p in 8765 8766; do curl -s --connect-timeout 1 http://localhost:$p/tabs >/dev/null && echo "$p up"; done
  ```
  Chromium is 8766. If it's not up, ask the user to open Chromium / the tracker tab.

## Tab Share endpoints used

- `GET  /tabs` → list open tabs (find/confirm the tracker tab).
- `POST /eval` with `{"code": "<js>"}` → run JS in the page. Returns
  `{..., "result": {"ok": true, "value": <jsonable>}}`. **Note:** `/eval` does
  **not** await Promises — keep the JS synchronous and drive scrolling with
  repeated calls + `sleep`, not `async/await`.
- `POST /extract` with `{"url": "..."}` → rendered `{text, links, ...}` (only
  returns currently-rendered rows, so scroll first).

## Instructions

### Step 1: Confirm the tracker is open

```bash
curl -s http://localhost:8766/tabs
```
If `https://simplify.jobs/tracker` isn't listed, open it:
```bash
curl -s -X POST http://localhost:8766/open -H 'Content-Type: application/json' \
  -d '{"url":"https://simplify.jobs/tracker","groupName":"Job Search"}'
```
Give it a couple of seconds to render before continuing.

### Step 2: Scroll the list container to load all rows

The scrollable list is `div.flex-1.overflow-y-auto`. Drive it down in a loop
(synchronous JS per call), pausing between calls so new rows render. Repeat
until `appliedCount` (a cheap proxy for row count) stops growing:

```bash
# one scroll nudge
curl -s -X POST http://localhost:8766/eval -H 'Content-Type: application/json' \
  -d '{"code":"(function(){var s=[...document.querySelectorAll(\"*\")].find(e=>e.scrollHeight>e.clientHeight+200);if(s){s.scrollTop=s.scrollHeight;}return s?s.scrollTop+\"/\"+s.scrollHeight:\"none\";})()"}'

# row-count probe (run between scrolls; stop when it stabilizes)
curl -s -X POST http://localhost:8766/eval -H 'Content-Type: application/json' \
  -d '{"code":"(document.body.innerText.match(/Applied/g)||[]).length"}'
```
Practically: loop ~15–30 times with `sleep 0.4`–`1` between nudges, re-checking
the count every few iterations. The header also shows `N TOTAL JOBS` — use it as
the target so you know when all rows are loaded.

### Step 3: Extract the rendered list text

Grab the innerText of the list container (more reliable than `/extract` for this page):

```bash
curl -s -X POST http://localhost:8766/eval -H 'Content-Type: application/json' \
  -d '{"code":"document.querySelector(\".flex-1.overflow-y-auto\").innerText"}' \
  > /tmp/simplify_raw.json

python3 -c "import json;open('/tmp/simplify_tracker.txt','w').write(json.load(open('/tmp/simplify_raw.json'))['result']['value'])"
```
Sanity check: the number of `Applied` markers in the text should match the
`N TOTAL JOBS` count from the header (±1).

### Step 3b: Capture apply URLs — only for NEW rows

`applied.md` has an **Apply** column (a `[Apply](url)` link, like `shortlist.md`).
The Simplify list view doesn't expose URLs, but **clicking a row** loads its detail
and reveals the real ATS apply URL (as an external `<a href>` and via the row's
`?id=<uuid>`). Clicking through all ~130 rows is wasteful, so **only fetch URLs for
rows that are actually new** (not already keyed in the tables):

1. Ask the parser which records are new:
   ```bash
   python3 scripts/parse_tracker.py /tmp/simplify_tracker.txt --md "<ws>/applied.md" --report-new
   ```
   → JSON list of `{company, title, key}` for rows not yet present.
2. For each new row, click it in the list and read the external apply href, e.g.:
   ```bash
   curl -s -X POST http://localhost:8766/eval -H 'Content-Type: application/json' \
     -d '{"code":"(function(){var c=[...document.querySelectorAll(\"div\")].find(e=>/COMPANY.*TITLE/s.test(e.innerText)&&e.getBoundingClientRect().height<160);if(c){c.click();}var a=[...document.querySelectorAll(\"a[href]\")].map(x=>x.href).find(h=>/^https?:/.test(h)&&!/simplify\\.jobs|google|facebook|cloudflare|village|featurebase|ui-avatars/.test(h));return a||\"\";})()"}'
   ```
   (Match the card by its company+title text; give the detail a moment to render.)
   Strip `?ref=Simplify…` tracking params. Build a `{"company||title": url}` map.
3. Write the map to `/tmp/simplify_urls.json`.

If a **suspected manual/shorthand** title is what made a row look "new" and it might
actually match an existing row, the captured URL disambiguates it — the merge uses
URL as the primary dedup key (below), so a shorthand row and its canonical twin
collapse to one. Prefer this only when there's no clear title match already.

### Step 4: Merge into applied.md (append-only, URL-aware)

```bash
python3 scripts/parse_tracker.py /tmp/simplify_tracker.txt \
  --md "<workspace>/applied.md" \
  --urls /tmp/simplify_urls.json \
  --json /tmp/simplify_parsed.json \
  --date $(date +%F)
```
This is an **append-only merge** — it never rewrites `applied.md`. Dedup precedence:
**URL match** (if both sides have one) **> company+title**. It only:
- adds new rows (with their `[Apply](url)` link from the `--urls` map),
- **URL-dedups** a shorthand-vs-canonical collision (same job, different title → one row),
- promotes an existing **Saved** row to **Applied** when Simplify shows an applied
  date (carrying its comment + Apply link across),
- updates the synced-date header counts.

`--urls` is optional; without it the merge still works (title-keyed only, no links
for new rows). It requires `--md` to point at an **existing** `applied.md` with the
`## Applied` / `## Saved` tables. Summary to stderr: `+X applied, +Y saved, Z promoted,
N deduped by URL`, plus any `WARN:` near-duplicate lines.

### Step 5: What's preserved (everything you didn't sync)

Because the merge never regenerates the file, all existing content survives with
zero effort — **including comments on existing rows** and every hand-maintained
section:

- Existing **Applied**/**Saved** rows and their **Comment** cells — untouched
  (a re-reported role is never overwritten or duplicated). This includes rows
  added by hand to `## Applied` (e.g. migrated `[x]` shortlist rows) — they're
  matched by company+title and left alone.
- `## Rejected (migrated from shortlist [nope] rows)` — classified rejections.
- `### Referral channels` and `### Maintenance`.

The Simplify list view exposes no ATS IDs or referral channels; those live in the
hand-maintained sections and persist automatically. **Deletions in Simplify do
not propagate** — this is a dedup ledger; prune by hand only if asked.

### Step 6: Clean up & report

Remove scratch files (`/tmp/simplify_raw.json`, `/tmp/simplify_tracker.txt`,
`/tmp/simplify_parsed.json`). Do **not** leave validation-only tabs behind, but
the tracker tab is a legitimate keeper in the "Job Search" group. Report counts:
total / applied / saved, and call out anything odd.

## Notes & gotchas

- **Lazy loading is the whole point** — skipping the scroll step silently gives
  you only ~25 of N rows. Always verify the Applied-marker count vs `N TOTAL JOBS`.
- **User-entered shorthand titles**: when the extension fails to read a role,
  the user types a short/abbreviated title (e.g. a bare language name or a
  one-word role tag). Keep these verbatim — don't "correct" or guess the real title.
- **Company normalization**: "<prefix> - Example Co - Career Page" → `Example Co`;
  leading req numbers ("207 Example …") and trailing " logo" are stripped by the parser.
- **Duplicates**: Simplify itself can contain real duplicate rows (the same company
  appearing several times).
  Already-synced rows are matched by (company+title) and skipped, so re-syncing
  won't multiply them; but distinct Simplify rows sharing a company+title collapse
  to one on merge (acceptable for a dedup ledger).
- **Shorthand title changed between syncs**: the merge key is company+title, so if
  a shorthand title is edited later it won't match the old row and gets added as a
  new one. The parser emits `WARN: possible near-duplicate` when a new row shares a
  company with an existing similar title — reconcile by hand.
- **Append-only**: the parser never rewrites the file; it errors if `--md` is
  missing or lacks the `## Applied`/`## Saved` tables. Existing comments are safe.
- **`/eval` is synchronous** — no `await`. Promise-based scroll loops return
  `{}`. Drive iteration from bash instead.
- **Dedup downstream**: `applied.md` is the dedup source for the job-search
  playbook — after syncing, matching rows in `shortlist.md` should be removed/marked.
