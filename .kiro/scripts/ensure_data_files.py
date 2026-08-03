#!/usr/bin/env python3
"""Ensure the pass's data files exist — run FIRST, before any other search step.

Creates `applied.md`, `thoughts.md`, `manual.md` skeletons if they're missing, so
`tracker.html` and every script always have valid files to read/write. Existing
files are left byte-for-byte untouched. Dry run by default; `--apply` writes.

Usage:
    ensure_data_files.py                  # dry run (prints what would be created)
    ensure_data_files.py --apply
    ensure_data_files.py --apply --json -
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import md_tables as M

THOUGHTS_SKELETON = """# Thoughts

Afterthought remarks I jot down while reviewing roles in `tracker.html`. Each is a
bullet. Before the next search pass, Kiro folds these into
`.kiro/steering/job-search-prefs.md` and then empties this list (leaving the heading).
"""

MANUAL_SKELETON = """# Manual URL additions

Roles found and applied/saved outside Simplify, captured via `tracker.html` as a URL + status. Each row is resolved into `applied.md`/`shortlist.md` at stage 0 and then deleted.

## Entries

| Added | URL | Status |
|---|---|---|
"""

# Companies to periodically check for new openings, added via tracker.html's chip strip
# (shown above manual.md, never its own tab). CSS Selector is reserved for future scraping
# and unused for now — kept in the schema so no migration is needed when that lands.
WATCHLIST_SKELETON = """# Company Watchlist

Companies to periodically check for new openings — added via `tracker.html`. CSS
selector is reserved for future scraping; unused for now.

## Companies

| Added | Company | URL | CSS Selector |
|---|---|---|---|
"""

APPLIED_SKELETON = """# Applied / In-Motion Tracker — Yoav Dim

Synced from the Simplify tracker (simplify.jobs/tracker). Keep updated so future searches skip these (dedup against this + shortlist Excluded).

**Last synced from Simplify:** {date} · 0 applied · 0 saved (not yet applied)

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|

## Saved (not yet applied)

An exact mirror of the Simplify tracker's Saved list — the single copy of it. **Status** is
your intent for the row, set in `tracker.html`: blank/`saved` (leave it), `applied` (push
mark-applied to Simplify), `rejected` (delete it from Simplify). The next sync reads the
status, acts on it, records the outcome, and then rebuilds this table.

| Saved | Company | Role | Raw | Location | Apply | Simplify | Status | Comment |
|---|---|---|---|---|---|---|---|---|

## Rejected (migrated from shortlist `[nope]` rows)

Roles cut from `shortlist.md` after review. Kept here so future search passes (stage 0) dedup against them and refine queries. **Reason** is classified from the Comment when the row was rejected; **Comment** preserves the original note verbatim.

Reason codes: `link-broken` (crawl/link failed) · `listing-removed` (role closed/gone) · `too-old` (past recency cutoff) · `not-qualified` (don't meet bar) · `not-interested` (role/company not appealing) · `sketchy-site` (untrustworthy listing site) · `unknown` (no/empty comment) · `other` (comment doesn't fit above).

| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |
|---|---|---|---|---|---|---|---|
"""


def today():
    return datetime.now().strftime("%Y-%m-%d")


TIER_TABLE = ("|  | Added | Company | Role | Location | Apply link | Notes | Comment |\n"
              "|---|---|---|---|---|---|---|---|\n")

# shortlist.md is the file the most scripts depend on — shortlist_add, crossref,
# liveness_sweep and migrate_resolved all read it and raise FileNotFoundError without it.
# Omitting it from the bootstrap left the "every script always has valid files" promise
# unkept on a fresh workspace.
SHORTLIST_SKELETON = """# Job Shortlist — Yoav Dim

Tiered candidate roles. Status box: `[ ]` open · `[x]` applied · `[nope]` rejected.
Notes legend: ✅ good fit · ⚠️ verify level/location · 🔗 referral · 📅 posted YYYY-MM-DD.

**Last searched the web:** {date}

## Tier 1 — Best fit (Toronto, junior, target domains: embedded / systems / low-level SW / distributed-consensus)

{tier}
## Tier 2 — General SWE, junior/new-grad + finance/quant (Toronto)

{tier}
## Tier 3 — Adjacent / verify before applying

{tier}
## Tier 4 — Commutable nearby (Markham / Mississauga / Oakville), lower priority

{tier}
## Tier 5 — Chip design + formal verification (lowest priority per "software-first")

{tier}
## Referral leads

{tier}
## Notes

## Excluded (and why)

Cuts logged here so future passes don't re-suggest them. A bullet in the structured form
`**Company — Role** (date): reason` is treated as a real per-role exclusion by
`dedup_index.py`; freeform category bullets are only ever a hint, because the same company
can still have a role that fits.
"""


def missing_skeletons(thoughts_path, manual_path, applied_path, shortlist_path=None,
                      date=None, watchlist_path=None):
    """Return {Path: skeleton-text} for every data file that doesn't exist yet."""
    date = date or today()
    wanted = [(Path(thoughts_path), THOUGHTS_SKELETON),
              (Path(manual_path), MANUAL_SKELETON),
              (Path(applied_path), APPLIED_SKELETON.format(date=date))]
    if shortlist_path is not None:
        wanted.append((Path(shortlist_path),
                       SHORTLIST_SKELETON.format(date=date, tier=TIER_TABLE)))
    if watchlist_path is not None:
        wanted.append((Path(watchlist_path), WATCHLIST_SKELETON))
    out = {}
    for p, skeleton in wanted:
        if not p.exists():
            out[p] = skeleton
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Ensure the pass's data files exist (applied.md, thoughts.md, manual.md)")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--thoughts", default="thoughts.md")
    ap.add_argument("--manual", default="manual.md")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--watchlist", default="watchlist.md")
    ap.add_argument("--date", default=today())
    ap.add_argument("--apply", action="store_true", help="write skeletons for missing files")
    ap.add_argument("--json", help="write the report as JSON here ('-' = stdout)")
    args = ap.parse_args()

    missing = missing_skeletons(args.thoughts, args.manual, args.applied, args.shortlist,
                               args.date, args.watchlist)
    missing_str = [str(p) for p in missing]
    wanted = [str(Path(args.thoughts)), str(Path(args.manual)), str(Path(args.applied)),
              str(Path(args.shortlist)), str(Path(args.watchlist))]
    existing = [p for p in wanted if p not in missing_str]

    created = []
    if args.apply:
        for p, text in missing.items():
            p.write_text(text, encoding="utf-8")
            created.append(str(p))

    report = {"date": args.date, "existing": existing, "dry_run": not args.apply}
    if args.apply:
        report["created"] = created
    else:
        report["would_create"] = missing_str
    if args.json:
        M.write_json(args.json, report)
    else:
        for p in wanted:
            if p in created:
                print(f"created  {p}", file=sys.stderr)
            elif p in existing:
                print(f"exists   {p}", file=sys.stderr)
            else:
                print(f"would create {p} (dry run)", file=sys.stderr)
        verb = "dry run: nothing written" if not args.apply else f"created {len(created)} file(s)"
        print(f"ensure-data-files: {verb}")


if __name__ == "__main__":
    main()
