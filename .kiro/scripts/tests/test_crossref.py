#!/usr/bin/env python3
"""Tests for crossref.py — applied.md -> shortlist.md reconciliation (§6)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import md_tables as M
import crossref as CR

SHORTLIST = """# Shortlist

## Tier 1 — Best fit

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-07-10 | Tenstorrent | Software Engineer, TT-Fabric | Toronto | [Apply](https://www.linkedin.com/jobs/view/4439917871/) | ✅ distributed |  |
| [ ] | 2026-07-10 | FreshCo | Untouched Role | Toronto | [Apply](https://fresh.test/job/1) | ✅ new |  |
| [nope] | 2026-07-01 | Align Technology | Jr. C++ Software Developer | Toronto | [Apply](https://www.linkedin.com/jobs/view/4428041861/) | ✅ C++ | listing-removed — gone (auto) |
| [ ] | 2026-07-05 | Xanadu | Systems Software Engineer | Toronto | [Apply](https://xanadu.applytojob.com/apply/confirm/LxwHAxMdlE) | ✅ |  |
| [x] | 2026-07-02 | DoneCo | Already Marked | Toronto | [Apply](https://done.test/job/1) | ✅ — **APPLIED 2026-07-02** |  |
| [ ] | 2026-07-06 | Kepler Communications | Embedded Software Designer | Toronto | [Apply](https://jobs.lever.co/kepler/abc123def456789012) | ✅ space |  |
| [ ] | 2026-07-07 | LSEG | c++ dev | Toronto | [Apply](https://lseg.test/job/9) | ✅ |  |
"""

APPLIED = """# Applied

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-15 | Tenstorrent | Software Engineer, TT-Fabric | Software Engineer, TT-Fabric | Toronto | [Apply](https://www.linkedin.com/jobs/view/4439917871/) |  |
| 2026-07-16 | Align Technology | Jr. C++ Software Developer | Jr. C++ Software Developer | Toronto | [Apply](https://www.linkedin.com/jobs/view/4428041861/) |  |
| 2026-06-22 | Xanadu | Systems Software Engineer | Systems Software Engineer | Toronto | [Apply](https://xanadu.applytojob.com/apply/LxwHAxMdlE/Systems-Software-Engineer) |  |
| 2026-06-14 | LSEG | c++ | c++ | Toronto | [Apply](https://lseg.wd3.myworkdayjobs.com/en-US/Careers/userHome) |  |

## Saved (not yet applied)

| Saved | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-28 | Kepler Communications | Embedded Software Designer | Embedded Software Designer | Toronto | [Apply](https://jobs.lever.co/kepler/abc123def456789012) |  |
"""


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.edits, self.unsure = CR.plan(SHORTLIST.split("\n"), APPLIED.split("\n"))
        self.by_co = {e["company"]: e for e in self.edits}

    def test_exact_match_marks_applied(self):
        self.assertIn("Tenstorrent", self.by_co)
        self.assertEqual(self.by_co["Tenstorrent"]["applied_date"], "2026-07-15")

    def test_unrelated_row_is_untouched(self):
        self.assertNotIn("FreshCo", self.by_co)

    def test_applied_wins_over_nope(self):
        e = self.by_co["Align Technology"]
        self.assertTrue(e["was_rejected"])
        self.assertTrue(e["clear_comment"])
        self.assertEqual(e["old_comment"], "listing-removed — gone (auto)")

    def test_ats_code_match_across_confirm_and_listing_paths(self):
        e = self.by_co["Xanadu"]
        self.assertEqual(e["matched_on"], "url")

    def test_already_marked_row_is_skipped(self):
        self.assertNotIn("DoneCo", self.by_co)

    def test_saved_section_does_not_count_as_applied(self):
        self.assertNotIn("Kepler Communications", self.by_co)

    def test_shorthand_title_is_reported_not_auto_marked(self):
        # applied.md has LSEG "c++"; shortlist has "c++ dev" -> containment, needs confirming
        self.assertNotIn("LSEG", self.by_co)
        unsure_co = {u["company"] for u in self.unsure}
        self.assertIn("LSEG", unsure_co)

    def test_notes_gain_the_applied_stamp(self):
        self.assertIn("**APPLIED 2026-07-15**", self.by_co["Tenstorrent"]["notes"])
        self.assertIn("✅ distributed", self.by_co["Tenstorrent"]["notes"])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.lines = SHORTLIST.split("\n")
        edits, self.unsure = CR.plan(self.lines, APPLIED.split("\n"))
        self.lines = CR.apply_plan(self.lines, edits)
        self.text = "\n".join(self.lines)
        self.tier = M.find_tables(self.lines, r"Tier 1")[0]
        self.rows = {r.get("company"): r for r in self.tier.rows}

    def test_status_flipped_to_applied(self):
        self.assertEqual(self.rows["Tenstorrent"].get("status"), "[x]")

    def test_nope_row_flipped_and_comment_cleared(self):
        row = self.rows["Align Technology"]
        self.assertEqual(row.get("status"), "[x]")
        self.assertEqual(row.get("comment"), "")
        self.assertNotIn("gone (auto)", self.text)

    def test_untouched_row_keeps_open_status(self):
        self.assertEqual(self.rows["FreshCo"].get("status"), "[ ]")
        self.assertEqual(self.rows["Kepler Communications"].get("status"), "[ ]")

    def test_line_count_is_unchanged(self):
        self.assertEqual(len(self.lines), len(SHORTLIST.split("\n")))

    def test_no_duplicate_applied_stamp_on_rerun(self):
        edits2, _ = CR.plan(self.lines, APPLIED.split("\n"))
        self.assertEqual(edits2, [])
        self.assertEqual(self.text.count("**APPLIED 2026-07-15**"), 1)

    def test_shorthand_row_left_alone_for_confirmation(self):
        self.assertEqual(self.rows["LSEG"].get("status"), "[ ]")


class NoteHelperTests(unittest.TestCase):
    def test_appends_to_existing_notes(self):
        self.assertEqual(CR.add_applied_note("✅ good", "2026-07-15"),
                         "✅ good — **APPLIED 2026-07-15**")

    def test_replaces_an_existing_stamp(self):
        out = CR.add_applied_note("✅ — **APPLIED 2026-01-01**", "2026-07-15")
        self.assertEqual(out.count("APPLIED"), 1)
        self.assertIn("2026-07-15", out)

    def test_empty_notes(self):
        self.assertEqual(CR.add_applied_note("", "2026-07-15"), "— **APPLIED 2026-07-15**")


if __name__ == "__main__":
    unittest.main()
