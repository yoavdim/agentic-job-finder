#!/usr/bin/env python3
"""End-of-pass housekeeping: header dates, Scratch tab cleanup.

Small, boring, and easy to forget — which is exactly why it's scripted:
  --bump-searched   `**Last searched the web:**` in shortlist.md -> today
  --sync-header     `**Last synced from Simplify:**` in applied.md -> today + live counts
  --close-scratch   close the whole "Scratch" tab group via Tab Share

The Tab Share `/close` call is safety-gated by the extension: passing a concrete
`expectGroup` closes only tabs in that group, so keepers in "Job Search" can't be touched
even if the selector is wrong.

Usage:
    housekeeping.py --bump-searched --sync-header
    housekeeping.py --close-scratch
    housekeeping.py --all
"""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import md_tables as M
import tab_share as TS

SEARCHED_RE = re.compile(r"^\*\*Last searched the web:\*\*\s*.*$")
SYNCED_RE = re.compile(r"^\*\*Last synced from Simplify:\*\*\s*.*$")


def bump_searched(lines, date):
    """Set the shortlist's last-searched header.

    Returns (lines, status) where status is "bumped" | "current" | "missing".
    "missing" is reported distinctly because a renamed header would otherwise make the
    bump a permanent silent no-op indistinguishable from "already today".
    """
    lines = list(lines)
    for i, l in enumerate(lines):
        if SEARCHED_RE.match(l.strip()):
            new = f"**Last searched the web:** {date}"
            if lines[i].strip() == new:
                return lines, "current"
            lines[i] = new
            return lines, "bumped"
    return lines, "missing"


def sync_header(lines, date):
    """Recompute applied.md's sync header from the actual table sizes.

    Returns (lines, status, (n_applied, n_saved)); status as per bump_searched().
    """
    lines = list(lines)
    applied_t = M.find_table(lines, "## Applied")
    saved_t = M.find_table(lines, "## Saved")
    n_applied = len(applied_t.rows) if applied_t else 0
    n_saved = len(saved_t.rows) if saved_t else 0
    for i, l in enumerate(lines):
        if SYNCED_RE.match(l.strip()):
            new = (f"**Last synced from Simplify:** {date} · {n_applied} applied · "
                   f"{n_saved} saved (not yet applied)")
            if lines[i].strip() == new:
                return lines, "current", (n_applied, n_saved)
            lines[i] = new
            return lines, "bumped", (n_applied, n_saved)
    return lines, "missing", (n_applied, n_saved)


def _post(path, payload, timeout=20):
    # post_raw (not tab_share.post): callers here need a raised-looking failure to
    # distinguish "the call errored" from "{} came back empty", which the retrying
    # post() can't tell apart (see close_scratch's error-vs-zero-closed comment below).
    resp, err = TS.post_raw(path, payload, timeout=timeout)
    if err:
        raise RuntimeError(err)
    return resp


def list_tabs(timeout=5):
    return TS.get("/tabs", timeout=timeout)


def host_of(url):
    m = re.match(r"https?://([^/:]+)", url or "")
    return m.group(1).lower() if m else ""


def close_scratch(group="Scratch"):
    """Close every tab in the `group` tab group. Returns (closed, rejected, error).

    The extension's `/close` gate requires BOTH `expectHost` and `expectGroup`
    (verified: a group-only call answers `{"error": "expectHost required (safety)"}`),
    and `/tabs` does not report group membership — so the group filter can only be
    applied server-side. We therefore enumerate the distinct hosts that are open and
    issue one gated call per host. A tab closes only when it matches the host AND is
    in `group`, so keepers in "Job Search" can never be caught by this.

    An `error` reply is returned as a real failure rather than being counted as
    "0 closed", which is how the earlier group-only call looked like a success.
    """
    tabs = list_tabs()
    if tabs is None:
        return 0, 0, "Tab Share not reachable on :8766"

    hosts = sorted({h for h in (host_of(t.get("url")) for t in tabs.get("tabs", [])) if h})
    if not hosts:
        return 0, 0, None

    closed = rejected = 0
    errors = []
    for host in hosts:
        try:
            res = _post("/close", {"expectGroup": group, "expectHost": host})
        except Exception as e:
            errors.append(f"{host}: {e}")
            continue
        if isinstance(res, dict) and res.get("error"):
            errors.append(f"{host}: {res['error']}")
            continue
        closed += len(res.get("closed") or [])
        # host-mismatch rejections are expected noise here: every call is scoped to one
        # host, so tabs on the other hosts are rejected by design. Anything else is real.
        for rej in res.get("rejected") or []:
            if rej.get("why") != "host-mismatch":
                rejected += 1
    return closed, rejected, "; ".join(errors) if errors else None


def main():
    ap = argparse.ArgumentParser(description="End-of-pass housekeeping")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--bump-searched", action="store_true")
    ap.add_argument("--sync-header", action="store_true")
    ap.add_argument("--close-scratch", action="store_true")
    ap.add_argument("--group", default="Scratch")
    ap.add_argument("--all", action="store_true", help="run every housekeeping action")
    args = ap.parse_args()

    do_bump = args.bump_searched or args.all
    do_sync = args.sync_header or args.all
    do_close = args.close_scratch or args.all
    if not (do_bump or do_sync or do_close):
        ap.error("pick at least one action (or --all)")

    missing = []

    if do_bump:
        lines = M.read_lines(args.shortlist)
        lines, status = bump_searched(lines, args.date)
        if status == "bumped":
            M.write_lines(args.shortlist, lines)
            print(f"shortlist.md: last-searched -> {args.date}", file=sys.stderr)
        elif status == "current":
            print(f"shortlist.md: last-searched already {args.date}", file=sys.stderr)
        else:
            missing.append(f"{args.shortlist} has no '**Last searched the web:**' header — "
                           f"nothing was bumped")

    if do_sync:
        lines = M.read_lines(args.applied)
        lines, status, (na, ns) = sync_header(lines, args.date)
        if status == "bumped":
            M.write_lines(args.applied, lines)
            print(f"applied.md: sync header -> {args.date} · {na} applied · {ns} saved",
                  file=sys.stderr)
        elif status == "current":
            print(f"applied.md: sync header already current ({na} applied, {ns} saved)",
                  file=sys.stderr)
        else:
            missing.append(f"{args.applied} has no '**Last synced from Simplify:**' header — "
                           f"nothing was bumped")

    for m in missing:
        print(f"WARN: {m}", file=sys.stderr)

    if do_close:
        closed, rejected, err = close_scratch(args.group)
        if err:
            print(f"close-scratch FAILED: {err}", file=sys.stderr)
            return 1
        print(f"closed {closed} tab(s) in group {args.group!r}"
              + (f", {rejected} rejected by the safety gate" if rejected else ""),
              file=sys.stderr)
        if closed == 0:
            print(f"note: nothing closed — either the {args.group!r} group is already empty "
                  f"or no open tab is in it", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
