#!/usr/bin/env python3
"""Capture the Simplify tracker list by calling its own API. No LLM, no scraping.

HOW, AND WHY THIS WAY
---------------------
The app fetches its tracker list from a paginated REST endpoint:

    GET https://api.simplify.jobs/v2/candidate/me/tracker/?page=N&size=25&archived=false

`simplify_actions.py` already calls `api.simplify.jobs` from the page context with the
right credentials and CSRF header, so this reuses that exact path (`plan_list` +
`execute_via_tab_share`) and just paginates. That means:

  - **ids by construction** — every row carries its application `id`, which is the handle
    the push-back needs. No network eavesdropping, no per-row click-through.
  - **no scrolling, no innerText parsing** — the response is already structured JSON, so
    there is nothing to scroll or tokenise.
  - **honest completeness** — the response includes `total` and `pages`, so a partial
    capture is detectable rather than guessed at.

An earlier version of this file hooked XHR/fetch to *observe* the app's own request. That
was the wrong instinct: the codebase already had a direct-call pattern, and eavesdropping
depended on catching a request that a rendered tab no longer makes. Calling the endpoint is
strictly simpler and always works.

Output: `records` in the shape parse_tracker.parse_block produces (so merge() consumes them
directly) plus a `{company||title: {id, url}}` map. `--out <prefix>` writes
`<prefix>.records.json` and `<prefix>.urls.json`.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import simplify_actions as SA

STATUS_SAVED = 1
STATUS_APPLIED = 2


def _company_name(row):
    co = row.get("company")
    if isinstance(co, dict):
        return co.get("name") or co.get("company_name") or ""
    return co or ""


def _first_event_date(events, status):
    """Earliest ISO date at which the row reached `status`, as YYYY-MM-DD, or ""."""
    best = None
    for e in events or []:
        try:
            if int(e.get("status")) == status and e.get("timestamp"):
                ts = str(e["timestamp"])[:10]
                if best is None or ts < best:
                    best = ts
        except (TypeError, ValueError):
            continue
    return best or ""


def _max_status(events):
    vals = []
    for e in events or []:
        try:
            vals.append(int(e.get("status")))
        except (TypeError, ValueError):
            continue
    return max(vals) if vals else None


def row_to_record(row):
    """One API row -> the record shape parse_tracker.parse_block emits.

    `saved`/`applied` are derived from `status_events` (same 1=SAVED, 2=APPLIED codes the
    rest of the skill uses), falling back to `tracked_date` for the saved date. `applied`
    is non-empty only when the row actually reached APPLIED, so parse_tracker treats it as
    applied — exactly as it would from the rendered text.
    """
    events = row.get("status_events") or []
    company = _company_name(row)
    title = row.get("job_posting_title") or ""
    saved = _first_event_date(events, STATUS_SAVED) or (row.get("tracked_date") or "")[:10]
    applied = _first_event_date(events, STATUS_APPLIED)
    return {
        "title": title.strip(),
        "company": company.strip(),
        "company_raw": company.strip(),
        "location": (row.get("job_posting_location") or "").strip(),
        "saved": saved,
        "applied": applied,
        "id": row.get("id") or "",
        "url": row.get("job_posting_url") or "",
        "max_status": _max_status(events),
    }


def norm_key(company, title):
    n = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
    return f"{n(company)}||{n(title)}"


def strip_tracking(url):
    """Drop Simplify's own `ref=Simplify...` referral param, keeping the rest intact.

    Handled carefully so a leading `?ref=...&a=1` becomes `?a=1`, not a dangling `&a=1`.
    """
    if not url:
        return ""
    url = re.sub(r"[?&]ref=Simplify[^&]*", "", url)
    # if the ref was first, the next param (now leading with &) must become the ? param
    url = re.sub(r"\?(?=$|&)", "", url)          # bare "?" left behind
    url = url.replace("?&", "?")
    if "?" not in url:
        url = re.sub(r"(https?://[^&]*)&", r"\1?", url, count=1)
    return url.rstrip("?&")


def fetch_page(page, size, archived, tab_id):
    plan = SA.plan_list(page=page, size=size, archived=archived)
    res = SA.execute_via_tab_share(plan, dry_run=False, tab_id=tab_id)
    if res.get("status") != "success":
        return None, res.get("error") or res.get("status") or "list call failed"
    body = (res.get("response") or {}).get("body")
    if not isinstance(body, dict):
        return None, f"unexpected list response: {str(body)[:200]}"
    return body, None


def capture(tab_id=None, size=50, archived=False, max_pages=40, tab_share_url=None):
    """Page through the tracker LIST endpoint and return a capture dict.

    `complete` is True only when every page was read and the collected count matches the
    endpoint's own `total` — a claim backed by the API, not inferred from the DOM.
    """
    if tab_id is None:
        tab = SA.find_tracker_tab(tab_share_url) if tab_share_url else SA.find_tracker_tab()
        if not tab:
            return {"error": "no simplify.jobs/tracker tab open — open it first"}
        tab_id = tab.get("id")

    records, seen_ids = [], set()
    total = None
    pages = None
    for page in range(max_pages):
        body, err = fetch_page(page, size, archived, tab_id)
        if err:
            if records:      # partial: report what we have, flagged incomplete
                break
            return {"error": err}
        total = body.get("total", total)
        pages = body.get("pages", pages)
        items = body.get("items") or []
        if not items:
            break
        for row in items:
            rec = row_to_record(row)
            if rec["id"] and rec["id"] in seen_ids:
                continue
            seen_ids.add(rec["id"])
            records.append(rec)
        if pages is not None and page + 1 >= pages:
            break
        if total is not None and len(records) >= total:
            break

    urls = {}
    for rec in records:
        key = norm_key(rec["company"], rec["title"])
        if key == "||":
            continue
        urls[key] = {"id": rec["id"], "url": strip_tracking(rec["url"])}

    return {
        "records": records,
        "urls": urls,
        "rows": len(records),
        "total": total,
        "ids": sum(1 for r in records if r["id"]),
        "complete": total is not None and len(records) >= total,
        "archived": archived,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Capture the Simplify tracker list via its API (no scraping, no LLM)")
    ap.add_argument("--out", default="/tmp/simplify_tracker",
                    help="output prefix; writes <out>.records.json and <out>.urls.json")
    ap.add_argument("--tab-id", type=int, default=None)
    ap.add_argument("--size", type=int, default=50, help="page size (endpoint default 25)")
    ap.add_argument("--archived", action="store_true",
                    help="capture the Archived list instead of Active (out of scope for the "
                         "mirror; here only for completeness)")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--json", action="store_true", help="print the full summary as JSON")
    args = ap.parse_args(argv)

    res = capture(tab_id=args.tab_id, size=args.size, archived=args.archived,
                  max_pages=args.max_pages)
    if "error" in res:
        print(f"capture failed: {res['error']}", file=sys.stderr)
        return 1

    Path(args.out + ".records.json").write_text(json.dumps(res["records"], indent=1),
                                                encoding="utf-8")
    Path(args.out + ".urls.json").write_text(json.dumps(res["urls"], indent=1),
                                             encoding="utf-8")

    if args.json:
        print(json.dumps({k: v for k, v in res.items() if k != "records"}, indent=1))

    scope = "Archived" if args.archived else "Active"
    print(f"captured {res['rows']} {scope} row(s) "
          f"(endpoint total {res['total']}), {res['ids']} with a Simplify id", file=sys.stderr)
    print(f"  records -> {args.out}.records.json\n  urls    -> {args.out}.urls.json",
          file=sys.stderr)
    if not res["complete"]:
        print("WARN: capture is incomplete (did not reach the endpoint's total). Do not "
              "rebuild the mirror from it without --force.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
