# Job Search Playbook

Core workflow: search → triage → sync → migrate.

## Pre-flight — save the browser first <a id="pre-flight"></a>

`tracker.html` only writes its `.md` files to disk when the user clicks **Save changes** in
the browser. Edits still browser-only are invisible to every CLI script that reads those
files from disk — and a script's own writes can clobber them. Before any stage below that
reads or writes tracker data (all of Stage 0, and every search/triage run that calls the
scripts), check the browser state with the Tab Share extension:

```bash
python3 .kiro/scripts/check_browser_saved.py
```

It finds every open `tracker.html` tab and asks the page whether its Save button is
enabled. If any tab has unsaved edits it prints the tab and **waits for you to save them in
the browser**, re-checking after each Enter; it only proceeds once every tab is saved —
"Wait until you save? [Y/n]" defaults to yes (Enter keeps waiting), and only an explicit
`n` proceeds despite unsaved edits. Exit 0 = all saved / you confirmed; 2 = aborted. Do not
skip this step.

`no_llm_sweep.py` runs this same check itself before any stage executes.

## Stage 0 — Maintenance <a id="stage-0"></a>

### 0a — Fold thoughts.md into prefs <a id="stage-0a"></a>
Read `thoughts.md` bullets and merge into `job-search-prefs.md`, then clear it.

### 0b — Sync Simplify tracker → applied.md <a id="stage-0b"></a>
```bash
python3 .kiro/skills/simplify-tracker-sync/scripts/saved_sync_cli.py --apply
```
Calls Simplify's list API, merges into `applied.md`, pushes local statuses back.

### 0c — File manual applied/rejected URLs <a id="stage-0c"></a>
`manual.md` rows have status `saved`, `applied`, or `rejected` — a rejection carries its
reason in the row's Comment column (set via the reason picker in `tracker.html`). Rows
transfer automatically when the URL structure alone yields both company and role
(`migrate_resolved.py --manual`): `applied` rows move to `## Applied`, `rejected` rows move
to `## Rejected` (reason classified from the Comment, dated with the drain day), then the
row is cleared. Rows whose URL doesn't encode both fields are left in place for this stage's 
LLM pass (it can read the page); rejected rows still sitting in the inbox stay indexed by 
`dedup_index`, so the role is never re-suggested.

### 0d — Promote manual saved URLs <a id="stage-0d"></a>
`saved` rows in `manual.md` are left in place by default — promoting one to a shortlist
candidate needs a tier + Notes classification, which is judgment, not something to do
silently. Resolve a `saved` row into `shortlist.md` only on explicit request by reading
the job description.

### 0e — Migrate resolved shortlist rows <a id="stage-0e"></a>
```bash
python3 .kiro/scripts/migrate_resolved.py --apply
```
Moves `[x]` rows to `## Applied`, `[nope]` rows to `## Rejected`.

### 0f — Liveness sweep <a id="stage-0f"></a>
```bash
python3 .kiro/scripts/liveness_sweep.py --apply
```
Removes dead links and stale postings from shortlist.

**0b + 0e + 0f in one call** (the `no-llm-sweep` profile in `run-config.md`, no LLM judgment
needed for any of it):
```bash
python3 .kiro/scripts/no_llm_sweep.py             # applies for real (default)
python3 .kiro/scripts/no_llm_sweep.py --dry-run   # preview: writes/pushes skipped
```
**Applies by default** — deliberately the one script that inverts the workspace's usual
dry-run-first convention, so this maintenance sweep can't drift from being run. Reads the
stage list from `run-config.md`'s `no-llm-sweep` profile itself (not a hardcoded copy) and
runs them in the order that matters: 0b before 0e so 0e's dedup sees what 0b just did; 0f
last since it deletes shortlist rows. `--dry-run` still skips writes and Simplify pushes,
but 0b's read of the live tracker list happens either way — it always talks to
`api.simplify.jobs` to build the plan, dry run or not.

## Stage 1 — Searches <a id="stage-1"></a>

### 1a — Web search <a id="stage-1a"></a>
`remote_web_search` across target domains + location.

### 1b — Regional boards <a id="stage-1b"></a>
BuiltIn, university boards, aggregators. Client-side rendered → use browser `/extract`.

### 1c — Company watchlist <a id="stage-1c"></a>
Read `watchlist.md` — companies tracked via `tracker.html`, each with a careers URL. Always
the browser, never a raw webfetch: the boards are JS-rendered (Workday, Lever, Applytojob,
SPA career sites).

**Selector-bearing companies are scripted.** For every `## Companies` row with a CSS
selector filled in, `watchlist_scrape.py` opens the careers URLs in parallel and extracts the job cards
by that selector (automatically following pagination if a `Next Page` selector is present) — read-only, nothing is written:
```bash
python3 .kiro/scripts/watchlist_scrape.py --json -   # new postings as JSON on stdout
```
It prints every new posting (`company`, `title`, `url`) not already tracked and leaves
`watchlist.md` alone — nothing lands in `## Scraped (watchlist)`. "Already tracked" covers
scraped, flushed and rejected rows, so nothing previously seen is re-surfaced. Review the
listings it prints and file keepers straight into the triage path. (The
`## Scraped (watchlist)` inbox and its flush belong to the separate scrape routine, not
this search.)

**Manual open-then-extract** (companies without a selector, or to double-check a board) —
**open every URL first, then extract**, two phases so all tabs load in parallel instead of
serially:
1. Loop over the watchlist URLs, `open_tab` each into "Scratch", collect the `tabId`s
2. Extract each tab by its `tabId` (`extract(tab_id=…)` — see §1d) and read the postings
```python
import sys; sys.path.insert(0, '.kiro/scripts/lib')
import chrome_interface as CI
ci = CI.ChromeInterface()
urls = [row['url'] for row in parse_watchlist('watchlist.md')]   # or paste the table URLs
tabs = [ci.open(u) for u in urls]                                # phase 1: all opens, no wait
import time; time.sleep(3)
for tid in tabs:                                                 # phase 2: extract each
    ci.scroll(tid)
    ci.close_modals(tid)
    print(ci.extract(tid)['text'])
```

These are already vetted targets, so go deeper than generic web search: scan for in-scope
roles from `job-search-prefs.md` (embedded / kernel / systems / DSP / junior-level, Toronto
area) and feed keepers — scripted or manual — into the same triage + ATS-verify path as the
other search stages (§1g).

### 1d — BuiltIn <a id="stage-1d"></a>
```bash
python3 -c "
import sys; sys.path.insert(0,'.kiro/scripts/lib')
import chrome_interface as CI
ci = CI.ChromeInterface()
tid = ci.open_loaded('https://builtintoronto.com/jobs/dev-engineering/entry-level')
ci.scroll(tid)
ci.close_modals(tid)
import json; print(json.dumps(ci.extract(tid)['links'], indent=2))
"
```
`/extract` with a bare `url` and no `tabId` reads whatever tab is currently **active**, not
the URL given — open the tab first and pass its `tabId` explicitly (see `tab_share.extract`'s
docstring). This was a real bug in this exact playbook line, found by actually running it.

### 1e — LinkedIn keyword searches <a id="stage-1e"></a>
```bash
python3 .kiro/scripts/linkedin_harvest.py "https://www.linkedin.com/jobs/search/?keywords=<kw>&location=Toronto%2C%20Ontario%2C%20Canada&f_E=2&sortBy=DD"
```

### 1f — LinkedIn recommended <a id="stage-1f"></a>
```bash
python3 .kiro/scripts/linkedin_harvest.py "https://www.linkedin.com/jobs/collections/recommended/"
```

### 1g — Triage + ATS verify <a id="stage-1g"></a>
For each candidate:
1. Company+title screen — drop obvious no's
2. Read full JD — responsibilities + requirements (years bar)
3. Verify on ATS — confirm live, get apply URL
4. Dedup — check against `applied.md` + `shortlist.md`

### 1h — Filter & tier <a id="stage-1h"></a>
Apply hard filters from `job-search-prefs.md`, assign tier.

## Stage 2 — Wrap-up <a id="stage-2"></a>

### 2a — Open keepers, close Scratch <a id="stage-2a"></a>
Open confirmed keepers into "Job Search" group, close "Scratch" group.

### 2b — Bump headers <a id="stage-2b"></a>
Update "Last searched" / "Last synced" dates.

---

## Quick reference

**Routine launcher** (PyQt5 picker → emits the prompt for the agent to run; reads
run-config.md, never writes it; search shows a validated stage-plan):
```bash
python3 routine_launcher.py
```

**Reject all open shortlist rows** (asks for a reason + comment; marks every `[ ]` row
`[nope]` — `[x]`/`[nope]` rows are left alone. Then `migrate_resolved.py --apply` moves
them to `## Rejected`; the same action exists as the **Reject all open** button on
tracker.html's shortlist page):
```bash
python3 .kiro/scripts/reject_shortlist.py          # interactive
python3 .kiro/scripts/reject_shortlist.py --reason not-interested --comment "flush" --yes
```

**Add to shortlist** (`--candidates` takes a JSON FILE PATH, not inline JSON — write to a
temp file first):
```bash
echo '[{"company":"X","role":"Y","url":"...","tier":1,"notes":"✅","evidence":"2 yrs"}]' > /tmp/cand.json
python3 .kiro/scripts/shortlist_add.py --candidates /tmp/cand.json --apply
```

**Check duplicates** (same file-path requirement):
```bash
echo '[{"company":"X","title":"Y","url":"..."}]' > /tmp/cand.json
python3 .kiro/scripts/dedup_index.py --candidates /tmp/cand.json
```

**Browser (Tab Share, port 8766):** prefer `.kiro/scripts/lib/chrome_interface.py` over raw curl or tab_share —
it gets the open→wait→extract-by-tabId sequence right, dismisses modals, and scrolls lazy lists.
```bash
python3 -c "
import sys; sys.path.insert(0,'.kiro/scripts/lib')
import chrome_interface as CI
ci = CI.ChromeInterface()
tid = ci.open_loaded('...', wait=3)
ci.scroll(tid)
ci.close_modals(tid)
print(ci.extract(tid))
print(ci.tabs())
"
```

**Files:** `shortlist.md` (candidates), `applied.md` (applications + rejected + saved), `thoughts.md` (inbox), `manual.md` (URL inbox), `tracker.html` (UI).

