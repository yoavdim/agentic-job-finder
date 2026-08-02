#!/usr/bin/env python3
"""Stage 0a2 — cross-reference applied.md into shortlist.md (search-playbook §6).

After a Simplify sync, any shortlist row that turns out to be applied gets flipped so it
isn't re-suggested or re-applied to:

    [ ]     -> [x]  + Notes gain "— **APPLIED <date>**"
    [nope]  -> [x]  + the stale rejection comment is cleared

That second rule is the playbook's "applied always wins over [nope]": the application is
ground truth, so assume you applied first and the listing was delisted later.

Rows in applied.md's **Saved** section are NOT applications — those are left `[ ]`.

Only confident matches are written. Fuzzy/shorthand candidates (Simplify titles are often
user-entered shorthand) are reported for confirmation instead, because the shorthand is lossy.

Usage:
    crossref.py --shortlist shortlist.md --applied applied.md            # dry run
    crossref.py --shortlist shortlist.md --applied applied.md --apply
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import md_tables as M

APPLIED_NOTE_RE = re.compile(r"—?\s*\*\*APPLIED\s+\d{4}-\d{2}-\d{2}\*\*", re.I)


def index_applied_rows(applied_lines):
    """{ats_code: row} and {(company,title): row} over the ## Applied table only."""
    t = M.find_table(applied_lines, "## Applied")
    by_code, by_key = {}, {}
    if t is None:
        return by_code, by_key
    for row in t.rows:
        keys, code = M.row_keys(row)
        if code:
            by_code.setdefault(code, row)
        for k in keys:
            by_key.setdefault(k, row)
    return by_code, by_key


def applied_note(date):
    return f"— **APPLIED {date}**"


def add_applied_note(notes, date):
    n = (notes or "").strip()
    if APPLIED_NOTE_RE.search(n):
        return APPLIED_NOTE_RE.sub(applied_note(date), n, count=1)
    return f"{n} {applied_note(date)}".strip() if n else applied_note(date)


def plan(shortlist_lines, applied_lines):
    """Compute the cross-reference edits. Returns (edits, needs_confirmation)."""
    by_code, by_key = index_applied_rows(applied_lines)
    edits, unsure = [], []

    for t in M.find_tables(shortlist_lines, r"Tier \d+"):
        if not t.has("status"):
            continue
        for row in t.rows:
            st = row.get("status")
            if M.is_applied(st):
                continue                      # already marked
            company, role = row.get("company"), row.get("role")
            if not company or not role:
                continue

            keys, code = M.row_keys(row)
            match, matched_on = None, ""
            if code and code in by_code:
                match, matched_on = by_code[code], "url"
            else:
                for k in keys:
                    if k in by_key:
                        match, matched_on = by_key[k], "company+title"
                        break

            if match is not None:
                was_rejected = M.is_rejected(st)
                date = match.get("date")
                new_notes = add_applied_note(row.get("notes"), date)
                edits.append({
                    "line_idx": row.line_idx,
                    "tier": t.heading,
                    "company": company, "role": role,
                    "matched_on": matched_on,
                    "applied_date": date,
                    "was_rejected": was_rejected,
                    "notes": new_notes,
                    # Applied wins over [nope]: drop the now-stale rejection reason.
                    "clear_comment": was_rejected,
                    "old_comment": row.get("comment"),
                })
                continue

            near = near_matches(company, role, by_key)
            if near:
                unsure.append({
                    "line_idx": row.line_idx, "tier": t.heading,
                    "company": company, "role": role,
                    "candidates": near,
                })

    return edits, unsure


def near_matches(company, role, by_key):
    """Same-company rows whose title relates by containment — likely the shorthand case."""
    c, t = M.norm_key(company, role)
    out = []
    for (kc, kt), row in by_key.items():
        if kc != c or kt == t:
            continue
        if kt in t or t in kt:
            out.append({"role": row.get("role"), "raw": row.get("raw"),
                        "date": row.get("date")})
    return out


def apply_plan(shortlist_lines, edits):
    """Rewrite the affected shortlist lines. Returns a NEW list; the caller's input is left
    untouched so a failure part-way through can't leave a half-edited list behind.

    Line count is unchanged (every edit rewrites one existing row), so indices stay valid
    for the whole pass.
    """
    shortlist_lines = list(shortlist_lines)
    tiers = M.find_tables(shortlist_lines, r"Tier \d+")
    by_idx = {}
    for t in tiers:
        for row in t.rows:
            by_idx[row.line_idx] = row
    for e in edits:
        row = by_idx.get(e["line_idx"])
        if row is None:
            continue
        row.set("status", "[x]")
        if row.table.has("notes"):
            row.set("notes", e["notes"])
        if e["clear_comment"] and row.table.has("comment"):
            row.set("comment", "")
        shortlist_lines[row.line_idx] = row.render()
    return shortlist_lines


def summarize(edits, unsure):
    flipped = sum(1 for e in edits if e["was_rejected"])
    return (f"{len(edits)} shortlist row(s) marked applied "
            f"({flipped} flipped from [nope]), {len(unsure)} need confirmation")


def main():
    ap = argparse.ArgumentParser(description="Cross-reference applied.md -> shortlist.md (§6)")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    ap.add_argument("--json", help="write the plan as JSON here ('-' = stdout)")
    args = ap.parse_args()

    shortlist = M.read_lines(args.shortlist)
    applied = M.read_lines(args.applied)
    edits, unsure = plan(shortlist, applied)

    if args.json:
        M.write_json(args.json, {"edits": edits, "needs_confirmation": unsure})

    if not edits and not unsure:
        print("nothing to cross-reference: no shortlist row matches an applied entry",
              file=sys.stderr)
        return

    for e in edits:
        flag = " (was [nope] — applied wins)" if e["was_rejected"] else ""
        print(f"  [x] {e['company']} — {e['role']}  applied {e['applied_date']} "
              f"(matched on {e['matched_on']}){flag}", file=sys.stderr)
        if e["was_rejected"] and e["old_comment"]:
            print(f"      clearing stale rejection comment: {e['old_comment']!r}", file=sys.stderr)

    for u in unsure:
        cands = ", ".join(f"{c['role']!r} ({c['date']})" for c in u["candidates"])
        print(f"  ?   {u['company']} — {u['role']}  possible match: {cands}", file=sys.stderr)

    if unsure:
        print("NOTE: '?' rows are shorthand/near matches — confirm with the user before "
              "marking them (playbook §6)", file=sys.stderr)

    if args.apply:
        shortlist = apply_plan(shortlist, edits)
        M.write_lines(args.shortlist, shortlist)
        print("applied: " + summarize(edits, unsure), file=sys.stderr)
    else:
        print("DRY RUN (use --apply to write): " + summarize(edits, unsure), file=sys.stderr)


if __name__ == "__main__":
    main()
