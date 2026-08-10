#!/usr/bin/env python3
"""Reject every still-open shortlist row — pick a reason + comment, then mark them.

Shortlist rows live in tier tables with a status box: `[ ]` open, `[x]` applied,
`[nope]` rejected. This script targets ONLY the `[ ]` rows — `[x]` (already applied)
and `[nope]` (already rejected) rows are left alone, as are rows with no status box
at all. Each open row is marked `[nope]` and its Comment is written as
`"<code> — <free text>"` so `migrate_resolved.classify_reason` reads the code back
verbatim (the same shape the reject dialog in tracker.html writes).

It stops after marking: the actual move into `## Rejected` in applied.md is
`migrate_resolved.py --apply` (stage 0e / the no-llm-sweep), which is a separate,
already-tested step.

Reasons are the shared taxonomy from `reasons.py`, identical to the launcher's
reject-shortlist reason picker and tracker.html's reject chips.

Interactive by default: reason menu -> optional comment -> confirmation. Scriptable
with --reason / --comment / --yes so an agent or wrapper can drive it without prompts.

Usage:
    reject_shortlist.py                                          # interactive
    reject_shortlist.py --reason not-interested --yes            # scripted, no comment
    reject_shortlist.py --reason too-old --comment "flush" --yes
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

import md_tables as M
from reasons import REASON_CODES


def open_rows(shortlist_lines):
    """[(Table, Row)] — every shortlist row whose status box is `[ ]`."""
    out = []
    for t in M.find_tables(shortlist_lines, r"Tier \d+"):
        if not t.has("status"):
            continue
        for row in t.rows:
            if M.is_open(row.get("status")):
                out.append((t, row))
    return out


def merged_comment(reason, comment):
    c = (comment or "").strip()
    return f"{reason} — {c}" if c else reason


def append_comment(existing, incoming):
    """Never clobber an existing comment — the new reason leads, the old note is
    appended after it (same policy as migrate_resolved.merge_comment, but the new
    reason comes first so it stays the leading reason code)."""
    e, i = (existing or "").strip(), (incoming or "").strip()
    if not e:
        return i
    if not i:
        return e
    if i.lower() in e.lower():
        return e
    if e.lower() in i.lower():
        return i
    return f"{i}; {e}"


def mark_all(shortlist_lines, reason, comment):
    """Mark every open row `[nope]` with the merged reason comment. Pure: no I/O.

    `[x]` / `[nope]` / no-status rows are untouched. A row that already has a comment
    keeps it: the new reason comment is put first, the old note appended after.
    Returns the new lines.
    """
    merged = merged_comment(reason, comment)
    for t, row in open_rows(shortlist_lines):
        row.set("status", "[nope]")
        row.set("comment", append_comment(row.get("comment"), merged))
        shortlist_lines[row.line_idx] = row.render()
    return shortlist_lines


def pick_reason_interactively():
    print("Pick a rejection reason:")
    for i, code in enumerate(REASON_CODES, 1):
        print(f"  {i}. {code}")
    while True:
        try:
            raw = input(f"reason [1-{len(REASON_CODES)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(REASON_CODES):
            return REASON_CODES[int(raw) - 1]
        if raw in REASON_CODES:
            return raw
        print(f"  (enter a number 1-{len(REASON_CODES)} or a code)")


def ask_comment():
    try:
        return input("comment (optional, Enter for none): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Reject every still-open shortlist row: mark [ ] -> [nope] with a "
                    "reason + comment (migrate separately with migrate_resolved.py --apply)")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--reason", default=None,
                    help=f"reason code, one of: {', '.join(REASON_CODES)}")
    ap.add_argument("--comment", default=None, help="free-text comment (optional)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--json", help="write the plan as JSON here ('-' = stdout)")
    args = ap.parse_args(argv)

    shortlist_lines = M.read_lines(args.shortlist)
    targets = open_rows(shortlist_lines)

    if not targets:
        print("nothing to reject: no `[ ]` shortlist rows (only `[x]`/`[nope]` left)",
              file=sys.stderr)
        return 0

    if args.reason and args.reason not in REASON_CODES:
        print(f"reject: unknown reason {args.reason!r} — expected one of "
              f"{', '.join(REASON_CODES)}", file=sys.stderr)
        return 2
    reason = args.reason or pick_reason_interactively()
    if reason is None:
        print("reject: aborted", file=sys.stderr)
        return 2

    comment = args.comment if args.comment is not None else ask_comment()
    merged = merged_comment(reason, comment)

    labels = [f"{t.heading}: {row.get('company')} — {row.get('role')}"
              for t, row in targets]
    if args.json:
        M.write_json(args.json, {
            "count": len(targets), "reason": reason, "comment": merged, "rows": labels,
        })

    for l in labels:
        print(f"  {l}", file=sys.stderr)
    print(f"would mark {len(targets)} row(s) [nope] with {merged!r}", file=sys.stderr)

    if not args.yes:
        try:
            ok = input(f"Reject {len(targets)} row(s) now? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ok = ""
        if ok not in ("y", "yes"):
            print("reject: aborted, nothing changed", file=sys.stderr)
            return 0

    M.backup_file(args.shortlist)
    M.write_lines(args.shortlist, mark_all(shortlist_lines, reason, comment),
                  backup=False)
    print(f"rejected: {len(targets)} row(s) marked [nope] with {merged!r} — next run "
          "`migrate_resolved.py --apply` (or the no-llm-sweep) to move them to "
          "`## Rejected`", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
