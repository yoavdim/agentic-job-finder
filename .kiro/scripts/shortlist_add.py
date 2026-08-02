#!/usr/bin/env python3
"""Write new rows into shortlist.md tiers, or log a cut in the Excluded section (§2/§4).

Replaces hand-editing pipe tables. Two reasons that matters:
  1. Column positions differ between tables, and getting them wrong corrupts a row silently.
     `md_tables` resolves by header name so this can't drift.
  2. Every insert is dedup-checked against applied.md + shortlist.md first, so a role that's
     already applied/shortlisted/judgment-rejected can't be re-added by accident.

Input is a JSON list of candidates:
    [{"company":"Foo","role":"Bar","location":"Toronto","url":"https://...",
      "tier":2,"notes":"✅ embedded","posted":"3 days ago","comment":""}]

`posted` accepts anything jobdates understands ("3 days ago", "July 20, 2026", ISO); it's
normalized into a `📅 posted YYYY-MM-DD` note. `added` defaults to today.

Usage:
    shortlist_add.py --candidates new.json                     # dry run
    shortlist_add.py --candidates new.json --apply
    shortlist_add.py --exclude cuts.json --apply               # [{company,role,reason}]
    shortlist_add.py --candidates new.json --json plan.json    # plan as JSON ('-' = stdout)
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import md_tables as M
import jobdates as JD
import candidate_lint as CL
from dedup_index import DedupIndex

EXCLUDED_HEADING = "## Excluded"


def fmt_apply(url, label="Apply"):
    return f"[{label}]({url})" if url else ""


def build_notes(cand, today):
    notes = (cand.get("notes") or "").strip()
    posted_iso = JD.parse_posting_date(cand.get("posted", ""), today)
    if posted_iso:
        notes = JD.add_posted_note(notes, posted_iso)
    return notes, posted_iso


def plan(shortlist_lines, applied_lines, candidates, today, force=False,
         max_age_days=CL.DEFAULT_MAX_AGE_DAYS, strict=True, manual_lines=None):
    """Returns (inserts, skipped). inserts = [{tier_heading, row, ...}]."""
    # Build the index from the in-memory line lists so callers can chain edits.
    idx = DedupIndex.from_lines(applied_lines, shortlist_lines, manual_lines)

    tiers = {}
    for t in M.find_tables(shortlist_lines, r"Tier \d+"):
        num = t.heading.split()[1].rstrip(":—-")
        tiers[num] = t

    # A single harvest routinely finds the same role on two sources (LinkedIn and BuiltIn,
    # say). The DedupIndex only knows what's already on disk, so intra-batch repeats have
    # to be tracked as we go or both copies get inserted.
    batch_keys, batch_codes = set(), set()

    inserts, skipped = [], []
    for cand in candidates:
        company = cand.get("company", "").strip()
        role = cand.get("role", cand.get("title", "")).strip()
        url = cand.get("url", "").strip()
        tier = str(cand.get("tier", "")).strip()

        if not company or not role:
            skipped.append({**cand, "why": "missing company or role"})
            continue
        if tier not in tiers:
            skipped.append({**cand, "why": f"no '## Tier {tier}' table in shortlist"})
            continue

        key = M.norm_key(company, role)
        code = M.ats_code(url) if url else ""
        code = code if M.is_strong_code(code) else ""
        if key in batch_keys or (code and code in batch_codes):
            skipped.append({**cand, "why": "duplicate of an earlier candidate in this batch"})
            continue

        v = idx.check(company, role, url)
        if v.skip and not force:
            skipped.append({**cand, "why": f"{v.bucket} ({v.matched_on}): {v.detail}"})
            continue

        # Structural checks only (candidate_lint deliberately judges no fit): did the
        # caller record the evidence and classification the playbook asks for, and is the
        # posting still inside the recency window. Advisory findings never block.
        findings = CL.lint_candidate(cand, today=today, max_age_days=max_age_days,
                                     strict=strict)
        blockers = CL.blocking(findings)
        if blockers and not force:
            why = "; ".join(f"[{f['code']}] {f['detail']}" for f in blockers)
            skipped.append({**cand, "why": f"incomplete row: {why}", "lint": findings})
            continue

        t = tiers[tier]
        notes, posted_iso = build_notes(cand, today)
        cells = t.build(
            status="[ ]",
            date=cand.get("added") or today,
            company=company,
            role=role,
            location=cand.get("location", ""),
            apply=fmt_apply(url, cand.get("apply_label", "Apply")),
            notes=notes,
            comment=cand.get("comment", ""),
        )
        batch_keys.add(key)
        if code:
            batch_codes.add(code)
        inserts.append({
            "tier": tier, "heading": t.heading, "company": company, "role": role,
            "row": M.row_md(cells), "posted": posted_iso,
            "near_duplicate": v.detail if v.bucket == "new" and v.detail else "",
            # Advisory notes always ride along; blockers only appear here when --force was
            # used, so the record shows the insert happened over an objection.
            "advisory": CL.advisory(findings),
            "lint_overridden": blockers if blockers else [],
        })

    return inserts, skipped


def apply_inserts(shortlist_lines, inserts):
    """Insert newest-first at the top of each tier's data block."""
    by_tier = {}
    for ins in inserts:
        by_tier.setdefault(ins["heading"], []).append(ins["row"])
    for heading, rows in by_tier.items():
        shortlist_lines = M.insert_rows(shortlist_lines, "## " + heading, rows,
                                       newest_first=True)
    return shortlist_lines


def add_excluded(shortlist_lines, cuts, today):
    """Append '- **Company — Role** (date): reason' bullets under ## Excluded."""
    end = None
    start = None
    for i, l in enumerate(shortlist_lines):
        s = l.strip()
        if s.lower().startswith(EXCLUDED_HEADING.lower()):
            start = i
            continue
        if start is not None and s.startswith("## "):
            end = i
            break
    if start is None:
        raise KeyError("shortlist.md has no '## Excluded' section")
    if end is None:
        end = len(shortlist_lines)
    # insert after the last bullet in the section
    last = start
    for i in range(start, end):
        if shortlist_lines[i].strip().startswith("-"):
            last = i
    bullets = [f"- **{c.get('company','')} — {c.get('role', c.get('title',''))}** "
               f"({c.get('date', today)}): {c.get('reason','no reason given')}"
               for c in cuts]
    return shortlist_lines[:last + 1] + bullets + shortlist_lines[last + 1:]


def main():
    ap = argparse.ArgumentParser(description="Add rows to shortlist.md tiers / Excluded")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--manual", default="manual.md",
                    help="manual.md inbox; its rows are real applications/saves awaiting "
                         "bookkeeping, so they block re-suggestion too")
    ap.add_argument("--candidates", help="JSON list of new roles to insert")
    ap.add_argument("--exclude", help="JSON list of [{company,role,reason}] cuts to log")
    ap.add_argument("--today", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--force", action="store_true",
                    help="insert even if dedup says known or the prefs lint objects")
    ap.add_argument("--max-age-days", type=int, default=CL.DEFAULT_MAX_AGE_DAYS,
                    help=f"recency cutoff for the stale-posting check "
                         f"(default {CL.DEFAULT_MAX_AGE_DAYS})")
    ap.add_argument("--no-strict", action="store_true",
                    help="drop the `evidence` requirement (playbook §1f asks for the "
                         "responsibilities + requirements to be read and recorded before a "
                         "role is added; use this only for pre-reviewed bulk imports)")
    ap.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    ap.add_argument("--json", help="write the plan as JSON here ('-' = stdout)")
    args = ap.parse_args()

    if not args.candidates and not args.exclude:
        ap.error("give --candidates and/or --exclude")

    shortlist = M.read_lines(args.shortlist)
    applied = M.read_lines(args.applied)
    manual = M.read_lines(args.manual) if Path(args.manual).exists() else []

    inserts, skipped, cuts = [], [], []
    if args.candidates:
        with open(args.candidates, encoding="utf-8") as fh:
            cands = json.load(fh)
        inserts, skipped = plan(shortlist, applied, cands, args.today, args.force,
                                max_age_days=args.max_age_days,
                                strict=not args.no_strict, manual_lines=manual)
        for ins in inserts:
            warn = f"  ⚠️ {ins['near_duplicate']}" if ins["near_duplicate"] else ""
            print(f"  + Tier {ins['tier']}: {ins['company']} — {ins['role']}"
                  f"{' 📅 ' + ins['posted'] if ins['posted'] else ''}{warn}", file=sys.stderr)
            for f in ins["advisory"]:
                print(f"      note: [{f['code']}] {f['detail']}", file=sys.stderr)
            for f in ins["lint_overridden"]:
                print(f"      ⚠️ FORCED past check: [{f['code']}] {f['detail']}",
                      file=sys.stderr)
        for s in skipped:
            print(f"  - skip {s.get('company')} — {s.get('role', s.get('title'))}: {s['why']}",
                  file=sys.stderr)
        lint_blocked = [s for s in skipped if s.get("lint")]
        if lint_blocked:
            print(f"\n{len(lint_blocked)} candidate(s) refused as incomplete — the row is "
                  f"missing evidence or a Notes classification, not judged a bad fit. Fill "
                  f"the field in, or pass --force.", file=sys.stderr)

    if args.exclude:
        with open(args.exclude, encoding="utf-8") as fh:
            cuts = json.load(fh)
        for c in cuts:
            print(f"  x excluded {c.get('company')} — {c.get('role', c.get('title'))}: "
                  f"{c.get('reason')}", file=sys.stderr)

    if args.json:
        M.write_json(args.json, {
            "inserts": inserts,
            "skipped": skipped,
            "cuts": [{**c, "date": c.get("date", args.today)} for c in cuts],
        })

    if args.apply:
        if inserts:
            shortlist = apply_inserts(shortlist, inserts)
        if cuts:
            shortlist = add_excluded(shortlist, cuts, args.today)
        M.write_lines(args.shortlist, shortlist)
        print(f"wrote {len(inserts)} row(s), {len(cuts)} exclusion(s); "
              f"{len(skipped)} skipped as already-known", file=sys.stderr)
    else:
        print(f"DRY RUN (use --apply): {len(inserts)} row(s), {len(cuts)} exclusion(s), "
              f"{len(skipped)} skipped", file=sys.stderr)


if __name__ == "__main__":
    main()
