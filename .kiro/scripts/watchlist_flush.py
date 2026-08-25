#!/usr/bin/env python3
"""Flush the scraped watchlist (fully scripted, no LLM).

`watchlist_scrape.py` (the scrape) appends every new listing as
`| Added | URL | Status |` rows in watchlist.md's `## Scraped (watchlist)` table, with the
title bundled inside the URL cell as `[Title](<url>)`. Those rows sit there until the human
acts on them in tracker.html (which sets Status when a row is saved/applied — such rows are
NOT touched here). This script flushes the STILL-UNMARKED rows (Status empty) into
applied.md's `## Scraped-flushed (watchlist)` table, the permanent dedup record:

    | Rejected | Company | Role | URL | Reason | Comment |

  - Rejected = today (the flush date)
  - Company  = matched from the `## Companies` table by URL host (blank when no match)
  - Role     = the title split out of `[Title](<url>)`
  - URL      = the listing URL, re-rendered as `[Role](<url>)`
  - Reason   = `too-old` (these are watchlist listings nobody acted on)
  - Comment  = audit text: the original scrape date + "(auto)"

Rows already in the flushed table (or any applied.md table) are never re-flushed: dedup is
by ats_code of the URL, the same key watchlist_scrape uses, so a flush is idempotent.

Dry run by default; `--apply` writes. No browser, no network.

Usage:
    watchlist_flush.py [--watchlist watchlist.md] [--applied applied.md]
                       [--today DATE] [--apply] [--json FILE]
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import check_browser_saved

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import md_tables as M
from chrome_interface import host_of

# Manual-format row the scraper wrote: `| Added | [Title](<url>) | Status |`.
TITLE_URL_RE = re.compile(r"\[([^\]]*)\]\(\s*<?(https?://[^)>\s]+)>?\s*\)")

# Flushed-table columns, in order.
FLUSH_HEADER = ["Rejected", "Company", "Role", "URL", "Reason", "Comment"]

# The `## Scraped (watchlist)` section carries a `**Last flush:** <date>` stamp under its
# heading (created by the data-file template as `never`, updated by the flush on a real
# flush). It's a marker for the human, not data: tables are found by heading, so the stamp
# never feeds any parser.
LAST_FLUSH_RE = re.compile(r"^\*\*Last flush:\*\*\s*(.*)$")


def split_title_url(cell):
    """`[Title](<url>)` -> (title, url). Returns (None, url) when the cell holds a bare
    URL or a non-link label, and ("", "") for an empty cell."""
    m = TITLE_URL_RE.search(cell or "")
    if m:
        return m.group(1).strip(), m.group(2)
    url = M.extract_url(cell)
    return (None, url) if url else ("", "")


def _last_flush_index(lines, heading_idx):
    """Index of the `**Last flush:**` line between the heading and the next heading."""
    for i in range(heading_idx + 1, len(lines)):
        if re.match(r"^\s*#{1,6}\s", lines[i]):
            return None
        if LAST_FLUSH_RE.match(lines[i]):
            return i
    return None


def _write_last_flush(lines, date, overwrite):
    """Set (or create) the `## Scraped (watchlist)` **Last flush:** stamp to `date`.

    Returns a new line list. With `overwrite=False` an existing stamp is left untouched —
    that's how a `never` stamp is materialised on a table that predates the field.
    """
    lines = list(lines)
    heading_idx = next((i for i, l in enumerate(lines)
                        if l.strip().lower().startswith("## scraped (watchlist)")), None)
    if heading_idx is None:
        return lines
    idx = _last_flush_index(lines, heading_idx)
    if idx is not None:
        if overwrite:
            lines[idx] = f"**Last flush:** {date}"
        return lines
    # No stamp yet: insert `<blank> <stamp> <blank>` right after the heading, reusing an
    # existing blank line so we never stack two in a row.
    j = heading_idx + 1
    if j < len(lines) and lines[j].strip() == "":
        j += 1
    if j == heading_idx + 1:
        lines[j:j] = ["", f"**Last flush:** {date}", ""]
    else:
        lines[j:j] = [f"**Last flush:** {date}", ""]
    return lines


def set_last_flush(lines, date):
    """Stamp `## Scraped (watchlist)` with the flush date (the flush's bookkeeping)."""
    return _write_last_flush(lines, date, overwrite=True)


def ensure_last_flush(lines):
    """Create a `**Last flush:** never` stamp when the scraped section lacks one."""
    return _write_last_flush(lines, "never", overwrite=False)


def company_for(url, companies):
    """Best `## Companies` company name for `url`, or ''.

    Matches by host of the careers URL vs host of the listing URL (so a lever.co careers
    page maps its own listings), preferring the longest shared host when several companies
    share a careers host (e.g. multiple lever accounts). '' when nothing matches.
    """
    if not url:
        return ""
    url_host = host_of(url)
    if not url_host:
        return ""
    best, best_host = "", ""
    for c in companies:
        chost = host_of(c.get("url"))
        if not chost:
            continue
        if url_host != chost and not url_host.endswith("." + chost):
            continue
        if len(chost) > len(best_host):
            best, best_host = c.get("company"), chost
    return best


def read_scraped(watchlist_lines):
    """[(line_idx, row)] for every `## Scraped (watchlist)` row."""
    t = M.find_table(watchlist_lines, "## Scraped (watchlist)")
    if t is None:
        return []
    return [(r.line_idx, r) for r in t.rows]


def read_companies(watchlist_lines):
    """[{"company","url"}] from `## Companies`."""
    t = M.find_table(watchlist_lines, "## Companies")
    if t is None:
        return []
    out = []
    for row in t.rows:
        co = row.get("company").strip()
        url = M.extract_url(row.get("url"))
        if co and url:
            out.append({"company": co, "url": url})
    return out


def flush_rows(today, watchlist_lines, applied_lines, manual_lines=None):
    """Plan the flush. Returns (to_flush, to_delete) — mutates nothing.

    `to_flush` is a list of dicts {line_idx, rejected, company, role, url, reason,
    comment}; `to_delete` the line indices in watchlist.md that will be removed.
    """
    companies = read_companies(watchlist_lines)

    # Dedup keys already tracked anywhere (flushed + all applied tables + manual + the
    # scraped rows themselves) so a flush is idempotent.
    tracked = set()
    for lines in (applied_lines, manual_lines):
        if not lines:
            continue
        for t in M.parse_tables(lines):
            for row in t.rows:
                code = M.ats_code(row.get("apply"))
                if code:
                    tracked.add(code)

    flushed = []
    to_delete = []
    for line_idx, row in read_scraped(watchlist_lines):
        if row.get("status").strip():
            continue                       # acted on in tracker.html — leave it
        added = row.get("date").strip()
        title, url = split_title_url(row.get("apply"))
        if not url:
            continue
        code = M.ats_code(url)
        if code in tracked:
            to_delete.append(line_idx)     # already recorded somewhere — clean it up
            continue
        tracked.add(code)
        to_delete.append(line_idx)         # flushed rows MOVE to applied.md
        flushed.append({
            "line_idx": line_idx,
            "rejected": today,
            "company": company_for(url, companies),
            "role": title or "",
            "url": url,
            "reason": "too-old",
            "comment": f"watchlist scrape {added}, unmarked at flush (auto)".strip(),
        })
    return flushed, to_delete


def render_flush(f):
    """`| Rejected | Company | Role | URL | Reason | Comment |` row string."""
    url_cell = M.esc(f"[{f['role'] or f['url']}](<{f['url']}>)")
    cells = [f["rejected"], f["company"], f["role"], url_cell,
             f["reason"], f["comment"]]
    return M.row_md(cells)


def apply_flush(today, watchlist_lines, applied_lines, to_flush, to_delete):
    """Apply the plan. Returns (new_watchlist_lines, new_applied_lines)."""
    new_applied = list(applied_lines)
    if to_flush:
        new_applied = M.ensure_table(new_applied, "## Scraped-flushed (watchlist)",
                                     FLUSH_HEADER)
        new_applied = M.insert_rows(new_applied, "## Scraped-flushed (watchlist)",
                                    [render_flush(f) for f in to_flush],
                                    newest_first=True)
    new_watchlist = M.delete_lines(list(watchlist_lines), to_delete)
    if to_flush:
        new_watchlist = set_last_flush(new_watchlist, today)
    return new_watchlist, new_applied


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Flush unmarked watchlist scrapes into applied.md")
    ap.add_argument("--watchlist", default="watchlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--manual", default="manual.md")
    ap.add_argument("--today", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", help="write the plan as JSON here ('-' = stdout)")
    args = ap.parse_args(argv)

    if not check_browser_saved.confirm_browser_saved():
        return 2

    watchlist = M.read_lines(args.watchlist)
    applied = M.read_lines(args.applied)
    manual = M.read_lines(args.manual) if Path(args.manual).exists() else []

    to_flush, to_delete = flush_rows(args.today, watchlist, applied, manual)
    if args.json:
        M.write_json(args.json, {
            "today": args.today, "flushed": len(to_flush), "cleaned": len(to_delete),
            "rows": [{"company": f["company"], "role": f["role"], "url": f["url"],
                      "reason": f["reason"], "comment": f["comment"]} for f in to_flush],
        })

    if not to_flush and not to_delete:
        print("flush: nothing to flush (no unmarked scraped rows)", file=sys.stderr)
        return 0

    if args.apply:
        new_watchlist, new_applied = apply_flush(
            args.today, watchlist, applied, to_flush, to_delete)
        M.write_lines(args.watchlist, new_watchlist)
        M.write_lines(args.applied, new_applied)
        print(f"flush: wrote {len(to_flush)} row(s) to '## Scraped-flushed (watchlist)' "
              f"in {args.applied}", file=sys.stderr)
    else:
        print(f"DRY RUN (use --apply to write) — flush {len(to_flush)} row(s), "
              f"remove {len(to_delete)} row(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
