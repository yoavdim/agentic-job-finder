#!/usr/bin/env python3
"""Structural checks on candidate rows before they enter shortlist.md.

WHAT THIS DOES NOT DO — and deliberately so
-------------------------------------------
It does not judge fit. It does not decide seniority, region, remote-vs-hybrid, DV-vs-FV,
or whether a degree requirement is a hard gate. Those are reading comprehension over a job
description, they are the reason an LLM is driving this pipeline at all, and a keyword
heuristic gets them wrong in both directions:

  - "Junior Software Engineer, Storage Lead"        -> "Lead" is not the level
  - "Verification Engineer"                        -> usually DV, but not always
  - "assertion-based verification"                 -> a formal technique that DV
                                                      testbenches also use
  - "PhD a plus"                                   -> not a requirement
  - "Toronto, occasional travel to Montreal"       -> not an out-of-region role

An earlier version of this module encoded exactly those rules in regex. Because a finding
blocks the insert, every false positive silently withheld a good role — the same failure as
the `## Excluded` prose matching that `dedup_index` had to be fixed for. Reintroducing it
one file over would have been the same bug with a different spelling.

WHAT IT DOES
------------
Checks that the caller RECORDED the judgment the playbook asks for, and does the two checks
that are pure arithmetic or pure schema:

  missing-evidence   Playbook §1f: "NEVER add a role to shortlist.md on title alone" — read
                     the responsibilities + requirements and capture the years-of-experience
                     bar. This asserts an `evidence` field exists and is substantive. It
                     CANNOT verify the read happened, only that a claim was recorded — but
                     that turns a silent omission into a visible one, and leaves an audit
                     trail in the plan JSON and in the row itself.
  missing-notes-flag The Notes legend is part of the documented schema (§4: ✅ good fit ·
                     ⚠️ verify level/location · 🔗 referral). A row with no marker has not
                     been classified. This is also what enforces the DS rule: prefs say a
                     data-science role must ALWAYS be surfaced flagged ⚠️ for a per-role
                     decision — so the caller marks it, rather than a regex guessing which
                     titles are DS.
  stale-posting      Date arithmetic on a date the caller already supplies. Fully
                     mechanical, no interpretation.

Everything here is overridable with --force, and `location-review` is advisory only: it
prints and never blocks, so a slip gets a second look without a heuristic holding the
gate.

Library use:
    findings = candidate_lint.lint_candidate(cand)   # [{code, severity, detail}]
    blocking = [f for f in findings if f["severity"] == "error"]
"""
import re

DEFAULT_MAX_AGE_DAYS = 31

# The documented Notes legend from the playbook (§4). Presence of one of these is the
# signal that the caller classified the row at all.
NOTES_LEGEND = ("✅", "⚠️", "🔗")

# `evidence` is prose, but a two-word stub is not a read. This is a floor on effort, not a
# judgement about content.
MIN_EVIDENCE_CHARS = 25

# Advisory only. Short, structured-ish location strings where a slip is worth a second
# look — but NEVER a block, because "Toronto (team in Montreal)" is a legitimate row.
LOCATION_REVIEW_RE = re.compile(
    r"(?<![\w-])(montr[eé]al|ottawa|gatineau|qu[eé]bec|QC|laval|remote)(?![\w-])", re.I)


def _s(cand, *keys):
    for k in keys:
        v = cand.get(k)
        if v:
            return str(v)
    return ""


def lint_candidate(cand, today=None, max_age_days=DEFAULT_MAX_AGE_DAYS, strict=True):
    """Findings for one candidate: [{code, severity, detail}].

    severity "error" blocks the insert unless --force; "warn" is advisory and prints only.
    `strict=False` drops the evidence requirement (useful for a bulk import where the rows
    were already reviewed elsewhere).
    """
    import jobdates as JD          # local import keeps this module importable on its own

    findings = []
    notes = _s(cand, "notes")
    evidence = _s(cand, "evidence").strip()

    if strict:
        if not evidence:
            findings.append({
                "code": "missing-evidence", "severity": "error",
                "detail": "no `evidence` field. Playbook §1f: read the responsibilities and "
                          "the requirements before adding a role, never the title alone — "
                          "then record what they said (especially the years-of-experience "
                          "bar) so the decision is reviewable"})
        elif len(evidence) < MIN_EVIDENCE_CHARS:
            findings.append({
                "code": "missing-evidence", "severity": "error",
                "detail": f"`evidence` is only {len(evidence)} chars ({evidence!r}) — record "
                          f"what the responsibilities/requirements actually said, including "
                          f"the years-of-experience bar"})

    if not any(flag in notes for flag in NOTES_LEGEND):
        findings.append({
            "code": "missing-notes-flag", "severity": "error",
            "detail": f"Notes carry no legend marker ({' '.join(NOTES_LEGEND)}) so the row is "
                      f"unclassified. Per prefs a data-science role in particular must be "
                      f"surfaced with ⚠️ for a per-role decision, never added silently"})

    posted = cand.get("posted") or ""
    if posted:
        iso = JD.parse_posting_date(posted, today)
        age = JD.age_days(iso, today) if iso else None
        if age is not None and age > max_age_days:
            findings.append({
                "code": "stale-posting", "severity": "error",
                "detail": f"posting dated {iso} is {age}d old (cutoff {max_age_days}d) — it "
                          f"would be swept as too-old on the next pass anyway"})

    location = _s(cand, "location")
    hit = LOCATION_REVIEW_RE.search(location)
    if hit:
        findings.append({
            "code": "location-review", "severity": "warn",
            "detail": f"location {location.strip()!r} mentions {hit.group(1)!r}; prefs exclude "
                      f"QC/Ottawa and remote-only. Advisory only — hybrid and "
                      f"'Toronto, some travel' rows are legitimate, so this never blocks"})

    return findings


def blocking(findings):
    return [f for f in findings if f.get("severity") == "error"]


def advisory(findings):
    return [f for f in findings if f.get("severity") == "warn"]


def lint_batch(candidates, today=None, max_age_days=DEFAULT_MAX_AGE_DAYS, strict=True):
    """{index: findings} for every candidate with at least one finding."""
    out = {}
    for i, cand in enumerate(candidates):
        f = lint_candidate(cand, today, max_age_days, strict)
        if f:
            out[i] = f
    return out
