#!/usr/bin/env python3
"""Tests for jobdates.py — posting-date normalization."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import jobdates as JD

TODAY = "2026-07-29"


class ParseTests(unittest.TestCase):
    def test_iso_passes_through(self):
        self.assertEqual(JD.parse_posting_date("2026-07-15", TODAY), "2026-07-15")

    def test_iso_inside_a_sentence(self):
        self.assertEqual(JD.parse_posting_date("Posted on 2026-07-15 by HR", TODAY),
                         "2026-07-15")

    def test_relative_days(self):
        self.assertEqual(JD.parse_posting_date("3 days ago", TODAY), "2026-07-26")

    def test_relative_hours_is_today(self):
        self.assertEqual(JD.parse_posting_date("5 hours ago", TODAY), TODAY)

    def test_relative_weeks(self):
        self.assertEqual(JD.parse_posting_date("2 weeks ago", TODAY), "2026-07-15")

    def test_relative_months(self):
        self.assertEqual(JD.parse_posting_date("1 month ago", TODAY), "2026-06-29")

    def test_word_relatives(self):
        self.assertEqual(JD.parse_posting_date("yesterday", TODAY), "2026-07-28")
        self.assertEqual(JD.parse_posting_date("Just posted", TODAY), TODAY)
        self.assertEqual(JD.parse_posting_date("last week", TODAY), "2026-07-22")

    def test_plus_days_ceiling(self):
        self.assertEqual(JD.parse_posting_date("30+ days ago", TODAY), "2026-06-29")

    def test_month_name_with_year(self):
        self.assertEqual(JD.parse_posting_date("July 20, 2026", TODAY), "2026-07-20")
        self.assertEqual(JD.parse_posting_date("Jul 20 2026", TODAY), "2026-07-20")

    def test_month_name_without_year_assumes_this_year(self):
        self.assertEqual(JD.parse_posting_date("July 20", TODAY), "2026-07-20")

    def test_month_name_without_year_in_the_future_rolls_back(self):
        # December with no year, from a July date, must mean last December
        self.assertEqual(JD.parse_posting_date("December 20", TODAY), "2025-12-20")

    def test_us_slash_format(self):
        self.assertEqual(JD.parse_posting_date("7/20/26", TODAY), "2026-07-20")

    def test_year_month_only_becomes_first_of_month(self):
        self.assertEqual(JD.parse_posting_date("2026-07", TODAY), "2026-07-01")

    def test_unparseable_returns_empty_never_a_guess(self):
        self.assertEqual(JD.parse_posting_date("sometime recently", TODAY), "")
        self.assertEqual(JD.parse_posting_date("", TODAY), "")
        self.assertEqual(JD.parse_posting_date(None, TODAY), "")

    def test_impossible_date_returns_empty(self):
        self.assertEqual(JD.parse_posting_date("February 30, 2026", TODAY), "")


class NoteTests(unittest.TestCase):
    def test_builds_the_note(self):
        self.assertEqual(JD.posted_note("2026-07-15"), "📅 posted 2026-07-15")
        self.assertEqual(JD.posted_note(""), "")

    def test_appends_to_existing_notes(self):
        self.assertEqual(JD.add_posted_note("✅ embedded", "2026-07-15"),
                         "✅ embedded · 📅 posted 2026-07-15")

    def test_replaces_an_existing_posted_note(self):
        out = JD.add_posted_note("✅ embedded · 📅 posted 2026-01-01", "2026-07-15")
        self.assertEqual(out, "✅ embedded · 📅 posted 2026-07-15")
        self.assertNotIn("2026-01-01", out)

    def test_replaces_a_reposted_note(self):
        out = JD.add_posted_note("✅ · 📅 reposted 2026-07", "2026-07-15")
        self.assertIn("📅 posted 2026-07-15", out)

    def test_empty_notes_gets_just_the_note(self):
        self.assertEqual(JD.add_posted_note("", "2026-07-15"), "📅 posted 2026-07-15")

    def test_no_date_leaves_notes_alone(self):
        self.assertEqual(JD.add_posted_note("✅ embedded", ""), "✅ embedded")


class AgeTests(unittest.TestCase):
    def test_age_in_days(self):
        self.assertEqual(JD.age_days("2026-07-15", TODAY), 14)

    def test_unparseable_age_is_none(self):
        self.assertIsNone(JD.age_days("", TODAY))
        self.assertIsNone(JD.age_days("nonsense", TODAY))


if __name__ == "__main__":
    unittest.main()
