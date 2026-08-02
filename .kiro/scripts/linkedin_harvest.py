#!/usr/bin/env python3
"""Harvest ALL job cards from a LinkedIn jobs search OR the recommended collection via Tab Share.

LinkedIn blocks /eval (CSP) and virtualizes the results list, so /extract only returns
the handful of cards currently in the DOM. This pages via the `&start=N` URL param, but
does NOT assume a page size: it measures how many distinct cards the FIRST access yields
and uses that as the step, then loops until no new ids show up (or the header total is hit).

Collection URLs (containing `/jobs/collections/`) are detected automatically: their card
links only upgrade from `?trackingId=...` to `/jobs/view/<id>/` after a longer SPA render,
so collection mode waits longer and uses a collection-aware title/company/location parser.
Collections default to a 4-batch cap (~20 start offsets ≈ ~140 cards); pass a number to
override, or `0` for unlimited.

Usage: linkedin_harvest.py "<url>" [max_batches]
Prints JSON: {"total_header": N, "count": M, "jobs":[{id,title,company,location}, ...]}
"""
import json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import tab_share as TS

def post(path, body, timeout=30, retries=2):
    return TS.post(path, body, timeout=timeout, retries=retries)

def tabs():
    return TS.tabs()

def set_start(url, n):
    u = re.sub(r"([?&])start=\d+", r"\1start=%d" % n, url)
    if "start=" not in u:
        u += ("&" if "?" in u else "?") + "start=%d" % n
    return u

def real_tab_url(marker):
    for t in tabs():
        if marker in t.get("url", ""):
            return t["url"]
    return None

POOL = 5            # parallel worker tabs
COLLECTION_WAIT = 12  # collection cards need longer to upgrade trackingId → /jobs/view/ links
COLLECTION_MAX_BATCHES = 4  # default cap for collection runs (~20 start offsets ≈ ~140 cards)

def scan_detail_collection(cards_text, order):
    """Collection enrichment. Card text shape:
        <Title>
        <Title> [with verification]
        <Company>
        <Location, City> (Mode)
    The title line also appears in the right-hand detail pane, so only accept a
    location that looks like one (contains a comma or an On-site/Hybrid/Remote tag)."""
    detail = {}
    for txt in cards_text:
        lines = [x.strip() for x in txt.split("\n") if x.strip()]
        for title in order:
            if title in detail:
                continue
            for idx, l in enumerate(lines):
                if l != title:
                    continue
                k = idx + 1
                while k < len(lines) and lines[k].startswith(title):
                    k += 1
                if k + 1 >= len(lines):
                    break
                comp = lines[k]
                loc = lines[k + 1]
                if not ("," in loc or "(" in loc):
                    continue   # not a location line → look for another hit
                if "alumni" in comp.lower() or "verification" in comp.lower() or "•" in comp:
                    comp = ""
                if "alumni" in loc.lower():
                    loc = ""
                detail[title] = (comp, loc)
                break
    return detail

def harvest(url, max_tabs=POOL, collection=False, max_batches=None):
    seen = {}
    order = []
    cards_text = []
    def take(d):
        added = 0
        for l in d.get("links", []):
            h = l.get("href", "")
            if "/jobs/view/" not in h:
                continue
            jid = h.split("/jobs/view/")[1].split("/")[0].split("?")[0]
            if jid in seen:
                continue
            seen[jid] = (l.get("text", "") or "").split("\n")[0].strip()
            order.append(jid)
            added += 1
        return added

    # Fetch a set of start-offsets IN PARALLEL: open all into Scratch, one wait, extract all.
    def fetch_batch(starts):
        tabs_ = {}
        for n in starts:
            r = post("/open", {"url": set_start(url, n), "groupName": "Scratch"})
            tabs_[n] = r.get("tabId")
        time.sleep(COLLECTION_WAIT if collection else 7)  # single wait for the whole batch to render
        out = {}
        for n, tid in tabs_.items():
            out[n] = post("/extract", {"tabId": tid} if tid else {"url": set_start(url, n)})
        # close the batch (gated)
        tids = [t for t in tabs_.values() if t]
        if tids:
            post("/close", {"tabId": tids, "expectHost": "www.linkedin.com", "expectGroup": "Scratch"})
        return out

    # Page 0 first (sequential) to learn the step size + header total.
    d0 = fetch_batch([0])[0]
    txt = d0.get("text", "")
    m = re.search(r"([\d,]+)\s+results", txt)
    total = int(m.group(1).replace(",", "")) if m else None
    step = take(d0) or 25
    cards_text.append(txt)

    # Remaining pages in parallel batches of POOL.
    next_start = step
    cap = (total + step * 2) if total else step * 20  # safety ceiling
    done = False
    batch_no = 0
    while not done and next_start <= cap:
        if max_batches is not None and batch_no >= max_batches:
            print("  max_batches reached (%d); stopping" % max_batches, file=sys.stderr)
            break
        batch_no += 1
        starts = [next_start + i * step for i in range(POOL)]
        starts = [s for s in starts if s <= cap]
        res = fetch_batch(starts)
        batch_added = 0
        for n in starts:
            d = res.get(n, {})
            cards_text.append(d.get("text", ""))
            batch_added += take(d)
        print("  batch %d start=%s: +%d new (total %d/%s)" % (batch_no, starts[0], batch_added, len(seen), total), file=sys.stderr)
        next_start = starts[-1] + step
        if batch_added == 0:      # a whole parallel batch yielded nothing new → done
            done = True
        if total is not None and len(seen) >= total:
            done = True

    # enrich title/company/location by scanning all captured page texts
    if collection:
        detail = scan_detail_collection(cards_text, [seen[j] for j in order])
    else:
        detail = {}
        for txt in cards_text:
            i = txt.find("results")
            seg = txt[i:] if i >= 0 else txt
            lines = [x.strip() for x in seg.split("\n") if x.strip()]
            j = 0
            while j < len(lines) - 2:
                title = lines[j]
                if j + 1 < len(lines) and lines[j+1].startswith(title) and "with verification" in lines[j+1]:
                    comp = lines[j+2] if j+2 < len(lines) else ""
                    loc = lines[j+3] if j+3 < len(lines) else ""
                    if "alumni" in comp.lower(): comp = ""
                    detail.setdefault(title, (comp, loc))
                    j += 3
                else:
                    j += 1

    jobs = []
    for jid in order:
        title = seen[jid]
        comp, loc = detail.get(title, ("", ""))
        jobs.append({"id": jid, "title": title, "company": comp, "location": loc})
    return {"total_header": total, "count": len(jobs), "jobs": jobs}

def cleanup_scratch():
    # Close any LinkedIn tabs left in the Scratch group (belt-and-suspenders vs. mid-run crashes).
    try:
        post("/close", {"expectGroup": "Scratch", "expectHost": "www.linkedin.com"}, retries=0)
    except Exception:
        pass

if __name__ == "__main__":
    try:
        url = sys.argv[1]
        collection = "/jobs/collections/" in url
        max_batches = None
        if len(sys.argv) > 2:
            max_batches = int(sys.argv[2])
        elif collection:
            max_batches = COLLECTION_MAX_BATCHES
        out = harvest(url, collection=collection, max_batches=max_batches)
        print(json.dumps(out, indent=1))
        print("harvested %d / header %s%s" % (out["count"], out["total_header"], " (collection)" if collection else ""), file=sys.stderr)
    finally:
        cleanup_scratch()
