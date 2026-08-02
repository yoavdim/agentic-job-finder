#!/usr/bin/env python3
"""Tests for ensure_data_files.py — data-file skeleton creation."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import ensure_data_files as E


class MissingSkeletonsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.thoughts = root / "thoughts.md"
        self.manual = root / "manual.md"
        self.applied = root / "applied.md"

    def test_returns_all_three_when_missing(self):
        # date= is keyword-only in effect here: missing_skeletons' 4th positional is
        # shortlist_path, not date. Passing "2026-07-31" positionally once wrote a REAL
        # file by that name into the repo root on every test run (shortlist_path defaults
        # to None -> skipped; a positional string there is truthy -> included -> written by
        # test_writes_skeletons below, relative to whatever the process cwd happened to be).
        missing = E.missing_skeletons(self.thoughts, self.manual, self.applied,
                                      date="2026-07-31")
        self.assertEqual(set(missing), {self.thoughts, self.manual, self.applied})

    def test_writes_skeletons(self):
        missing = E.missing_skeletons(self.thoughts, self.manual, self.applied,
                                      date="2026-07-31")
        for p, text in missing.items():
            p.write_text(text, encoding="utf-8")
        self.assertTrue(self.thoughts.exists())
        self.assertTrue(self.manual.exists())
        self.assertTrue(self.applied.exists())

    def test_thoughts_skeleton_has_heading(self):
        self.assertIn("# Thoughts", E.THOUGHTS_SKELETON)

    def test_manual_skeleton_has_entries_table(self):
        self.assertIn("## Entries", E.MANUAL_SKELETON)
        self.assertIn("| Added | URL | Status |", E.MANUAL_SKELETON)
        self.assertIn("|---|---|---|", E.MANUAL_SKELETON)

    def test_applied_skeleton_has_heading(self):
        self.assertIn("# Applied / In-Motion Tracker — Yoav Dim", E.APPLIED_SKELETON)

    def test_applied_skeleton_has_sync_header_and_tables(self):
        text = E.APPLIED_SKELETON.format(date="2026-07-31")
        self.assertIn("**Last synced from Simplify:** 2026-07-31 · 0 applied · 0 saved (not yet applied)",
                      text)
        self.assertIn("## Applied", text)
        self.assertIn("| Applied | Company | Role | Raw | Location | Apply | Comment |", text)
        self.assertIn("## Saved (not yet applied)", text)
        self.assertIn("| Saved | Company | Role | Raw | Location | Apply | Simplify | Status | Comment |", text)
        self.assertIn("## Rejected", text)
        self.assertIn("| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |", text)

    def test_existing_files_are_left_untouched(self):
        self.applied.write_text("# Applied\n\n- keep me\n", encoding="utf-8")
        missing = E.missing_skeletons(self.thoughts, self.manual, self.applied,
                                      date="2026-07-31")
        self.assertNotIn(self.applied, missing)
        self.assertEqual(set(missing), {self.thoughts, self.manual})

    def test_only_missing_ones_are_returned(self):
        self.thoughts.write_text("# Thoughts\n", encoding="utf-8")
        self.applied.write_text("# Applied\n", encoding="utf-8")
        missing = E.missing_skeletons(self.thoughts, self.manual, self.applied,
                                      date="2026-07-31")
        self.assertEqual(set(missing), {self.manual})

    def test_applied_skeleton_uses_provided_date(self):
        text = E.APPLIED_SKELETON.format(date="2026-01-02")
        self.assertIn("**Last synced from Simplify:** 2026-01-02", text)

    def test_shortlist_path_none_by_default_means_no_shortlist_in_the_plan(self):
        # the exact regression: a positional 4th arg silently becomes shortlist_path, not
        # date, and — since it's a truthy string, not None — gets included in the plan and
        # written to a REAL relative path by whatever process cwd is active. Pin the
        # keyword-only-in-practice contract so that mistake can't reappear silently.
        missing = E.missing_skeletons(self.thoughts, self.manual, self.applied,
                                      date="2026-07-31")
        self.assertEqual(set(missing), {self.thoughts, self.manual, self.applied})
        self.assertFalse(any(p.name == "2026-07-31" for p in missing),
                         "a date string must never be mistaken for a shortlist path")


if __name__ == "__main__":
    unittest.main()
