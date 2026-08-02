#!/usr/bin/env python3
"""Harvest ALL jobs from a saved Simplify job search via Tab Share (port 8766).

Simplify's /jobs feed is client-side: the first page renders ~24 cards, then the
app pages deeper through a Typesense `multi_search` POST (js-ha.simplify.jobs)
as you scroll. This script drives the already-open logged-in Chromium session:

  1. Resolves the saved search by label from localStorage["jobsSavedSearches"]
     (the search criteria live in the account, so only the label is needed).
  2. Opens the search URL in a Scratch tab and seeds page 1 from the rendered
     job cards (company/title/location — no ids on cards).
  3. Installs an XHR hook that records every Typesense multi_search response
     (structured docs WITH posting ids) as scrolling loads them.
  4. Scrolls the results container to the bottom until the card count reaches
     the "Showing N of M Jobs" header (or the count stops growing).
  5. Merges page 1 (seed) + captured pages and prints JSON:
       {"total_header": N, "count": M, "url": ..., "saved_search": ...,
        "jobs": [{id, title, company, location, job_type, work_arrangement, experience}, ...]}

Read-only: opens a disposable Scratch tab (closed in `finally`), never mutates
anything. The job `id` is the Simplify posting UUID — a detail page is
`https://simplify.jobs/jobs?<search-query>&jobId=<id>`.

Usage: simplify_search.py [saved_search_label]   (default: "Toronto")
"""
import datetime
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "lib"))
import tab_share as TS

JOBS_ORIGIN = "https://simplify.jobs"
PAGE_1_SEED_SLEEP = 7.0   # initial render before the seed parse
SCROLL_SLEEP = 2.5        # pause between scroll nudges so the page renders
SCROLL_SLEEP_LAST = 1.5   # shorter pause for the stall probes

TYPE_RE = re.compile(r"^(Full-Time|Part-Time|Internship|Contract|Co-op|Apprenticeship)$", re.I)
ARRANGEMENT_RE = re.compile(r"^(In Person|Hybrid|Remote)$", re.I)
HEADER_RE = re.compile(r"Showing\s+([\d,]+)\s+of\s+([\d,]+)\s+Jobs")


def post(path, body, timeout=30, retries=2):
    return TS.post(path, body, timeout=timeout, retries=retries)


def tabs():
    return TS.tabs()


def eval_value(code, tab_id=None):
    """POST /eval and return the value (or None on failure)."""
    return TS.eval_value(code, tab_id=tab_id)


# ---- pure parsing / merging (unit-tested) ----

def parse_card(text):
    """Parse one job card's innerText → job dict (id unknown on cards).

    Card line shape (observed): company / title / job-type / [salary] /
    location / work-arrangement. Salary lines contain '$'; type and
    arrangement match their own patterns.
    """
    lines = [x.strip() for x in text.split("\n") if x.strip()]
    if len(lines) < 3:
        return None
    company, title = lines[0], lines[1]
    job_type = location = arrangement = ""
    for line in lines[2:]:
        if not job_type and TYPE_RE.match(line):
            job_type = line
        elif not arrangement and ARRANGEMENT_RE.match(line):
            arrangement = line
        elif "$" in line:
            continue  # salary — not captured in the output schema
        elif not location:
            location = line
    if not company or not title or company == title:
        return None
    return {"id": "", "title": title, "company": company, "location": location,
            "job_type": job_type, "work_arrangement": arrangement, "experience": "",
            "posted": ""}


def epoch_to_iso(value):
    """Unix epoch seconds → 'YYYY-MM-DD' (or '' if missing/invalid)."""
    try:
        return (datetime.datetime.fromtimestamp(int(value), datetime.timezone.utc)
                .strftime("%Y-%m-%d"))
    except (TypeError, ValueError, OSError):
        return ""


def parse_multi_search_doc(doc):
    """Typesense search document → job dict (id = posting uuid)."""
    if not isinstance(doc, dict):
        return None
    locations = doc.get("locations") or []
    return {
        "id": doc.get("posting_id") or doc.get("id") or "",
        "title": doc.get("title") or "",
        "company": doc.get("company_name") or "",
        "location": locations[0] if locations else "",
        "job_type": doc.get("type") or "",
        "work_arrangement": doc.get("travel_requirements") or "",
        "experience": "; ".join(doc.get("experience_level") or []),
        "posted": epoch_to_iso(doc.get("start_date") or doc.get("posted")),
    }


def find_saved_query(ls_value, label):
    """Extract the query string for a saved search label from the localStorage
    value of jobsSavedSearches (`[{userId, searches:[{id,label,query}]}]`)."""
    try:
        data = json.loads(ls_value)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    for entry in data:
        searches = entry.get("searches") if isinstance(entry, dict) else None
        if not isinstance(searches, list):
            continue
        for s in searches:
            if isinstance(s, dict) and s.get("label") == label and s.get("query"):
                return s["query"]
    return None


def best_offset(cards, hooked, lo, hi):
    """Card/hook alignment: hooked[i] corresponds to cards[offset+i].

    The lazy loader appends pages in order and the hook captures responses in
    fetch order, so captured doc i lines up with rendered card offset+i. Page 1
    renders before the hook installs, so `offset` is ~the page-1 card count.
    Return the offset in [lo, hi] with the most title matches.
    """
    titles = [c["title"].lower() for c in cards]
    best, best_count = None, -1
    for off in range(lo, hi + 1):
        n = sum(1 for i, j in enumerate(hooked)
                if off + i < len(titles) and j["title"].lower() == titles[off + i])
        if n > best_count:
            best, best_count = off, n
    return best if best is not None else lo


def merge_cards_and_ids(cards, hooked):
    """Attach hook ids (possibly sparse docs) to the DOM card list.

    The rendered job cards always carry company/title/location/etc but no id;
    the captured Typesense docs carry the posting id (and sometimes little
    else). Docs are matched to cards by position (best_offset), which is exact
    except for page-1 cards — those render before the hook installs, so they
    keep an empty id rather than risk crossing to a same-titled posting.

    Returns: cards, each with the matched id (+ hook experience when present);
    any hooked doc that matched no card is appended with its own fields.
    """
    off = best_offset(cards, hooked, 16, 40)
    used = set()
    out = []
    for k, c in enumerate(cards):
        nxt = None
        if off <= k and k - off < len(hooked):
            cand = hooked[k - off]
            if id(cand) not in used and cand["title"].lower() == c["title"].lower():
                nxt = cand
        if nxt is None and k >= off:
            for cand in hooked:
                if id(cand) in used:
                    continue
                if cand["title"].lower() == c["title"].lower():
                    nxt = cand
                    break
        if nxt is not None:
            used.add(id(nxt))
            c = dict(c, id=nxt["id"] or c.get("id") or "",
                     experience=nxt.get("experience") or c.get("experience") or "",
                     posted=nxt.get("posted") or c.get("posted") or "")
        out.append(c)
    for j in hooked:
        if id(j) not in used:
            out.append(j)
    return out


# ---- Tab Share JS snippets ----

def js_read_saved_searches():
    return ('(function(){ try { return localStorage.getItem("jobsSavedSearches") || ""; }'
            ' catch(e){ return ""; } })()')


def js_seed_cards():
    return ('(function(){ var c = document.querySelectorAll("[data-testid=job-card]");'
            ' var out = []; for (var i=0;i<c.length;i++){ out.push(c[i].innerText); }'
            ' return out; })()')


def js_page_info():
    return ('(function(){ var t = document.body.innerText || "";'
            ' var m = t.match(/Showing\\s+([\\d,]+)\\s+of\\s+([\\d,]+)\\s+Jobs/);'
            ' return { cards: document.querySelectorAll("[data-testid=job-card]").length,'
            ' shown: m ? parseInt(m[1].replace(/,/g,"")) : 0,'
            ' total: m ? parseInt(m[2].replace(/,/g,"")) : 0 }; })()')


def js_scroll_bottom():
    return ('(function(){ var c = document.querySelector("[data-testid=job-card]");'
            ' if (!c) return "no-cards"; var el = c;'
            ' while (el && el.scrollHeight <= el.clientHeight + 50 && el.parentElement){ el = el.parentElement; }'
            ' if (el && el.scrollHeight > el.clientHeight + 50){ el.scrollTop = el.scrollHeight; }'
            ' return el ? (el.scrollTop + "/" + el.scrollHeight) : "none"; })()')


def js_scroll_wiggle():
    """Nudge the results container up a little, then back to the bottom — a
    stalled lazy-loader usually resumes on the second nudge."""
    return ('(function(){ var c = document.querySelector("[data-testid=job-card]");'
            ' if (!c) return "no-cards"; var el = c;'
            ' while (el && el.scrollHeight <= el.clientHeight + 50 && el.parentElement){ el = el.parentElement; }'
            ' if (!el) return "none";'
            ' el.scrollTop = Math.max(0, el.scrollTop - 600);'
            ' el.scrollTop = el.scrollHeight;'
            ' return el.scrollTop + "/" + el.scrollHeight; })()')


def js_install_hook():
    """Record every Typesense multi_search response into window.__sjobs.

    /eval is synchronous (no await), so we override XHR.prototype.send and
    attach a 'load' listener per request — each search page's response is
    parsed into compact job dicts as it arrives.
    """
    return r"""(function(){
    window.__sjobs = window.__sjobs || [];
    window.__sjobsTotal = window.__sjobsTotal || 0;
    var send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function(body){
        var xhr = this;
        xhr.addEventListener("load", function(){
            try {
                var url = String(xhr.responseURL || "");
                if (url.indexOf("multi_search") === -1) return;
                var data = JSON.parse(xhr.responseText);
                var res = data.results && data.results[0];
                if (!res) return;
                if (res.found) { window.__sjobsTotal = res.found; }
                var hits = res.hits || [];
                for (var i = 0; i < hits.length; i++){
                    var d = hits[i].document || {};
                    if (!d.posting_id) continue;
                    window.__sjobs.push({
                        id: d.posting_id,
                        title: d.title || "",
                        company: d.company_name || "",
                        location: (d.locations && d.locations.length) ? d.locations[0] : "",
                        job_type: d.type || "",
                        work_arrangement: d.travel_requirements || "",
                        experience: (d.experience_level || []).join("; "),
                        posted: d.start_date ? d.start_date : ""
                    });
                }
            } catch(e){}
        });
        return send.apply(this, arguments);
    };
    return "hooked";
})()"""


def js_collect():
    return '(function(){ return window.__sjobs || []; })()'


def filter_by_age(jobs, max_age_days, today=None):
    """Drop jobs whose posted date is known AND older than max_age_days.

    Jobs with no posted date are kept (page-1 cards render before the hook;
    "no date is fine — don't penalize" per the prefs). Returns (kept, dropped,
    nodate_count).
    """
    if not max_age_days:
        return jobs, [], 0
    today = today or datetime.date.today()
    cutoff = (today - datetime.timedelta(days=max_age_days)).isoformat()
    kept, dropped = [], []
    for j in jobs:
        p = j.get("posted")
        if p and p < cutoff:
            dropped.append(j)
        else:
            kept.append(j)
    nodate = sum(1 for j in jobs if not j.get("posted"))
    return kept, dropped, nodate


# ---- orchestration ----

def find_simplify_tab():
    for t in tabs():
        url = t.get("url", "")
        if url.startswith(JOBS_ORIGIN):
            return {"tabId": t.get("id") or t.get("tabId"), "url": url}
    return None


def read_saved_query_from_tab(tab_id, label):
    ls = eval_value(js_read_saved_searches(), tab_id=tab_id)
    if ls is None:
        return None, "could not read localStorage on the Simplify tab"
    query = find_saved_query(ls, label)
    if query is None:
        return None, "no saved search labeled %r found (open simplify.jobs and save the search)" % label
    return query, None


def harvest(label="Toronto", max_rounds=None, max_jobs=300, max_age_days=None,
            wait=SCROLL_SLEEP):
    origin = find_simplify_tab()
    opened_origin = False
    if origin is None:
        r = post("/open", {"url": JOBS_ORIGIN + "/jobs", "groupName": "Scratch"})
        if not r.get("tabId"):
            return {"error": "could not open a simplify.jobs tab; is Chromium (port 8766) up and logged in?"}
        origin = {"tabId": r["tabId"], "url": JOBS_ORIGIN + "/jobs"}
        opened_origin = True
        time.sleep(PAGE_1_SEED_SLEEP)

    query, err = read_saved_query_from_tab(origin["tabId"], label)
    if err:
        return {"error": err}

    url = JOBS_ORIGIN + "/jobs?" + query
    r = post("/open", {"url": url, "groupName": "Scratch"})
    tid = r.get("tabId")
    if not tid:
        return {"error": "could not open the search URL in a Scratch tab"}

    search_tab = {"tabId": tid, "url": url}
    try:
        # Wait for the initial render; the page-1 cards double as the
        # "did it render" check (a bare /jobs without login shows no cards).
        time.sleep(PAGE_1_SEED_SLEEP)
        seed = []
        for _ in range(3):
            raw_cards = eval_value(js_seed_cards(), tab_id=tid)
            if raw_cards:
                seed = [c for c in (parse_card(t) for t in raw_cards) if c]
            if seed:
                break
            time.sleep(3)

        info = eval_value(js_page_info(), tab_id=tid) or {}
        total = info.get("total") or 0
        if not seed and not total:
            return {"error": "the search page did not render (log into simplify.jobs first)"}

        # Hook Typesense responses, then scroll until everything is loaded.
        # /eval can occasionally drop a call, so verify the hook took and
        # re-install it if a round loaded cards but captured nothing new
        # (re-installing is idempotent — __sjobs is preserved).
        def ensure_hook():
            for _ in range(3):
                if eval_value(js_install_hook(), tab_id=tid) == "hooked":
                    return True
                time.sleep(1)
            return False
        if not ensure_hook():
            print("  warning: Typesense hook did not install — job ids will be missing",
                  file=sys.stderr)
        cap = max_rounds or (total // 20 + 15 if total else 200)

        seen = set()
        prev_cards = 0
        stalls = 0
        rounds = 0
        while rounds < cap:
            rounds += 1
            eval_value(js_scroll_bottom(), tab_id=tid)
            time.sleep(wait if rounds % 3 else SCROLL_SLEEP_LAST)
            info = eval_value(js_page_info(), tab_id=tid) or {}
            jobs = [j for j in (parse_multi_search_doc(d)
                    for d in (eval_value(js_collect(), tab_id=tid) or [])) if j]
            ids = {j["id"] for j in jobs if j["id"]}
            new = len(ids - seen)
            seen |= ids
            cards_n = info.get("cards") or 0
            if info.get("total"):
                total = info["total"]
            print("  round %d: +%d new ids (captured %d, %d cards rendered)"
                  % (rounds, new, len(seen), cards_n), file=sys.stderr)
            if total and cards_n >= total:
                break
            if max_jobs and cards_n >= max_jobs:
                break
            if new == 0 and cards_n <= prev_cards:
                # Lazy loader stalled. Near the target we're done; far from it,
                # nudge the scroll to re-trigger it before giving up.
                if total and cards_n >= total * 0.9:
                    stalls += 1
                    if stalls >= 2:
                        break
                else:
                    eval_value(js_scroll_wiggle(), tab_id=tid)
                    stalls += 1
                    if stalls >= 6:
                        break
            else:
                stalls = 0
            prev_cards = cards_n

        hooked = []
        seen_again = set()
        for d in (eval_value(js_collect(), tab_id=tid) or []):
            j = parse_multi_search_doc(d)
            if not j or not j["id"] or j["id"] in seen_again:
                continue
            seen_again.add(j["id"])
            hooked.append(j)
        final_info = eval_value(js_page_info(), tab_id=tid) or {}
        if final_info.get("total"):
            total = final_info["total"]

        # Re-read every rendered card (the scroll loop loaded the whole list)
        # and attach the captured posting ids by title. This survives sparse
        # Typesense responses, which omit company/location.
        raw_cards = eval_value(js_seed_cards(), tab_id=tid) or []
        cards = [c for c in (parse_card(t) for t in raw_cards) if c]
        jobs = merge_cards_and_ids(cards, hooked)
        if max_age_days:
            jobs, dropped, nodate = filter_by_age(jobs, max_age_days)
            cutoff = (datetime.date.today() -
                      datetime.timedelta(days=max_age_days)).isoformat()
            print("  --max-age-days %d: dropped %d posted before %s; kept %d "
                  "(%d of them have no posted date)" %
                  (max_age_days, len(dropped), cutoff, len(jobs), nodate),
                  file=sys.stderr)
        if total and len(jobs) < total:
            print("  warning: incomplete harvest — got %d of %d jobs "
                  "(rendered %d cards)" % (len(jobs), total, prev_cards), file=sys.stderr)
        return {
            "total_header": total,
            "count": len(jobs),
            "url": url,
            "saved_search": label,
            "jobs": jobs,
        }
    finally:
        post("/close", {"tabId": [search_tab["tabId"]],
                        "expectHost": "simplify.jobs", "expectGroup": "Scratch"}, retries=0)
        if opened_origin:
            post("/close", {"tabId": [origin["tabId"]],
                            "expectHost": "simplify.jobs", "expectGroup": "Scratch"}, retries=0)


if __name__ == "__main__":
    args = list(sys.argv[1:])
    max_rounds = None
    max_jobs = 300
    max_age_days = None

    def _flag(name):
        if name in args:
            i = args.index(name)
            try:
                return int(args[i + 1])
            except (ValueError, IndexError):
                return None

    for flag, attr in (("--max-rounds", "max_rounds"),
                       ("--max-jobs", "max_jobs"),
                       ("--max-age-days", "max_age_days")):
        val = _flag(flag)
        if val is not None:
            globals()[attr] = val
        if flag in args:
            del args[args.index(flag):args.index(flag) + 2]
    label = args[0] if args else "Toronto"
    out = harvest(label, max_rounds=max_rounds, max_jobs=max_jobs,
                  max_age_days=max_age_days)
    print(json.dumps(out, indent=1))
    if "error" in out:
        sys.exit(1)
    print("harvested %d / header %s from saved search %r"
          % (out["count"], out.get("total_header"), label), file=sys.stderr)
