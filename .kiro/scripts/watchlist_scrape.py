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
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from chrome_interface import ChromeInterface, resolve_href, url_origin
import md_tables as M


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
        for it in items:
            title = (it.get("text") or "").strip()
            href = (it.get("href") or "").strip()
            if not title or not href:
                continue
            abs_url = resolve_href(href, origin)
            if not abs_url:
                continue
            key = M.ats_code(abs_url)
            if key in keys or key in recorded:
                continue
            keys.add(key)
            new.append({"title": title, "url": abs_url, "key": key})
        return {"company": company, "error": None, "new": new, "seen": len(items)}
    finally:
        ci.close([tid], expect_host="*")


def scrape_rows(today, new_entries):
    """Rendered `## Scraped (watchlist)` rows: `| Added | URL | Status |`."""
    return [M.row_md([today, M.esc(f"[{e['title']}](<{e['url']}>)"), ""])
            for e in new_entries]


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
    args = ap.parse_args(argv)

    ci = ChromeInterface()
    if not ci.is_up():
        print("scrape: Tab Share not reachable on :8766/:8765 — nothing scraped",
              file=sys.stderr)
        return 2

    watchlist = M.read_lines(args.watchlist)
    applied = M.read_lines(args.applied)
    manual = M.read_lines(args.manual)
    recorded = recorded_keys(watchlist, applied, manual)
    companies = [c for c in read_companies(watchlist) if c["selector"]]

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

    new_entries = [e for r in results for e in r["new"]]
    if args.json:
        M.write_json(args.json, {
            "results": results, "new_count": len(new_entries),
            "new": [{"company": r["company"], "title": e["title"], "url": e["url"]}
                    for r in results for e in r["new"]],
        })

    if not new_entries:
        print(f"scrape: no new postings across {len(companies)} company/ies",
              file=sys.stderr)
        return 0

    if args.apply:
        watchlist = M.ensure_table(watchlist, "## Scraped (watchlist)",
                                   ["Added", "URL", "Status"])
        watchlist = M.insert_rows(watchlist, "## Scraped (watchlist)",
                                  scrape_rows(args.today, new_entries),
                                  newest_first=False)
        M.write_lines(args.watchlist, watchlist)
        print(f"scrape: wrote {len(new_entries)} new row(s) to "
              f"'## Scraped (watchlist)' in {args.watchlist}", file=sys.stderr)
    else:
        print(f"DRY RUN (use --apply to write) — {len(new_entries)} new row(s) to "
              f"'## Scraped (watchlist)'", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
