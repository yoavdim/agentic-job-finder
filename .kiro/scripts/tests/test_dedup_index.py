#!/usr/bin/env python3
"""Tests for dedup_index.py — the judgment-vs-liveness rejection split (§7.5)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from dedup_index import DedupIndex

APPLIED = """# Applied

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-15 | Tenstorrent | Software Engineer, TT-Fabric | Software Engineer, TT-Fabric | Toronto | [Apply](https://www.linkedin.com/jobs/view/4439917871/) |  |
| 2026-06-29 | Xanadu | Quantum Software Developer | q sw dev | Toronto | [Apply](https://xanadu.applytojob.com/apply/0glmWNMBAM/Quantum-Software-Developer) |  |

## Saved (not yet applied)

| Saved | Company | Role | Raw | Location | Apply | Simplify | Status | Comment |
|---|---|---|---|---|---|---|---|---|
| 2026-07-28 | Kepler Communications | Embedded Software Designer | Embedded Software Designer | Toronto | [Apply](https://jobs.lever.co/kepler/abc123def456789012345) |  |  |  |
| 2026-07-28 | Pending Reject Co | Some Role | Some Role | Toronto | [Apply](https://jobs.lever.co/pending/bbb123def456789012345) |  | rejected | not-interested — nope |

## Rejected (migrated from shortlist `[nope]` rows)

| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |
|---|---|---|---|---|---|---|---|
| 2026-07-15 | Trexo Robotics | Junior Robotics Software Developer | Junior Robotics Software Developer | Toronto | [Apply](https://builtin.com/job/junior-robotics/9118662) | listing-removed | listing removed |
| 2026-07-15 | Boring Corp | Backend Dev | Backend Dev | Toronto | [Apply](https://boring.test/job/1) | not-interested | not appealing |
| 2026-07-15 | Sketch Inc | Dev | Dev | Toronto | [Apply](https://sketch.test/job/2) | sketchy-site | untrustworthy |
| 2026-07-15 | Old Corp | Stale Role | Stale Role | Toronto | [Apply](https://old.test/job/3) | too-old | 60d old |
| 2026-07-15 | Huh Corp | Mystery | Mystery | Toronto | [Apply](https://huh.test/job/4) | unknown |  |
"""

SHORTLIST = """# Shortlist

## Tier 1 — Best fit

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-07-15 | DoorDash | Software Engineer, Backend | Toronto | [Apply](https://www.linkedin.com/jobs/view/4438904097/) | ✅ |  |

## Excluded (and why)
- **Out of region:** Tower/DRW (Montreal), Huawei Ottawa
- **Defunct/acquired:** Untether AI (bankrupt), Alphawave
"""


class IndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        (d / "applied.md").write_text(APPLIED, encoding="utf-8")
        (d / "shortlist.md").write_text(SHORTLIST, encoding="utf-8")
        cls.idx = DedupIndex.load(d / "applied.md", d / "shortlist.md")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_applied_role_is_skipped(self):
        v = self.idx.check("Tenstorrent", "Software Engineer, TT-Fabric")
        self.assertEqual(v.bucket, "applied")
        self.assertTrue(v.skip)

    def test_url_match_wins_even_when_the_title_differs(self):
        # shorthand raw title, matched by ATS code
        v = self.idx.check("Xanadu", "totally different title",
                           "https://xanadu.applytojob.com/apply/confirm/0glmWNMBAM")
        self.assertEqual(v.bucket, "applied")
        self.assertEqual(v.matched_on, "url")

    def test_saved_role_is_skipped_as_in_motion(self):
        v = self.idx.check("Kepler Communications", "Embedded Software Designer")
        self.assertEqual(v.bucket, "saved")
        self.assertTrue(v.skip)

    def test_shortlisted_role_is_skipped(self):
        v = self.idx.check("DoorDash", "Software Engineer, Backend")
        self.assertEqual(v.bucket, "shortlisted")
        self.assertTrue(v.skip)

    def test_judgment_rejection_blacklists_the_role(self):
        for company, role in [("Boring Corp", "Backend Dev"), ("Sketch Inc", "Dev")]:
            v = self.idx.check(company, role)
            self.assertEqual(v.bucket, "rejected-judgment", company)
            self.assertTrue(v.skip, company)

    def test_liveness_rejection_is_resuggestable(self):
        # a dead link is not a verdict on the role — playbook §7.5
        v = self.idx.check("Trexo Robotics", "Junior Robotics Software Developer")
        self.assertEqual(v.bucket, "rejected-liveness")
        self.assertFalse(v.skip)
        self.assertEqual(v.reason, "listing-removed")

    def test_too_old_is_resuggestable(self):
        v = self.idx.check("Old Corp", "Stale Role")
        self.assertEqual(v.bucket, "rejected-liveness")
        self.assertFalse(v.skip)

    def test_unclear_rejection_is_not_auto_skipped(self):
        v = self.idx.check("Huh Corp", "Mystery")
        self.assertEqual(v.bucket, "rejected-unclear")
        self.assertFalse(v.skip)

    def test_brand_new_role_is_new(self):
        v = self.idx.check("Fresh Startup", "Embedded Engineer")
        self.assertEqual(v.bucket, "new")
        self.assertFalse(v.skip)

    def test_excluded_prose_company_is_a_hint_not_a_skip(self):
        # A company named in a freeform Excluded bullet must NOT be blacklisted: those
        # bullets record conditional cuts, so another role there can still be a keeper.
        v = self.idx.check("Untether AI", "Some Role")
        self.assertEqual(v.bucket, "new")
        self.assertFalse(v.skip)
        self.assertIn("Excluded bullet", v.detail)

    def test_structured_excluded_role_bullet_does_skip(self):
        idx = DedupIndex()
        idx.excluded_text = ["**Acme — Senior Backend Engineer** (2026-07-31): senior level"]
        idx._index_excluded_roles()
        v = idx.check("Acme", "Senior Backend Engineer")
        self.assertEqual(v.bucket, "excluded")
        self.assertTrue(v.skip)

    def test_structured_exclusion_does_not_bleed_to_other_roles(self):
        idx = DedupIndex()
        idx.excluded_text = ["**Acme — Senior Backend Engineer** (2026-07-31): senior level"]
        idx._index_excluded_roles()
        v = idx.check("Acme", "Junior Embedded Engineer")
        self.assertEqual(v.bucket, "new")
        self.assertFalse(v.skip)

    def test_excluded_match_respects_word_boundaries(self):
        # plain containment made "Ada" match the word "Canada"
        idx = DedupIndex()
        idx.excluded_text = ["**Out of region:** MDA Geospatial (Canada, QC)"]
        idx._index_excluded_roles()
        self.assertEqual(idx._excluded_hit("Ada"), "")
        self.assertIn("MDA", idx._excluded_hit("MDA"))

    def test_saved_row_blocks_re_suggestion(self):
        v = self.idx.check("Kepler Communications", "Embedded Software Designer")
        self.assertEqual(v.bucket, "saved")
        self.assertTrue(v.skip)

    def test_saved_row_pending_rejection_does_not_block(self):
        # its Status says `rejected`, so the next sync removes it from Simplify and files a
        # real reason. Letting it keep blocking would turn a rejection into a permanent bar,
        # which is the judgment-vs-liveness mistake in another form.
        v = self.idx.check("Pending Reject Co", "Some Role")
        self.assertEqual(v.bucket, "new")
        self.assertFalse(v.skip)

    def test_near_duplicate_is_reported_but_not_skipped(self):
        # distinct level at a company we already applied to
        v = self.idx.check("Tenstorrent", "Software Engineer, TT-Fabric II")
        self.assertEqual(v.bucket, "new")
        self.assertFalse(v.skip)
        self.assertIn("near-duplicate", v.detail)

    def test_applied_outranks_other_buckets_for_the_same_key(self):
        idx = DedupIndex()
        lines = APPLIED.split("\n")
        import md_tables as M
        rej = M.find_table(lines, "## Rejected")
        app = M.find_table(lines, "## Applied")
        # feed the rejection first, then the application, same key
        row = app.rows[0]
        idx._add("rejected-judgment", row, reason="not-interested")
        idx._add("applied", row)
        v = idx.check(row.get("company"), row.get("role"))
        self.assertEqual(v.bucket, "applied")

    def test_stats_counts_every_bucket(self):
        s = self.idx.stats()
        self.assertEqual(s["applied"], 2)
        self.assertEqual(s["saved"], 1)
        self.assertEqual(s["shortlisted"], 1)
        self.assertEqual(s["rejected-judgment"], 2)
        self.assertEqual(s["rejected-liveness"], 2)
        self.assertEqual(s["rejected-unclear"], 1)

    def test_case_and_whitespace_insensitive(self):
        v = self.idx.check("  tenstorrent ", "SOFTWARE ENGINEER,   TT-Fabric")
        self.assertEqual(v.bucket, "applied")


if __name__ == "__main__":
    unittest.main()
