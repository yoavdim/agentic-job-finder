---
name: simplify-tracker-sync
description: Syncs the user's Simplify.jobs application tracker into a local applied.md, and harvests the user's saved-job searches (e.g. the "Toronto" search) into a JSON result set. Both drive the already-open Chromium tab via the Tab Share extension (port 8766). The tracker sync scroll-loads all rows, extracts the rendered list, parses every application (title, company, location, applied/saved dates), and append-merges new rows into applied.md (never overwrites existing rows/comments; promotes saved→applied; keeps the Saved table an exact mirror of Simplify — rows no longer saved there are dropped). The saved-search harvest opens the search in a disposable tab, scrolls the client-side Typesense-backed feed to completion (default top ~300), and returns {company,title,location,job_type,work_arrangement,experience,id}. Use when the user wants to sync/update/import what they applied to from Simplify, refresh applied.md, pull their Simplify tracker, or pull/harvest results for a saved Simplify search.
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
- "Pull my saved search" / "harvest my Simplify search" (e.g. the "Toronto" saved search)
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
  repeated calls + `sleep`, not `async/await`. Without a `tabId` it runs in the
  **active** tab — `simplify_actions.py` always sends `tabId` (resolved to the
  tracker tab via `/tabs`) so its API calls land on simplify.jobs, not whatever
  tab happens to be focused.
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

### Step 3b: Capture apply URLs + Simplify IDs — only for NEW rows

`applied.md` tables carry the job links: an **Applied** row's **Apply** cell holds the
`[Apply](url)` link **plus** the `[Simplify](https://simplify.jobs/tracker?id=<uuid>)`
tracker link when the id was captured; the **Saved** table has a dedicated **Simplify**
column for the tracker link (added automatically on the next sync via `migrate_schema`,
or ahead of time with `--migrate-schema`).
The Simplify list view doesn't expose URLs, but **clicking a row** loads its detail
and reveals the real ATS apply URL (as an external `<a href>`) and the tracker
entry's `?id=<uuid>`. Clicking through all ~130 rows is wasteful, so **only fetch
metadata for rows that are actually new** (not already keyed in the tables):

1. Ask the parser which records are new:
   ```bash
   python3 scripts/parse_tracker.py /tmp/simplify_tracker.txt --md "<ws>/applied.md" --report-new
   ```
   → JSON list of `{company, title, key}` for rows not yet present.
2. For each new row, click it in the list and read the external apply href **and**
   the row's `?id=<uuid>` (the Simplify application id — the action handle for
   pushing status back later):
   ```bash
   curl -s -X POST http://localhost:8766/eval -H 'Content-Type: application/json' \
     -d '{"code":"(function(){var c=[...document.querySelectorAll(\"div\")].find(e=>/COMPANY.*TITLE/s.test(e.innerText)&&e.getBoundingClientRect().height<160);var h=location.href;if(c){c.click();}var a=[...document.querySelectorAll(\"a[href]\")].map(x=>x.href).find(h=>/^https?:/.test(h)&&!/simplify\\.jobs|google|facebook|cloudflare|village|featurebase|ui-avatars/.test(h));var m=(h+location.href).match(/[?&]id=([0-9a-fA-F-]{36})/);return {id:m?m[1]:\"\",url:a||\"\"};})()"}'
   ```
   (Match the card by its company+title text; give the detail a moment to render.)
   Strip `?ref=Simplify…` tracking params.
3. Build a `{"company||title": {"id": "<uuid>", "url": "<url>"}}` map and write it to
   `/tmp/simplify_urls.json`. Legacy bare-URL strings are still accepted by the
   parser, but without the `id` the row can't be updated in Simplify later.

If a **suspected manual/shorthand** title is what made a row look "new" and it might
actually match an existing row, the captured URL disambiguates it — the merge uses
URL as the primary dedup key (below), so a shorthand row and its canonical twin
collapse to one. Prefer this only when there's no clear title match already.

### Step 4: Merge into applied.md (Applied append-only, Saved exact mirror)

```bash
# 1) DRY RUN first (this is the default — nothing is written)
python3 scripts/parse_tracker.py /tmp/simplify_tracker.txt \
  --md "<workspace>/applied.md" \
  --urls /tmp/simplify_urls.json \
  --json /tmp/simplify_parsed.json \
  --date $(date +%F) \
  --total-jobs <N from the "N TOTAL JOBS" header>

# 2) then re-run with --apply to write
python3 scripts/parse_tracker.py ... --apply
```

**Always pass `--total-jobs <N>`.** The Saved table is an exact mirror rebuilt from the
capture, so a capture that under-scrolled the lazy-loading list would DELETE the rows it
missed, along with their comments, apply URLs and Simplify ids. Three guards, all
overridable with `--force`:

| guard | fires when | result |
|---|---|---|
| `--total-jobs N` | fewer records parsed than the tracker header reports | refused, exit 2 |
| empty / all-applied capture | Saved table is non-empty but the capture has no still-saved rows | refused, exit 2 |
| `--max-drop-frac` (default 0.30) | the mirror would drop more than that share of Saved rows | refused, exit 2 |

The dry run **names every row the mirror would drop** (`- mirror drops <company> — <title>`),
so check that list before applying. `--apply` writes every file it touches **together or
not at all**, and snapshots each one into `.kiro/backups/` first — these data files are
gitignored, so that snapshot is the only undo.

**The Saved list lives here and nowhere else.** The old `## Tier 6` mirror in
`shortlist.md` is gone: `shortlist.md` is purely user-curated and this sync never writes to
it. `## Saved` carries a `Status` column (blank/`saved` | `applied` | `rejected`) holding
transient intent set in `tracker.html`, which `saved_sync` consumes on the next run — see
Step 4b.

This is an **append-only merge for the Applied table** — it never rewrites existing
Applied rows. The **Saved table is an exact mirror** of the Simplify tracker: it is
rebuilt from the current records on every sync, so rows that are no longer saved on
Simplify (rejected/deleted there) disappear. Dedup precedence:
**URL match** (if both sides have one) **> company+title**. It only:
- adds new rows (with their `[Apply](url)` link from the `--urls` map, and the
  Simplify id folded into the Applied row's Apply cell / written to the Saved row's
  Simplify column),
- **URL-dedups** a shorthand-vs-canonical collision (same job, different title → one row),
- promotes an existing **Saved** row to **Applied** when Simplify shows an applied
  date (carrying its comment + Apply link + Simplify id across),
- updates the synced-date header counts,
- keeps the Saved table an **exact mirror**: still-saved rows are re-emitted with
  their comment/URL/id, rows no longer saved on Simplify are dropped. Rejections
  aren't tracked locally — a rejected entry just stops appearing as "saved".
`--urls` is optional; without it the merge still works (title-keyed only, no links
for new rows). It requires `--md` to point at an **existing** `applied.md` with the
`## Applied` / `## Saved` tables. Summary to stderr: `+X applied, +Y saved (mirror keeps K,
drops D), Z promoted, N deduped by URL, W with Simplify id`, the name of every row the
mirror drops, the backup paths, plus any `WARN:` near-duplicate lines.

**Dedup keys are org-namespaced.** `ats_code()` returns e.g. `greenhouse:lyft:123` and
`workday:nvidia:jr1998773`, because a bare per-ATS id collides across organisations
(greenhouse job `123` exists at many companies, and stripping a Workday query string left
every NVIDIA link reducing to the shared site name). A URL with no recognisable ATS id is a
**weak** code and is not used for identity at all — several roles can share one careers-page
link — so those rows fall through to company+title matching. This logic lives in
`.kiro/scripts/lib/md_tables.py`; `parse_tracker.py` imports it directly rather than keeping
a synced copy (an earlier copy had already drifted from the original).

### Step 4b: Push local Saved statuses back to Simplify (`saved_sync.py`)

`## Saved`'s `Status` column is a request, not stored state. `scripts/saved_sync.py` reads
it, pushes it, records the outcome, and the mirror rebuild then reflects reality.

**The order is load-bearing — do not reorder it.**

```
capture (read-only) → read local statuses → classify → report → execute → write
```

Interleaving reads and mutations loses data in two specific ways, both of which happened in
the design this replaced:

- A row that is **both** user-rejected and stale gets acted on twice: the first delete
  succeeds, the second fails against a dead id, and the row is then treated as
  "push failed, retry next pass" forever.
- Anything deleted **before** it is classified loses the local record of *why* it left. It
  is absent from the next capture, so nothing writes it into `## Rejected`, and
  `dedup_index` re-suggests it later.

`plan()` is therefore a pure function of (snapshot, local rows, today) — no I/O, no remote
calls — so the guarantee is structural rather than a convention.

**Precedence, evaluated once per row:**

| # | condition | action |
|---|---|---|
| 1 | snapshot says APPLIED or beyond | application wins — **never delete**; promote to `## Applied` |
| 2 | local status `applied` | push `mark_applied` |
| 3 | local status `rejected` | delete (SAVED-gated) + record the user's reason |
| 4 | saved longer than `--max-saved-age-days` (60) | delete (SAVED-gated) + record `too-old` ` (auto)` |
| 5 | otherwise | stays in the mirror |

3 outranks 4 so an explicitly rejected row keeps the user's own reason rather than being
relabelled by the automatic rule. 1 outranks everything.

**Safety properties, all of which have tests:**

- Every delete **re-verifies remote status immediately before firing** — the snapshot may
  be stale. If the row has since progressed to applied, the delete is refused and it
  becomes an applied row instead.
- Only an **exact SAVED** authorises a delete. Status is read as the *furthest* state the
  application reached, not the last event in `status_events`, so an out-of-order reply
  cannot authorise deleting a real application. Unrecognised status codes block.
- A **failed or unverifiable push keeps its Status** so the next pass retries; the mirror is
  built from outcomes, not optimism. A row with no Simplify id is reported blocked.
- More than `--max-auto-delete` (5) stale auto-deletes in one run is **refused** without
  `--force`. The stale rule trims stragglers; a big batch means a backlog or a bug.
- `too-old` is a **liveness** reason, so an auto-rejected role can return if it reposts.

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
hand-maintained sections and persist automatically. **Deletions in Simplify propagate
to the Saved table** (it's an exact mirror — a rejected row simply disappears on the
next sync). The **Applied** table is append-only and is never pruned by the merge.

### Step 6: Clean up & report

Remove scratch files (`/tmp/simplify_raw.json`, `/tmp/simplify_tracker.txt`,
`/tmp/simplify_parsed.json`). Do **not** leave validation-only tabs behind, but
the tracker tab is a legitimate keeper in the "Job Search" group. Report counts:
total / applied / saved, and call out anything odd.

## Saved-search harvest (pull a saved search's results)

Harvests the full result set of one of the user's **saved Simplify searches**,
resolved by **label only** (criteria live in the account). The feed at
`https://simplify.jobs/jobs` is fully client-side: page 1 renders ~24
`div[data-testid="job-card"]` cards with **no ids**, and deeper pages load via a
**Typesense `POST js-ha.simplify.jobs:443/multi_search`** XHR as the results
container scrolls. The script drives the user's logged-in Chromium to read the
rendered cards and XHR-intercepts the Typesense responses for the posting ids.

```bash
python3 scripts/simplify_search.py "Toronto"            # ~top 300 jobs
python3 scripts/simplify_search.py "Toronto" --max-jobs 50
python3 scripts/simplify_search.py "Toronto" --max-rounds 6   # explicit scroll cap
```

Output (JSON to stdout): `{total_header, count, url, saved_search, jobs}` with
each job `{company, title, location, job_type, work_arrangement, experience,
posted, id}` where `id` is the posting uuid (the `?jobId=<uuid>` detail-link
handle) and `posted` is the Typesense `start_date` (unix epoch → `YYYY-MM-DD`).
Caveats: ~24 page-1 rows come back with **no id** (they render before the capture
hook installs); a warning on stderr flags an incomplete harvest. The script finds
or opens a Simplify tab, resolves the saved-search query from
`localStorage["jobsSavedSearches"]`, runs the harvest in a disposable **Scratch**
tab it closes in `finally`, and never mutates the user's own tabs or searches.

`--max-age-days N` (default 300 via `--max-jobs`) drops jobs whose `posted` date
is known and older than `N` days; rows with no `posted` date are kept ("no date
is fine — don't penalize"). The drop report goes to stderr. `posted` semantics
(verified against the ATS): it is **when the posting entered Simplify's index /
when the ATS last touched it** — for genuinely new postings it equals the ATS
post/creation date exactly (Caseware Lever `createdAt` 2026-07-30 = posted
2026-07-30; Konrad Greenhouse `first_published` 2024-06-06 = posted 2024-06-06),
but for long-lived postings it can lag the ATS `first_published` by months
(HeyGen: ATS first_pub 2024-01-29, posted 2024-06-05) or equal the ATS
`updated_at` (Tenstorrent: ATS first_pub 2025-03-15, posted 2026-07-14). Treat it
as a conservative freshness signal, not the ATS's original post date — the ATS
page read during the JD/liveness hop stays the authority for "still live".

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
- **Merge semantics**: the Applied table is append-only (existing rows/comments are
  never rewritten; existing Saved comments/URLs/ids survive a rebuild too, carried
  across by company+title). The Saved table is an **exact mirror** — rows no longer
  saved on Simplify are dropped on the next sync. It errors if `--md` is
  missing or lacks the `## Applied`/`## Saved` tables.
- **`/eval` is synchronous** — no `await`. Promise-based scroll loops return
  `{}`. Drive iteration from bash instead.
- **Dedup downstream**: `applied.md` is the dedup source for the job-search
  playbook — after syncing, matching rows in `shortlist.md` should be removed/marked.
