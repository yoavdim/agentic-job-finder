#!/usr/bin/env python3
"""Scrape the watchlist (fully scripted, no LLM).

For every `## Companies` row in watchlist.md that has a CSS selector (filled by the
selector pass),
drive a browser tab to the careers URL, scroll to load lazy lists, dismiss consent/cookie
modals, extract job cards via the selector, and record entries not yet tracked anywhere as
new rows in watchlist.md's `## Scraped (watchlist)` table — manual.md format
(`| Added | URL | Status |`, status empty; it is set later in tracker.html when the row is
marked saved/applied).

"Already tracked" = every entry already present in the scraped table, the flushed table
(`## Scraped-flushed (watchlist)` in applied.md), every table of applied.md, and manual.md —
so applied / saved / rejected / saved-for-later roles are never re-surfaced, and a marked
row is never re-added.

The browser is driven through the shared ChromeInterface (lib/chrome_interface.py) —
the same interface linkedin_harvest, simplify_search, liveness_sweep and housekeeping
use, which consolidates the open->scroll->extract->close operations that used to be
copied per-script. The selector pass's probe lives there too (`ChromeInterface.probe`), so
`watchlist_selectors` shares it the same way every other script shares the interface.

Usage:
    watchlist_scrape.py [--watchlist watchlist.md] [--applied applied.md]
                        [--manual manual.md] [--today DATE] [--scroll-steps N] [--wait N]
                        [--apply] [--json FILE]
"""
import argparse
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import check_browser_saved

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from chrome_interface import ChromeInterface, resolve_href, url_origin, host_of
import md_tables as M
import watchlist_flush as WF


# ---- watchlist reading / dedup ----

def read_companies(watchlist_lines):
    """[(company, url, selector)] for `## Companies` rows that have a company + URL."""
    t = M.find_table(watchlist_lines, "## Companies")
    if t is None:
        return []
    out = []
    for row in t.rows:
        company = row.get("company").strip()
        url = M.extract_url(row.get("url")).strip()
        if company and url:
            out.append({"company": company, "url": url,
                        "selector": row.get("selector").strip()})
    return out


def recorded_keys(watchlist_lines, applied_lines, manual_lines):
    """Dedup key (ats_code / cleaned URL) for every entry already tracked anywhere.

    `apply` resolves to the URL column of manual-format tables (`## Scraped (watchlist)`,
    `## Entries`) and the apply column of applied.md's tables, and to the URL column of
    `## Companies` — covering scraped, flushed, applied/saved/rejected and manual rows.
    """
    keys = set()
    for lines in (watchlist_lines, applied_lines, manual_lines):
        for t in M.parse_tables(lines):
            for row in t.rows:
                url = M.extract_url(row.get("apply"))
                if url:
                    keys.add(M.ats_code(url))
    return keys


def plan_company(company, url, selector, recorded, wait=3, scroll_steps=2, ci=None):
    """Scrape one company. Returns a dict; mutates nothing.

    Entries are keyed by ats_code of the resolved URL, so the same listing extracted twice
    in one run is deduped, and any listing already in `recorded` is skipped.
    """
    ci = ci or ChromeInterface()
    origin = url_origin(url)
    tid = ci.open_loaded(url, wait=wait)
    if not tid:
        return {"company": company, "error": f"could not open {url}", "new": [], "seen": 0}
    try:
        ci.scroll(tid, steps=scroll_steps)
        ci.close_modals(tid)
        res = ci.extract_elements(tid, selector)
        if "error" in res:
            return {"company": company, "error": res["error"], "new": [], "seen": 0}
        items, keys, new = res.get("items", []), set(), []
        live_keys = set()          # every ats_code on the live page
        for it in items:
            title = (it.get("text") or "").strip()
            href = (it.get("href") or "").strip()
            if not title or not href:
                continue
            abs_url = resolve_href(href, origin)
            if not abs_url:
                continue
            key = M.ats_code(abs_url)
            live_keys.add(key)
            if key in keys or key in recorded:
                continue
            keys.add(key)
            new.append({"title": title, "url": abs_url, "key": key})
        return {"company": company, "error": None, "new": new,
                "seen": len(items), "live_keys": live_keys}
    finally:
        ci.close([tid], expect_host="*")


def scrape_rows(today, new_entries):
    """Rendered `## Scraped (watchlist)` rows: `| Added | URL | Status |`."""
    return [M.row_md([today, M.esc(f"[{e['title']}](<{e['url']}>)"), ""])
            for e in new_entries]


def find_removed(watchlist_lines, results, recorded):
    """Scraped rows whose listings have disappeared from the live page.

    Returns a list of dicts with the same shape as `watchlist_flush.flush_rows`
    output (line_idx, rejected, company, role, url, reason, comment) plus the
    line indices to delete.

    A row is "removed" when:
    - its Status is empty (acted-on rows are left alone)
    - it is NOT already tracked somewhere else (like applied.md)
    - its company was scraped in this run (no error)
    - its ats_code is NOT in that company's live_keys
    """
    companies = WF.read_companies(watchlist_lines)
    
    # Map company name -> live keys for successful scrapes
    live_keys_by_company = {
        r["company"]: r.get("live_keys", set()) 
        for r in results if not r.get("error")
    }

    removed, to_delete = [], []
    for line_idx, row in WF.read_scraped(watchlist_lines):
        if row.get("status").strip():
            continue  # acted on in tracker.html
        
        added = row.get("date").strip()
        title, url = WF.split_title_url(row.get("apply"))
        if not url:
            continue
            
        code = M.ats_code(url)
        if code in recorded:
            # It was tracked somewhere else (e.g. Simplify sync put it in applied.md).
            # Clean it from the inbox, but do not flush it.
            to_delete.append(line_idx)
            continue

        company_name = WF.company_for(url, companies)
        if company_name not in live_keys_by_company:
            continue  # company wasn't scraped successfully in this run
            
        if code not in live_keys_by_company[company_name]:
            to_delete.append(line_idx)
            removed.append({
                "line_idx": line_idx,
                "rejected": "",   # filled by caller with today
                "company": company_name,
                "role": title or "",
                "url": url,
                "reason": "listing-removed",
                "comment": f"watchlist scrape {added}, listing removed (auto)".strip(),
            })
    return removed, to_delete


def sync_simplify(applied_path, apply_):
    """Run the 0b Simplify tracker sync to ensure applied.md is up to date."""
    workspace = HERE.parent.parent
    script = workspace / ".kiro" / "skills" / "simplify-tracker-sync" / "scripts" / "saved_sync_cli.py"
    if not script.exists():
        return
    cmd = [sys.executable, str(script), "--applied", applied_path]
    if apply_:
        cmd.append("--apply")
    print("scrape: syncing Simplify tracker...", file=sys.stderr)
    res = subprocess.run(cmd, cwd=str(workspace))
    if res.returncode != 0:
        print("scrape: Simplify sync failed, continuing anyway", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Scrape the watchlist for new postings (scripted, no LLM)")
    ap.add_argument("--watchlist", default="watchlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--manual", default="manual.md")
    ap.add_argument("--today", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--scroll-steps", type=int, default=2,
                    help="scroll-to-bottom passes per board (lazy lists)")
    ap.add_argument("--wait", type=int, default=3, help="render wait after load")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", help="write the plan as JSON here ('-' = stdout)")
    ap.add_argument("--companies", nargs="+", help="filter companies by name")
    ap.add_argument("--to-flush", action="store_true", help="write new items to the flushed list instead of scraped list")
    args = ap.parse_args(argv)

    ci = ChromeInterface()
    if not ci.is_up():
        print("scrape: Tab Share not reachable on :8766/:8765 — nothing scraped",
              file=sys.stderr)
        return 2

    # Check that there are no unsaved edits in the browser
    if not check_browser_saved.confirm_browser_saved():
        return 2

    # Sync Simplify tracker first so applied.md is completely up to date
    sync_simplify(args.applied, args.apply)

    watchlist = M.read_lines(args.watchlist)
    applied = M.read_lines(args.applied)
    manual = M.read_lines(args.manual)
    recorded = recorded_keys(watchlist, applied, manual)
    companies = [c for c in read_companies(watchlist) if c["selector"]]
    if args.companies:
        target_names = {name.lower() for name in args.companies}
        companies = [c for c in companies if c["company"].lower() in target_names]

    if not companies:
        print("scrape: no watchlist company has a CSS selector yet (run the selector pass first)",
              file=sys.stderr)
        return 0

    results = []
    for c in companies:
        r = plan_company(c["company"], c["url"], c["selector"], recorded,
                         wait=args.wait, scroll_steps=args.scroll_steps, ci=ci)
        results.append(r)
        print(f"  {r['company']}: {len(r['new'])} new, {r['seen']} seen"
              + (f"  [{r['error']}]" if r["error"] else ""), file=sys.stderr)

    new_entries = [{"company": r["company"], **e} for r in results for e in r["new"]]

    # Detect listings that disappeared from their company's careers page,
    # or that were tracked by the sync we just ran.
    removed, remove_indices = find_removed(watchlist, results, recorded)
    for r in removed:
        r["rejected"] = args.today

    if args.json:
        M.write_json(args.json, {
            "results": [{k: v for k, v in r.items() if k != "live_keys"}
                        for r in results],
            "new_count": len(new_entries),
            "new": [{"company": r["company"], "title": e["title"], "url": e["url"]}
                    for r in results for e in r["new"]],
            "removed_count": len(removed),
            "removed": [{"company": r["company"], "role": r["role"],
                         "url": r["url"]} for r in removed],
        })

    if not new_entries and not removed:
        print(f"scrape: no new postings and no removed listings across "
              f"{len(companies)} company/ies", file=sys.stderr)
        return 0

    if args.apply:
        applied_dirty = False
        watchlist_dirty = False

        if new_entries:
            if args.to_flush:
                flushed_new = []
                for e in new_entries:
                    flushed_new.append({
                        "line_idx": -1,
                        "rejected": args.today,
                        "company": e["company"],
                        "role": e["title"],
                        "url": e["url"],
                        "reason": "other",
                        "comment": f"watchlist scrape {args.today}, directly flushed",
                    })
                applied = M.ensure_table(applied, "## Scraped-flushed (watchlist)",
                                         WF.FLUSH_HEADER)
                applied = M.insert_rows(applied, "## Scraped-flushed (watchlist)",
                                        [WF.render_flush(r) for r in flushed_new],
                                        newest_first=True)
                applied_dirty = True
            else:
                watchlist = M.ensure_table(watchlist, "## Scraped (watchlist)",
                                           ["Added", "URL", "Status"])
                watchlist = M.insert_rows(watchlist, "## Scraped (watchlist)",
                                          scrape_rows(args.today, new_entries),
                                          newest_first=False)
                watchlist_dirty = True

        if removed:
            # Remove the gone rows from the scraped table
            watchlist = M.delete_lines(watchlist, remove_indices)
            watchlist_dirty = True
            # Flush them into applied.md
            applied = M.ensure_table(applied, "## Scraped-flushed (watchlist)",
                                     WF.FLUSH_HEADER)
            applied = M.insert_rows(applied, "## Scraped-flushed (watchlist)",
                                    [WF.render_flush(r) for r in removed],
                                    newest_first=True)
            applied_dirty = True

        if applied_dirty:
            M.write_lines(args.applied, applied)
        if watchlist_dirty:
            M.write_lines(args.watchlist, watchlist)

        parts = []
        if new_entries:
            if args.to_flush:
                parts.append(f"{len(new_entries)} new row(s) directly flushed")
            else:
                parts.append(f"{len(new_entries)} new row(s)")
        if removed:
            parts.append(f"{len(removed)} removed listing(s) flushed")
        print(f"scrape: {', '.join(parts)}", file=sys.stderr)
    else:
        parts = []
        if new_entries:
            if args.to_flush:
                parts.append(f"{len(new_entries)} new row(s) to flush directly")
            else:
                parts.append(f"{len(new_entries)} new row(s)")
        if removed:
            parts.append(f"{len(removed)} removed listing(s) to flush")
            for r in removed:
                print(f"  - removed: {r['company']} — {r['role']}",
                      file=sys.stderr)
        print(f"DRY RUN (use --apply to write) — {', '.join(parts)}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
