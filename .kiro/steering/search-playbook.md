# Job Search Playbook (process reference)

The reusable **method** for building and maintaining `shortlist.md` / `applied.md`. This file is the *how*. The *who/what* — profile, career goals, filter & priority criteria, browser pref, ATS formats — lives in `job-search-prefs.md`. When this file says "filter" or "prioritise", apply the criteria from `job-search-prefs.md`.

## 1. Search — sources & how to hit them
- **remote_web_search**: primary discovery. Run 3–5 *parallel* queries per pass, one per domain/company cluster. Always include your target location(s) (from `job-search-prefs.md`) + year.
- **BuiltIn**: has real filters. Use the regional BuiltIn board from `job-search-prefs.md` and its level paths (`/jobs/dev-engineering/entry-level`, `/mid-level`). Pagination is `?page=N` — but it's **client-side**, so:
  - `web_fetch` strips query params and `curl` of the bare URL only gives page 1.
  - To read deep pages: open them via the browser extension, then `/extract` (see §4). `curl -A <UA> "...?page=N" | grep -oE '/job/[a-z0-9-]+/[0-9]+'` works for getting job slugs/IDs.
- **Aggregators**: the general job boards plus any regional/university boards are listed in `job-search-prefs.md` (BuiltIn, Indeed, Teal, Simplify, LinkedIn, etc.). Treat as leads; verify on the ATS.
- **Verify every role** on its real ATS before listing (greenhouse, ashby, recruitee, workday, applytojob, company site). Confirm it's live + grab the real apply URL. ATS URL formats are in `job-search-prefs.md`.
- **Liveness check for new finds = same rigour as the §8 sweep. HTTP 200 is NOT proof it's live.** Aggregators (builtin, teal, ycombinator, cryptocurrencyjobs, etc.) are JS-rendered and serve removed listings as 200 with a normal title, injecting "Sorry, this job was removed" / "no longer available" via JS. Before adding ANY new row from these sources, open it in the browser and `/extract` the rendered text, then reject it if it hits a removal marker (see §8 for the phrase list). Never add a role that's already dead. Direct-ATS links can trust status more, but still sanity-check the rendered page when in doubt.
- **LinkedIn** (login-walled, but readable via the logged-in Chromium session — the `remote_web_search` tool still can't read it, so use the browser):
  - Open a filtered jobs search, e.g. `https://www.linkedin.com/jobs/search/?keywords=<kw>&location=<your-location-string>&f_E=2&sortBy=DD` (use the URL-encoded location string from `job-search-prefs.md`; `f_E=2` = entry level; `f_E=1` internship, `3` associate; `sortBy=DD` = newest; add `f_TPR=r604800` for past-week). One keyword cluster per search.
  - **Use `/extract`, NOT `/eval`** — LinkedIn's CSP blocks `/eval` (`unsafe-eval` denied). `/extract` injects a function so it bypasses CSP and returns `{text, links}`.
  - Job cards come from the `links` array as `.../jobs/view/<id>/` with the title in the link text. The canonical role URL is `https://www.linkedin.com/jobs/view/<id>/`.
  - **The search URL mutates after load** (LinkedIn appends `currentJobId=…`); target the *current* URL from `/tabs`, not the one you opened with.
  - **Only ~7–9 cards render per extract — you MUST paginate or you'll badly undercount** (a search showing "80 results" gives ~8 per page). LinkedIn blocks `/eval` so you can't scroll-load; page via the URL `&start=N`. **Don't assume the page size** — measure it from page 0 and step by that. Don't judge a search "thin" off page 1; that's a *you* bug, not LinkedIn.
  - **Use the harvester:** `.kiro/scripts/linkedin_harvest.py "<search URL>"` (in this workspace) does all of this — measures page size, pages in **parallel batches of 5 Scratch tabs**, dedups, closes tabs (with a `finally` cleanup), and prints `{count, total_header, jobs:[{id,title,company,location}]}`. Prefer it over hand-paging.
  - Filter noise: consultancy/body-shops (blocklist in `job-search-prefs.md`) and localized-language title dupes (a translated title that's the same posting as an English one) repeat across pages — dedup by (title, company) and skip alumni-count lines.

### Triage rule (applies to ALL sources, not just LinkedIn)
- **Two-stage filter — decide by company+title first; only then read the full listing.**
  1. **Company+title screen:** drop the obvious no's (consultancy body-shops, pure web/mobile/GenAI-labeling, senior/staff, remote-only, already-applied, localized-language dupes). Flag DS roles ⚠️ (never drop). Keep anything that *might* fit your target domains (from `job-search-prefs.md`) OR is a plausible junior SWE at a real product company.
  2. **Read the full listing for every survivor before recommending.** NEVER add a role to `shortlist.md` on title alone — open it (reader: `.kiro/scripts/read_jobs.py <id...>`, parallel batches of 5) and confirm the actual content. Title lies (e.g. "Android OS Engineer" = Linux-kernel/C++ OS work = keeper; many "Software Engineer" = JS/web = drop).
  - **Read the two sections that actually decide it** (names vary): **"What you'll do" / "Responsibilities" / "The role"** (→ is it your domain?) and **"Minimum/Basic/Required Qualifications" / "Requirements" / "What you bring"** (→ the hard gate: years, degree, must-have skills). The intro/"About the company" blurb is NOT enough — a role can read domain-relevant up top but demand 8+ yrs or a PhD in the requirements. Capture the **years-of-experience** bar and note it in the row (e.g. "asks 2–5 yrs").
  - **The description is lazy/slow-rendered — it's a TIMING issue, not a "see more" gate.** The full JD text is already in the DOM (no click needed; `/extract` reads it read-only). When the reader returns only page chrome / "sections not found", the page just hadn't finished loading (or LinkedIn briefly rate-limited after heavy harvesting). **Fix = retry `/extract` on that id alone with a longer wait (~10–12s), 1–2 times.** Do NOT click "see more" or add click-based tooling for this — it's unnecessary and less safe. Never drop OR add a survivor whose requirements you never actually saw; if it won't render after retries, open it for the user rather than guessing.
- **Dedup by ATS/company+title against `applied.md` before adding** — e.g. a hit that's already in `applied.md`, so skip.
- Reader script `.kiro/scripts/read_jobs.py` opens job-view ids in a 5-tab Scratch pool, extracts the responsibilities + requirements sections, flags removed listings, and closes the batch.
  - Still **verify each promising role on its real ATS** and grab the ATS apply URL when possible — LinkedIn is discovery; many roles are "Easy Apply" or repost aggregator listings. Apply the same soft-404/removal check. Note LinkedIn's own `/jobs/view/<id>/` as the link only when there's no better ATS URL.

## 2. Filter & prioritise
Apply the **hard filters** and **prioritisation** from `job-search-prefs.md`:
- Excluded roles → drop them, and log the cut + reason in the `shortlist.md` "Excluded" section (prevents re-suggesting + supports dedup across runs).
- Everything else → keep, and assign a tier per the priority ranking (never exclude a low-priority item — just tier it lower).

## 3. Browser skill — open & read pages
- Tab Share extension HTTP API. Probe: `for p in 8765 8766; do curl -s --connect-timeout 1 http://localhost:$p/tabs >/dev/null && echo "$p up"; done`.
- **Chromium = port 8766** (user's pref for job apps). Firefox = 8765.
- Open into a group: `curl -s -X POST http://localhost:8766/open -H 'Content-Type: application/json' -d '{"url":"...","groupName":"Scratch"}'` for validation, or `"Job Search"` for keepers (see the two-group rule below).
  - One URL per curl call — multi-URL bash loops got mangled by the shell. Issue separate calls (parallel is fine).
- Read rendered page text (for JS/client-side pages BuiltIn-style): `curl -s -X POST http://localhost:8766/extract -d '{"url":"..."}'` → returns `{url,title,text,links,...}`. This is how to scrape paginated/JS pages the fetch tool can't.
- Run JS in a page: `curl -s -X POST http://localhost:8766/eval -d '{"code":"..."}'` → `{result:{ok,value}}`. **`/eval` is synchronous — no `await`/Promises**; drive iteration (e.g. scrolling) from bash with repeated calls + `sleep`.
- List open tabs to verify what actually opened: `curl -s http://localhost:8766/tabs`.
- **Two tab groups — keep disposable and keeper tabs separate:**
  - **"Scratch"** — every validation/liveness/`/extract` tab goes here (`/open` with `groupName:"Scratch"`). Reuse tabs where practical (`/navigate {tabId}`) rather than opening one per URL. These are disposable.
  - **"Job Search"** — ONLY confirmed keepers (genuinely new roles for the user to review/apply). Never auto-close these.
- **Closing tabs (`/close`) is SAFETY-GATED** — a tab closes only if it matches all given criteria. Required fields: `expectHost` (URL hostname must equal it) and `expectGroup` (group title; `null`/`""` = ungrouped, `"*"` = skip the group check). Selector is `tabId`(s) and/or `url`(s) (with optional `prefix:true`), OR — the clean case — a concrete `expectGroup` alone closes **every tab in that group**. Returns `{closed:[...], rejected:[...]}` (rejects list host/group mismatches so you get feedback, not silent no-ops).
  - **End of a pass:** close the whole Scratch group in one call — `/close {"expectGroup":"Scratch","expectHost":"<host>"}` per host, or just leave the Scratch group for reuse next run. Because keepers live in "Job Search", a Scratch-group close can never touch them.
  - URLs mutate (LinkedIn `currentJobId`, Simplify `?id=`, applytojob `/confirm/`) — prefer closing by `tabId` (from `/tabs`) or by whole group, not by the URL you opened with.

## 4. Build / edit the table
- File: `shortlist.md`. Tiers 1→5 by priority: 1 = best-fit domain in your primary location, junior; 2 = general junior SWE + secondary domains; 3 = adjacent/verify; 4 = commutable nearby location; 5 = lowest-priority type. Use the exact tier ranking in `job-search-prefs.md`. Referral leads + Notes + Excluded at the end. **Tier by priority axes (location → level → target-type → domain-fit), NOT by topic label** — e.g. a junior best-domain role in your primary location belongs in Tier 1 even if it's tagged with an off-topic label.
- Columns: `(blank checkbox) | Added | Company | Role | Location | Apply link | Notes | Comment`. Status box `[ ]` open → `[x]` applied → `[nope]` rejected (literal in tables, not clickable). **Added** = the date the row was added to the shortlist (`YYYY-MM-DD`, today's date on insert) — always fill it for new rows, independent of the listing's posting date. Apply link as clickable markdown: `[Apply](url)` (use `[Careers site](url)` when only a search page exists). The **Comment** column is user-filled from `tracker.html` (often the rejection reason).
- Notes legend: ✅ good fit · ⚠️ verify level/location · 🔗 referral.
- **Posting date:** when a listing states when it was posted (an explicit date or a relative "N days ago" / "posted last week"), record it in the Notes as `📅 posted <YYYY-MM-DD>` — convert relative ages to an absolute date using the current date. If no date is shown, add nothing (no date is fine — don't penalize, per prefs §Prioritisation).
- Keep an **Excluded** section documenting cuts + reasons.
- **`tracker.html`** is an HTML+JS view over these `.md` files (File System Access API in Chromium, surgical single-cell edits). It renders the tables, toggles the status box, edits Comments, and hides `[nope]` rows. Row-click drives a Chrome split-pane via Tab Share (`splitViewId` partner detection).

## 5. Simplify tracker sync → `applied.md` (append-only merge)
The user tracks applications in Simplify.jobs. Syncing **merges** new rows into `applied.md` — it does **not** rebuild the file. Existing rows, their comments, and every other section are left byte-for-byte untouched.

- **Skill:** `simplify-tracker-sync` (in `.kiro/skills/` in this workspace) automates the whole thing. Activate it for any "sync/update/import what I applied to" request. It documents the full procedure + ships `scripts/parse_tracker.py`.
- **Mechanism (gist):** the tracker list at `https://simplify.jobs/tracker` is client-side and **lazy-loads on scroll**, so a plain fetch/`/extract` only returns the first ~25 rows. Drive the already-open Chromium tab via the Tab Share API (port 8766):
  1. `POST /eval {"code": ...}` to scroll `div.flex-1.overflow-y-auto` to the bottom repeatedly until the `Applied`-marker count stops growing (target = the `N TOTAL JOBS` header). Remember `/eval` is synchronous (§3) — loop scroll nudges from bash with `sleep` between them.
  2. `POST /eval` to grab the list container's `innerText`.
  3. **URL capture for new rows only** — `parse_tracker.py … --report-new` lists rows not yet in the tables; click *only those* rows in Simplify to read their real ATS apply URL (the list view hides it; a row-click reveals an external `<a href>` + a `?id=<uuid>`). Strip `?ref=Simplify…`. Build a `{"company||title": url}` map.
  4. `parse_tracker.py <text> --md applied.md --urls <map>` → **append-only merge** into the existing `## Applied` / `## Saved` tables, writing an `[Apply](url)` link (like `shortlist.md`) in the **Apply** column for new rows.
- **Merge rules (append-only):** dedup precedence **URL match > company+title**.
  - Row already in `## Applied` (by URL or company+title) → **left untouched** (keeps comment + link). Never overwritten/duplicated.
  - Row already in `## Saved` but Simplify now shows an applied date → **promoted** to `## Applied`, carrying comment + Apply link; removed from Saved.
  - Brand-new row → inserted newest-first, with its `[Apply](url)` from the URL map.
  - Everything else (hand-added `[x]` rows in `## Applied`, the Rejected/referral/Maintenance sections, all comments/links) is preserved because the file is never regenerated. No sentinel/marker needed.
  - **Deletions do NOT propagate** — removing a row in Simplify won't remove it here (dedup ledger; keeping history is intended). Prune by hand if needed.
- **Post-sync fuzzy reconcile (new entries only):** after the merge, review the parser's `WARN: possible near-duplicate` lines — these only ever concern the **rows just added** this sync (never a re-scan of the whole table). For each warned new row, fuzzy-check it (company + approximate title, ignoring shorthand/level noise) against existing rows; if it's really the same job as an existing entry, merge them by hand (keep the row with the URL/comment, delete the other) — **confirm with the user** when the match is ambiguous. Rows with no warning are left alone; never fuzzy-re-dedup already-present entries.
- **Manual/shorthand titles + URL dedup:** some titles are **user-entered shorthand** (e.g. a bare language name or a one-word role tag) — keep verbatim. A stable shorthand matches itself on re-sync (no dup). The real win: the **Apply URL is the primary dedup key**, so a shorthand row and a canonical-title migration of the *same job* collapse to one row via URL even though the titles differ. If a shorthand title *changes* and there's no URL to match on, the parser prints `WARN: possible near-duplicate` — reconcile by hand. When you suspect a shorthand row is actually a known role, capture its URL to disambiguate (but skip if there's already a clear title match). Simplify's own real duplicate rows (a company can legitimately appear several times) are kept as-is; don't dedup unless asked.

## 6. Cross-referencing `applied.md` → `shortlist.md`
After a sync, reconcile the shortlist so applied roles aren't re-suggested:
- Match shortlist rows to applied entries by title + company (exact, ATS-slug, or obvious abbreviation). Mark hits `[ ]`→`[x]` and append `— **APPLIED <date>**` to Notes.
- **Applied always wins over `[nope]`.** If a shortlist row is `[nope]` (even "listing-removed") but Simplify shows it applied, the application is ground truth — flip it to `[x]`, append `— **APPLIED <date>**`, and clear the stale rejection comment. (Assume: applied first, delisted later.) Don't ask; just do it and note it.
- For **manual-shorthand** applied entries, infer the matching shortlist role but **ask the user to confirm** before marking (the shorthand is lossy). Note the original shorthand in parens.
- A role in Simplify's **Saved (not applied)** section is *not* applied — leave it `[ ]`.
- Watch for near-duplicate roles that are actually distinct (different ATS slug / level) — don't over-match.

## 7. Migrate resolved shortlist rows (`[nope]` rejected + `[x]` applied)
On the **next search pass (stage 0)**, move resolved rows out of `shortlist.md` into `applied.md`, then delete them from `shortlist.md`. Two kinds:

**A) `[x]` applied rows → the `## Applied` table in `applied.md`.**
- **Prefer the user's entry / keep its comment.** If the shortlist row has a **Comment**, that comment must land in `applied.md` regardless of whether Simplify already tracks the role.
  - If the role already matches a row in `## Applied` (company + title, exact / ATS-slug / obvious abbrev): don't add a duplicate — if the shortlist row has a comment, **write it onto the existing matched row** (don't overwrite a non-empty comment already there; if both exist, append `; `). No comment → just drop the shortlist row.
  - If the role is **not** already in `## Applied`: add a row directly to `## Applied` with the shortlist row's own apply link in the **Apply** column: `| <applied-date> | Company | Role | Location | [Apply](url) | <comment> |`. Applied date = the `APPLIED <date>` from Notes if present, else the `Added` date. (The append-only sync then matches it by URL/company+title — never duplicated.)
- The shortlist `[x]` row is removed after handling.

**B) `[nope]` rejected rows → `applied.md` Rejected table.**
The user rejects shortlist rows in `tracker.html` by setting the status box to `[nope]` (row hides in the UI, stays in the file). Usually they add *why* in the **Comment** column. Migrate every `[nope]` row into the **Rejected** table, then delete it from `shortlist.md`.

**Classify the reason** from the Comment (read intent, not just keywords) into one of:
- `link-broken` — the crawl/link failed; couldn't reach the listing.
- `listing-removed` — listing gone / role closed / no longer posted.
- `too-old` — listing older than the recency cutoff (see prefs §Prioritisation).
- `not-qualified` — user doesn't meet the bar (keep the *why* from the comment).
- `not-interested` — role/company not appealing (keep the *why*).
- `sketchy-site` — untrustworthy job-listing site.
- `unknown` — Comment empty/absent.
- `other` — Comment present but fits none of the above (preserve it verbatim).

**Migration procedure:**
1. Find all `[nope]` rows across every tier in `shortlist.md`.
2. For each: classify (above), then append a row to the `applied.md` **Rejected** table, carrying the shortlist row's apply link: `| <date> | Company | Role | Location | [Apply](url) | <reason-code> | <original comment verbatim> |`. Date = today (the migration date) unless the comment implies otherwise.
3. Remove the migrated row from `shortlist.md`.
4. If a comment is ambiguous between codes, prefer the more specific/actionable one; if genuinely unclear, use `other` and keep the full comment. Don't invent reasons the user didn't give — empty → `unknown`.
5. **Rejected rows are NOT a blanket blacklist — the reason type decides.** Two classes:
   - **Judgment rejections** (`not-interested`, `not-qualified`, `sketchy-site`) = a decision about the *role/company itself* → **don't re-suggest**; treat like Excluded.
   - **Liveness rejections** = facts about a *dead/stale link*, NOT verdicts on the role → **re-suggest freely if the role turns up live again** (relisted, new req, or found on another source). A dead link never blacklists a role. Specifically:
     - `listing-removed` — the posting was pulled/closed. If it's reposted or a new req opens → valid candidate again.
     - `link-broken` — the crawl/URL failed (404, DNS, etc.). The role may have been live the whole time; a working link found later → valid candidate.
     - `too-old` — only the *dated posting* was stale. A **fresh repost with a new date resets the clock** → valid candidate again.
   - **How to re-add:** make a **NEW shortlist row** for the live posting (new apply URL, today's `Added` date, note "relisted"). Do **not** revive/edit the old Rejected entry — the old posting really is dead; leave it in the Rejected table as history. The new row is a fresh, distinct posting that happens to be the same role.
   Use recurring judgment reasons (e.g. many `sketchy-site` from one aggregator) to tighten future queries; liveness reasons are just history, not filters.
6. **Auto vs manual:** rows the user rejected (`[nope]`) are *manual* rejections. Rows removed by the liveness sweep (§8) are *auto* rejections — append ` (auto)` to the Comment so they're distinguishable, e.g. `| ... | link-broken | dead apply URL (auto) |`.

## 8. Pre-search liveness & staleness sweep (stage 0, after §5/§6/§7, before new searches)
Once `[x]`/`[nope]`/Simplify reconciliation is done, re-validate the **still-open rows** (`[ ]`) in `shortlist.md` before searching for new ones. Only check open rows — skip `[x]`, `[nope]`, and already-migrated rows.

**Check age FIRST, then liveness.** If a row would be removed for age anyway, don't bother probing its link — just remove it as `too-old`. Only run the liveness probe on rows that survive the age cut.

For each open row:
- **Too old (check first)**: if a posting date is known (from Notes `📅 posted`, the listing, or the `Added` date as a fallback) and it's past the recency cutoff (prefs §Prioritisation — clearly >1 month), remove as `too-old` — skip the liveness check entirely. (Undated rows aren't penalized; `Added` is the fallback age signal.)
- **HTTP status is NOT enough — soft-404s are the trap.** Aggregators (builtin, teal, ycombinator, etc.) are **JS-rendered**: a removed listing still returns **HTTP 200** with a normal `<title>`, and the "removed" notice is injected by JavaScript — a `curl`/status probe will wrongly call it alive. (This exact shortcut caused a whole batch of removed BuiltIn listings to be missed once.) So:
  - **Direct ATS links** (greenhouse, applytojob, ashby, workday, recruitee, company site): HTTP status is usually honest — 404/410/DNS-fail/redirect-to-careers-home → `link-broken`. Still open + read rendered text if the status looks alive but stale.
  - **Aggregators + any suspicious 200**: **must** open in the browser and `/extract` the *rendered* text (per §3), then scan for removal markers (case-insensitive): `this job was removed`, `no longer accepting applications`, `no longer available`, `position has been filled`, `job is closed`, `posting has expired`, `sorry, this job`. Match → `listing-removed`. BuiltIn's exact phrasing is `"Sorry, this job was removed at <time> on <date>"`.
  - **403** is usually bot-blocking (some employer sites do this) not dead — don't remove on 403 alone. **500** may be transient — re-check before removing.
- **Listing removed / role closed**: reason `listing-removed`.

Any row hitting one of these is **auto-removed**: migrate it to the `applied.md` **Rejected** table exactly as in §7 (dated today), with the matching reason code and Comment noting the specifics + ` (auto)`, then delete it from `shortlist.md`. Rows that are still live and recent stay put. Prefer the browser/`/extract` path for ATS pages that block plain fetch; batch checks in parallel where possible, and close any validation-only tabs afterward (§3).

## Routine-run checklist
1. Re-read `job-search-prefs.md` + this file + skim `shortlist.md` (esp. Excluded) AND `applied.md` (esp. **Rejected**) to dedup (never re-suggest an applied/in-motion role, or a **judgment**-rejected one; but a **liveness**-rejected role — `listing-removed`/`link-broken`/`too-old` — CAN be re-suggested if it's live again, per §7.5). Then, in order: (a) if asked to refresh applications, run the **Simplify tracker sync** (§5) + cross-reference (§6) into `shortlist.md`; (b) **migrate resolved rows** out of `shortlist.md` (§7): `[x]` applied → into `## Applied` (carry comment; no dup); `[nope]` rejected → the Rejected table, classified; (c) run the **liveness & staleness sweep** (§8) over the remaining open (`[ ]`) rows, auto-removing dead/removed/too-old listings. Only after (a)–(c) proceed to new searches.
2. Search across sources — **all of these every pass** (don't skip any):
   a. `remote_web_search` across the domains listed in `job-search-prefs.md` + new ones.
   a2. **Regional / university boards** listed in `job-search-prefs.md` (e.g. a local startup or university job board) — check every pass; startup/spinout roles that don't show on the big aggregators. Client-side rendered → open via the browser + `/extract` (§3) to read listings; verify each on its real ATS before adding.
   b. **Regional BuiltIn board** (§1, from prefs) — the entry/mid-level dev listings (`/jobs/dev-engineering/entry-level`, `/mid-level`); a top source. Client-side paginated (`?page=N`) — open via browser + `/extract` to read deep pages.
   c. **LinkedIn keyword searches** (§1) via `linkedin_harvest.py` — run the domain keyword clusters from `job-search-prefs.md`, paginate fully.
   d. **LinkedIn recommended collection** — `https://www.linkedin.com/jobs/collections/recommended/` — harvest it too (same tooling; job ids are `?currentJobId=<id>` / `/jobs/view/<id>/`). It surfaces good roles keyword searches miss. Always check it.
   Everything from (b)–(d) goes through the two-stage triage (§1): company+title screen → **read the JD body (responsibilities + requirements) before adding**.
3. Verify live + get apply URLs on the ATS — **render-check aggregator links (§1), not just HTTP status** (soft-404s return 200). Drop any new find that's already removed.
4. Filter & tier (§2). New finds → insert into correct tier; cuts → Excluded with reason.
5. Open genuinely new keepers into the **"Job Search"** group; do all validation in the **"Scratch"** group (§3). Confirm via /tabs. At the end, close the Scratch group with the gated `/close` (§3) — keepers in "Job Search" are never touched.
6. Update the **"Last searched the web"** date at the top of `shortlist.md` to the current date on every search pass.
7. When user applies (or reports applying): if syncing from Simplify, re-run the sync (§5) then cross-reference (§6); otherwise mark/remove the role in `shortlist.md` and add it to `applied.md` (dated).
8. Keep prose minimal; do most reasoning silently; don't over-ask — use discretion.

## Files
- `shortlist.md` — candidate roles, tiered, with status boxes (`[ ]`/`[x]`/`[nope]`) + `Added` date + Comment column.
- `applied.md` — applied/in-motion tracker, **append-merged from Simplify** (Applied + Saved, each with an **Apply** `[Apply](url)` column; migrated `[x]` rows land directly in Applied) + hand-maintained Rejected (migrated `[nope]`, classified per §7) and referral sections. Dedup source (URL-keyed); never regenerated.
- `tracker.html` — HTML+JS editor/viewer over `shortlist.md`/`applied.md` (Chromium; split-pane preview via Tab Share).
- `.kiro/steering/job-search-prefs.md` — profile, career goals, filters, priorities, ATS URL formats (the *who/what*).
- `.kiro/steering/search-playbook.md` — this method file (the *how*).
- `.kiro/scripts/linkedin_harvest.py` — full LinkedIn search/collection harvest (parallel, measures page size, self-cleaning). Job-search-specific → lives in this workspace.
- `.kiro/scripts/read_jobs.py` — reads full LinkedIn job listings by id (parallel 5-tab Scratch pool), targeting responsibilities + requirements, for the triage read-step.
- `.kiro/skills/simplify-tracker-sync/` — skill that syncs Simplify → `applied.md` (`SKILL.md` + `scripts/parse_tracker.py`). Job-search-specific → lives in this workspace.
- Tab Share browser extension — install/location per the project README; the playbook only uses its `localhost:8766` HTTP API (`/open`,`/extract`,`/navigate`,`/close`).
