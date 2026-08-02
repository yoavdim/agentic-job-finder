#!/usr/bin/env python3
"""Simplify tracker actions via Tab Share API (Chromium port 8766).

Executes synchronous API calls in the page context of an already-authenticated
Simplify tracker session. Mutations are guarded:

- `mark_applied` — Tier-6 `[x]` push-back: PUT the application to Applied while
  preserving the Saved history.
- `delete_saved` — Tier-6 `[nope]` push-back: DELETE the application from the
  tracker, but ONLY when the website still says SAVED. If the website says
  APPLIED, application wins over rejection — never delete (`rejection_outcome`).

Usage:
    from simplify_actions import plan_action, execute_via_tab_share

    plan = plan_action("mark_applied", application_id="abc-123",
                       current_events=status_events_from_inspect)
    result = execute_via_tab_share(plan, tab_share_url="http://localhost:8766")
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "lib"))
import tab_share as TS


# Simplify status codes (confirmed from the app bundle: SAVED=1, APPLIED=2)
STATUS_SAVED = 1
STATUS_APPLIED = 2

# The tracker's pipeline past "applied": screen -> interview -> offer. Each of these
# means an application definitely exists, so all of them protect it from delete-on-reject.
# Anything OUTSIDE this known set is treated as unverifiable rather than assumed — an
# unrecognised code must never authorise a delete, nor be asserted as "applied".
STATUS_POST_APPLIED = (3, 4, 5)
KNOWN_STATUSES = (STATUS_SAVED, STATUS_APPLIED) + STATUS_POST_APPLIED


def current_status(body):
    """Current application status from a tracker detail body (SAVED=1, APPLIED=2).

    This value gates an IRREVERSIBLE remote DELETE (`rejection_outcome`), so it is
    deliberately the most conservative code here and does NOT rely on list ordering.
    Reading `status_events[-1]` would mean that a response returning events out of
    order, with SAVED last, could delete an application that was actually submitted.

    Instead the status is the MAXIMUM status reached: the tracker's own progression is
    monotonic (saved -> applied -> screen -> ...), so the furthest state an application
    ever reached is its current one, regardless of the order events arrive in.

    An unrecognised status code is preserved (not filtered out) so it still raises the
    maximum and lands on "block" in `rejection_outcome`, rather than being dropped and
    letting a stale SAVED event authorise a delete.

    Returns None when the body has no usable events — unknown, never guessed.
    """
    events = (body or {}).get("status_events") or []
    statuses = []
    for e in events:
        if not isinstance(e, dict):
            continue
        try:
            statuses.append(int(e.get("status")))
        except (TypeError, ValueError):
            continue
    return max(statuses) if statuses else None


def rejection_outcome(current_status):
    """Decide what a Tier-6 `[nope]` row does on the website.

    Returns one of:
        "delete"  — website still says SAVED: remove the application.
        "applied" — website has progressed to APPLIED or beyond: application wins over
                    rejection; keep it (migrate the row as applied instead).
        "block"   — status unknown: cannot verify, keep the row in the shortlist.

    Only an EXACT saved status authorises the delete. Every known status at or past
    APPLIED is protected, and an unrecognised code blocks rather than falling through in
    either direction: a status added upstream must never be read as "safe to remove", and
    must not be asserted as "applied" either.
    """
    if current_status == STATUS_SAVED:
        return "delete"
    if current_status == STATUS_APPLIED or current_status in STATUS_POST_APPLIED:
        return "applied"
    return "block"


def _utc_now_iso():
    """Current UTC time in JS `new Date().toISOString()` format."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def build_mark_applied_body(current_events=None):
    """Build the mark_applied PUT body exactly like the app's tracker handler.

    The app keeps every existing event with status < APPLIED (the Saved history)
    and appends a new Applied event with the current timestamp; if an Applied
    event already exists it is kept as-is and nothing is appended. With no known
    events, only the new Applied event is sent.
    """
    if not current_events:
        return {"status_events": [{"status": STATUS_APPLIED, "timestamp": _utc_now_iso()}]}

    def status_of(e):
        """Event status as an int, or None for a malformed event."""
        if not isinstance(e, dict):
            return None
        try:
            return int(e.get("status"))
        except (TypeError, ValueError):
            return None

    lower = [e for e in current_events
             if (status_of(e) is not None and status_of(e) < STATUS_APPLIED)]
    already = next((e for e in current_events if status_of(e) == STATUS_APPLIED), None)
    events = list(lower)
    events.append(already if already is not None
                  else {"status": STATUS_APPLIED, "timestamp": _utc_now_iso()})
    return {"status_events": events}

# API endpoints (relative to the Simplify API origin — NOT the landing site:
# simplify.jobs/v2/... serves the Next.js HTML fallback)
SIMPLIFY_ORIGIN = "https://api.simplify.jobs"


def plan_list(page=0, size=25, archived=False):
    """Build a plan dict for one page of the tracker LIST endpoint.

    This is the app's own list request, observed in its network traffic:
        GET /v2/candidate/me/tracker/?page=N&size=25&archived=false&sort_by=tracked_date
    Calling it directly is how the capture reads the tracker — no scrolling, no innerText
    parsing, no eavesdropping on the page. It reuses the exact auth path as the mutating
    actions (`_build_eval_code` adds withCredentials + the CSRF header).
    """
    q = (f"?page={int(page)}&size={int(size)}&value="
         f"&archived={'true' if archived else 'false'}"
         f"&sort_by=tracked_date&sort_direction=desc")
    return {
        "action": "list",
        "application_id": None,
        "fetch": {"url": f"{SIMPLIFY_ORIGIN}/v2/candidate/me/tracker/{q}", "method": "GET"},
    }


def plan_action(action, *, application_id, current_events=None, simplify_url=None):
    """Build a plan dict for a Simplify action.

    action: "inspect" | "mark_applied" | "delete_saved"
    application_id: the Simplify application UUID
    current_events: optional status_events list from a prior inspect — used to
        build an app-shaped mark_applied body (preserves the Saved history)
    simplify_url: optional detail page URL (for reference)

    Returns a dict with action type and fetch parameters.
    """
    if action not in ("inspect", "mark_applied", "delete_saved"):
        raise ValueError(f"Unknown action: {action}")

    plan = {
        "action": action,
        "application_id": application_id,
        "simplify_url": simplify_url,
    }

    if action == "inspect":
        plan["fetch"] = {
            "url": f"{SIMPLIFY_ORIGIN}/v2/candidate/me/tracker/{application_id}/detail",
            "method": "GET",
        }
    elif action == "mark_applied":
        plan["fetch"] = {
            "url": f"{SIMPLIFY_ORIGIN}/v2/candidate/me/tracker/{application_id}",
            "method": "PUT",
            "body": build_mark_applied_body(current_events),
        }
    elif action == "delete_saved":
        plan["fetch"] = {
            "url": f"{SIMPLIFY_ORIGIN}/v2/candidate/me/tracker/{application_id}",
            "method": "DELETE",
        }

    return plan


def _build_eval_code(plan):
    """Generate the JS to run the API call synchronously in page context.

    Tab Share's /eval is synchronous — it does NOT await Promises — so a
    `fetch(...)` IIFE returns an empty `{}` (the Promise serializes to nothing).
    A synchronous XMLHttpRequest instead returns the real result.
    """
    fetch_spec = plan["fetch"]
    url = fetch_spec["url"]
    method = fetch_spec["method"]

    headers = {"Client": "Dunder"}
    body = ""
    if "body" in fetch_spec:
        body = json.dumps(fetch_spec["body"])
        headers["Content-Type"] = "application/json"

    # Simplify guards API calls with X-CSRF-TOKEN (a JWT stored in the "csrf"
    # cookie on simplify.jobs — observed in the app's own network requests).
    # The cookie is host-only, so it isn't sent cross-origin to api.simplify.jobs;
    # the app copies it into the header, and so do we. Fallback to the meta tag.
    js_code = f"""
(function() {{
    var out = {{status: 0, ok: false, body: null, responseText: "", error: null}};
    try {{
        var xhr = new XMLHttpRequest();
        xhr.open({json.dumps(method)}, {json.dumps(url)}, false);
        xhr.withCredentials = true;
        var headers = {json.dumps(headers)};
        var csrf = null;
        var m = document.cookie.match(/(?:^|; )csrf=([^;]+)/);
        if (m) {{ csrf = decodeURIComponent(m[1]); }}
        if (!csrf) {{
            var el = document.querySelector('meta[name="csrf-token"]');
            if (el) {{ csrf = el.getAttribute('content'); }}
        }}
        if (csrf) {{ headers['X-CSRF-TOKEN'] = csrf; }}
        for (var k in headers) {{ xhr.setRequestHeader(k, headers[k]); }}
        var body = {json.dumps(body)};
        xhr.send(body || null);
        out.status = xhr.status;
        out.ok = xhr.status >= 200 && xhr.status < 300;
        out.responseText = xhr.responseText;
        try {{ out.body = JSON.parse(xhr.responseText); }} catch (e) {{}}
    }} catch (e) {{ out.error = String(e); }}
    return out;
}})()
"""
    return js_code.strip()


def find_tracker_tab(tab_share_url="http://localhost:8766"):
    """Return the simplify.jobs tracker tab dict, or None.

    Tab Share's /eval without a tabId runs in the *active* tab, so every action
    should target the tracker tab explicitly. This finds it by URL; returning
    None means the tracker isn't open (callers should fail loudly, not fall
    through to whatever tab happens to be active).
    """
    for t in TS.tabs(base=tab_share_url, timeout=6):
        url = t.get("url") or ""
        if "simplify.jobs" in url and "/tracker" in url:
            return t
    return None


def execute_via_tab_share(plan, tab_share_url="http://localhost:8766", dry_run=True,
                          tab_id=None):
    """Execute a planned action via Tab Share /eval endpoint.

    plan: dict from plan_action()
    tab_share_url: Tab Share API base URL
    dry_run: if True, only return the plan without executing
    tab_id: explicit tab to run in; when None, the simplify.jobs tracker tab is
        located automatically (fails loudly if it isn't open)

    Returns: dict with success status and response/error
    """
    result = {
        "action": plan["action"],
        "application_id": plan["application_id"],
        "dry_run": dry_run,
    }

    if dry_run:
        result["status"] = "skipped"
        result["message"] = "Dry run — no mutation performed"
        return result

    if tab_id is None:
        tab = find_tracker_tab(tab_share_url)
        tab_id = tab.get("id") if tab else None
        if tab_id is None:
            result["status"] = "error"
            result["error"] = "No simplify.jobs/tracker tab open — open it first"
            return result

    eval_code = _build_eval_code(plan)

    resp_data, err = TS.post_raw(
        "/eval", {"code": eval_code, "tabId": tab_id}, base=tab_share_url, timeout=30)
    if err:
        result["status"] = "error"
        result["error"] = err
        return result

    # Tab Share returns {result: {ok: bool, value: any}}
    if "result" in resp_data:
        if resp_data["result"].get("ok"):
            fetch_result = resp_data["result"].get("value", {})
            result["status"] = "success" if fetch_result.get("ok") else "failed"
            result["response"] = fetch_result
        else:
            result["status"] = "error"
            result["error"] = resp_data["result"].get("value", "Unknown error")
    else:
        result["status"] = "error"
        result["error"] = f"Unexpected response: {resp_data}"

    return result
