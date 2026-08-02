#!/usr/bin/env python3
"""Shared rejection-reason vocabulary for the job-search workspace.

One place for the reason codes that migrate_resolved and saved_sync classify comments
into (playbook §7), and the judgment-vs-liveness split that decides whether a rejected
role may be re-suggested (playbook §7.5). dedup_index and shortlist_add map a reason to
its dedup bucket through `classify_bucket`.
"""
import re

REASON_CODES = [
    "link-broken", "listing-removed", "too-old", "not-qualified",
    "not-interested", "sketchy-site", "unknown", "other",
]

JUDGMENT_REASONS = {"not-interested", "not-qualified", "sketchy-site"}
LIVENESS_REASONS = {"listing-removed", "link-broken", "too-old"}


def classify_bucket(reason):
    """Map a rejection reason code to its dedup bucket.

    rejected-judgment — a verdict on the role/company itself (skip: a blacklist)
    rejected-liveness — a fact about a dead/stale link (re-suggestable)
    rejected-unclear  — 'unknown'/'other'/missing (reported, never auto-skipped)
    """
    r = (reason or "").strip().lower()
    if r in JUDGMENT_REASONS:
        return "rejected-judgment"
    if r in LIVENESS_REASONS:
        return "rejected-liveness"
    return "rejected-unclear"


_HEAD_RE = re.compile(r"\s+[—–-]\s+")


def split_leading_code(comment):
    """('<code>', 'rest') from "<code> — free text", or ('', comment) if there's no
    recognised leading code. tracker.html writes reject comments in this shape; a
    hand-typed comment has no code and falls through to a caller's own keyword rules.
    """
    c = (comment or "").strip()
    if not c:
        return "", ""
    head = _HEAD_RE.split(c, maxsplit=1)[0].strip().lower()
    return (head, c) if head in REASON_CODES else ("", c)
