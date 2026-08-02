#!/usr/bin/env python3
"""Tests for shortlist_add.py and housekeeping.py — row writing + header dates."""
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import md_tables as M
import shortlist_add as SA
import housekeeping as HK

SHORTLIST = """# Job Shortlist

**Last searched the web:** 2026-07-22

## Tier 1 — Best fit (Toronto, junior)

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-07-15 | DoorDash | Software Engineer, Backend | Toronto | [Apply](https://www.linkedin.com/jobs/view/4438904097/) | ✅ backend |  |

## Tier 2 — General SWE

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|

## Excluded (and why)
- **Out of region:** Tower/DRW (Montreal)
- **Defunct/acquired:** Untether AI (bankrupt)

## Notes
Prose.
"""

APPLIED = """# Applied

**Last synced from Simplify:** 2026-07-15 · 1 applied · 1 saved (not yet applied)

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-15 | Tenstorrent | Software Engineer, TT-Fabric | Software Engineer, TT-Fabric | Toronto | [Apply](https://www.linkedin.com/jobs/view/4439917871/) |  |

## Saved (not yet applied)

| Saved | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-28 | Kepler | Embedded Designer | Embedded Designer | Toronto | [Apply](https://k.test/1) |  |

## Rejected (migrated from shortlist `[nope]` rows)

| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |
|---|---|---|---|---|---|---|---|
| 2026-07-15 | Boring Corp | Backend Dev | Backend Dev | Toronto | [Apply](https://b.test/1) | not-interested | meh |
| 2026-07-15 | Trexo Robotics | Robotics Dev | Robotics Dev | Toronto | [Apply](https://t.test/1) | listing-removed | gone |
"""

TODAY = "2026-07-29"


def cand(**kw):
    # `evidence` and a Notes legend marker are what candidate_lint requires: the row has to
    # record that the listing was actually read and classified (playbook §1f / §4).
    base = {"company": "NewCo", "role": "Embedded Engineer", "location": "Toronto",
            "url": "https://new.test/job/1", "tier": 1, "notes": "✅ embedded",
            "evidence": "Responsibilities: C/C++ firmware. Requirements: 2-4 yrs, BSc."}
    base.update(kw)
    return base


class PlanTests(unittest.TestCase):
    def _plan(self, cands, force=False):
        return SA.plan(SHORTLIST.split("\n"), APPLIED.split("\n"), cands, TODAY, force)

    def test_new_role_is_inserted_into_its_tier(self):
        ins, skipped = self._plan([cand()])
        self.assertEqual(len(ins), 1)
        self.assertEqual(ins[0]["tier"], "1")
        self.assertEqual(skipped, [])

    def test_row_columns_land_correctly(self):
        ins, _ = self._plan([cand()])
        cells = M.split_cells(ins[0]["row"])
        self.assertEqual(cells[0], "[ ]")            # status box
        self.assertEqual(cells[1], TODAY)            # Added
        self.assertEqual(cells[2], "NewCo")          # Company
        self.assertEqual(cells[3], "Embedded Engineer")
        self.assertEqual(cells[4], "Toronto")
        self.assertIn("[Apply](https://new.test/job/1)", cells[5])
        self.assertIn("✅ embedded", cells[6])

    def test_posting_date_is_normalized_into_the_notes(self):
        ins, _ = self._plan([cand(posted="3 days ago")])
        self.assertIn("📅 posted 2026-07-26", ins[0]["row"])
        self.assertEqual(ins[0]["posted"], "2026-07-26")

    def test_undated_candidate_gets_no_posted_note(self):
        ins, _ = self._plan([cand()])
        self.assertNotIn("📅", ins[0]["row"])

    def test_already_applied_role_is_skipped(self):
        ins, skipped = self._plan([cand(company="Tenstorrent",
                                        role="Software Engineer, TT-Fabric")])
        self.assertEqual(ins, [])
        self.assertIn("applied", skipped[0]["why"])

    def test_already_shortlisted_role_is_skipped(self):
        ins, skipped = self._plan([cand(company="DoorDash",
                                        role="Software Engineer, Backend")])
        self.assertEqual(ins, [])
        self.assertIn("shortlisted", skipped[0]["why"])

    def test_judgment_rejected_role_is_skipped(self):
        ins, skipped = self._plan([cand(company="Boring Corp", role="Backend Dev")])
        self.assertEqual(ins, [])
        self.assertIn("rejected-judgment", skipped[0]["why"])

    def test_liveness_rejected_role_can_be_readded(self):
        # playbook §7.5: a dead link never blacklists a role
        ins, skipped = self._plan([cand(company="Trexo Robotics", role="Robotics Dev")])
        self.assertEqual(len(ins), 1)
        self.assertEqual(skipped, [])

    def test_force_overrides_dedup(self):
        ins, skipped = self._plan([cand(company="DoorDash",
                                        role="Software Engineer, Backend")], force=True)
        self.assertEqual(len(ins), 1)
        self.assertEqual(skipped, [])

    def test_missing_tier_is_skipped_not_guessed(self):
        ins, skipped = self._plan([cand(tier=9)])
        self.assertEqual(ins, [])
        self.assertIn("no '## Tier 9'", skipped[0]["why"])

    def test_missing_company_or_role_is_skipped(self):
        ins, skipped = self._plan([cand(company=""), cand(role="")])
        self.assertEqual(ins, [])
        self.assertEqual(len(skipped), 2)

    def test_near_duplicate_is_flagged_but_still_inserted(self):
        ins, _ = self._plan([cand(company="Tenstorrent",
                                  role="Software Engineer, TT-Fabric Extra")])
        self.assertEqual(len(ins), 1)
        self.assertIn("near-duplicate", ins[0]["near_duplicate"])


class ApplyTests(unittest.TestCase):
    def test_insert_lands_at_top_of_the_tier(self):
        lines = SHORTLIST.split("\n")
        ins, _ = SA.plan(lines, APPLIED.split("\n"), [cand()], TODAY)
        out = SA.apply_inserts(lines, ins)
        t = M.find_tables(out, r"Tier 1")[0]
        self.assertEqual(t.rows[0].get("company"), "NewCo")
        self.assertEqual(t.rows[1].get("company"), "DoorDash")

    def test_insert_into_an_empty_tier(self):
        lines = SHORTLIST.split("\n")
        ins, _ = SA.plan(lines, APPLIED.split("\n"), [cand(tier=2)], TODAY)
        out = SA.apply_inserts(lines, ins)
        t = M.find_tables(out, r"Tier 2")[0]
        self.assertEqual(len(t.rows), 1)
        self.assertEqual(t.rows[0].get("company"), "NewCo")

    def test_multiple_tiers_in_one_pass(self):
        lines = SHORTLIST.split("\n")
        ins, _ = SA.plan(lines, APPLIED.split("\n"),
                         [cand(), cand(company="Other", tier=2)], TODAY)
        out = SA.apply_inserts(lines, ins)
        self.assertEqual(len(M.find_tables(out, r"Tier 1")[0].rows), 2)
        self.assertEqual(len(M.find_tables(out, r"Tier 2")[0].rows), 1)

    def test_prose_sections_survive(self):
        lines = SHORTLIST.split("\n")
        ins, _ = SA.plan(lines, APPLIED.split("\n"), [cand()], TODAY)
        out = "\n".join(SA.apply_inserts(lines, ins))
        self.assertIn("**Last searched the web:** 2026-07-22", out)
        self.assertIn("Prose.", out)
        self.assertIn("- **Out of region:** Tower/DRW (Montreal)", out)


class ExcludedTests(unittest.TestCase):
    def test_cut_is_appended_as_a_bullet(self):
        out = SA.add_excluded(SHORTLIST.split("\n"),
                              [{"company": "BadCo", "role": "DV Engineer",
                                "reason": "DV role — hard filter"}], TODAY)
        text = "\n".join(out)
        self.assertIn(f"- **BadCo — DV Engineer** ({TODAY}): DV role — hard filter", text)

    def test_existing_bullets_are_preserved(self):
        out = "\n".join(SA.add_excluded(SHORTLIST.split("\n"),
                                       [{"company": "X", "role": "Y", "reason": "z"}], TODAY))
        self.assertIn("- **Defunct/acquired:** Untether AI (bankrupt)", out)

    def test_bullet_lands_inside_the_excluded_section(self):
        out = SA.add_excluded(SHORTLIST.split("\n"),
                              [{"company": "X", "role": "Y", "reason": "z"}], TODAY)
        text = "\n".join(out)
        excluded_block = text.split("## Excluded (and why)", 1)[1].split("## Notes", 1)[0]
        self.assertIn("**X — Y**", excluded_block)

    def test_missing_reason_is_recorded_not_invented(self):
        out = "\n".join(SA.add_excluded(SHORTLIST.split("\n"),
                                       [{"company": "X", "role": "Y"}], TODAY))
        self.assertIn("no reason given", out)


class HousekeepingTests(unittest.TestCase):
    def test_bump_searched_updates_the_header(self):
        lines, status = HK.bump_searched(SHORTLIST.split("\n"), TODAY)
        self.assertEqual(status, "bumped")
        self.assertIn(f"**Last searched the web:** {TODAY}", "\n".join(lines))

    def test_bump_searched_is_idempotent(self):
        lines, _ = HK.bump_searched(SHORTLIST.split("\n"), TODAY)
        lines, status = HK.bump_searched(lines, TODAY)
        self.assertEqual(status, "current")

    def test_sync_header_recounts_from_the_tables(self):
        lines, status, (na, ns) = HK.sync_header(APPLIED.split("\n"), TODAY)
        self.assertEqual(status, "bumped")
        self.assertEqual((na, ns), (1, 1))
        self.assertIn(f"**Last synced from Simplify:** {TODAY} · 1 applied · 1 saved",
                      "\n".join(lines))

    def test_sync_header_counts_reflect_edits(self):
        lines = APPLIED.split("\n")
        t = M.find_table(lines, "## Applied")
        lines = M.insert_rows(lines, "## Applied",
                              [M.row_md(t.build(date=TODAY, company="New", role="Role"))])
        lines, _, (na, ns) = HK.sync_header(lines, TODAY)
        self.assertEqual(na, 2)

    def test_missing_header_is_reported_distinctly_from_already_current(self):
        # conflating these two made a renamed header a permanent silent no-op
        _, status = HK.bump_searched(["# No header here", ""], TODAY)
        self.assertEqual(status, "missing")
        _, status, _ = HK.sync_header(["# No header here", ""], TODAY)
        self.assertEqual(status, "missing")

    def test_headers_are_not_mutated_in_place(self):
        original = SHORTLIST.split("\n")
        snapshot = list(original)
        HK.bump_searched(original, TODAY)
        self.assertEqual(original, snapshot)

    @patch.object(HK, "list_tabs", return_value=None)
    def test_close_scratch_reports_when_tab_share_is_down(self, _t):
        closed, rejected, err = HK.close_scratch()
        self.assertEqual(closed, 0)
        self.assertIn("not reachable", err)

    @patch.object(HK, "list_tabs", return_value={"tabs": [
        {"url": "https://builtintoronto.com/job/a/1"},
        {"url": "https://builtintoronto.com/job/b/2"},
        {"url": "https://www.linkedin.com/jobs/view/3/"},
    ]})
    @patch.object(HK, "_post", return_value={"closed": [1, 2], "rejected": []})
    def test_close_scratch_sends_both_gate_fields_per_host(self, post, _t):
        closed, rejected, err = HK.close_scratch("Scratch")
        self.assertIsNone(err)
        # /close requires BOTH expectHost and expectGroup; /tabs exposes no group info,
        # so the call is issued once per distinct host and the extension filters by group.
        hosts = sorted(c.args[1]["expectHost"] for c in post.call_args_list)
        self.assertEqual(hosts, ["builtintoronto.com", "www.linkedin.com"])
        for c in post.call_args_list:
            self.assertEqual(c.args[1]["expectGroup"], "Scratch")
        self.assertEqual(closed, 4)   # 2 per host call

    @patch.object(HK, "list_tabs", return_value={"tabs": [{"url": "https://x.com/a"}]})
    @patch.object(HK, "_post", return_value={"error": "expectHost required (safety)"})
    def test_close_scratch_surfaces_an_error_reply_as_failure(self, _p, _t):
        # the old group-only call got this reply and reported "closed 0" as success
        closed, rejected, err = HK.close_scratch("Scratch")
        self.assertEqual(closed, 0)
        self.assertIn("expectHost required", err)

    @patch.object(HK, "list_tabs", return_value={"tabs": [{"url": "https://x.com/a"}]})
    @patch.object(HK, "_post", return_value={"closed": [], "rejected": [
        {"why": "host-mismatch", "host": "other.com"},
        {"why": "group-mismatch"},
    ]})
    def test_host_mismatch_rejections_are_expected_noise(self, _p, _t):
        closed, rejected, err = HK.close_scratch("Scratch")
        self.assertIsNone(err)
        # one call per host means other hosts are rejected by design; only the
        # group-mismatch is a real rejection worth reporting
        self.assertEqual(rejected, 1)


if __name__ == "__main__":
    unittest.main()
