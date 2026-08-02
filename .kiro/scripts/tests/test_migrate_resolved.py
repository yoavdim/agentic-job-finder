#!/usr/bin/env python3
"""Tests for migrate_resolved.py — stage 0b migration (playbook §7)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import md_tables as M
import migrate_resolved as MR


SHORTLIST = """# Job Shortlist

## Tier 1 — Best fit

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-07-15 | DoorDash | Software Engineer, Backend | Toronto | [Apply](https://www.linkedin.com/jobs/view/4438904097/) | ✅ backend |  |
| [x] | 2026-07-01 | Acme | Embedded Dev | Markham | [Apply](https://job-boards.greenhouse.io/acme/jobs/123) | ✅ embedded | strong fit |
| [x] | 2026-07-02 | Tenstorrent | Software Engineer, TT-Fabric | Toronto | [Apply](https://www.linkedin.com/jobs/view/4439917871/) | ✅ — **APPLIED 2026-07-15** | via referral |
| [nope] | 2026-06-22 | Huawei Canada | Compiler Engineer | Markham | [Apply](https://huaweicanada.recruitee.com/o/compiler-engineer-2-16) | ✅ compiler | not-interested — too far |
| [nope] | 2026-06-20 | Shady Corp | Dev | Toronto | [Apply](https://sketchy.test/job/1) | ⚠️ |  |
"""

APPLIED = """# Applied

**Last synced from Simplify:** 2026-07-15 · 1 applied · 0 saved (not yet applied)

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-15 | Tenstorrent | Software Engineer, TT-Fabric | Software Engineer, TT-Fabric | Toronto | [Apply](https://www.linkedin.com/jobs/view/4439917871/) |  |

## Saved (not yet applied)

| Saved | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|

## Rejected (migrated from shortlist `[nope]` rows)

| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |
|---|---|---|---|---|---|---|---|
"""

TODAY = "2026-07-29"


class ReasonClassificationTests(unittest.TestCase):
    def test_reads_the_code_the_reject_dialog_wrote(self):
        # tracker.html writes "<code> — <free text>"
        self.assertEqual(MR.classify_reason("not-interested — too far")[0], "not-interested")
        self.assertEqual(MR.classify_reason("listing-removed")[0], "listing-removed")
        self.assertEqual(MR.classify_reason("link-broken — 404 url")[0], "link-broken")

    def test_preserves_the_comment_verbatim(self):
        code, verbatim = MR.classify_reason("not-interested — too far")
        self.assertEqual(verbatim, "not-interested — too far")

    def test_empty_comment_is_unknown(self):
        self.assertEqual(MR.classify_reason("")[0], "unknown")
        self.assertEqual(MR.classify_reason(None)[0], "unknown")

    def test_keyword_fallback_for_handtyped_comments(self):
        self.assertEqual(MR.classify_reason("404 url")[0], "link-broken")
        self.assertEqual(MR.classify_reason("listing removed")[0], "listing-removed")
        self.assertEqual(MR.classify_reason("job not found")[0], "listing-removed")
        self.assertEqual(MR.classify_reason("asks 8 yrs of exp")[0], "not-qualified")
        self.assertEqual(MR.classify_reason("looks like a scam site")[0], "sketchy-site")

    def test_unmatched_comment_becomes_other_not_a_guess(self):
        self.assertEqual(MR.classify_reason("in oakville")[0], "other")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.actions = MR.plan(SHORTLIST.split("\n"), APPLIED.split("\n"), TODAY)

    def test_open_rows_are_left_alone(self):
        touched = [a["company"] for a in self.actions["applied_new"] + self.actions["rejected_new"]]
        self.assertNotIn("DoorDash", touched)

    def test_new_applied_row_carries_comment_and_link(self):
        acme = next(a for a in self.actions["applied_new"] if a["company"] == "Acme")
        self.assertEqual(acme["comment"], "strong fit")
        self.assertIn("greenhouse.io/acme/jobs/123", acme["apply"])

    def test_applied_date_prefers_the_APPLIED_note_over_added(self):
        # Tenstorrent row: Added 2026-07-02, Notes say APPLIED 2026-07-15
        # It already exists in ## Applied, so it becomes a comment fold, not an insert.
        fold = next(a for a in self.actions["applied_comment"] if a["company"] == "Tenstorrent")
        self.assertEqual(fold["comment"], "via referral")

    def test_existing_applied_row_is_not_duplicated(self):
        companies = [a["company"] for a in self.actions["applied_new"]]
        self.assertNotIn("Tenstorrent", companies)

    def test_rejected_rows_are_classified(self):
        by_co = {a["company"]: a for a in self.actions["rejected_new"]}
        self.assertEqual(by_co["Huawei Canada"]["reason"], "not-interested")
        self.assertEqual(by_co["Shady Corp"]["reason"], "unknown")
        self.assertEqual(by_co["Shady Corp"]["date"], TODAY)

    def test_every_resolved_row_is_marked_for_deletion(self):
        self.assertEqual(len(self.actions["delete"]), 4)   # 2 [x] + 2 [nope]


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.shortlist = SHORTLIST.split("\n")
        self.applied = APPLIED.split("\n")
        actions = MR.plan(self.shortlist, self.applied, TODAY)
        self.shortlist, self.applied, _m = MR.apply_plan(
            self.shortlist, self.applied, actions)
        self.s_text = "\n".join(self.shortlist)
        self.a_text = "\n".join(self.applied)

    def test_resolved_rows_are_gone_from_shortlist(self):
        for gone in ("Acme", "Huawei Canada", "Shady Corp"):
            self.assertNotIn(gone, self.s_text)

    def test_open_row_survives_untouched(self):
        self.assertIn("| [ ] | 2026-07-15 | DoorDash | Software Engineer, Backend |", self.s_text)

    def test_new_applied_row_landed_with_correct_columns(self):
        t = M.find_table(self.applied, "## Applied")
        acme = next(r for r in t.rows if r.get("company") == "Acme")
        self.assertEqual(acme.get("date"), "2026-07-01")
        self.assertEqual(acme.get("role"), "Embedded Dev")
        self.assertEqual(acme.get("location"), "Markham")
        self.assertEqual(acme.get("comment"), "strong fit")

    def test_comment_folded_onto_existing_row_without_duplicating_it(self):
        t = M.find_table(self.applied, "## Applied")
        tt = [r for r in t.rows if r.get("company") == "Tenstorrent"]
        self.assertEqual(len(tt), 1)
        self.assertEqual(tt[0].get("comment"), "via referral")

    def test_rejected_rows_landed_with_reason_and_verbatim_comment(self):
        t = M.find_table(self.applied, "## Rejected")
        by_co = {r.get("company"): r for r in t.rows}
        self.assertEqual(by_co["Huawei Canada"].get("reason"), "not-interested")
        self.assertEqual(by_co["Huawei Canada"].get("comment"), "not-interested — too far")
        self.assertEqual(by_co["Shady Corp"].get("reason"), "unknown")

    def test_other_sections_are_preserved(self):
        self.assertIn("**Last synced from Simplify:** 2026-07-15", self.a_text)
        self.assertIn("## Saved (not yet applied)", self.a_text)

    def test_migration_is_idempotent(self):
        actions2 = MR.plan(self.shortlist, self.applied, TODAY)
        self.assertEqual(actions2["delete"], [])
        self.assertEqual(actions2["applied_new"], [])
        self.assertEqual(actions2["rejected_new"], [])


class CommentMergeTests(unittest.TestCase):
    def test_never_clobbers_an_existing_comment(self):
        merged, changed = MR.merge_comment("original note", "new note")
        self.assertEqual(merged, "original note; new note")
        self.assertTrue(changed)

    def test_empty_existing_takes_the_incoming(self):
        self.assertEqual(MR.merge_comment("", "new")[0], "new")

    def test_no_incoming_is_a_noop(self):
        merged, changed = MR.merge_comment("original", "")
        self.assertEqual(merged, "original")
        self.assertFalse(changed)

    def test_duplicate_text_is_not_appended_twice(self):
        merged, changed = MR.merge_comment("already says this", "says this")
        self.assertFalse(changed)


class CrossFileIndexTests(unittest.TestCase):
    """Regression for the lost-comment bug.

    `plan()` records a shortlist row index and `apply_plan()` writes into applied.md, so the
    two indices must stay separate. They were collapsed into one `line_idx`, which meant the
    comment was either dropped silently or written onto whatever applied.md row happened to
    sit at that line number.

    Every fixture here deliberately puts the matching applied.md row at a DIFFERENT line
    number from the shortlist row. The original fixtures were small enough that the indices
    coincided, which is exactly why the bug survived 244 tests.
    """

    def _applied(self, n_filler, target_line_hint=None):
        rows = [f"| 2026-07-{10 + (i % 18):02d} | Filler{i} | Role{i} | Role{i} | Toronto "
                f"| [Apply](https://job-boards.greenhouse.io/f/jobs/{900 + i}) |  |"
                for i in range(n_filler)]
        rows.append("| 2026-07-20 | Acme | Kernel Engineer | Kernel Engineer | Toronto "
                    "| [Apply](https://job-boards.greenhouse.io/acme/jobs/111) |  |")
        return ("# Applied\n\n## Applied\n\n"
                "| Applied | Company | Role | Raw | Location | Apply | Comment |\n"
                "|---|---|---|---|---|---|---|\n"
                + "\n".join(rows) +
                "\n\n## Rejected\n\n"
                "| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |\n"
                "|---|---|---|---|---|---|---|---|\n").split("\n")

    def _shortlist(self):
        return ("# Shortlist\n\n## Tier 1 — Best fit\n\n"
                "|  | Added | Company | Role | Location | Apply link | Notes | Comment |\n"
                "|---|---|---|---|---|---|---|---|\n"
                "| [x] | 2026-07-20 | Acme | Kernel Engineer | Toronto "
                "| [Apply](https://job-boards.greenhouse.io/acme/jobs/111) | ✅ "
                "| loved the team |\n").split("\n")

    def test_comment_lands_on_the_matched_applied_row(self):
        for n_filler in (0, 1, 5, 11, 20):
            with self.subTest(filler=n_filler):
                shortlist = self._shortlist()
                applied = self._applied(n_filler)
                actions = MR.plan(shortlist, applied, TODAY)
                self.assertEqual(len(actions["applied_comment"]), 1)
                sl2, ap2, _m = MR.apply_plan(shortlist, applied, actions)
                text = "\n".join(ap2)
                acme = [l for l in text.splitlines()
                        if l.startswith("| 2026-07-20 | Acme")]
                self.assertEqual(len(acme), 1, text)
                self.assertIn("loved the team", acme[0],
                              "the comment must reach the Acme row")
                # and no Filler row may have been touched
                for line in text.splitlines():
                    if "Filler" in line:
                        self.assertTrue(line.rstrip().endswith("|  |"),
                                        f"unrelated row was modified: {line}")

    def test_plan_records_both_indices_distinctly(self):
        shortlist = self._shortlist()
        applied = self._applied(11)
        actions = MR.plan(shortlist, applied, TODAY)
        edit = actions["applied_comment"][0]
        self.assertIn("applied_idx", edit)
        self.assertIn("line_idx", edit)
        self.assertNotEqual(edit["applied_idx"], edit["line_idx"],
                            "fixture must place the rows at different line numbers")
        # line_idx addresses the shortlist row that is about to be deleted
        self.assertIn(edit["line_idx"], actions["delete"])

    def test_apply_plan_refuses_a_bogus_applied_index(self):
        shortlist = self._shortlist()
        applied = self._applied(3)
        actions = MR.plan(shortlist, applied, TODAY)
        actions["applied_comment"][0]["applied_idx"] = 9999
        with self.assertRaises(KeyError):
            MR.apply_plan(shortlist, applied, actions)

    def test_apply_plan_refuses_an_edit_with_no_applied_index(self):
        shortlist = self._shortlist()
        applied = self._applied(3)
        actions = MR.plan(shortlist, applied, TODAY)
        del actions["applied_comment"][0]["applied_idx"]
        with self.assertRaises(KeyError):
            MR.apply_plan(shortlist, applied, actions)

    def test_apply_plan_does_not_mutate_its_inputs(self):
        shortlist = self._shortlist()
        applied = self._applied(5)
        sl_snap, ap_snap = list(shortlist), list(applied)
        actions = MR.plan(shortlist, applied, TODAY)
        MR.apply_plan(shortlist, applied, actions)
        self.assertEqual(shortlist, sl_snap)
        self.assertEqual(applied, ap_snap)


if __name__ == "__main__":
    unittest.main()
