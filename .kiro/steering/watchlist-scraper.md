# Watchlist Scraper

How the company watchlist (`watchlist.md`) gets scraped for new postings — three passes:
the selector pass needs an LLM (selector decisions); the scrape and the flush are fully
scripted with no LLM and no human dialogs. All browser work goes through the shared
`ChromeInterface` (`.kiro/scripts/lib/chrome_interface.py`) in the "Scratch" tab group, so
`housekeeping.py --close-scratch` can always reap anything left behind.

## Data model

- `watchlist.md` → `## Companies` holds the vetted targets:
  `| Added | Company | URL | CSS Selector | Referee |`. **CSS Selector is the only column a
  script writes** — everything else is edited via `tracker.html`.
- `watchlist.md` → `## Scraped (watchlist)` is the working inbox, manual.md format
  `| Added | URL | Status |`. The title is bundled in the URL cell as `[Title](<url>)`.
  Status is empty until the row is filed in `tracker.html` — filing MOVES the row out of
  the inbox into manual.md's `## Entries` (Save to manual → `saved`, Applied → `applied`,
  Reject → `rejected` + reason), so the inbox only ever holds unfiled postings and the
  flush's "empty Status → too-old" rule never misfires.
- `applied.md` → `## Scraped-flushed (watchlist)` is the permanent dedup record:
  `| Rejected | Company | Role | URL | Reason | Comment |`. It is the seen-store for the scrape.

## Pick a CSS selector per company (LLM)

The LLM decides; `watchlist_selectors.py` is the deterministic probe + write-back tool:

1. Probe a candidate selector live:
   ```bash
   python3 .kiro/scripts/watchlist_selectors.py probe \
     --url "<careers-url>" --selector "a[data-automation-id='jobTitle']"
   ```
   It opens the URL in a Scratch tab, scrolls, dismisses consent modals, extracts every
   matching element, closes the tab, and prints `{ok, count, samples}` as JSON. Iterate:
   - **0 matches** → selector too imprecise (e.g. a bare `h3`)
   - **several matches per job card** → too broad
   - **exactly one match per card** → the target
2. Commit the winner into `## Companies`' CSS Selector cell (via `md_tables`, never a direct
   file edit):
   ```bash
   python3 .kiro/scripts/watchlist_selectors.py write \
     --company Xanadu --selector "a[data-automation-id='jobTitle']" --apply
   ```
   Only this command edits that column.

## Scrape the watchlist (scripted, no LLM)

```bash
python3 .kiro/scripts/watchlist_scrape.py --apply
```

For every `## Companies` row that has a CSS selector: open the careers URL, scroll to load
lazy lists, dismiss modals, extract job cards via the selector, resolve relative hrefs, and
append every entry not already tracked anywhere as `| Added | [Title](<url>) | Status |`
(with Status empty) to `## Scraped (watchlist)`.

**"Already tracked"** = the scraped table itself, the flushed table, every applied.md table,
and manual.md — so applied / saved / rejected / flushed roles are never re-surfaced, and a
row already in the inbox isn't duplicated. Dedup key is the ATS code of the resolved URL.

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
