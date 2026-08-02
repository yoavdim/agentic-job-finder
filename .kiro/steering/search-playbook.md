# Job Search Playbook

Core workflow: search → triage → sync → migrate.

## Stage 0 — Maintenance <a id="stage-0"></a>

### 0a — Fold thoughts.md into prefs <a id="stage-0a"></a>
Read `thoughts.md` bullets and merge into `job-search-prefs.md`, then clear it.

### 0b — Sync Simplify tracker → applied.md <a id="stage-0b"></a>
```bash
python3 .kiro/skills/simplify-tracker-sync/scripts/saved_sync_cli.py --apply
```
Calls Simplify's list API, merges into `applied.md`, pushes local statuses back.

### 0c — Process manual.md URLs <a id="stage-0c"></a>
`manual.md` rows have status `applied` or `saved` (no `rejected`). Only `applied` rows
auto-transfer, and only when the URL structure alone yields both company and role
(`migrate_resolved.py --manual`): they move to `## Applied` in `applied.md`, then the row
is cleared. `saved` rows are **left in place by default** — promoting one to a shortlist
candidate needs a tier + Notes classification, which is judgment, not something to do
silently. Resolve a `saved` row into `shortlist.md` only on explicit request.

### 0d — Migrate resolved shortlist rows <a id="stage-0d"></a>
```bash
python3 .kiro/scripts/migrate_resolved.py --apply
```
Moves `[x]` rows to `## Applied`, `[nope]` rows to `## Rejected`.

### 0e — Liveness sweep <a id="stage-0e"></a>
```bash
python3 .kiro/scripts/liveness_sweep.py --apply
```
Removes dead links and stale postings from shortlist.

**0b + 0d + 0e in one call** (the `no-llm-sweep` profile in `run-config.md`, no LLM judgment
needed for any of it):
```bash
python3 .kiro/scripts/no_llm_sweep.py             # applies for real (default)
python3 .kiro/scripts/no_llm_sweep.py --dry-run   # preview: writes/pushes skipped
```
**Applies by default** — deliberately the one script that inverts the workspace's usual
dry-run-first convention, so this maintenance sweep can't drift from being run. Reads the
stage list from `run-config.md`'s `no-llm-sweep` profile itself (not a hardcoded copy) and
runs them in the order that matters: 0b before 0d so 0d's dedup sees what 0b just did; 0e
last since it deletes shortlist rows. `--dry-run` still skips writes and Simplify pushes,
but 0b's read of the live tracker list happens either way — it always talks to
`api.simplify.jobs` to build the plan, dry run or not.

## Stage 1 — Searches <a id="stage-1"></a>

### 1a — Web search <a id="stage-1a"></a>
`remote_web_search` across target domains + location.

### 1b — Regional boards <a id="stage-1b"></a>
BuiltIn, university boards, aggregators. Client-side rendered → use browser `/extract`.

### 1c — BuiltIn <a id="stage-1c"></a>
```bash
python3 -c "
import sys; sys.path.insert(0,'.kiro/scripts/lib')
import tab_share as TS
tid = TS.open_tab('https://builtintoronto.com/jobs/dev-engineering/entry-level', group_name='Scratch')
import time; time.sleep(3)
print(TS.extract(tab_id=tid)['text'])
"
```
`/extract` with a bare `url` and no `tabId` reads whatever tab is currently **active**, not
the URL given — open the tab first and pass its `tabId` explicitly (see `tab_share.extract`'s
docstring). This was a real bug in this exact playbook line, found by actually running it.

### 1d — LinkedIn keyword searches <a id="stage-1d"></a>
```bash
python3 .kiro/scripts/linkedin_harvest.py "https://www.linkedin.com/jobs/search/?keywords=<kw>&location=Toronto%2C%20Ontario%2C%20Canada&f_E=2&sortBy=DD"
```

### 1e — LinkedIn recommended <a id="stage-1e"></a>
```bash
python3 .kiro/scripts/linkedin_harvest.py "https://www.linkedin.com/jobs/collections/recommended/"
```

### 1f — Triage + ATS verify <a id="stage-1f"></a>
For each candidate:
1. Company+title screen — drop obvious no's
2. Read full JD — responsibilities + requirements (years bar)
3. Verify on ATS — confirm live, get apply URL
4. Dedup — check against `applied.md` + `shortlist.md`

### 1g — Filter & tier <a id="stage-1g"></a>
Apply hard filters from `job-search-prefs.md`, assign tier.

## Stage 2 — Wrap-up <a id="stage-2"></a>

### 2a — Open keepers, close Scratch <a id="stage-2a"></a>
Open confirmed keepers into "Job Search" group, close "Scratch" group.

### 2b — Bump headers <a id="stage-2b"></a>
Update "Last searched" / "Last synced" dates.

---

## Quick reference

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

**Browser (Tab Share, port 8766):** prefer `.kiro/scripts/lib/tab_share.py` over raw curl —
it gets the open→wait→extract-by-tabId sequence right (see §1c: `/extract` with a bare `url`
silently reads the active tab instead, not the URL passed).
```bash
python3 -c "
import sys; sys.path.insert(0,'.kiro/scripts/lib')
import tab_share as TS
tid = TS.open_tab('...', group_name='Scratch')
import time; time.sleep(3)
print(TS.extract(tab_id=tid))
print(TS.tabs())
"
```

**Files:** `shortlist.md` (candidates), `applied.md` (applications + rejected + saved), `thoughts.md` (inbox), `manual.md` (URL inbox), `tracker.html` (UI).

