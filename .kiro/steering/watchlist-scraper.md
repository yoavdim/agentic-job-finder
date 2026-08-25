# Watchlist Scraper

How the company watchlist (`watchlist.md`) gets scraped for new postings — three passes:
the selector pass needs an LLM (selector decisions); the scrape and the flush are fully
scripted with no LLM and no human dialogs. All browser work goes through the shared
`ChromeInterface` (`.kiro/scripts/lib/chrome_interface.py`) in the "Scratch" tab group, so
`housekeeping.py --close-scratch` can always reap anything left behind.

## Data model

- `watchlist.md` → `## Companies` holds the vetted targets:
  `| Added | Company | URL | CSS Selector | Next Page | Referee |`. **CSS Selector and Next Page are the only columns a
  script writes** — everything else is edited via `tracker.html`.
- `watchlist.md` → `## Scraped (watchlist)` is the working inbox, manual.md format
  `| Added | URL | Status |`. The title is bundled in the URL cell as `[Title](<url>)`.
  Status is empty until the row is filed in `tracker.html` — filing MOVES the row out of
  the inbox into manual.md's `## Entries` (Save to manual → `saved`, Applied → `applied`,
  Reject → `rejected` + reason), so the inbox only ever holds unfiled postings and the
  flush's "empty Status → too-old" rule never misfires.
- `applied.md` → `## Scraped-flushed (watchlist)` is the permanent dedup record:
  `| Rejected | Company | Role | URL | Reason | Comment |`. It is the seen-store for the scrape.

## Fill Selectors for the Watchlist (LLM Task)

**The Goal:** For any company missing selectors in `watchlist.md`, your task is to find and record two CSS selectors:

1. **CSS Selector (Job Cards):** A selector that uniquely identifies each individual job listing card/link on the company's careers page.
2. **Next Page:** A selector that identifies the "Next Page" pagination button. If the site has infinite scroll, a "Load More" button, or does not use pagination (all jobs are on one page), you must explicitly write `none`.

### Step 1: Find Candidate Selectors

Because these are SPA sites, you cannot use `curl` to view the HTML. You must inspect the live DOM by injecting JS via `ChromeInterface`. 
Write a temporary Python script in your workspace that uses `ChromeInterface.open_loaded()` to open the careers page and `ChromeInterface.eval()` to query the DOM for candidate elements. For example:
- **For Job Cards:** Query for `<a>` tags with job titles.
- **For Next Page:** Query for buttons or links containing text like "Next" or `aria-label="next"`.

### Step 2: Verify the Job Card Selector

Once you have candidate selectors, you must verify the job card selector matches exactly one element per card:

```bash
python3 .kiro/scripts/watchlist_selectors.py probe \
  --url "<careers-url>" --selector "a[data-automation-id='jobTitle']"
```

It opens the URL in a Scratch tab, extracts matching elements, and prints `{ok, count, samples}`.

- **0 matches** → selector too imprecise
- **several matches per job card** → too broad
- **exactly one match per card** → the target

### Step 3: Commit the Selectors

Write BOTH selectors to the table using `watchlist_selectors.py write`:

```bash
python3 .kiro/scripts/watchlist_selectors.py write \
  --company "Company Name" \
  --selector "a[data-automation-id='jobTitle']" \
  --next-page "button[aria-label='next']" \
  --apply
```

*(If there is no next page button, pass `--next-page "none"` so the system knows it was checked).* Only this command safely edits the table without breaking markdown formatting.

## Scrape the watchlist (scripted, no LLM)

```bash
python3 .kiro/scripts/watchlist_scrape.py --apply
```

For every `## Companies` row that has a CSS selector:

1. Open the careers URL in a Scratch tab.
2. Scroll to load lazy lists and dismiss modals.
3. Extract job cards via the CSS selector, and resolve relative hrefs.
4. If a `Next Page` selector exists, click it, wait, and repeat steps 2-3 (up to 5 extra pages, stopping early if no new unique items are found).
5. All tabs are run concurrently via a thread pool for extreme speed.
6. Finally, append every entry not already tracked anywhere as `| Added | [Title](<url>) | Status |`
   (with Status empty) to `## Scraped (watchlist)`.

**"Already tracked"** = the scraped table itself, the flushed table, every applied.md table,
and manual.md — so applied / saved / rejected / flushed roles are never re-surfaced, and a
row already in the inbox isn't duplicated. Dedup key is the ATS code of the resolved URL.

**"Listing removed" auto-flush:** During the scrape, if a scraped row's listing is no longer
present on the live company careers page (and the row is still unmarked, and its company's
scrape succeeded), it is immediately removed from the inbox and flushed to
`## Scraped-flushed (watchlist)` with reason `listing-removed` rather than waiting for
the `too-old` manual flush.

Dry run by default; `--apply` writes. `--json FILE` dumps the per-company plan.

## Flush the inbox (scripted, no LLM)

```bash
python3 .kiro/scripts/watchlist_flush.py --apply
```

Moves every **unmarked** scraped row (Status empty) out of `## Scraped (watchlist)` into
`## Scraped-flushed (watchlist)` in applied.md:

- **Rejected** = today (the flush date)
- **Company** = matched from `## Companies` by URL host (blank when nothing matches)
- **Role** = the title split out of `[Title](<url>)`
- **URL** = the listing URL, re-rendered as `[Role](<url>)`
- **Reason** = `too-old` (unacted watchlist listings)
- **Comment** = `watchlist scrape <added-date>, unmarked at flush (auto)`

Rows are filed in tracker.html (Save to manual / Applied / Reject — each MOVES the row into
manual.md's `## Entries`, carrying the status and, for a rejection, the reason). Rows that
reach this flush are exactly the ones never filed. The flush is idempotent:
rows already present in the flushed table (by ATS code) are not re-flushed — they're just
removed from the inbox if they somehow reappear.

## When

Run the selector pass, the scrape, and the flush whenever the watchlist is due for a check
(the selector pass only when a company needs its first selector, or a board's layout
changed). Sequence matters: scrape after selectors, flush after scrape — and after you've
had a chance to mark any keepers in tracker.html.
