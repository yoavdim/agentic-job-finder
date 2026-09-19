"""Tests for the watchlist scripts (selector picks, scrape rows, flush).
No browser: the tab-driving paths are mocked; the md-tables surgery is real."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import md_tables as M
import watchlist_scrape as WS
import watchlist_selectors as SEL
import watchlist_flush as WF

WATCHLIST = """# Watchlist

## Companies

| Added | Company | URL | CSS Selector | Referee |
|---|---|---|---|---|
| 2026-08-03 | Autodesk | https://autodesk.wd1.myworkdayjobs.com/Ext?job=1 | a[data-automation-id='jobTitle'] | yes |
| 2026-08-04 | Kepler | https://jobs.lever.co/kepler?location=Toronto |  | no |

## Scraped (watchlist)

| Added | URL | Status |
|---|---|---|
"""

APPLIED = """# Applied

## Scraped-flushed (watchlist)

| Rejected | Company | Role | URL | Reason | Comment |
|---|---|---|---|---|---|
| 2026-08-05 | Autodesk | Old Role | [Old](https://autodesk.wd1.myworkdayjobs.com/Ext?job=0) | too-old | was listed pre-flush |
"""

MANUAL = """# Manual Entries

## Entries

| Added | URL | Status |
|---|---|---|
"""


class ReadCompaniesTests(unittest.TestCase):
    def test_reads_only_companies_with_company_and_url(self):
        out = WS.read_companies(WATCHLIST.splitlines())
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["company"], "Autodesk")
        self.assertEqual(out[0]["selector"], "a[data-automation-id='jobTitle']")
        self.assertEqual(out[1]["selector"], "")

    def test_missing_companies_table_is_empty(self):
        self.assertEqual(WS.read_companies(["# x", "", "| a |"]), [])


class RecordedKeysTests(unittest.TestCase):
    def test_tracks_scraped_flushed_applied_and_manual_rows(self):
        keys = WS.recorded_keys(WATCHLIST.splitlines(), APPLIED.splitlines(),
                                MANUAL.splitlines())
        self.assertIn(M.ats_code("https://autodesk.wd1.myworkdayjobs.com/Ext?job=0"), keys)

    def test_tracks_own_scraped_rows(self):
        wl = WATCHLIST.splitlines() + [
            "| 2026-08-05 | [New](https://autodesk.wd1.myworkdayjobs.com/Ext?job=9) |  |"]
        keys = WS.recorded_keys(wl, APPLIED.splitlines(), MANUAL.splitlines())
        self.assertIn(M.ats_code("https://autodesk.wd1.myworkdayjobs.com/Ext?job=9"), keys)


class ScrapeRowsTests(unittest.TestCase):
    def test_rows_are_manual_format_added_url_status(self):
        rows = WS.scrape_rows("2026-08-06",
                              [{"title": "Sr Role", "url": "https://x.test/job/1", "key": "k"}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].split("|")[1].strip(), "2026-08-06")
        self.assertIn("Sr Role", rows[0])
        self.assertTrue(rows[0].endswith("| |") or rows[0].endswith("|  |"))
        self.assertIn("https://x.test/job/1", rows[0])


class PlanCompanyTests(unittest.TestCase):
    def test_new_entries_resolve_relative_hrefs_and_dedup(self):
        res = {"count": 3, "items": [
            {"text": "A", "href": "/job/a/1"},
            {"text": "B", "href": "https://builtintoronto.com/job/b/2"},
            {"text": "dup", "href": "/job/a/1"},
        ]}
        ci = WS.ChromeInterface()
        with patch.object(ci, "open_loaded", return_value=1), \
             patch.object(ci, "scroll"), \
             patch.object(ci, "close_modals"), \
             patch.object(ci, "extract_elements", return_value=res), \
             patch.object(ci, "close") as cl:
            out = WS.plan_company("Built In", "https://builtintoronto.com/jobs",
                                  "a.card", "", recorded=set(), ci=ci)
        self.assertIsNone(out["error"])
        self.assertEqual(len(out["new"]), 2)
        self.assertEqual(out["new"][0]["url"],
                         "https://builtintoronto.com/job/a/1")
        self.assertEqual(out["new"][1]["url"],
                         "https://builtintoronto.com/job/b/2")
        cl.assert_called_once_with([1], expect_host="*")

    def test_error_surfaces_without_closing_twice(self):
        # scroll/close_modals are patched because plan_company drives them before
        # extracting; unpatched they reach the real Tab Share over HTTP.
        ci = WS.ChromeInterface()
        with patch.object(ci, "open_loaded", return_value=1), \
             patch.object(ci, "scroll"), \
             patch.object(ci, "close_modals"), \
             patch.object(ci, "extract_elements", return_value={"error": "bad selector"}), \
             patch.object(ci, "close") as cl:
            out = WS.plan_company("X", "https://x.test", "a[", "", recorded=set(), ci=ci)
        self.assertEqual(out["error"], "bad selector")
        cl.assert_called_once_with([1], expect_host="*")


class WriteSelectorTests(unittest.TestCase):
    def test_write_updates_the_css_selector_cell(self):
        lines = WATCHLIST.splitlines()
        new_lines, changed = SEL.write_selector(lines, "Kepler",
                                                "a[data-testid=job-link]")
        self.assertTrue(changed)
        row = next(r for r in M.find_table(new_lines, "## Companies").rows
                   if r.get("company") == "Kepler")
        self.assertEqual(row.get("selector"), "a[data-testid=job-link]")

    def test_write_is_noop_when_unchanged(self):
        lines = WATCHLIST.splitlines()
        new_lines, changed = SEL.write_selector(
            lines, "Autodesk", "a[data-automation-id='jobTitle']")
        self.assertFalse(changed)
        self.assertEqual(new_lines, lines)

    def test_write_raises_for_unknown_company(self):
        with self.assertRaises(KeyError):
            SEL.write_selector(WATCHLIST.splitlines(), "Nope Inc", "a.x")


class ProbeCommandTests(unittest.TestCase):
    def test_probe_delegates_to_chrome_interface(self):
        with patch.object(SEL.ChromeInterface, "probe", return_value={
                "ok": True, "count": 1, "samples": []}) as prb:
            out = SEL.probe("https://x.test", "a.card", wait=2)
        self.assertTrue(out["ok"])
        prb.assert_called_once()
        self.assertEqual(prb.call_args.args[0], "https://x.test")
        self.assertEqual(prb.call_args.args[1], "a.card")
        self.assertEqual(prb.call_args.kwargs["wait"], 2)


FLUSH_WATCHLIST = WATCHLIST + """| 2026-08-06 | [Sr Firmware Engineer](<https://autodesk.wd1.myworkdayjobs.com/Ext?job=2>) |  |
| 2026-08-07 | [Embedded SWE](<https://jobs.lever.co/kepler/abc123>) |  |
| 2026-08-07 | [Marked Applied](<https://autodesk.wd1.myworkdayjobs.com/Ext?job=3>) | applied |
"""

FLUSH_APPLIED = """# Applied

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-18 | Tenstorrent | Kernel Engineer | Kernel Engineer | Toronto | [Apply](https://job-boards.greenhouse.io/tenstorrent/jobs/900) |  |
"""


class FlushTests(unittest.TestCase):
    def test_splits_title_url_into_title_and_url(self):
        title, url = WF.split_title_url("[Sr Firmware Engineer](<https://x.test/job/1>)")
        self.assertEqual(title, "Sr Firmware Engineer")
        self.assertEqual(url, "https://x.test/job/1")

    def test_split_bare_url_keeps_none_title(self):
        title, url = WF.split_title_url("https://x.test/job/1")
        self.assertIsNone(title)
        self.assertEqual(url, "https://x.test/job/1")

    def test_company_matches_by_host(self):
        companies = [{"company": "Autodesk",
                      "url": "https://autodesk.wd1.myworkdayjobs.com/Ext?job=1"},
                     {"company": "Kepler",
                      "url": "https://jobs.lever.co/kepler?location=Toronto"}]
        self.assertEqual(
            WF.company_for("https://autodesk.wd1.myworkdayjobs.com/Ext?job=2", companies),
            "Autodesk")
        self.assertEqual(
            WF.company_for("https://jobs.lever.co/kepler/abc", companies), "Kepler")
        self.assertEqual(WF.company_for("https://builtintoronto.com/job/9", companies), "")

    def test_flush_plan_skips_marked_rows_and_tracks_flushed(self):
        to_flush, to_delete = WF.flush_rows(
            "2026-08-08", FLUSH_WATCHLIST.splitlines(),
            FLUSH_APPLIED.splitlines(), [])
        # the "applied" status row is left alone; the two unmarked rows flush
        self.assertEqual(len(to_flush), 2)
        self.assertEqual(to_flush[0]["company"], "Autodesk")
        self.assertEqual(to_flush[0]["role"], "Sr Firmware Engineer")
        self.assertEqual(to_flush[0]["reason"], "too-old")
        self.assertIn("2026-08-06", to_flush[0]["comment"])
        self.assertEqual(to_flush[1]["company"], "Kepler")
        self.assertEqual(to_flush[1]["role"], "Embedded SWE")
        # flushed rows MOVE out of the scraped table (the "applied" row stays)
        self.assertEqual(to_delete, [f["line_idx"] for f in to_flush])

    def test_flush_is_idempotent_against_its_own_table(self):
        applied = (FLUSH_APPLIED
                   + "\n## Scraped-flushed (watchlist)\n\n"
                   + "| Rejected | Company | Role | URL | Reason | Comment |\n"
                   + "|---|---|---|---|---|---|\n"
                   + "| 2026-08-08 | Autodesk | Sr Firmware Engineer "
                   + "| [Sr Firmware Engineer](<https://autodesk.wd1.myworkdayjobs.com/Ext?job=2>) "
                   + "| too-old | watchlist scrape 2026-08-06 (auto) |\n")
        to_flush, to_delete = WF.flush_rows(
            "2026-08-09", FLUSH_WATCHLIST.splitlines(), applied.splitlines(), [])
        # the already-flushed row is not re-flushed but IS cleaned from the scraped
        # table; the other unmarked row flushes (and moves)
        self.assertEqual(len(to_flush), 1)
        self.assertEqual(to_flush[0]["role"], "Embedded SWE")
        self.assertEqual(len(to_delete), 2)

    def test_render_flush_builds_the_row(self):
        row = WF.render_flush({"rejected": "2026-08-08", "company": "Autodesk",
                               "role": "Sr Role",
                               "url": "https://autodesk.wd1.myworkdayjobs.com/Ext?job=2",
                               "reason": "too-old", "comment": "watchlist scrape 2026-08-06 (auto)"})
        self.assertTrue(row.startswith("| 2026-08-08 | Autodesk | Sr Role |"))
        self.assertIn("[Sr Role](<https://autodesk.wd1.myworkdayjobs.com/Ext?job=2>)", row)
        self.assertTrue(row.endswith("| too-old | watchlist scrape 2026-08-06 (auto) |"))

    def test_apply_flush_moves_rows_and_creates_table(self):
        to_flush, to_delete = WF.flush_rows(
            "2026-08-08", FLUSH_WATCHLIST.splitlines(), FLUSH_APPLIED.splitlines(), [])
        new_wl, new_app = WF.apply_flush("2026-08-08", FLUSH_WATCHLIST.splitlines(),
                                         FLUSH_APPLIED.splitlines(), to_flush, to_delete)
        # scraped table still holds the "applied" row, unmarked rows are gone
        scraped = M.find_table(new_wl, "## Scraped (watchlist)")
        self.assertEqual([r.get("status") for r in scraped.rows], ["applied"])
        # flushed table exists in applied.md with both rows
        flushed = M.find_table(new_app, "## Scraped-flushed (watchlist)")
        self.assertEqual(len(flushed.rows), 2)
        self.assertEqual({r.get("company") for r in flushed.rows},
                         {"Autodesk", "Kepler"})
        # a real flush stamps the last-flush date under the scraped heading
        self.assertIn("**Last flush:** 2026-08-08", new_wl)

    def test_apply_flush_noop_leaves_stamp_untouched(self):
        new_wl, new_app = WF.apply_flush("2026-08-08", FLUSH_WATCHLIST.splitlines(),
                                         FLUSH_APPLIED.splitlines(), [], [])
        self.assertEqual(new_wl, FLUSH_WATCHLIST.splitlines())
        self.assertEqual(new_app, FLUSH_APPLIED.splitlines())


class LastFlushStampTests(unittest.TestCase):
    def test_ensure_creates_never_stamp_for_stamped_section(self):
        out = WF.ensure_last_flush(WATCHLIST.splitlines())
        self.assertIn("**Last flush:** never", out)
        # stamp sits between the scraped heading and its table
        heading = out.index("## Scraped (watchlist)")
        self.assertEqual(out[heading + 1], "")
        self.assertEqual(out[heading + 2], "**Last flush:** never")

    def test_ensure_preserves_an_existing_stamp(self):
        lines = WATCHLIST.splitlines() + ["| 2026-08-06 | [x](<https://x.test/job/1>) |  |"]
        lines = WF.ensure_last_flush(lines)
        out = WF.ensure_last_flush(lines)
        self.assertIn("**Last flush:** never", out)

    def test_set_overwrites_stamp(self):
        lines = WATCHLIST.splitlines() + ["| 2026-08-06 | [x](<https://x.test/job/1>) |  |"]
        lines = WF.ensure_last_flush(lines)
        out = WF.set_last_flush(lines, "2026-08-08")
        self.assertIn("**Last flush:** 2026-08-08", out)
        self.assertNotIn("**Last flush:** never", out)

    def test_helpers_noop_when_section_absent(self):
        self.assertEqual(WF.ensure_last_flush(["# x", ""]),
                         ["# x", ""])
        self.assertEqual(WF.set_last_flush(["# x", ""], "2026-08-08"),
                         ["# x", ""])

    def test_stamp_never_breaks_table_parsing(self):
        lines = WF.set_last_flush(
            WATCHLIST.splitlines() + ["| 2026-08-06 | [x](<https://x.test/job/1>) |  |"],
            "2026-08-08")
        t = M.find_table(lines, "## Scraped (watchlist)")
        self.assertEqual(len(t.rows), 1)
        self.assertEqual(t.rows[0].get("apply"), "[x](<https://x.test/job/1>)")

# Watchlist with scraped rows for removal tests.
# Autodesk has a selector (was scraped); Kepler has no selector (not scraped).
REMOVAL_WATCHLIST = """# Watchlist

## Companies

| Added | Company | URL | CSS Selector | Referee |
|---|---|---|---|---|
| 2026-08-03 | Autodesk | https://job-boards.greenhouse.io/autodesk | a.job-title | yes |
| 2026-08-04 | Kepler | https://jobs.lever.co/kepler?location=Toronto |  | no |

## Scraped (watchlist)

| Added | URL | Status |
|---|---|---|
| 2026-08-06 | [Still There](<https://job-boards.greenhouse.io/autodesk/jobs/10>) |  |
| 2026-08-06 | [Gone Role](<https://job-boards.greenhouse.io/autodesk/jobs/20>) |  |
| 2026-08-06 | [Marked Applied](<https://job-boards.greenhouse.io/autodesk/jobs/30>) | applied |
| 2026-08-06 | [Kepler Role](<https://jobs.lever.co/kepler/abc123>) |  |
"""


class FindRemovedTests(unittest.TestCase):
    def _autodesk_result(self, live_job_ids):
        """Fake scrape result for Autodesk with the given job ids on the live page."""
        live_keys = set()
        for jid in live_job_ids:
            live_keys.add(M.ats_code(
                f"https://job-boards.greenhouse.io/autodesk/jobs/{jid}"))
        return {"company": "Autodesk", "error": None, "new": [],
                "seen": len(live_job_ids), "live_keys": live_keys}

    def test_removed_listing_is_flushed(self):
        # job=10 is still live, job=20 is gone
        results = [self._autodesk_result([10])]
        removed, to_delete = WS.find_removed(
            REMOVAL_WATCHLIST.splitlines(), results, set())
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["role"], "Gone Role")
        self.assertEqual(removed[0]["reason"], "listing-removed")
        self.assertEqual(removed[0]["company"], "Autodesk")
        self.assertIn("listing removed (auto)", removed[0]["comment"])
        self.assertEqual(len(to_delete), 1)

    def test_errored_company_rows_are_left_alone(self):
        results = [{"company": "Autodesk", "error": "timeout",
                     "new": [], "seen": 0}]
        removed, to_delete = WS.find_removed(
            REMOVAL_WATCHLIST.splitlines(), results, set())
        self.assertEqual(removed, [])
        self.assertEqual(to_delete, [])

    def test_marked_rows_are_left_alone(self):
        # job=30 is gone from live but has Status "applied" → should not be removed
        results = [self._autodesk_result([10])]
        removed, _ = WS.find_removed(
            REMOVAL_WATCHLIST.splitlines(), results, set())
        roles = [r["role"] for r in removed]
        self.assertNotIn("Marked Applied", roles)

    def test_unscraped_company_rows_left_alone(self):
        # Kepler has no selector, so no result for it → its rows are untouched
        results = [self._autodesk_result([10])]
        removed, _ = WS.find_removed(
            REMOVAL_WATCHLIST.splitlines(), results, set())
        roles = [r["role"] for r in removed]
        self.assertNotIn("Kepler Role", roles)

    def test_all_live_means_nothing_removed(self):
        results = [self._autodesk_result([10, 20, 30])]
        removed, to_delete = WS.find_removed(
            REMOVAL_WATCHLIST.splitlines(), results, set())
        self.assertEqual(removed, [])
        self.assertEqual(to_delete, [])


if __name__ == "__main__":
    unittest.main()
