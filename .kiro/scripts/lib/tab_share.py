#!/usr/bin/env python3
"""One HTTP client for the Tab Share extension (localhost:8765 Firefox / 8766 Chromium).

Before this module, six scripts each reimplemented this client, none saying why:
`simplify_search.py` and `linkedin_harvest.py` had the byte-identical `post()`/`tabs()`
(just reformatted); `read_jobs.py` had a third minified variant with no retry logic at
all; `liveness_sweep.py`, `simplify_actions.py`, and `housekeeping.py` each had their own
`/extract`, `/eval`, and `/close` callers with different timeouts and error shapes. Unlike
`parse_tracker.py`'s `md_tables` copy (which stated a reason: skill portability), none of
these had a comment explaining the duplication — it reads as six independent "quickly
write a post() helper" moments, not a deliberate split. The result was real behavior drift
(retries silently absent in one caller, present in another) with no benefit to show for it.

Usage:
    import tab_share as TS
    TS.tabs()                                  # GET /tabs -> list of tab dicts
    TS.post("/eval", {"code": "..."})          # POST, JSON body -> JSON response (or {})
    TS.eval_value("(function(){...})()")       # /eval, unwrapped to the JS return value
    TS.extract(url)                            # /extract -> {"text", "title", "links", ...}
    TS.close(tab_ids, expect_host, expect_group)
    TS.is_up()                                 # cheap reachability probe
"""
import json
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8766"   # Chromium; 8765 is Firefox (see playbook §3)


def post(path, body, base=DEFAULT_BASE, timeout=30, retries=2, retry_wait=2):
    """POST JSON to `base+path`. Returns the decoded response, or {} if every attempt
    fails — callers that need to distinguish "empty" from "failed" should use `post_raw`.
    """
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                base + path, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            if attempt == retries:
                return {}
            time.sleep(retry_wait)


def post_raw(path, body, base=DEFAULT_BASE, timeout=30):
    """POST JSON to `base+path`. Returns (response_dict, error_str). No retries — for
    callers that need to see and report the failure themselves rather than getting {}.
    """
    data = json.dumps(body).encode()
    try:
        req = urllib.request.Request(
            base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except urllib.error.URLError as e:
        return {}, f"Tab Share connection failed: {e.reason}"
    except json.JSONDecodeError as e:
        return {}, f"JSON decode error: {e}"
    except Exception as e:
        return {}, str(e)


def get(path, base=DEFAULT_BASE, timeout=6):
    """GET `base+path`. Returns the decoded response, or None on failure."""
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def tabs(base=DEFAULT_BASE, timeout=6):
    """GET /tabs -> list of tab dicts (or [] on failure)."""
    return (get("/tabs", base=base, timeout=timeout) or {}).get("tabs", [])


def is_up(base=DEFAULT_BASE, timeout=3):
    """Cheap reachability probe: is the extension listening at all."""
    return get("/tabs", base=base, timeout=timeout) is not None


def eval_value(code, tab_id=None, base=DEFAULT_BASE, timeout=30):
    """POST /eval and return the unwrapped JS return value, or None on failure/error.

    Tab Share wraps the result as {"result": {"ok": bool, "value": ...}}; this returns
    `value` only when `ok` is true, matching how every caller actually used the raw dict.
    """
    body = {"code": code}
    if tab_id:
        body["tabId"] = tab_id
    res = post("/eval", body, base=base, timeout=timeout).get("result") or {}
    return res.get("value") if res.get("ok") else None


def extract(url=None, tab_id=None, group_name=None, base=DEFAULT_BASE, timeout=45):
    """POST /extract. Reads rendered {text, title, links, ...} from a tab.

    IMPORTANT: passing `url` alone does NOT open that URL — /extract with no `tabId` reads
    whatever tab is currently ACTIVE, silently, with no error if it's the wrong page. This
    was verified directly: `extract(url="https://builtintoronto.com/...")` with some other
    tab focused returned that other tab's content, `ok: True`, url field showing the wrong
    page. Always resolve a `tab_id` first (via `open_tab` or `find_tab`) and pass it
    explicitly; `url` here is accepted only because a caller might already know the tab was
    just opened at that exact URL and wants a label, not a routing mechanism.
    """
    body = {}
    if tab_id:
        body["tabId"] = tab_id
    elif url:
        body["url"] = url
    if group_name:
        body["groupName"] = group_name
    return post("/extract", body, base=base, timeout=timeout)


def open_tab(url, group_name=None, base=DEFAULT_BASE, timeout=30):
    """POST /open. Returns the tab id, or None on failure."""
    body = {"url": url}
    if group_name:
        body["groupName"] = group_name
    return post("/open", body, base=base, timeout=timeout).get("tabId")


def close(tab_ids=None, urls=None, expect_group=None, expect_host=None,
         base=DEFAULT_BASE, timeout=20, retries=0):
    """POST /close. The extension gates this on `expectHost` + `expectGroup` (or "*" to
    skip the group check) so a selector mistake can't touch tabs outside the given group.
    Returns the response dict ({"closed": [], "rejected": []} shape) or {} on failure.
    """
    body = {}
    if tab_ids:
        body["tabId"] = tab_ids if isinstance(tab_ids, list) else [tab_ids]
    if urls:
        body["url"] = urls if isinstance(urls, list) else [urls]
    if expect_group is not None:
        body["expectGroup"] = expect_group
    if expect_host is not None:
        body["expectHost"] = expect_host
    return post("/close", body, base=base, timeout=timeout, retries=retries)


def navigate(tab_id, url, base=DEFAULT_BASE, timeout=15):
    """POST /navigate (reuse an existing tab instead of opening a new one)."""
    return post("/navigate", {"tabId": tab_id, "url": url}, base=base, timeout=timeout)


def find_tab(url_substring, base=DEFAULT_BASE, timeout=6):
    """First open tab whose URL contains `url_substring`, or None."""
    for t in tabs(base=base, timeout=timeout):
        if url_substring in (t.get("url") or ""):
            return t
    return None
