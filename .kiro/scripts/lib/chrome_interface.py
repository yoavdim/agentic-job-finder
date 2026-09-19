#!/usr/bin/env python3
"""One shared browser-driving interface over Tab Share (lib/chrome_interface.py).

tab_share.py is the thin HTTP client for the extension (post/tabs/extract/close).
Above it, every script used to re-implement the same browser-driving operations in its
own slightly different way — and the duplication drifted:

  - linkedin_harvest / read_jobs / simplify_search each had an open->sleep->extract->
    close batch loop with different waits and close gates
  - liveness_sweep had `render_text` (open -> extract by tab id -> close) and its own
    `host_of`
  - simplify_search had container-aware scroll helpers (`js_scroll_bottom` / wiggle)
  - housekeeping had the per-host gated group close (`close_scratch`)
  - the watchlist scripts added CSS-selector element extraction, consent-modal
    dismissal, relative-href resolution, and live selector probing

That is a lot of nearly-the-same code with no shared home. This module is that home:
the ChromeInterface class is the browser-driving layer every scraping/probing script in
the workspace drives. The name is deliberately generic — it is the shared browser
interface for all of them, not a helper for one scraper.

Usage:
    import chrome_interface as CI
    ci = CI.ChromeInterface()                       # Chromium :8766, Scratch group
    if not ci.is_up(): ...
    tid = ci.open_loaded("https://careers.example.com")
    ci.scroll(tid, steps=2)                         # lazy lists
    ci.close_modals(tid)                            # consent/cookie dismissal
    res = ci.extract_elements(tid, "a[data-automation-id='jobTitle']")
    ci.close([tid], expect_host="careers.example.com")
"""
import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tab_share as TS

SCRATCH_GROUP = "Scratch"


class _ElementHTML(HTMLParser):
    """Visible text + first href from one element's outerHTML."""

    # skipped so icon markup doesn't leak into the title
    SKIP_TAGS = frozenset({"script", "style", "svg", "noscript"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.href = ""
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if not self.href:
            href = dict(attrs).get("href")
            if href:
                self.href = href

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self):
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def parse_element_html(html):
    """{text, href, html} for one element's outerHTML."""
    p = _ElementHTML()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    return {"text": p.text, "href": p.href, "html": html}

# ---- browser snippets. All are IIFEs: a top-level `return` is a SyntaxError in an
# /eval context (verified live). ----

_EXTRACT_JS = (
    "(function(){"
    "var out=[];"
    "var els=document.querySelectorAll({sel});"
    "for(var i=0;i<els.length;i++){"
    "var e=els[i];"
    "var t=(e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim();"
    "var h=e.href||e.getAttribute('href')||'';"
    "if(!h){"
    "var a=e.closest('a');"
    "if(a){h=a.href||a.getAttribute('href')||'';}"
    "}"
    "if(t||h){out.push({text:t,href:h});}"
    "}"
    "return {count:out.length,items:out};"
    "})()"
)

_SCROLL_JS = "(function(){window.scrollTo(0,document.body.scrollHeight);return true;})()"

# Find the innermost scrollable ancestor of an element matching `sel` and jump it to the
# bottom (a lazy list container). `{sel}` is injected as a JSON string literal.
_SCROLL_CONTAINER_JS = (
    "(function(){"
    "var c=document.querySelector({sel});"
    "if(!c){return 'no-cards';}"
    "var el=c;"
    "while(el&&el.scrollHeight<=el.clientHeight+50&&el.parentElement){el=el.parentElement;}"
    "if(el&&el.scrollHeight>el.clientHeight+50){el.scrollTop=el.scrollHeight;}"
    "return el?(el.scrollTop+'/'+el.scrollHeight):'none';"
    "})()"
)

# Nudge the container up a little then back to the bottom — a stalled lazy-loader
# usually resumes on the second nudge.
_SCROLL_WIGGLE_JS = (
    "(function(){"
    "var c=document.querySelector({sel});"
    "if(!c){return 'no-cards';}"
    "var el=c;"
    "while(el&&el.scrollHeight<=el.clientHeight+50&&el.parentElement){el=el.parentElement;}"
    "if(!el){return 'none';}"
    "el.scrollTop=Math.max(0,el.scrollTop-600);"
    "el.scrollTop=el.scrollHeight;"
    "return el.scrollTop+'/'+el.scrollHeight;"
    "})()"
)

# Best-effort consent/cookie dismissal; any failure in a single candidate is swallowed —
# a modal left in place is a scrape-quality problem, not a reason to abort.
_CLOSE_MODALS_JS = (
    "(function(){"
    "var out=[];"
    "function vis(e){var r=e.getBoundingClientRect?e.getBoundingClientRect():"
    "{width:0,height:0};return r.width>0&&r.height>0;}"
    "var bySel=['#onetrust-accept-btn-handler','#onetrust-accept-all-btn-handler',"
    "'#cookieChoiceDismiss','.accept-cookies','.cookie-accept','button.accept',"
    "'button.accept-all','a.accept','.fc-button.fc-cta-consent',"
    "'.didomi-consent-va button'];"
    "for(var si=0;si<bySel.length;si++){var s=bySel[si];var n;"
    "try{n=document.querySelectorAll(s);}catch(e){continue;}"
    "for(var i=0;i<n.length;i++){if(vis(n[i])){try{n[i].click();out.push(s);}catch(e){}}}}"
    "if(out.length===0){var all=document.querySelectorAll('button');"
    "for(var j=0;j<all.length;j++){var b=all[j];"
    "var label=(b.textContent||'').trim();"
    "if(vis(b)&&/\\b(accept|allow|agree|ok|close|dismiss)\\b/i.test(label)&&label.length<40){"
    "try{b.click();out.push('button:'+label.slice(0,20));}catch(e){}}}}"
    "return {clicked:out};"
    "})()"
)

_READY_JS = "(function(){return document.readyState;})()"

def host_of(url):
    """Host (no port) of a URL, lowercased, or '' when unparsable."""
    m = re.match(r"https?://([^/:]+)", url or "")
    return m.group(1).lower() if m else ""

def url_origin(url):
    """Scheme://host portion of a URL (for resolving relative hrefs), or ''."""
    m = re.match(r"^(https?://[^/]+)", url or "")
    return m.group(1) if m else ""

def resolve_href(href, origin):
    """Turn an anchor `href` into an absolute URL against `origin`.
    Filters out empty and purely anchor links (#)."""
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return ""
    return urljoin(origin, href)


class ChromeInterface:
    """The shared browser-driving interface over Tab Share.

    Holds one Tab Share base URL and one tab group; every operation is scoped to them,
    so a caller opening tabs in the "Scratch" group can never touch keepers in "Job
    Search" even if a selector is wrong (the extension re-gates close on host+group).
    """

    def __init__(self, base=TS.DEFAULT_BASE, group=SCRATCH_GROUP, wait=3,
                 load_timeout=30):
        self.base = base
        self.group = group
        self.wait = wait
        self.load_timeout = load_timeout

    # ---- connectivity ----

    def is_up(self):
        return TS.is_up(self.base)

    def tabs(self):
        return TS.tabs(base=self.base)

    # ---- tab lifecycle ----

    def open(self, url):
        """Open `url` in a new tab in this group. Returns the tab id, or None."""
        return TS.open_tab(url, group_name=self.group, base=self.base)

    def open_loaded(self, url, wait=None, load_timeout=None):
        """Open `url`, wait for the document to finish loading, then one beat for
        client-side rendering. Returns the tab id, or None if it could not be opened."""
        tid = self.open(url)
        if not tid:
            return None
        deadline = time.time() + (load_timeout or self.load_timeout)
        while time.time() < deadline:
            if self.ready(tid) == "complete":
                break
            time.sleep(0.5)
        time.sleep(wait if wait is not None else self.wait)
        return tid

    def ready(self, tab_id):
        """The tab's document.readyState, or None when it can't be read.

        Read via /query so it also works on pages that refuse /eval — there the load poll
        could never succeed, so every open paid the full load_timeout.
        """
        res = TS.query("html", tab_id=tab_id, base=self.base)
        if res.get("ready"):
            return res["ready"]
        return self.eval(tab_id, _READY_JS)

    def eval(self, tab_id, code, timeout=30):
        return TS.eval_value(code, tab_id=tab_id, base=self.base, timeout=timeout)

    def post_raw(self, path, body, timeout=30):
        """Raw {response, error} for arbitrary paths (no retry, no interpretation).
        The response is the parsed body dict; error is a message string or None."""
        return TS.post_raw(path, body, base=self.base, timeout=timeout)

    def extract(self, tab_id, timeout=45):
        """Rendered {text, title, links, ...} from `tab_id`."""
        return TS.extract(tab_id=tab_id, base=self.base, timeout=timeout)

    # ---- rendering ----

    def extract_text(self, url, timeout=45):
        """Rendered page text + title for `url`. Returns (text, error).

        Extracts by tab id, never by URL (see tab_share.extract), and closes the tab so a
        caller looping over many URLs doesn't accumulate one open tab each.
        """
        tab_id = self.open(url)
        if not tab_id:
            return "", "could not open a tab for this URL"
        try:
            data = self.extract(tab_id, timeout=timeout)
            if not data:
                return "", "extract failed or timed out"
            return (data.get("text") or "") + " " + (data.get("title") or ""), None
        finally:
            self.close([tab_id], expect_host=host_of(url) or "*")

    def extract_elements(self, tab_id, selector):
        """{count, items:[{text,href,html}]} for elements matching `selector`, or
        {"error": ...}.

        Selectors must target the anchor itself (`a.posting-title`, not
        `a.posting-title h5`) — href is read from the match or its descendants, never an
        ancestor. Uses /query, falling back to /eval for extensions predating it.
        """
        if not selector:
            return {"error": "empty selector"}
        res = TS.query(selector, tab_id=tab_id, base=self.base)
        if res:
            if "error" in res:
                return res
            items = res.get("items") or []
            if items and isinstance(items[0], str):
                items = [parse_element_html(h) for h in items]
                res = dict(res, items=items, count=len(items))
            return res
        code = _EXTRACT_JS.replace("{sel}", json.dumps(selector))
        return self.eval(tab_id, code) or {"error": "query and eval both failed"}

    # ---- page-driving ----

    def _scroll(self, tab_id, selector=None, mode=None, fallback_js=None):
        """One /scroll call, falling back to `fallback_js` via /eval when unavailable.
        Returns the position string. See extract_elements for why /scroll is preferred."""
        res = TS.scroll(tab_id=tab_id, selector=selector, mode=mode, base=self.base)
        if res:
            return res.get("position")
        if fallback_js is None:
            return None
        code = (fallback_js if selector is None
                else fallback_js.replace("{sel}", json.dumps(selector)))
        return self.eval(tab_id, code)

    def scroll(self, tab_id, steps=1, pause=2):
        """Scroll the WINDOW to the bottom `steps` times, pausing `pause` between."""
        for _ in range(max(1, steps)):
            self._scroll(tab_id, fallback_js=_SCROLL_JS)
            time.sleep(pause)

    def scroll_container(self, tab_id, selector):
        """Scroll the lazy-list container holding `selector` to the bottom. Returns the
        position string (or 'no-cards' when the selector matches nothing)."""
        return self._scroll(tab_id, selector=selector,
                            fallback_js=_SCROLL_CONTAINER_JS)

    def scroll_wiggle(self, tab_id, selector):
        """Nudge the container up then back to the bottom (stalled lazy-loader nudge)."""
        return self._scroll(tab_id, selector=selector, mode="wiggle",
                            fallback_js=_SCROLL_WIGGLE_JS)

    def close_modals(self, tab_id):
        """Best-effort consent/cookie dismissal. Returns the list of clickers used."""
        res = self.eval(tab_id, _CLOSE_MODALS_JS)
        return (res or {}).get("clicked", [])

    # ---- close ----

    def close(self, tab_ids, expect_host="*", group=None, timeout=20):
        """Gated close of `tab_ids` in this group. The extension requires BOTH
        `expectHost` and `expectGroup` (or "*" to skip the host check), so a selector
        mistake can't touch tabs outside this group."""
        return TS.close(tab_ids=tab_ids, expect_group=group or self.group,
                        expect_host=expect_host, base=self.base, timeout=timeout)

    def close_group(self, group=None):
        """Close every tab in `group` (default: this interface's group). Returns
        (closed, rejected, error).

        /tabs does not report group membership and the close gate requires a concrete
        `expectHost`, so the group filter can only be applied server-side: enumerate the
        distinct open hosts and issue one gated call per host. Tabs in other groups are
        rejected by the gate on every call by design; only non-host-mismatch rejections
        are counted.
        """
        group = group or self.group
        raw = TS.get("/tabs", base=self.base, timeout=5)
        if raw is None:
            return 0, 0, "Tab Share not reachable"
        hosts = sorted({h for h in (host_of(t.get("url")) for t in raw.get("tabs", [])) if h})
        if not hosts:
            return 0, 0, None
        closed = rejected = 0
        errors = []
        for host in hosts:
            resp, err = self._post_close({"expectGroup": group, "expectHost": host})
            if err:
                errors.append(f"{host}: {err}")
                continue
            if isinstance(resp, dict) and resp.get("error"):
                errors.append(f"{host}: {resp['error']}")
                continue
            closed += len(resp.get("closed") or [])
            for rej in resp.get("rejected") or []:
                if rej.get("why") != "host-mismatch":
                    rejected += 1
        return closed, rejected, "; ".join(errors) if errors else None

    def _post_close(self, payload):
        """One gated /close call via post_raw (not tab_share.post): the per-host close
        needs a raised-looking failure to distinguish "the call errored" from
        "{} came back empty", which the retrying post() can't tell apart."""
        return TS.post_raw("/close", payload, base=self.base, timeout=20)

    # ---- probing (selector-pass decisions) ----

    def probe(self, url, selector, wait=None, scroll_steps=1):
        """Live-test `selector` against `url`. Returns a dict for JSON output.

        Open the URL, scroll, dismiss modals, extract elements matching `selector`,
        close the tab. The result shape is what the LLM-driven selector pass consumes:
        0 matches => too imprecise; several matches per card => too broad; one per card
        is the target.
        """
        if not self.is_up():
            return {"ok": False, "error": "Tab Share not reachable on :8766/:8765"}
        tid = self.open_loaded(url, wait=wait)
        if not tid:
            return {"ok": False, "error": f"could not open a tab for {url}"}
        try:
            self.scroll(tid, steps=scroll_steps)
            self.close_modals(tid)
            res = self.extract_elements(tid, selector)
            if "error" in res:
                return {"ok": False, "error": res["error"]}
            items = res.get("items", [])
            return {"ok": True, "url": url, "selector": selector,
                    "count": len(items), "samples": items[:5]}
        finally:
            self.close([tid], expect_host="*")
