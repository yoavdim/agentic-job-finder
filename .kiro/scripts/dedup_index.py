#!/usr/bin/env python3
"""Dedup index over applied.md + shortlist.md (search-playbook §7.5, checklist step 1).

Answers one question for a batch of candidates: **have I seen this role, and does that mean
I should skip it?** The distinction the playbook insists on is that a rejection is not
automatically a blacklist:

  judgment rejections  (not-interested / not-qualified / sketchy-site) -> SKIP, it's a verdict
                                                                          on the role itself
  liveness rejections  (listing-removed / link-broken / too-old)       -> RE-SUGGESTABLE, it's
                                                                          a fact about a dead
                                                                          link, not the role
  unclear (unknown / other)                                            -> reported, never
                                                                          auto-skipped

The same "a past cut is not a blanket ban" rule governs the `## Excluded` section, and it
is easy to get catastrophically wrong. Its bullets are mostly *conditional*, naming a
category and then the companies it applied to ("Senior backend cold-applies: Databricks,
Stripe, ..."). Keying a skip off the company names in that prose blacklisted whole
companies — a junior embedded role at Stripe was being dropped because a senior backend
role there had been cut once, which also contradicts the prefs ("never exclude a role just
because the company is big", "never silently drop a DS role"). So:

  structured bullet (`**Company — Role**: reason`) -> a real per-role exclusion, SKIP
  prose category bullet mentioning a company       -> a HINT on a `new` verdict, never a skip

Library use:
    idx = DedupIndex.load("applied.md", "shortlist.md", "manual.md")
    verdict = idx.check(company="Foo", title="Bar", url="https://...")
    if verdict.skip: ...

CLI:
    dedup_index.py --candidates cands.json        # [{company,title,url}, ...]
    dedup_index.py --stats
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import md_tables as M
from reasons import classify_bucket

# How each bucket is treated. skip=True means "don't re-suggest".
BUCKET_SKIP = {
    "applied": True,
    "saved": True,             # in motion; don't re-surface as a new find
    "shortlisted": True,       # already on the list
    "excluded": True,          # ONLY from a structured `**Company — Role**` bullet, which
                               # names one specific role. Prose company mentions never
                               # reach this bucket.
    "rejected-judgment": True,
    "rejected-liveness": False,
    "rejected-unclear": False,
    "new": False,
}


class Verdict:
    __slots__ = ("bucket", "skip", "matched_on", "detail", "reason")

    def __init__(self, bucket, matched_on="", detail="", reason=""):
        self.bucket = bucket
        self.skip = BUCKET_SKIP.get(bucket, False)
        self.matched_on = matched_on   # "url" | "company+title" | "excluded-text" | ""
        self.detail = detail
        self.reason = reason           # rejection reason code, when relevant

    def as_dict(self):
        return {"bucket": self.bucket, "skip": self.skip, "matched_on": self.matched_on,
                "detail": self.detail, "reason": self.reason}

    def __repr__(self):
        return f"<Verdict {self.bucket} skip={self.skip} on={self.matched_on!r}>"


# shortlist_add.py writes structured cuts as:
#     - **Company — Role** (2026-07-31): reason
# Those name one specific role and ARE a real exclusion. Everything else under
# ## Excluded is freeform category prose and is only ever a hint (see _read_excluded).
EXCLUDED_ROLE_RE = re.compile(
    r"^\*\*(?P<company>[^*]+?)\s+[—–-]\s+(?P<role>[^*]+?)\*\*"
    r"(?:\s*\((?P<date>[^)]*)\))?\s*:?\s*(?P<reason>.*)$")


class DedupIndex:
    def __init__(self):
        self.by_key = {}     # (company, title) -> (bucket, detail, reason)
        self.by_code = {}    # ats_code        -> (bucket, detail, reason)
        self.by_url = {}     # exact cleaned URL -> (bucket, detail, reason)
        # normalized company -> [bool: does that row carry a strong ATS code?]
        # Used by _blind_company to tell "we have a key for this employer" from
        # "title matching is all we have".
        self._company_rows = {}
        self.excluded_text = []
        self.counts = {}

    # ---------- construction ----------
    def _add(self, bucket, row, reason=""):
        detail = f"{row.get('company')} — {row.get('role')}"
        keys, code = M.row_keys(row)
        co = M.norm(row.get("company"))
        if co:
            self._company_rows.setdefault(co, []).append(bool(code))
        for k in keys:
            # First writer wins per key, but a stronger bucket may overwrite a weaker one.
            prev = self.by_key.get(k)
            if prev is None or self._rank(bucket) > self._rank(prev[0]):
                self.by_key[k] = (bucket, detail, reason)
        if code:
            prev = self.by_code.get(code)
            if prev is None or self._rank(bucket) > self._rank(prev[0]):
                self.by_code[code] = (bucket, detail, reason)
        self.counts[bucket] = self.counts.get(bucket, 0) + 1

    @staticmethod
    def _rank(bucket):
        # applied is the strongest signal: an applied role stays applied even if a
        # later section also mentions it (playbook §6: applied always wins over [nope]).
        order = ["rejected-unclear", "rejected-liveness", "excluded", "shortlisted",
                 "rejected-judgment", "saved", "applied"]
        return order.index(bucket) if bucket in order else -1

    @classmethod
    def load(cls, applied_path=None, shortlist_path=None, manual_path=None):
        """Load an index straight from file paths (reads only what exists)."""
        applied = (M.read_lines(applied_path)
                   if applied_path and Path(applied_path).exists() else [])
        shortlist = (M.read_lines(shortlist_path)
                     if shortlist_path and Path(shortlist_path).exists() else [])
        manual = (M.read_lines(manual_path)
                  if manual_path and Path(manual_path).exists() else [])
        return cls.from_lines(applied, shortlist, manual)

    @classmethod
    def from_lines(cls, applied_lines, shortlist_lines, manual_lines=None):
        """Build an index from in-memory line lists, so callers can chain edits
        without touching disk (shortlist_add does this before --apply)."""
        idx = cls()
        t = M.find_table(applied_lines, "## Applied")
        if t:
            for row in t.rows:
                idx._add("applied", row)
        t = M.find_table(applied_lines, "## Saved")
        if t:
            for row in t.rows:
                # `## Saved` is the single copy of the Simplify Saved list. A row whose
                # Status says `rejected` is on its way out on the next sync, so it must NOT
                # keep blocking the role — otherwise rejecting something would silently
                # bar it from ever being suggested again, which is the judgment-vs-liveness
                # mistake in another guise. It is skipped here and will land in
                # `## Rejected` with a real reason once the sync pushes it.
                if (row.get("status") or "").strip().lower() == "rejected":
                    continue
                idx._add("saved", row)
        t = M.find_table(applied_lines, "## Rejected")
        if t:
            for row in t.rows:
                reason = (row.get("reason") or "").strip().lower()
                idx._add(classify_bucket(reason), row, reason=reason)
        for t in M.find_tables(shortlist_lines, r"Tier \d+"):
            for row in t.rows:
                idx._add("shortlisted", row)
        idx._index_manual(manual_lines or [])
        idx.excluded_text = cls._read_excluded(shortlist_lines)
        idx._index_excluded_roles()
        return idx

    def _index_manual(self, manual_lines):
        """Index `manual.md`'s inbox rows.

        A row sitting in the inbox is a role you have ALREADY applied to or saved — the
        only thing missing is the bookkeeping. Leaving this file out of the index (as it was
        until now) meant a later search could re-suggest a role you had already applied to,
        and you could apply twice. That hole widened once stage 0d began deliberately
        leaving rows it couldn't resolve from the URL alone.

        Only the URL is dependable here: the row has no company/role columns. A recognised
        ATS code is used when there is one; otherwise the row is indexed under its EXACT
        cleaned URL. Exact URL equality is not a heuristic — the same URL is the same page —
        so it is safe in a way that a weak *code* is not (a weak code can be a shared
        careers landing page, which is why `row_keys` drops those).
        """
        t = M.find_table(manual_lines, "## Entries")
        if t is None:
            return
        ci_url = t.col("url") if t.has("url") else 1
        ci_status = t.col("status") if t.has("status") else 2
        for row in t.rows:
            cells = row.cells
            url = M.extract_url(cells[ci_url]) if len(cells) > ci_url else ""
            if not url:
                continue
            status = (cells[ci_status] if len(cells) > ci_status else "").strip().lower()
            bucket = "applied" if status == "applied" else "saved"
            detail = f"in manual.md inbox ({status or 'saved'}): {url}"
            code = M.ats_code(url)
            if M.is_strong_code(code):
                prev = self.by_code.get(code)
                if prev is None or self._rank(bucket) > self._rank(prev[0]):
                    self.by_code[code] = (bucket, detail, "")
            clean = M.clean_url(url)
            if clean:
                prev = self.by_url.get(clean)
                if prev is None or self._rank(bucket) > self._rank(prev[0]):
                    self.by_url[clean] = (bucket, detail, "")
            self.counts[bucket] = self.counts.get(bucket, 0) + 1

    def _index_excluded_roles(self):
        """Register the structured `**Company — Role**` bullets as real exclusions.

        Only these get skip=True. A bullet that merely lists company names under a
        category heading cannot identify a role, so it stays a hint.
        """
        for bullet in self.excluded_text:
            m = EXCLUDED_ROLE_RE.match(bullet.strip())
            if not m:
                continue
            company = m.group("company").strip()
            role = m.group("role").strip()
            if not company or not role:
                continue
            key = M.norm_key(company, role)
            detail = f"{company} — {role}" + (
                f": {m.group('reason').strip()}" if m.group("reason").strip() else "")
            if key not in self.by_key or self._rank("excluded") > self._rank(self.by_key[key][0]):
                self.by_key[key] = ("excluded", detail, "")
            self.counts["excluded"] = self.counts.get("excluded", 0) + 1

    @staticmethod
    def _read_excluded(lines):
        """Collect the bullet text under '## Excluded'.

        These bullets are freeform prose and — critically — most of them record a
        *conditional* cut, naming a category and then the companies it applied to:

            - **Senior backend / data-scientist cold-applies:** Databricks, Stripe, ...
            - **Remote-only** (against in-office pref): Groq, Coinbase, ...

        The company names in those bullets are NOT blacklisted companies; they are
        companies where one particular posting failed a filter. Matching on company
        alone therefore cannot be a skip decision — see `_excluded_hit`.
        """
        out, inside = [], False
        for l in lines:
            s = l.strip()
            if s.lower().startswith("## excluded"):
                inside = True
                continue
            if inside and s.startswith("## "):
                break
            if inside and s.startswith("-"):
                out.append(s.lstrip("- ").strip())
        return out

    # ---------- query ----------
    def check(self, company, title, url=""):
        # Strong codes only — a bare-URL fallback is shareable between distinct roles
        # (see M.row_keys), so it must not decide identity on its own.
        code = M.ats_code(url) if url else ""
        if not M.is_strong_code(code):
            code = ""
        if code and code in self.by_code:
            bucket, detail, reason = self.by_code[code]
            return Verdict(bucket, "url", detail, reason)

        # Exact cleaned-URL equality (currently only manual.md inbox rows carry these).
        clean = M.clean_url(url) if url else ""
        if clean and clean in self.by_url:
            bucket, detail, reason = self.by_url[clean]
            return Verdict(bucket, "exact-url", detail, reason)

        key = M.norm_key(company, title)
        if key in self.by_key:
            bucket, detail, reason = self.by_key[key]
            return Verdict(bucket, "company+title", detail, reason)

        notes = []

        # Dedup-blind: this company is already in the ledger, but neither this candidate nor
        # the rows we hold for it have a strong ATS code — so a title mismatch (Simplify
        # shorthand, a renamed req) is the ONLY thing between here and a double application.
        # Reported, never a skip: it's a statement about our keys, not about the role.
        blind = self._blind_company(company, code)
        if blind:
            notes.append(f"dedup is blind here: {blind}")

        # A company named in an Excluded bullet is a HINT, never a skip (see
        # _read_excluded): the bullets record conditional cuts, so the same company can
        # still have a role that fits. Surfaced as context on a `new` verdict so the
        # reviewer sees why it might have been cut before and can judge this posting.
        hit = self._excluded_hit(company)
        if hit:
            notes.append(f"company appears in an Excluded bullet (check it still applies): {hit}")

        near = self.near_duplicates(company, title)
        if near:
            notes.append("possible near-duplicate of: " + "; ".join(near))

        v = Verdict("new", "excluded-text" if hit else "", " | ".join(notes))
        return v

    def _blind_company(self, company, candidate_code):
        """Describe why dedup can't be trusted for this company, or "".

        Fires only when BOTH sides lack a strong key: the candidate has no recognised ATS
        code, and every row we hold for that company also has none. In that state the only
        available key is the title, and titles are exactly what Simplify shorthand mangles.
        """
        if candidate_code:
            return ""                   # we have a strong key; titles don't matter
        c = M.norm(company)
        if not c:
            return ""
        rows = self._company_rows.get(c)
        if not rows:
            return ""
        keyed = sum(1 for has_code in rows if has_code)
        if keyed:
            return ""                   # at least one side is keyed; a future match can hit
        return (f"{len(rows)} row(s) already recorded for {company}, none with a usable "
                f"apply URL, and this candidate has none either — only the title can match")

    def _excluded_hit(self, company):
        """An Excluded bullet that mentions this company, or "".

        Matched on whole words only. Plain substring containment produced false hits on
        short names (company "Ada" matching the word "Canada" in a bullet).
        """
        c = M.norm(company)
        if not c or len(c) < 2:
            return ""
        pat = re.compile(r"(?<![\w-])" + re.escape(c) + r"(?![\w-])")
        for bullet in self.excluded_text:
            if pat.search(M.norm(bullet)):
                return bullet[:120]
        return ""

    def near_duplicates(self, company, title):
        """Same company, containment-related title. Reported for a human/LLM look —
        never used to auto-skip, because 'Engineer I' vs 'Engineer II' are distinct roles."""
        c, t = M.norm_key(company, title)
        if not c or not t:
            return []
        out = []
        for (kc, kt), (bucket, detail, _r) in self.by_key.items():
            if kc != c or kt == t:
                continue
            if kt in t or t in kt:
                out.append(f"{detail} [{bucket}]")
        return sorted(set(out))

    def stats(self):
        return dict(sorted(self.counts.items()))


def main():
    ap = argparse.ArgumentParser(description="Dedup index over applied.md + shortlist.md")
    ap.add_argument("--applied", default="applied.md")
    ap.add_argument("--shortlist", default="shortlist.md")
    ap.add_argument("--manual", default="manual.md",
                    help="manual.md inbox — its rows are real applications/saves "
                         "awaiting bookkeeping, so they must block re-suggestion too")
    ap.add_argument("--candidates", help="JSON list of {company,title,url} to check")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--json", help="write results as JSON here ('-' = stdout)")
    args = ap.parse_args()

    idx = DedupIndex.load(args.applied, args.shortlist, args.manual)

    if args.stats or not args.candidates:
        print("index: " + ", ".join(f"{k}={v}" for k, v in idx.stats().items()), file=sys.stderr)
        print(f"       {len(idx.by_code)} url keys, {len(idx.excluded_text)} excluded bullets",
              file=sys.stderr)
        if not args.candidates:
            return

    cands = json.load(open(args.candidates, encoding="utf-8"))
    results = []
    for c in cands:
        v = idx.check(c.get("company", ""), c.get("title", c.get("role", "")), c.get("url", ""))
        results.append({**c, **v.as_dict()})

    if args.json:
        M.write_json(args.json, results)
    else:
        json.dump(results, sys.stdout, indent=1)
        print()

    keep = [r for r in results if not r["skip"]]
    drop = [r for r in results if r["skip"]]
    flagged = [r for r in keep if r["detail"]]
    print(f"\n{len(keep)} to consider ({len(flagged)} with a note), "
          f"{len(drop)} already known", file=sys.stderr)
    for r in drop:
        print(f"  SKIP {r.get('company')} — {r.get('title', r.get('role'))}"
              f"  [{r['bucket']}] {r['detail']}", file=sys.stderr)
    for r in flagged:
        print(f"  ?    {r.get('company')} — {r.get('title', r.get('role'))}"
              f"  {r['detail']}", file=sys.stderr)
    if flagged:
        print("NOTE: '?' rows are KEPT — the note is context to weigh, not a rejection "
              "(playbook §7.5)", file=sys.stderr)


if __name__ == "__main__":
    main()
