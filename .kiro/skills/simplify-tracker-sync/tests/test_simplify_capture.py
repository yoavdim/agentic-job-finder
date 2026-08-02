#!/usr/bin/env python3
"""Tests for simplify_capture — the API-based tracker capture.

The capture calls the tracker's own list endpoint (paginated) via the same authenticated
Tab Share path the mutating actions use. There is no scraping and no network eavesdropping;
these tests drive it with a fake `fetch_page` so nothing touches the network.

The row->record mapping is the important surface: it derives saved/applied dates from
`status_events` (the same 1=SAVED, 2=APPLIED codes the rest of the skill uses) and carries
the application id through by construction.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("simplify_capture", SCRIPTS / "simplify_capture.py")
CAP = importlib.util.module_from_spec(_spec)
sys.modules["simplify_capture"] = CAP
_spec.loader.exec_module(CAP)


def row(id_, company, title, events, url="https://x.test/j", loc="Toronto",
        tracked="2026-07-20T10:00:00"):
    return {"id": id_, "company": {"name": company}, "job_posting_title": title,
            "job_posting_url": url, "job_posting_location": loc,
            "tracked_date": tracked, "status_events": events}


class RowToRecordTests(unittest.TestCase):
    def test_saved_only_row(self):
        rec = CAP.row_to_record(row("i1", "Acme", "Engineer",
                                    [{"status": 1, "timestamp": "2026-07-20T10:00:00"}]))
        self.assertEqual(rec["company"], "Acme")
        self.assertEqual(rec["title"], "Engineer")
        self.assertEqual(rec["saved"], "2026-07-20")
        self.assertEqual(rec["applied"], "")            # not yet applied
        self.assertEqual(rec["id"], "i1")

    def test_applied_row_has_both_dates(self):
        rec = CAP.row_to_record(row("i2", "Beta", "Dev", [
            {"status": 1, "timestamp": "2026-07-10T00:00:00"},
            {"status": 2, "timestamp": "2026-07-15T00:00:00"}]))
        self.assertEqual(rec["saved"], "2026-07-10")
        self.assertEqual(rec["applied"], "2026-07-15")

    def test_applied_date_survives_out_of_order_events(self):
        rec = CAP.row_to_record(row("i3", "C", "R", [
            {"status": 2, "timestamp": "2026-07-15T00:00:00"},
            {"status": 1, "timestamp": "2026-07-10T00:00:00"}]))
        self.assertEqual(rec["saved"], "2026-07-10")
        self.assertEqual(rec["applied"], "2026-07-15")

    def test_saved_date_falls_back_to_tracked_date(self):
        rec = CAP.row_to_record(row("i4", "D", "R", [], tracked="2026-06-01T09:00:00"))
        self.assertEqual(rec["saved"], "2026-06-01")

    def test_company_object_or_string(self):
        self.assertEqual(CAP.row_to_record(
            {"id": "x", "company": "Plain Co", "job_posting_title": "T",
             "status_events": []})["company"], "Plain Co")

    def test_multi_location_is_carried_verbatim(self):
        rec = CAP.row_to_record(row("i5", "E", "R", [], loc="Toronto | Vancouver"))
        self.assertEqual(rec["location"], "Toronto | Vancouver")


class PaginationTests(unittest.TestCase):
    def _fake(self, pages_data):
        """pages_data: list of (items, total, pages) per page index."""
        calls = []

        def fake_fetch(page, size, archived, tab_id):
            calls.append(page)
            if page >= len(pages_data):
                return {"items": [], "total": pages_data[-1][1],
                        "pages": pages_data[-1][2]}, None
            items, total, pages = pages_data[page]
            return {"items": items, "total": total, "pages": pages}, None

        return fake_fetch, calls

    def test_pages_until_total_reached(self):
        p0 = [row(f"a{i}", "Co", f"R{i}", []) for i in range(2)]
        p1 = [row(f"b{i}", "Co", f"S{i}", []) for i in range(2)]
        fake, calls = self._fake([(p0, 4, 2), (p1, 4, 2)])
        orig = CAP.fetch_page
        CAP.fetch_page = fake
        try:
            res = CAP.capture(tab_id=1, size=2)
        finally:
            CAP.fetch_page = orig
        self.assertEqual(res["rows"], 4)
        self.assertEqual(res["total"], 4)
        self.assertTrue(res["complete"])
        self.assertEqual(calls, [0, 1])

    def test_incomplete_capture_is_flagged(self):
        p0 = [row(f"a{i}", "Co", f"R{i}", []) for i in range(2)]
        fake, _calls = self._fake([(p0, 10, 5)])   # says 10 total, only 1 page returns
        orig = CAP.fetch_page
        CAP.fetch_page = fake
        try:
            res = CAP.capture(tab_id=1, size=2, max_pages=1)
        finally:
            CAP.fetch_page = orig
        self.assertFalse(res["complete"])

    def test_ids_present_for_every_row(self):
        p0 = [row(f"a{i}", "Co", f"R{i}", []) for i in range(3)]
        fake, _c = self._fake([(p0, 3, 1)])
        orig = CAP.fetch_page
        CAP.fetch_page = fake
        try:
            res = CAP.capture(tab_id=1)
        finally:
            CAP.fetch_page = orig
        self.assertEqual(res["ids"], 3)
        self.assertEqual(len(res["urls"]), 3)

    def test_duplicate_ids_across_pages_are_not_double_counted(self):
        dup = row("same", "Co", "R", [])
        fake, _c = self._fake([([dup], 1, 2), ([dup], 1, 2)])
        orig = CAP.fetch_page
        CAP.fetch_page = fake
        try:
            res = CAP.capture(tab_id=1, size=1)
        finally:
            CAP.fetch_page = orig
        self.assertEqual(res["rows"], 1)


class TrackingParamTests(unittest.TestCase):
    def test_ref_simplify_is_stripped(self):
        self.assertEqual(
            CAP.strip_tracking("https://x.test/j?ref=Simplify.jobs&a=1"),
            "https://x.test/j?a=1")

    def test_plain_url_untouched(self):
        self.assertEqual(CAP.strip_tracking("https://x.test/j"), "https://x.test/j")


if __name__ == "__main__":
    unittest.main()
