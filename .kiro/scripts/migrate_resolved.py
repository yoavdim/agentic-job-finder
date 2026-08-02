#!/usr/bin/env python3
"""Stage 0b — migrate resolved shortlist rows into applied.md (search-playbook §7).

`[x]` applied  -> the `## Applied` table (or the comment is folded onto the existing row)
`[nope]` rejected -> the `## Rejected` table, with a classified reason code

Then the migrated rows are deleted from shortlist.md.

Simplify Saved rows are NOT handled here: that list lives in applied.md's `## Saved`
table and is synced by the skill's `saved_sync` (push-then-pull), which owns the
mark-applied / delete-on-reject push-back. This script only migrates the user-curated
tiers.

Reason classification is near-deterministic because tracker.html's reject dialog already
writes the comment as "<reason-code> — <free text>". The keyword fallback only runs on
hand-typed comments.

Usage:
    migrate_resolved.py --shortlist shortlist.md --applied applied.md            # dry run
    migrate_resolved.py --shortlist shortlist.md --applied applied.md --apply
    migrate_resolved.py ... --json plan.json
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import md_tables as M
import url_identity as UI
from reasons import split_leading_code


# Fallback keyword -> code, first match wins. Only used when the comment has no
# explicit leading reason code (i.e. it was hand-typed, not set via the reject dialog).
KEYWORD_RULES = [
    ("link-broken",     r"\b404\b|\b410\b|dead link|broken|link fail|url fail|dns"),
    ("listing-removed", r"remov|no longer|closed|filled|expired|not found|job is gone|delisted|taken down"),
    ("too-old",         r"too old|stale|outdated|old posting"),
    ("not-qualified",   r"not qualif|under.?qualif|too senior|senior|years? of exp|\d+\s*\+?\s*yrs|phd|master|degree required|overqualif"),
    ("not-interested",  r"not interest|no interest|don'?t want|pass on|not appealing|not for me|meh"),
    ("sketchy-site",    r"sketch|scam|spam|shady|untrust|fishy"),
]

APPLIED_NOTE_RE = re.compile(r"\*\*APPLIED\s+(\d{4}-\d{2}-\d{2})\*\*", re.I)


def classify_reason(comment):
    """Return (code, comment_verbatim). Empty comment -> 'unknown'.

    An explicit leading code (`reasons.split_leading_code`, shared with saved_sync) wins;
    otherwise a keyword fallback for hand-typed comments; otherwise 'other'.
    """
    code, c = split_leading_code(comment)
    if code:
        return code, c
    if not c:
        return "unknown", ""
    low = c.lower()
    for code, pat in KEYWORD_RULES:
        if re.search(pat, low):
            return code, c
    return "other", c


def applied_date_for(row, today):
    """Applied date = the APPLIED note in Notes if present, else the row's Added date."""
    m = APPLIED_NOTE_RE.search(row.get("notes"))
    if m:
        return m.group(1)
    return row.get("date") or today


def index_applied(applied_lines):
    """Build dedup indexes over the Applied table: {key: row}, {ats_code: row}."""
    t = M.find_table(applied_lines, "## Applied")
    if t is None:
        return None, {}, {}
    by_key, by_code = {}, {}
    for row in t.rows:
        keys, code = M.row_keys(row)
        for k in keys:
            by_key[k] = row
        if code:
            by_code[code] = row
    return t, by_key, by_code


def merge_comment(existing, incoming):
    """Never clobber a non-empty existing comment; append instead."""
    e, i = (existing or "").strip(), (incoming or "").strip()
    if not i:
        return e, False
    if not e:
        return i, True
    if i.lower() in e.lower():
        return e, False
    return f"{e}; {i}", True


def plan(shortlist_lines, applied_lines, today):
    """Compute the migration without touching anything. Returns a plan dict.

    Pure: no I/O, no remote calls. Simplify Saved rows are not this script's concern —
    they live in applied.md's `## Saved` and are handled by the skill's `saved_sync`.
    """
    tiers = M.find_tables(shortlist_lines, r"Tier \d+")
    _, by_key, by_code = index_applied(applied_lines)

    actions = {
        "applied_new": [],      # rows to insert into ## Applied
        "applied_comment": [],  # {applied_idx, line_idx, ...} edits onto existing Applied rows
        "rejected_new": [],     # rows to insert into ## Rejected
        "delete": [],           # shortlist line indices to remove
        "skipped": [],
        "warnings": [],
    }

    for t in tiers:
        if not t.has("status"):
            continue
        for line_idx, n in t.ragged_rows():
            actions["warnings"].append(
                f"{t.heading}: row at line {line_idx + 1} has {n} cells, expected {t.ncol} "
                f"— the row is shifted/corrupt, so its fields are being read wrong")
        for row in t.rows:
            st = row.get("status")
            company, role = row.get("company"), row.get("role")
            if not company and not role:
                continue
            if M.has_no_status(st):
                # Neither open nor resolved: no stage would ever touch this row.
                actions["warnings"].append(
                    f"{t.heading}: {company} — {role} (line {row.line_idx + 1}) has no "
                    f"status box, so no stage will ever pick it up. Give it [ ], [x] or "
                    f"[nope]")
                continue

            if M.is_applied(st):
                comment = row.get("comment")
                keys, code = M.row_keys(row)
                match = by_code.get(code) if code else None
                if match is None:
                    match = next((by_key[k] for k in keys if k in by_key), None)

                if match is not None:
                    merged, changed = merge_comment(match.get("comment"), comment)
                    if changed:
                        # TWO different files are indexed here, so the two indices must
                        # stay distinct: `applied_idx` addresses the row in applied.md
                        # that receives the comment, `line_idx` addresses the shortlist
                        # row being retired. Collapsing them into one field silently
                        # dropped the user's comment (or wrote it onto an unrelated
                        # application whose applied.md line number happened to collide).
                        actions["applied_comment"].append({
                            "applied_idx": match.line_idx,
                            "line_idx": row.line_idx,
                            "company": company, "role": role,
                            "comment": merged,
                        })
                    else:
                        actions["skipped"].append({
                            "company": company, "role": role,
                            "why": "already in ## Applied, no new comment",
                        })
                else:
                    actions["applied_new"].append({
                        "date": applied_date_for(row, today),
                        "company": company, "role": role, "raw": role,
                        "location": row.get("location"),
                        "apply": row.get("apply"),
                        "comment": comment,
                        "tier": t.heading,
                        "line_idx": row.line_idx,
                    })
                actions["delete"].append(row.line_idx)

            elif M.is_rejected(st):
                reason, verbatim = classify_reason(row.get("comment"))
                actions["rejected_new"].append({
                    "date": today,
                    "company": company, "role": role, "raw": role,
                    "location": row.get("location"),
                    "apply": row.get("apply"),
                    "reason": reason,
                    "comment": verbatim,
                    "tier": t.heading,
                    "line_idx": row.line_idx,
                })
                actions["delete"].append(row.line_idx)

    return actions


def plan_manual(manual_lines, applied_lines, actions=None):
    """Add `manual.md` inbox rows that can be resolved WITHOUT an LLM to the plan.

    `manual.md` holds `| Added | URL | Status |` — a URL and nothing else. Turning a URL into
    company/role/location is normally reading comprehension (stage 0c), but the subset whose
    fields the URL *structure* already encodes can be filed mechanically, which is what lets
    a fully-scripted sweep drain part of the inbox.

    Deliberately narrow:
    - `applied` + `url_identity` yields BOTH company and role -> insert into `## Applied`,
      drop the manual row.
    - `applied` + anything missing -> LEFT IN PLACE and reported. A half-filled ledger row is
      worse than an item still sitting in a box labelled "inbox", and nothing is lost: the
      row stays visible in tracker.html and `dedup_index` now indexes it, so it can't be
      re-suggested while it waits.
    - `saved` -> ALWAYS left. Placing it in the shortlist needs a tier, a Notes
      classification and evidence, all of which are judgment.

    Any link text the row carries is stored verbatim as `Raw`, never parsed: the observed
    page titles have no shared format (four hosts, four shapes, one of them
    "Thank you for applying"), so interpreting them belongs to the LLM.

    Returns the actions dict, extended with `manual_new` / `manual_delete` / `manual_left`.
    """
    actions = actions if actions is not None else {
        "applied_new": [], "applied_comment": [], "rejected_new": [], "delete": [],
        "skipped": [], "warnings": [],
    }
    actions.setdefault("manual_new", [])
    actions.setdefault("manual_delete", [])
    actions.setdefault("manual_left", [])

    t = M.find_table(manual_lines, "## Entries")
    if t is None:
        return actions

    _, by_key, by_code = index_applied(applied_lines)
    ci_url = t.col("url") if t.has("url") else 1
    ci_status = t.col("status") if t.has("status") else 2

    for row in t.rows:
        cells = row.cells
        cell = cells[ci_url] if len(cells) > ci_url else ""
        url = M.extract_url(cell)
        status = (cells[ci_status] if len(cells) > ci_status else "").strip().lower()
        added = (cells[0] if cells else "").strip()
        # markdown link text, when tracker.html captured a page title. Kept verbatim.
        m = re.match(r"\s*\[([^\]]*)\]\(", cell or "")
        link_text = (m.group(1).strip() if m else "")

        def leave(why):
            actions["manual_left"].append({"line_idx": row.line_idx, "url": url,
                                           "status": status or "saved", "why": why})

        if not url:
            leave("no URL in the row")
            continue
        if status != "applied":
            leave(f"status is {status or 'saved'!r}: placing it needs a tier + Notes "
                  f"classification, which is judgment (stage 0c)")
            continue

        ident = UI.identify(url, raw_title=link_text)
        if not ident.complete:
            leave(f"URL structure yields no {' or '.join(ident.missing())} "
                  f"(stage 0c can read the page)")
            continue

        code = M.ats_code(url)
        match = by_code.get(code) if M.is_strong_code(code) else None
        if match is None:
            match = next((by_key[k] for k in (M.norm_key(ident.company, ident.role),)
                          if k in by_key), None)
        if match is not None:
            actions["skipped"].append({
                "company": ident.company, "role": ident.role,
                "why": "manual.md row already in ## Applied",
            })
            actions["manual_delete"].append(row.line_idx)
            continue

        actions["manual_new"].append({
            "date": added,                       # §10: the Added date IS the applied date
            "company": ident.company, "role": ident.role,
            "raw": ident.raw_title or ident.role,
            "location": "",                      # never encoded in a URL
            "apply": f"[Apply]({url})",
            "comment": "",
            "line_idx": row.line_idx,
            "source": ident.source,
        })
        actions["manual_delete"].append(row.line_idx)

    return actions


def apply_plan(shortlist_lines, applied_lines, actions, manual_lines=None):
    """Apply a plan. Returns (new_shortlist_lines, new_applied_lines, new_manual_lines)."""
    applied_lines = list(applied_lines)

    # 1. comment edits onto existing Applied rows (index-stable: no lines added yet).
    #    `applied_idx` indexes applied.md — never the shortlist's `line_idx`.
    if actions["applied_comment"]:
        t = M.find_table(applied_lines, "## Applied")
        by_idx = {r.line_idx: r for r in t.rows} if t else {}
        for edit in actions["applied_comment"]:
            idx = edit.get("applied_idx")
            if idx is None:
                raise KeyError(
                    f"applied_comment edit for {edit.get('company')} — {edit.get('role')} "
                    f"has no 'applied_idx'; refusing to guess which applied.md row to write")
            row = by_idx.get(idx)
            if row is None:
                raise KeyError(
                    f"applied_comment edit for {edit.get('company')} — {edit.get('role')} "
                    f"points at applied.md line {idx}, which is not a row of '## Applied'")
            row.set("comment", edit["comment"])
            applied_lines[row.line_idx] = row.render()

    # 2. new rows into ## Applied (newest first). Shortlist migrations and drained
    #    manual.md rows land in the same table, so they are inserted together and sorted
    #    as one batch.
    new_applied = list(actions["applied_new"]) + list(actions.get("manual_new", []))
    if new_applied:
        t = M.find_table(applied_lines, "## Applied")
        rows = [M.row_md(t.build(
            date=a["date"], company=a["company"], role=a["role"], raw=a["raw"],
            location=a["location"], apply=a["apply"], comment=a["comment"],
        )) for a in sorted(new_applied, key=lambda a: a["date"], reverse=True)]
        applied_lines = M.insert_rows(applied_lines, "## Applied", rows, newest_first=True)

    # 3. new rows into ## Rejected
    if actions["rejected_new"]:
        t = M.find_table(applied_lines, "## Rejected")
        if t is None:
            raise KeyError("applied.md has no '## Rejected' table")
        rows = [M.row_md(t.build(
            date=a["date"], company=a["company"], role=a["role"], raw=a["raw"],
            location=a["location"], apply=a["apply"], reason=a["reason"],
            comment=a["comment"],
        )) for a in actions["rejected_new"]]
        applied_lines = M.insert_rows(applied_lines, "## Rejected", rows, newest_first=True)

    # 4. drop migrated rows from shortlist, and drained rows from the manual inbox
    shortlist_lines = M.delete_lines(shortlist_lines, actions["delete"])
    if manual_lines is not None:
        manual_lines = M.delete_lines(list(manual_lines),
                                      actions.get("manual_delete", []))
    return shortlist_lines, applied_lines, manual_lines


def summarize(actions):
    by_reason = {}
    for a in actions["rejected_new"]:
        by_reason[a["reason"]] = by_reason.get(a["reason"], 0) + 1
    parts = [f"{len(actions['applied_new'])} -> ## Applied"]
    if actions.get("manual_new"):
        parts.append(f"{len(actions['manual_new'])} manual.md row(s) -> ## Applied")
    if actions.get("manual_left"):
        parts.append(f"{len(actions['manual_left'])} manual.md row(s) left for stage 0c")
    parts += [
        f"{len(actions['applied_comment'])} comment(s) folded onto existing Applied rows",
        f"{len(actions['rejected_new'])} -> ## Rejected",
        f"{len(actions['delete'])} row(s) removed from shortlist",
    ]
    if actions["warnings"]:
        parts.append(f"{len(actions['warnings'])} warning(s)")
    if by_reason:
        parts.append("reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(by_reason.items())))
    if actions["skipped"]:
        parts.append(f"{len(actions['skipped'])} skipped (already tracked)")
    return "; ".join(parts)


def main():
    ap = argparse.ArgumentParser(description="Migrate resolved shortlist rows (playbook §7)")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--manual", default=None,
                    help="also drain manual.md rows whose company AND role are encoded in "
                         "the URL structure (no LLM). Rows needing the page read are left "
                         "in place for stage 0c.")
    ap.add_argument("--today", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--apply", action="store_true", help="write the files (default: dry run)")
    ap.add_argument("--json", help="write the plan as JSON here ('-' = stdout)")
    args = ap.parse_args()

    shortlist = M.read_lines(args.shortlist)
    applied = M.read_lines(args.applied)
    manual = M.read_lines(args.manual) if args.manual else None

    actions = plan(shortlist, applied, args.today)
    if manual is not None:
        actions = plan_manual(manual, applied, actions)

    if args.json:
        M.write_json(args.json, actions)

    if not (actions["delete"] or actions.get("manual_delete")):
        print("nothing to migrate: no [x]/[nope] shortlist rows and no drainable "
              "manual.md rows", file=sys.stderr)
        for left in actions.get("manual_left", []):
            print(f"  manual.md left: {left['url'][:66]} — {left['why']}", file=sys.stderr)
        return

    if args.apply:
        shortlist, applied, manual = apply_plan(shortlist, applied, actions, manual)
        M.write_lines(args.shortlist, shortlist)
        M.write_lines(args.applied, applied)
        if manual is not None:
            M.write_lines(args.manual, manual)
        print("migrated: " + summarize(actions), file=sys.stderr)
        for w in actions["warnings"]:
            print("WARN:", w, file=sys.stderr)
    else:
        print("DRY RUN (use --apply to write): " + summarize(actions), file=sys.stderr)
        for a in actions["applied_new"]:
            print(f"  [x] {a['company']} — {a['role']}  ({a['date']})", file=sys.stderr)
        for a in actions["applied_comment"]:
            print(f"  [x] {a['company']} — {a['role']}  comment -> {a['comment']!r}", file=sys.stderr)
        for a in actions["rejected_new"]:
            print(f"  [nope] {a['company']} — {a['role']}  reason={a['reason']}", file=sys.stderr)

        for w in actions["warnings"]:
            print("WARN:", w, file=sys.stderr)


if __name__ == "__main__":
    main()
