#!/usr/bin/env python3
"""Decide watchlist CSS selectors (LLM-managed).

The LLM is the decision-maker; this script is the deterministic probe + write-back tool it
drives (see .kiro/steering/watchlist-scraper.md for the selector pass):

  probe  — open a company careers URL in a Scratch tab and live-test ONE CSS selector
           against the rendered DOM. Prints {count, samples} as JSON so the LLM can
           iterate: 0 matches => selector too imprecise (e.g. a bare `h3`); several
           matches per job card => too broad; exactly one match per card is the target.
  write  — commit the winning selector into watchlist.md's CSS Selector cell (the only
           way a script edits that table — md_tables, never a direct file edit).

Example LLM loop:
    python3 watchlist_selectors.py probe --url <careers-url> --selector "a[data-automation-id='jobTitle']"
    python3 watchlist_selectors.py write --company Xanadu --selector "a[data-automation-id='jobTitle']" --apply

Usage:
    watchlist_selectors.py probe --url URL --selector CSS [--wait N] [--json]
    watchlist_selectors.py write --company CO --selector CSS [--watchlist watchlist.md] [--apply]
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import check_browser_saved
from chrome_interface import ChromeInterface
import md_tables as M


def probe(url, selector, wait=3):
    """Live-test `selector` against `url`. Returns a dict for JSON output."""
    return ChromeInterface().probe(url, selector, wait=wait)


def write_selector(lines, company, selector=None, next_page=None):
    """Write `selector` and/or `next_page` into watchlist.md's row for `company`.

    Returns (new_lines, changed). Raises KeyError when the company has no row.
    """
    t = M.find_table(lines, "## Companies")
    if t is None:
        raise KeyError("watchlist.md has no '## Companies' table")
    row = next((r for r in t.rows
                if r.get("company").strip().lower() == company.strip().lower()), None)
    if row is None:
        raise KeyError(f"no watchlist row for company {company!r}")
    
    changed = False
    if selector is not None and row.get("selector").strip() != selector.strip():
        row.set("selector", selector)
        changed = True
    if next_page is not None and row.get("next page", "").strip() != next_page.strip():
        row.set("next page", next_page)
        changed = True

    if not changed:
        return lines, False
    new_lines = list(lines)
    new_lines[row.line_idx] = row.render()
    return new_lines, True


def cmd_probe(args):
    out = probe(args.url, args.selector, args.wait)
    if args.json == "-":
        print(json.dumps(out, indent=1))
    elif args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
    else:
        print(json.dumps(out, indent=1))
    return 0 if out.get("ok") else 2


def cmd_write(args):
    if not check_browser_saved.confirm_browser_saved():
        return 2
    lines = M.read_lines(args.watchlist)
    try:
        new_lines, changed = write_selector(lines, args.company, selector=args.selector, next_page=args.next_page)
    except KeyError as e:
        print(f"write: {e}", file=sys.stderr)
        return 2
    if not changed:
        print(f"watchlist: {args.company!r} already uses that selector", file=sys.stderr)
        return 0
    old = next(l for l in lines if args.company.lower() in l.lower())
    new = next(l for l in new_lines if l != old)
    if args.apply:
        M.write_lines(args.watchlist, new_lines)
        print(f"watchlist: wrote CSS Selector for {args.company!r}", file=sys.stderr)
        return 0
    print(f"DRY RUN (use --apply to write) — {args.company}:", file=sys.stderr)
    print(f"  - {old}", file=sys.stderr)
    print(f"  + {new}", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Watchlist CSS selector probe + write-back (LLM-managed)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe", help="live-test one CSS selector against a careers URL")
    p.add_argument("--url", required=True)
    p.add_argument("--selector", required=True)
    p.add_argument("--wait", type=int, default=3, help="render wait after load")
    p.add_argument("--json", nargs="?", const="-", help="write JSON here ('-' = stdout)")
    p.set_defaults(fn=cmd_probe)

    w = sub.add_parser("write", help="commit a selector into watchlist.md")
    w.add_argument("--company", required=True)
    w.add_argument("--selector", help="The CSS selector for the job cards")
    w.add_argument("--next-page", help="The CSS selector for the next page button (or 'none' if N/A)")
    w.add_argument("--watchlist", default="watchlist.md")
    w.add_argument("--apply", action="store_true")
    w.set_defaults(fn=cmd_write)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
