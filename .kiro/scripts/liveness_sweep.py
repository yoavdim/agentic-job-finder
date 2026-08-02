#!/usr/bin/env python3
"""Stage 0c — liveness & staleness sweep over open shortlist rows (search-playbook §8).

Order matters: **age is checked first**. A row that fails the recency cutoff is removed as
`too-old` without spending a network probe on it.

The soft-404 trap drives the host split: aggregators are JS-rendered, so a removed listing
still answers HTTP 200 with a normal <title> and injects the removal notice via JavaScript.
Those MUST be read as rendered text through the Tab Share browser API. Direct ATS hosts are
honest enough to trust their status code.

Usage:
    liveness_sweep.py --shortlist shortlist.md                    # dry run, probes only
    liveness_sweep.py --shortlist shortlist.md --apply            # + migrate dead rows
    liveness_sweep.py --shortlist shortlist.md --no-browser       # skip render checks
"""
import argparse
import concurrent.futures as cf
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import jobdates as JD
import md_tables as M
import migrate_resolved as MR
import tab_share as TS

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")

# Rendered-text markers that mean the posting is gone (case-insensitive substrings).
REMOVAL_MARKERS = [
    "this job was removed",
    "no longer accepting applications",
    "no longer available",
    "position has been filled",
    "job is closed",
    "posting has expired",
    "sorry, this job",
    "this position is no longer",
    "job not found",
    "posting is no longer active",
]

# JS-rendered boards: status code is meaningless, must render.
AGGREGATOR_HOSTS = (
    "builtin.com", "builtintoronto.com", "builtinnyc.com",
    "tealhq.com", "ycombinator.com", "cryptocurrencyjobs.co", "web3.career",
    "indeed.com", "ca.indeed.com", "linkedin.com", "simplify.jobs",
    "glassdoor.com", "ziprecruiter.com", "jobs.entrepreneurs.utoronto.ca",
    "wellfound.com", "angel.co",
)

# Real ATS hosts: HTTP status is trustworthy.
ATS_HOSTS = (
    "greenhouse.io", "job-boards.greenhouse.io", "boards.greenhouse.io",
    "applytojob.com", "ashbyhq.com", "myworkdayjobs.com", "recruitee.com",
    "lever.co", "smartrecruiters.com", "bamboohr.com", "workable.com",
    "jobvite.com", "icims.com", "taleo.net", "breezy.hr", "successfactors.com",
)

# A redirect landing on one of these paths means "listing gone, here's the board".
CAREERS_HOME_RE = re.compile(
    r"/(careers|jobs|openings|positions|search|home|userHome)/?$", re.I)


def host_of(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) or "").lower() if m else ""


def classify_host(url):
    h = host_of(url)
    if any(h == a or h.endswith("." + a) for a in AGGREGATOR_HOSTS):
        return "aggregator"
    if any(h == a or h.endswith("." + a) for a in ATS_HOSTS):
        return "ats"
    return "unknown"


# ---------- age ----------

POSTED_RE = re.compile(r"📅\s*(?:posted|reposted)\s*(\d{4}-\d{2}-\d{2}|\d{4}-\d{2})", re.I)


def row_age_date(row):
    """Best available date for the row: the 📅 posted note, else the Added date.
    Returns (iso_date, source) or (None, None)."""
    m = POSTED_RE.search(row.get("notes"))
    if m:
        d = m.group(1)
        return (d if len(d) == 10 else d + "-01"), "posted"
    added = row.get("date").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", added):
        return added, "added"
    return None, None


def is_too_old(row, today, max_age_days):
    """(too_old, date, age_days, source). `source` is "posted" or "added".

    The source is carried through because the two mean different things and the rejection
    comment is a permanent record: "posted 2026-05-01" is a fact about the listing, while
    the `Added` fallback only says when the row entered the shortlist. Reporting the
    fallback as a posting date put a claim in the Rejected table that was never true.
    """
    d, src = row_age_date(row)
    if not d:
        return False, None, None, None    # undated rows are never penalized (prefs)
    age = JD.age_days(d, today)
    if age is None:
        return False, None, None, None
    return age > max_age_days, d, age, src


# ---------- probes ----------

def http_probe(url, timeout=15):
    """Return {status, final_url, error}. Follows redirects (urllib default)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"status": r.status, "final_url": r.geturl(), "error": None}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final_url": url, "error": None}
    except urllib.error.URLError as e:
        return {"status": None, "final_url": url, "error": str(e.reason)}
    except Exception as e:
        return {"status": None, "final_url": url, "error": str(e)}


SCRATCH_GROUP = "Scratch"


def render_text(url, timeout=45):
    """Rendered page text via Tab Share /extract. Returns (text, error).

    Opens `url` in its own tab and extracts BY that tab's id. `TS.extract(url=...)` alone
    does not open the URL — /extract with no tabId silently reads whatever tab is currently
    active (verified directly: it returned an unrelated open tab's content, `ok: True`, with
    no error). This function used to rely on that bare-url path, so a liveness check could
    silently read the wrong page and misjudge a role as alive/dead based on unrelated tab
    content — exactly the kind of failure §8 exists to catch, happening inside the checker
    itself. `groupName` puts the opened tab in Scratch so `housekeeping.py --close-scratch`
    reaps it at the end of the pass (playbook §3); the tab is also closed here directly so a
    caller checking many URLs in a loop doesn't accumulate one open tab per URL.
    """
    tab_id = TS.open_tab(url, group_name=SCRATCH_GROUP, timeout=timeout)
    if not tab_id:
        return "", "could not open a tab for this URL"
    try:
        data = TS.extract(tab_id=tab_id, timeout=timeout)
        if not data:
            return "", "extract failed or timed out"
        return (data.get("text") or "") + " " + (data.get("title") or ""), None
    finally:
        TS.close(tab_ids=[tab_id], expect_group=SCRATCH_GROUP, expect_host=host_of(url))


def find_marker(text):
    low = (text or "").lower()
    for m in REMOVAL_MARKERS:
        if m in low:
            return m
    return None


def tab_share_up():
    return TS.is_up()


def check_url(url, use_browser):
    """Decide liveness for one URL.

    Returns {verdict, reason, detail} where verdict is
    'alive' | 'dead' | 'unknown' (unknown = needs a human/browser look).
    """
    if not url:
        return {"verdict": "dead", "reason": "link-broken", "detail": "no apply URL in row"}

    kind = classify_host(url)

    # Aggregators: status is meaningless. Render or defer — never guess from the code.
    if kind == "aggregator":
        if not use_browser:
            return {"verdict": "unknown", "reason": None,
                    "detail": f"aggregator ({host_of(url)}) needs a render check; browser disabled"}
        text, err = render_text(url)
        if err:
            return {"verdict": "unknown", "reason": None, "detail": f"render failed: {err}"}
        marker = find_marker(text)
        if marker:
            return {"verdict": "dead", "reason": "listing-removed",
                    "detail": f"rendered page says {marker!r}"}
        if len(text.strip()) < 200:
            return {"verdict": "unknown", "reason": None,
                    "detail": "rendered page nearly empty (load timing?) — recheck"}
        return {"verdict": "alive", "reason": None, "detail": "rendered, no removal marker"}

    # Direct ATS / unknown hosts: trust the status code, with the documented exceptions.
    p = http_probe(url)
    st, err, final = p["status"], p["error"], p["final_url"]

    if err:
        return {"verdict": "dead", "reason": "link-broken", "detail": f"request failed: {err}"}
    if st in (404, 410):
        return {"verdict": "dead", "reason": "link-broken", "detail": f"HTTP {st}"}
    if st == 403:
        return {"verdict": "alive", "reason": None,
                "detail": "HTTP 403 — bot-blocked, not treated as dead"}
    if st is not None and 500 <= st < 600:
        return {"verdict": "unknown", "reason": None,
                "detail": f"HTTP {st} may be transient — recheck before removing"}
    if M.clean_url(final) != M.clean_url(url) and CAREERS_HOME_RE.search(final):
        return {"verdict": "dead", "reason": "link-broken",
                "detail": f"redirected to careers home: {final}"}

    # 200 on a real ATS is honest, but confirm with a render when the browser is there.
    if use_browser:
        text, rerr = render_text(url)
        if not rerr:
            marker = find_marker(text)
            if marker:
                return {"verdict": "dead", "reason": "listing-removed",
                        "detail": f"rendered page says {marker!r}"}
    return {"verdict": "alive", "reason": None, "detail": f"HTTP {st}"}


# ---------- sweep ----------

def sweep(shortlist_lines, today, max_age_days, use_browser, workers=5, age_only=False):
    """Check every open row. Returns (results, anomalies); mutates nothing.

    `workers` defaults to 5 to match the Scratch tab-pool size the playbook specifies —
    each render check opens a tab, so more workers than the pool means more concurrent tabs
    than intended.

    `age_only=True` applies the age cut and makes NO network requests at all: surviving rows
    report "unknown" (never removed, per §8's rule that a row you could not read stays put).
    Note that `use_browser=False` alone still performs HTTP probes — it only disables the
    rendered-text checks.
    """
    candidates = []
    anomalies = []
    for t in M.find_tables(shortlist_lines, r"Tier \d+"):
        if not t.has("status"):
            continue
        for line_idx, n in t.ragged_rows():
            anomalies.append(f"{t.heading}: row at line {line_idx + 1} has {n} cells, "
                             f"expected {t.ncol} — the row is shifted/corrupt, so its "
                             f"fields are being read wrong")
        for row in t.rows:
            status = row.get("status")
            if M.has_no_status(status):
                # Invisible to every stage otherwise: not open, not applied, not rejected.
                anomalies.append(
                    f"{t.heading}: {row.get('company')} — {row.get('role')} (line "
                    f"{row.line_idx + 1}) has no status box, so no stage will ever pick it "
                    f"up. Give it [ ], [x] or [nope]")
                continue
            if not M.is_open(status):
                continue          # only open [ ] rows; [x]/[nope] are stage 0b's job
            candidates.append((t.heading, row))

    results = []
    to_probe = []

    for heading, row in candidates:
        old, date, age, src = is_too_old(row, today, max_age_days)
        base = {
            "tier": heading, "line_idx": row.line_idx,
            "company": row.get("company"), "role": row.get("role"),
            "location": row.get("location"), "apply": row.get("apply"),
            "url": M.extract_url(row.get("apply")),
        }
        if old:
            # Age cut short-circuits the probe entirely. Name the date's source: only
            # "posted" is a fact about the listing; "added" is when the row was created.
            what = ("posting dated" if src == "posted"
                    else "row added (no posting date known)")
            results.append({**base, "verdict": "dead", "reason": "too-old",
                            "date_source": src,
                            "detail": f"{what} {date}, {age}d ago "
                                      f"(cutoff {max_age_days}d)"})
        else:
            to_probe.append(base)

    if to_probe and age_only:
        for b in to_probe:
            results.append({**b, "verdict": "unknown", "reason": None,
                            "detail": "age-only run: liveness not checked"})
    elif to_probe:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(check_url, b["url"], use_browser): b for b in to_probe}
            for fut in cf.as_completed(futs):
                b = futs[fut]
                try:
                    verdict = fut.result()
                except Exception as e:
                    verdict = {"verdict": "unknown", "reason": None, "detail": f"probe crashed: {e}"}
                results.append({**b, **verdict})

    results.sort(key=lambda r: r["line_idx"])
    return results, anomalies


def to_migration_plan(results, today):
    """Turn dead rows into a migrate_resolved-shaped plan (Rejected inserts + deletions)."""
    actions = {"applied_new": [], "applied_comment": [], "rejected_new": [],
               "delete": [], "skipped": []}
    for r in results:
        if r["verdict"] != "dead":
            continue
        actions["rejected_new"].append({
            "date": today,
            "company": r["company"], "role": r["role"], "raw": r["role"],
            "location": r["location"], "apply": r["apply"],
            "reason": r["reason"],
            "comment": f"{r['detail']} (auto)",
            "tier": r["tier"],
        })
        actions["delete"].append(r["line_idx"])
    return actions


def main():
    ap = argparse.ArgumentParser(description="Liveness & staleness sweep (playbook §8)")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--today", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--max-age-days", type=int, default=31,
                    help="recency cutoff; clearly >1 month per prefs (default 31)")
    ap.add_argument("--no-browser", action="store_true",
                    help="skip Tab Share render checks (aggregators report 'unknown'). "
                         "NOTE: HTTP probes still run — use --age-only to go fully offline")
    ap.add_argument("--age-only", action="store_true",
                    help="apply the age cut and make no network requests at all; surviving "
                         "rows report 'unknown' and are left in place")
    ap.add_argument("--apply", action="store_true",
                    help="migrate dead rows into applied.md Rejected and drop them")
    ap.add_argument("--json", help="write full results as JSON here ('-' = stdout)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    use_browser = not args.no_browser and not args.age_only
    if use_browser and not tab_share_up():
        print("WARN: Tab Share not reachable on :8766 — aggregator rows will report "
              "'unknown' instead of being guessed at", file=sys.stderr)
        use_browser = False

    shortlist = M.read_lines(args.shortlist)
    results, anomalies = sweep(shortlist, args.today, args.max_age_days, use_browser,
                              args.workers, age_only=args.age_only)

    if args.json:
        M.write_json(args.json, {"results": results, "anomalies": anomalies})

    for a in anomalies:
        print(f"WARN: {a}", file=sys.stderr)

    counts = {"alive": 0, "dead": 0, "unknown": 0}
    for r in results:
        counts[r["verdict"]] += 1
    print(f"checked {len(results)} open row(s): {counts['alive']} alive, "
          f"{counts['dead']} dead, {counts['unknown']} unknown", file=sys.stderr)

    for r in results:
        if r["verdict"] == "dead":
            print(f"  DEAD  {r['company']} — {r['role']}  [{r['reason']}] {r['detail']}",
                  file=sys.stderr)
        elif r["verdict"] == "unknown":
            print(f"  ?     {r['company']} — {r['role']}  {r['detail']}", file=sys.stderr)

    if counts["unknown"]:
        print("NOTE: 'unknown' rows were left in place — recheck them by hand; "
              "never remove a row you could not actually read", file=sys.stderr)

    if not counts["dead"]:
        return

    if args.apply:
        applied = M.read_lines(args.applied)
        actions = to_migration_plan(results, args.today)
        shortlist, applied, _manual = MR.apply_plan(shortlist, applied, actions)
        M.write_lines(args.shortlist, shortlist)
        M.write_lines(args.applied, applied)
        print(f"applied: {len(actions['rejected_new'])} row(s) -> Rejected, "
              f"removed from shortlist", file=sys.stderr)
    else:
        print(f"DRY RUN — use --apply to migrate the {counts['dead']} dead row(s)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
