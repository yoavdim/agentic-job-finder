#!/usr/bin/env python3
"""Derive a role's company/title from an apply URL's STRUCTURE. No LLM, no network.

SCOPE, AND WHY IT'S THIS NARROW
-------------------------------
This module reads only what the URL *structure* encodes: the org slug in a host or path
segment, and a title slug when the path genuinely contains one. It does NOT parse page
titles, and it does not fetch anything.

That boundary is deliberate and was arrived at by looking at real data. Four page titles
observed across the hosts in this workspace:

    Systems Software Engineer - Xanadu - Career Page                       (applytojob)
    Thank you for applying                                                 (greenhouse)
    Kepler Communications                                                  (lever)
    AI and Automation Software Engineer in MARKHAM, Canada - Advanced …    (AMD careers)

Four shapes, one of which is not a job title at all — and the greenhouse case is worse than
useless because it looks plausible while describing a confirmation page. There is no format
to key off. Recovering fields from title prose is reading comprehension, so it belongs to
the LLM stage, not here. Any title text a caller already has is passed through **verbatim**
as `Raw` (which is defined as the verbatim title) rather than being interpreted.

The practical consequence, stated plainly: only a minority of URLs yield a role. A caller
that needs one must be prepared to be told "no" and defer the row.

    ident = identify("https://xanadu.applytojob.com/apply/AbC/Systems-Software-Engineer")
    ident.company  -> "Xanadu"
    ident.role     -> "Systems Software Engineer"
    ident.complete -> True
"""
import re
from urllib.parse import unquote

# Org slug lives in a subdomain for these hosts: <org>.<host>/...
SUBDOMAIN_ORG_HOSTS = (
    "applytojob.com",
    "recruitee.com",
    "myworkdayjobs.com",
    "bamboohr.com",
    "breezy.hr",
)

# Org slug is the first path segment for these: <host>/<org>/...
PATH_ORG_HOSTS = (
    "greenhouse.io",
    "ashbyhq.com",
    "lever.co",
    "smartrecruiters.com",
)

# Hosts that carry no company identity at all in the URL — the org is the aggregator.
AGGREGATOR_HOSTS = (
    "linkedin.com", "indeed.com", "builtin.com", "builtintoronto.com",
    "glassdoor.com", "ziprecruiter.com", "simplify.jobs", "wellfound.com",
    "tealhq.com", "ycombinator.com", "google.com",
)

# Org slugs that are the ATS's own marketing subdomain rather than an employer.
NOT_AN_ORG = {"www", "jobs", "job-boards", "boards", "careers", "apply", "my", "api"}

# Path segments that never contain a human title.
NOT_A_TITLE_SEGMENT = {
    "apply", "confirm", "jobs", "job", "o", "en-us", "en", "careers", "career",
    "job_application_requests", "application", "applications", "confirmation",
    "userhome", "search", "position", "positions", "opening", "openings",
}

# A title slug has to look like words, not an opaque id. Rejecting ids is the whole point:
# `4567`, a uuid, or `t3b9vphmocbvtm4c7u80wwh9ztk3q1us` are not titles.
_HEXISH = re.compile(r"^[0-9a-f-]+$", re.I)
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_VOWEL = re.compile(r"[AEIOUaeiou]")


def host_of(url):
    m = re.match(r"https?://([^/:?#]+)", (url or "").strip())
    return m.group(1).lower() if m else ""


def path_segments(url):
    m = re.match(r"https?://[^/]+(/[^?#]*)", (url or "").strip())
    if not m:
        return []
    return [unquote(s) for s in m.group(1).split("/") if s]


def _base_host(host):
    """'xanadu.applytojob.com' -> 'applytojob.com' (last two labels, or three for .hr etc)."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_aggregator(host):
    return any(host == a or host.endswith("." + a) for a in AGGREGATOR_HOSTS)


def slug_to_words(slug):
    """'Systems-Software-Engineer' -> 'Systems Software Engineer'. Returns "" if the slug
    doesn't look like words (opaque ids, hashes, uuids are rejected, not guessed at)."""
    if not slug:
        return ""
    s = unquote(slug).strip()
    if s.lower() in NOT_A_TITLE_SEGMENT:
        return ""
    if not _HAS_LETTER.search(s):
        return ""                       # pure digits
    if _HEXISH.match(s) and not re.search(r"[-_]", s):
        return ""                       # hex blob / uuid-ish with no word separators
    words = re.split(r"[-_+]+", s)
    words = [w for w in words if w]
    if not words:
        return ""
    # An id-looking single token ("t3b9vphmocbvtm4c7u80wwh9ztk3q1us", "4438904097") is not a
    # title. Require either several tokens, or one token that reads like a word.
    if len(words) == 1:
        w = words[0]
        if len(w) > 18 or not _HAS_VOWEL.search(w) or re.search(r"\d{3}", w):
            return ""
    out = " ".join(words).strip()
    # Preserve the slug's own casing — `Systems-Software-Engineer` is already title-cased,
    # and word-level re-casing would invent things ("For", "Ai"). Only lift the first
    # character, so an all-lowercase slug still reads as a title.
    return out[:1].upper() + out[1:] if out else ""


def ats_org(url):
    """The employer's org slug as the URL structurally encodes it, or "".

    Structural only: a subdomain label or a fixed path position. Never inferred from prose.
    """
    host = host_of(url)
    if not host or _is_aggregator(host):
        return ""

    base = _base_host(host)
    if any(host.endswith(h) for h in SUBDOMAIN_ORG_HOSTS):
        label = host[: -(len(base) + 1)] if host != base else ""
        # workday is <org>.wdN.myworkdayjobs.com — drop the shard label
        label = re.sub(r"\.wd\d+$", "", label)
        label = label.split(".")[0]
        return "" if label in NOT_AN_ORG else label

    if any(host.endswith(h) for h in PATH_ORG_HOSTS):
        segs = path_segments(url)
        if segs and segs[0].lower() not in NOT_AN_ORG | NOT_A_TITLE_SEGMENT:
            return segs[0]
        return ""

    return ""


def slug_role(url):
    """A role title, only when the URL PATH encodes one. "" otherwise.

    Concretely this covers applytojob's `/apply/<code>/<Title-Slug>` and recruitee's
    `/o/<title-slug>`. Greenhouse/ashby/lever job paths end in an opaque id and yield "".
    """
    segs = path_segments(url)
    if not segs:
        return ""
    host = host_of(url)

    if host.endswith("applytojob.com"):
        # /apply/<code>/<Title-Slug>  — the confirm variant has no title
        if len(segs) >= 3 and segs[0].lower() == "apply" and segs[1].lower() != "confirm":
            return slug_to_words(segs[2])
        return ""

    if host.endswith("recruitee.com"):
        if len(segs) >= 2 and segs[0].lower() == "o":
            return slug_to_words(segs[1])
        return ""

    # Everything else: only trust a trailing segment that reads like words AND isn't
    # preceded by a title-bearing convention we don't know. Deliberately conservative.
    return ""


def company_from_org(org):
    """Turn an org slug into something presentable. Casing only — never a rename.

    Mapping a slug to a real legal/brand name ("doordashcanada" -> "DoorDash Canada",
    "advanced micro devices, inc" -> "AMD") is a judgment call and belongs to the LLM or to
    the alias table in the sync, not to a URL parser.
    """
    if not org:
        return ""
    words = [w for w in re.split(r"[-_]+", org) if w]
    return " ".join(w if w.isupper() else w.capitalize() for w in words)


class Identity:
    __slots__ = ("url", "company", "role", "org", "raw_title", "source")

    def __init__(self, url, company="", role="", org="", raw_title="", source=""):
        self.url = url
        self.company = company
        self.role = role
        self.org = org
        self.raw_title = raw_title      # carried through verbatim, never parsed
        self.source = source            # which structural rule produced the role

    @property
    def complete(self):
        """True when BOTH fields came from structure — the bar for a headless insert."""
        return bool(self.company and self.role)

    def missing(self):
        return [f for f, v in (("company", self.company), ("role", self.role)) if not v]

    def as_dict(self):
        return {"url": self.url, "company": self.company, "role": self.role,
                "org": self.org, "raw_title": self.raw_title, "source": self.source,
                "complete": self.complete}

    def __repr__(self):
        return f"<Identity {self.company!r} {self.role!r} complete={self.complete}>"


def identify(url, raw_title=""):
    """Best structural identity for `url`. `raw_title` is stored verbatim, not parsed."""
    org = ats_org(url)
    role = slug_role(url)
    source = ""
    if role:
        host = host_of(url)
        source = "applytojob-slug" if host.endswith("applytojob.com") else \
                 "recruitee-slug" if host.endswith("recruitee.com") else "path-slug"
    return Identity(url=url, company=company_from_org(org), role=role, org=org,
                    raw_title=(raw_title or "").strip(), source=source)
