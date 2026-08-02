#!/usr/bin/env python3
"""Tests for liveness_sweep.py — stage 0c sweep (playbook §8). No network: probes mocked."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import md_tables as M
import liveness_sweep as LS


SHORTLIST = """# Job Shortlist

## Tier 1 — Best fit

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-07-15 | LiveCo | Embedded Dev | Toronto | [Apply](https://job-boards.greenhouse.io/liveco/jobs/1) | ✅ · 📅 posted 2026-07-15 |  |
| [ ] | 2026-07-15 | DeadCo | Kernel Dev | Toronto | [Apply](https://job-boards.greenhouse.io/deadco/jobs/2) | ✅ · 📅 posted 2026-07-20 |  |
| [ ] | 2026-01-05 | StaleCo | Old Role | Toronto | [Apply](https://job-boards.greenhouse.io/staleco/jobs/3) | ✅ · 📅 posted 2026-01-05 |  |
| [ ] | 2026-07-20 | AggCo | Builtin Role | Toronto | [Apply](https://builtintoronto.com/job/builtin-role/999) | ✅ |  |
| [x] | 2026-07-01 | AppliedCo | Done | Toronto | [Apply](https://x.test/a) | ✅ |  |
| [nope] | 2026-07-01 | RejectedCo | Nope | Toronto | [Apply](https://x.test/b) | ✅ |  |
"""

APPLIED = """# Applied

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|

## Rejected (migrated from shortlist `[nope]` rows)

| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |
|---|---|---|---|---|---|---|---|
"""

TODAY = "2026-07-29"


class HostClassificationTests(unittest.TestCase):
    def test_aggregators_are_flagged_for_rendering(self):
        for u in ("https://builtintoronto.com/job/x/1",
                  "https://www.linkedin.com/jobs/view/123/",
                  "https://www.ycombinator.com/companies/x/jobs/y",
                  "https://ca.indeed.com/viewjob?jk=1"):
            self.assertEqual(LS.classify_host(u), "aggregator", u)

    def test_real_ats_hosts_are_trusted(self):
        for u in ("https://job-boards.greenhouse.io/acme/jobs/1",
                  "https://xanadu.applytojob.com/apply/ABC/Role",
                  "https://jobs.ashbyhq.com/org/uuid",
                  "https://huaweicanada.recruitee.com/o/slug"):
            self.assertEqual(LS.classify_host(u), "ats", u)

    def test_company_site_is_unknown(self):
        self.assertEqual(LS.classify_host("https://taalas.com/position/x/"), "unknown")


class AgeTests(unittest.TestCase):
    def test_posted_note_is_preferred_over_added(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        row = t.rows[2]   # StaleCo
        d, src = LS.row_age_date(row)
        self.assertEqual(d, "2026-01-05")
        self.assertEqual(src, "posted")

    def test_falls_back_to_added_when_no_posted_note(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        row = t.rows[3]   # AggCo, no 📅
        d, src = LS.row_age_date(row)
        self.assertEqual(d, "2026-07-20")
        self.assertEqual(src, "added")

    def test_too_old_beyond_cutoff(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        old, date, age, _src = LS.is_too_old(t.rows[2], TODAY, 31)
        self.assertTrue(old)
        self.assertGreater(age, 31)

    def test_recent_row_is_not_too_old(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        old, _, _, _ = LS.is_too_old(t.rows[0], TODAY, 31)
        self.assertFalse(old)

    def test_undated_row_is_never_penalized(self):
        lines = ["|  | Added | Company | Role | Location | Apply link | Notes | Comment |",
                 "|---|---|---|---|---|---|---|---|",
                 "| [ ] |  | NoDate | Role | Toronto | [Apply](https://x.test/1) | ✅ |  |"]
        lines = ["## Tier 1 — x", ""] + lines
        t = M.find_tables(lines, r"Tier 1")[0]
        old, _, _, _ = LS.is_too_old(t.rows[0], TODAY, 31)
        self.assertFalse(old)


class MarkerTests(unittest.TestCase):
    def test_detects_builtin_phrasing(self):
        self.assertEqual(
            LS.find_marker("Sorry, this job was removed at 4:02 PM on July 20, 2026"),
            "this job was removed")

    def test_detects_linkedin_phrasing(self):
        self.assertIsNotNone(LS.find_marker("No longer accepting applications"))

    def test_live_page_has_no_marker(self):
        self.assertIsNone(LS.find_marker("Apply now for this exciting embedded role"))


class CheckUrlTests(unittest.TestCase):
    def test_missing_url_is_link_broken(self):
        r = LS.check_url("", use_browser=False)
        self.assertEqual(r["verdict"], "dead")
        self.assertEqual(r["reason"], "link-broken")

    @patch.object(LS, "http_probe", return_value={"status": 404, "final_url": "u", "error": None})
    def test_ats_404_is_link_broken(self, _p):
        r = LS.check_url("https://job-boards.greenhouse.io/a/jobs/1", use_browser=False)
        self.assertEqual(r["verdict"], "dead")
        self.assertEqual(r["reason"], "link-broken")

    @patch.object(LS, "http_probe", return_value={"status": 403, "final_url": "u", "error": None})
    def test_403_is_not_treated_as_dead(self, _p):
        r = LS.check_url("https://job-boards.greenhouse.io/a/jobs/1", use_browser=False)
        self.assertEqual(r["verdict"], "alive")

    @patch.object(LS, "http_probe", return_value={"status": 503, "final_url": "u", "error": None})
    def test_5xx_is_unknown_not_dead(self, _p):
        r = LS.check_url("https://job-boards.greenhouse.io/a/jobs/1", use_browser=False)
        self.assertEqual(r["verdict"], "unknown")

    @patch.object(LS, "http_probe", return_value={"status": None, "final_url": "u",
                                                  "error": "Name or service not known"})
    def test_dns_failure_is_link_broken(self, _p):
        r = LS.check_url("https://gone.test/job/1", use_browser=False)
        self.assertEqual(r["verdict"], "dead")
        self.assertEqual(r["reason"], "link-broken")

    @patch.object(LS, "http_probe")
    def test_redirect_to_careers_home_is_link_broken(self, probe):
        probe.return_value = {"status": 200,
                              "final_url": "https://acme.test/careers",
                              "error": None}
        r = LS.check_url("https://acme.test/job/12345", use_browser=False)
        self.assertEqual(r["verdict"], "dead")
        self.assertEqual(r["reason"], "link-broken")

    def test_aggregator_without_browser_is_unknown_never_guessed(self):
        r = LS.check_url("https://builtintoronto.com/job/x/1", use_browser=False)
        self.assertEqual(r["verdict"], "unknown")
        self.assertIn("render check", r["detail"])

    @patch.object(LS, "render_text",
                  return_value=("Sorry, this job was removed at 4:02 PM on July 20, 2026", None))
    def test_aggregator_removal_marker_is_listing_removed(self, _r):
        r = LS.check_url("https://builtintoronto.com/job/x/1", use_browser=True)
        self.assertEqual(r["verdict"], "dead")
        self.assertEqual(r["reason"], "listing-removed")

    @patch.object(LS, "render_text", return_value=("x" * 500, None))
    def test_aggregator_without_marker_is_alive(self, _r):
        r = LS.check_url("https://builtintoronto.com/job/x/1", use_browser=True)
        self.assertEqual(r["verdict"], "alive")

    @patch.object(LS, "render_text", return_value=("", None))
    def test_empty_render_is_unknown_not_dead(self, _r):
        r = LS.check_url("https://builtintoronto.com/job/x/1", use_browser=True)
        self.assertEqual(r["verdict"], "unknown")

    @patch.object(LS, "http_probe", return_value={"status": 200, "final_url": "u", "error": None})
    @patch.object(LS, "render_text", return_value=("This position is no longer open", None))
    def test_ats_200_but_rendered_removal_marker_is_caught(self, _r, _p):
        r = LS.check_url("https://job-boards.greenhouse.io/a/jobs/1", use_browser=True)
        self.assertEqual(r["verdict"], "dead")
        self.assertEqual(r["reason"], "listing-removed")


class SweepTests(unittest.TestCase):
    def _fake_check(self, url, use_browser):
        if "deadco" in url:
            return {"verdict": "dead", "reason": "link-broken", "detail": "HTTP 404"}
        return {"verdict": "alive", "reason": None, "detail": "HTTP 200"}

    def test_only_open_rows_are_checked(self):
        with patch.object(LS, "check_url", side_effect=self._fake_check):
            results, _anom = LS.sweep(SHORTLIST.split("\n"), TODAY, 31, use_browser=False)
        companies = {r["company"] for r in results}
        self.assertNotIn("AppliedCo", companies)    # [x] is stage 0b's job
        self.assertNotIn("RejectedCo", companies)   # [nope] likewise
        self.assertIn("LiveCo", companies)

    def test_too_old_row_is_not_probed_at_all(self):
        with patch.object(LS, "check_url", side_effect=self._fake_check) as chk:
            results, _anom = LS.sweep(SHORTLIST.split("\n"), TODAY, 31, use_browser=False)
        probed = {c.args[0] for c in chk.call_args_list}
        self.assertFalse(any("staleco" in u for u in probed),
                         "age cut must short-circuit the network probe")
        stale = next(r for r in results if r["company"] == "StaleCo")
        self.assertEqual(stale["reason"], "too-old")

    def test_dead_row_is_reported_with_its_reason(self):
        with patch.object(LS, "check_url", side_effect=self._fake_check):
            results, _anom = LS.sweep(SHORTLIST.split("\n"), TODAY, 31, use_browser=False)
        dead = next(r for r in results if r["company"] == "DeadCo")
        self.assertEqual(dead["verdict"], "dead")
        self.assertEqual(dead["reason"], "link-broken")


class MigrationHandoffTests(unittest.TestCase):
    def _results(self):
        return [
            {"tier": "Tier 1", "line_idx": 8, "company": "DeadCo", "role": "Kernel Dev",
             "location": "Toronto", "apply": "[Apply](https://x.test/2)",
             "url": "https://x.test/2", "verdict": "dead", "reason": "link-broken",
             "detail": "HTTP 404"},
            {"tier": "Tier 1", "line_idx": 7, "company": "LiveCo", "role": "Embedded Dev",
             "location": "Toronto", "apply": "[Apply](https://x.test/1)",
             "url": "https://x.test/1", "verdict": "alive", "reason": None,
             "detail": "HTTP 200"},
            {"tier": "Tier 1", "line_idx": 9, "company": "HuhCo", "role": "Mystery",
             "location": "Toronto", "apply": "[Apply](https://x.test/3)",
             "url": "https://x.test/3", "verdict": "unknown", "reason": None,
             "detail": "render failed"},
        ]

    def test_only_dead_rows_enter_the_plan(self):
        plan = LS.to_migration_plan(self._results(), TODAY)
        self.assertEqual(len(plan["rejected_new"]), 1)
        self.assertEqual(plan["rejected_new"][0]["company"], "DeadCo")
        self.assertEqual(plan["delete"], [8])

    def test_auto_suffix_marks_machine_rejections(self):
        plan = LS.to_migration_plan(self._results(), TODAY)
        self.assertTrue(plan["rejected_new"][0]["comment"].endswith("(auto)"))

    def test_unknown_rows_are_never_removed(self):
        plan = LS.to_migration_plan(self._results(), TODAY)
        self.assertNotIn(9, plan["delete"])

    def test_plan_is_consumable_by_migrate_resolved(self):
        import migrate_resolved as MR
        shortlist = SHORTLIST.split("\n")
        applied = APPLIED.split("\n")
        t = M.find_tables(shortlist, r"Tier 1")[0]
        deadco = next(r for r in t.rows if r.get("company") == "DeadCo")
        results = [{"tier": t.heading, "line_idx": deadco.line_idx,
                    "company": "DeadCo", "role": "Kernel Dev", "location": "Toronto",
                    "apply": deadco.get("apply"), "url": "x",
                    "verdict": "dead", "reason": "link-broken", "detail": "HTTP 404"}]
        plan = LS.to_migration_plan(results, TODAY)
        shortlist, applied, _m = MR.apply_plan(shortlist, applied, plan)
        self.assertNotIn("DeadCo", "\n".join(shortlist))
        rej = M.find_table(applied, "## Rejected")
        self.assertEqual(rej.rows[0].get("company"), "DeadCo")
        self.assertEqual(rej.rows[0].get("reason"), "link-broken")
        self.assertIn("(auto)", rej.rows[0].get("comment"))


if __name__ == "__main__":
    unittest.main()
