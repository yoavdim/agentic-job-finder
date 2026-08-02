#!/usr/bin/env python3
"""Stage 0f — push local `## Saved` statuses back to Simplify, then rebuild the mirror.

This is the driver that makes the Saved flow runnable without a human in the loop. It wires
the already-tested pieces together in the one order that is safe:

    capture (read-only) -> read local statuses -> classify -> report -> execute -> write

`saved_sync.plan()` is a pure function, so the "classify once from a pre-mutation snapshot"
guarantee is structural rather than a convention this script has to remember. See
saved_sync's module docstring for why the order matters: interleaving reads and mutations
double-acts on a row that is both rejected and stale, and loses the local record of why
anything left.

Dry run by default. `--apply` is the only thing that mutates, locally or remotely, and it
snapshots applied.md into `.kiro/backups/` first (these files are gitignored, so that
snapshot is the only undo).

Usage:
    saved_sync_cli.py --applied applied.md                    # dry run: show the plan
    saved_sync_cli.py --applied applied.md --apply
    saved_sync_cli.py --applied applied.md --capture cap.txt  # reuse an existing capture
"""
import argparse
import datetime
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PT = _load("parse_tracker", HERE / "parse_tracker.py")
SS = _load("saved_sync", HERE / "saved_sync.py")
SA = _load("simplify_actions", HERE / "simplify_actions.py")
CAP = _load("simplify_capture", HERE / "simplify_capture.py")


def read_local_saved(applied_lines):
    """{key: {status, comment, app_id, url, saved, company, role}} from `## Saved`.

    Keyed the same way the capture is (company + verbatim title), so `plan()` can pair the
    two sides without any fuzzy matching.
    """
    t_map, _u_map, loc, cols = PT.parse_existing(applied_lines, "## Saved")
    if not loc:
        return {}
    out = {}
    for key, row in t_map.items():
        cells = row["cells"]
        get = lambda name: (cells[cols[name]]
                            if name in cols and len(cells) > cols[name] else "")
        sid = ""
        m = PT.SIMPLIFY_LINK_RE.search(get("simplify") or "")
        if m:
            sid = m.group(1)
        out[key] = {
            "status": get("status"),
            "comment": get("comment"),
            "app_id": sid,
            "url": PT.clean_url(get("apply")),
            "saved": get("saved") or (cells[0] if cells else ""),
            "company": get("company"),
            "role": get("role"),
            "raw": get("raw"),
        }
    return out


def snapshot_rows(records):
    """parse_tracker/API records -> the snapshot shape saved_sync.plan() expects.

    The API already returns ISO dates, so no re-parsing is needed; the id and url are
    carried through so a decision has everything a downstream Rejected insert needs.
    """
    rows = []
    for r in records:
        rows.append({
            "key": PT.norm_key(r["company"], r["title"]),
            "company": r["company"],
            "title": r["title"],
            "location": r.get("location", ""),
            "saved": r.get("saved", ""),
            "applied": r.get("applied", ""),
            "url": r.get("url", ""),
            "id": r.get("id", ""),
        })
    return rows


def _insert_rejected(lines, rejected_rows, today):
    """Append confirmed deletes to `## Rejected`, each with its classified reason."""
    if not rejected_rows:
        return lines
    loc = PT.find_section(lines, "## Rejected")
    if not loc:
        return lines
    _, sep, _first, _end = loc
    cols = PT.header_cols(lines, sep)
    ncol = len(PT.split_md_row(lines[sep]))
    new = []
    for d, (reason, verbatim) in rejected_rows:
        cells = PT.build_cells(cols, ncol, date=today.isoformat(), company=d.company,
                               title=d.role, raw=d.role,
                               location=d.snapshot.get("location", ""),
                               apply_url=d.snapshot.get("url", ""),
                               reason=reason, comment=verbatim)
        new.append(PT.row_md(cells))
    at = PT.find_section(lines, "## Rejected")[2]
    for row in reversed(new):
        lines.insert(at, row)
    return lines


def _restamp_status(lines, retained):
    """Re-write the Status cell of rows whose push failed, so the next run retries them."""
    if not retained:
        return lines
    t_map, _u, loc, cols = PT.parse_existing(lines, "## Saved")
    if not loc or "status" not in cols:
        return lines
    ci = cols["status"]
    for key_tuple, status in retained.items():
        row = t_map.get(key_tuple)
        if not row:
            continue
        cells = row["cells"]
        while len(cells) <= ci:
            cells.append("")
        cells[ci] = status
        lines[row["idx"]] = PT.row_md(cells)
    return lines


def describe(decisions):
    """Only the rows this script will actually act on.

    ACT_KEEP and ACT_PROMOTE are both excluded: neither needs a remote call. A promotion
    just means Simplify is ahead of the local file, and reconciling that is
    `parse_tracker.merge()`'s job — it dedups against `## Applied`, which this script does
    not read. Listing them here implied 26 pending actions when there were none.
    """
    lines = []
    for d in decisions:
        if d.action in (SS.ACT_KEEP, SS.ACT_PROMOTE):
            continue
        tag = {SS.ACT_MARK_APPLIED: "-> mark_applied on Simplify",
               SS.ACT_DELETE: "-> DELETE from Simplify"}.get(d.action, d.action)
        extra = f" [{d.reason}{' auto' if d.auto else ''}]" if d.reason else ""
        idnote = "" if d.app_id else "  (no Simplify id — will be blocked)"
        lines.append(f"  {d.company} — {d.role}: {tag}{extra}{idnote}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Push Saved statuses to Simplify, then mirror")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--capture", default=None,
                    help="path to a saved capture (records JSON from simplify_capture.py); "
                         "omit to capture live via the tracker API")
    ap.add_argument("--size", type=int, default=50, help="API page size for the live capture")
    ap.add_argument("--today", default=datetime.date.today().isoformat())
    ap.add_argument("--max-saved-age-days", type=int,
                    default=SS.DEFAULT_MAX_SAVED_AGE_DAYS,
                    help=f"auto-reject rows saved longer ago than this "
                         f"(default {SS.DEFAULT_MAX_SAVED_AGE_DAYS})")
    ap.add_argument("--max-auto-delete", type=int, default=SS.DEFAULT_MAX_AUTO_DELETE,
                    help="refuse a run that would auto-delete more stale rows than this")
    ap.add_argument("--apply", action="store_true",
                    help="perform the pushes and write applied.md (default: dry run)")
    ap.add_argument("--force", action="store_true", help="override the guards")
    ap.add_argument("--json", help="write the plan/outcomes as JSON here ('-' = stdout)")
    args = ap.parse_args(argv)

    today = datetime.date.fromisoformat(args.today)

    # ---- 1. capture (read-only, via the tracker API — no scraping) --------------
    if args.capture:
        records = json.loads(Path(args.capture).read_text(encoding="utf-8"))
        cap_meta = {"source": args.capture, "rows": len(records)}
        complete = True                 # a saved capture file is taken as authoritative
    else:
        res = CAP.capture(size=args.size)
        if "error" in res:
            print(f"capture failed: {res['error']}", file=sys.stderr)
            return 1
        records = res["records"]
        complete = res["complete"]
        cap_meta = {k: v for k, v in res.items() if k not in ("records", "urls")}
    if not records:
        print("capture returned no rows — refusing to rebuild the mirror from nothing",
              file=sys.stderr)
        return 1
    if not complete and not args.force:
        print("REFUSED: the capture did not reach the endpoint's reported total, so the "
              "Saved mirror rebuild could delete rows it simply didn't see. Re-run, or pass "
              "--force if you are certain.", file=sys.stderr)
        return 2

    urls_map = {}
    for r in records:
        key = CAP.norm_key(r["company"], r["title"])
        if key != "||" and r.get("id"):
            urls_map[key] = {"id": r["id"], "url": CAP.strip_tracking(r.get("url", ""))}

    # ---- 2. read local intent from the Saved table ------------------------------
    applied_lines = PT.migrate_schema(
        Path(args.applied).read_text(encoding="utf-8").split("\n"))
    local = read_local_saved(applied_lines)

    # ---- 3. classify ONCE, from the pre-mutation snapshot -----------------------
    snap = snapshot_rows(records)
    decisions = SS.plan(snap, local, today, max_saved_age_days=args.max_saved_age_days)
    summary = SS.summarize(decisions)
    guards = SS.guard_errors(decisions, max_auto_delete=args.max_auto_delete)

    # ---- 4. report --------------------------------------------------------------
    saved_recs = [r for r in records if not r["applied"]]
    print(f"captured {len(records)} row(s) ({len(saved_recs)} not-yet-applied); "
          f"local Saved table holds {len(local)}", file=sys.stderr)
    print(f"plan: {summary['manual_deletes']} rejected by you, "
          f"{summary['auto_deletes']} stale auto-reject(s), "
          f"{summary['counts'].get(SS.ACT_MARK_APPLIED, 0)} to mark applied, "
          f"{summary['counts'].get(SS.ACT_KEEP, 0)} unchanged", file=sys.stderr)
    actionable = describe(decisions)
    for line in actionable:
        print(line, file=sys.stderr)
    if not actionable:
        print("  nothing to push: no Saved row has a local `applied`/`rejected` status and "
              "none are stale", file=sys.stderr)

    if guards and not args.force:
        for g in guards:
            print(f"REFUSED  {g}", file=sys.stderr)
        print("Nothing was pushed or written. Pass --force to override.", file=sys.stderr)
        return 2
    for g in guards:
        print(f"WARN (forced past guard): {g}", file=sys.stderr)

    if args.json:
        blob = {"capture": cap_meta, "summary": summary,
                "decisions": [d.as_dict() for d in decisions]}
        (print(json.dumps(blob, indent=1)) if args.json == "-"
         else Path(args.json).write_text(json.dumps(blob, indent=1), encoding="utf-8"))

    if not args.apply:
        print("DRY RUN: nothing pushed, nothing written. Re-run with --apply.",
              file=sys.stderr)
        return 0

    # ---- 5. execute the pushes (the only remote mutation) -----------------------
    tab = SA.find_tracker_tab()
    if tab is None and SS.needs_push(decisions):
        print("no simplify.jobs/tracker tab open — cannot push. Open it and retry.",
              file=sys.stderr)
        return 1
    actions = SS.SimplifyActions(SA, tab_id=tab.get("id") if tab else None)
    outcomes = SS.execute(decisions, actions)
    for o in outcomes:
        if o.status != SS.OUTCOME_OK:
            print(f"  [{o.status}] {o.decision.company} — {o.decision.role}: {o.detail}",
                  file=sys.stderr)

    # ---- 6. write applied.md ourselves (no hand-off) ----------------------------
    # The pushes changed Simplify, so the capture records are adjusted to match before the
    # mirror is rebuilt from them: a confirmed delete is dropped (and filed under Rejected),
    # a confirmed mark_applied / applied-wins is stamped applied (so it moves to Applied). A
    # failed push leaves its record untouched (stays saved) and its Status is re-stamped
    # afterwards so the next run retries it — never guessing that a push worked.
    by_key = {PT.norm_key(r["company"], r["title"]): r for r in records}
    rejected_rows, retained_status = [], {}
    for o in outcomes:
        d = o.decision
        rec = by_key.get(d.key)
        if d.action == SS.ACT_DELETE and o.ok:
            if rec:
                rec["_drop"] = True
            rejected_rows.append((d, SS.classify_reason(d.comment)))
        elif d.action in (SS.ACT_MARK_APPLIED,) and o.ok and rec and not rec["applied"]:
            rec["applied"] = today.isoformat()
        elif o.status == SS.OUTCOME_APPLIED_WINS and rec and not rec["applied"]:
            rec["applied"] = o.applied_date or today.isoformat()
        elif d.action in (SS.ACT_MARK_APPLIED, SS.ACT_DELETE) and not o.ok:
            retained_status[d.key] = SS.retained_status(o)

    merge_records = [r for r in records if not r.get("_drop")]
    metadata, merge_urls = PT.build_url_maps(
        {k: v for k, v in urls_map.items()})
    warn = []
    applied_lines, stats = PT.merge(applied_lines, merge_records, warn,
                                    urls=merge_urls, metadata=metadata)
    applied_lines = _insert_rejected(applied_lines, rejected_rows, today)
    applied_lines = _restamp_status(applied_lines, retained_status)
    applied_lines = PT.update_sync_header(applied_lines, today.isoformat())

    PT._flush({args.applied: applied_lines}, apply=True)

    print(f"wrote {args.applied}: +{stats['added_applied']} applied, "
          f"+{stats['added_saved']} saved (mirror keeps {stats['mirror_saved']}, "
          f"drops {stats['mirror_dropped']}), {len(rejected_rows)} -> Rejected, "
          f"{stats['id_linked']} rows carry a Simplify id", file=sys.stderr)
    for w in warn:
        print("WARN:", w, file=sys.stderr)
    if retained_status:
        print(f"NOTE: {len(retained_status)} push(es) failed; their Status was kept so the "
              f"next run retries them", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
