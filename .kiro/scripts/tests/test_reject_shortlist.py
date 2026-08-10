"""Tests for reject_shortlist.py — the pure core: only `[ ]` rows are selected and
marked [nope], `[x]` / `[nope]` / no-box rows are left alone, and the comment is
written in the "<code> — <free text>" shape classify_reason reads back."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import reject_shortlist as R

SAMPLE = """# Job Shortlist

## Tier 1 — Best fit

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-08-05 | iVedha Inc. | Jr. Embedded Linux Engineer | Toronto | [Apply](https://x/) | ok |  |
| [x] | 2026-08-02 | Cerebras | ML Systems | Toronto | [Apply](https://y/) | ok |  |
| [nope] | 2026-08-05 | Aviva | Data Scientist | Markham | [Apply](https://z/) | ok | not-qualified — Msc |

## Tier 2 — General

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-08-05 | Stripe | SWE New Grad | Toronto | [Apply](https://s/) | ok | saw on builtin |
| n/a | 2026-08-05 | Rockstar | Anim Tools | Toronto | [Apply](https://r/) | ok |  |
"""


class OpenRowsTests(unittest.TestCase):
    def test_selects_only_open_rows(self):
        rows = R.open_rows(SAMPLE.split("\n"))
        self.assertEqual(
            [r.get("company") for _t, r in rows],
            ["iVedha Inc.", "Stripe"])

    def test_applied_rejected_and_no_box_rows_are_excluded(self):
        rows = R.open_rows(SAMPLE.split("\n"))
        companies = {r.get("company") for _t, r in rows}
        self.assertNotIn("Cerebras", companies)  # [x]
        self.assertNotIn("Aviva", companies)     # [nope]
        self.assertNotIn("Rockstar", companies)  # no status box


class MarkAllTests(unittest.TestCase):
    def test_open_rows_become_nope_with_merged_comment(self):
        out = R.mark_all(SAMPLE.split("\n"), "not-interested", "flush")
        for company in ("iVedha Inc.", "Stripe"):
            line = next(l for l in out if company in l)
            self.assertIn("[nope]", line)
            self.assertIn("not-interested — flush", line)

    def test_x_nope_and_no_box_rows_are_untouched(self):
        out = R.mark_all(SAMPLE.split("\n"), "not-interested", "flush")
        cerebras = next(l for l in out if "Cerebras" in l)
        aviva = next(l for l in out if "Aviva" in l)
        rockstar = next(l for l in out if "Rockstar" in l)
        self.assertIn("[x]", cerebras)
        self.assertIn("[nope]", aviva)
        self.assertIn("not-qualified — Msc", aviva)
        self.assertNotIn("[nope]", rockstar)

    def test_bare_reason_writes_only_the_code(self):
        out = R.mark_all(SAMPLE.split("\n"), "too-old", "")
        line = next(l for l in out if "iVedha" in l)
        self.assertIn("[nope]", line)
        self.assertIn("too-old", line)
        self.assertNotIn("too-old —", line)

    def test_existing_comment_is_appended_not_clobbered(self):
        out = R.mark_all(SAMPLE.split("\n"), "not-interested", "flush")
        stripe = next(l for l in out if "Stripe" in l)
        self.assertIn("not-interested — flush; ", stripe)

    def test_merged_comment(self):
        self.assertEqual(R.merged_comment("a", "b"), "a — b")
        self.assertEqual(R.merged_comment("a", ""), "a")
        self.assertEqual(R.merged_comment("a", " "), "a")

    def test_append_comment(self):
        self.assertEqual(R.append_comment("", "x"), "x")
        self.assertEqual(R.append_comment("old", ""), "old")
        self.assertEqual(R.append_comment("old", "new"), "new; old")
        self.assertEqual(R.append_comment("already has new", "new"), "already has new")
        self.assertEqual(R.append_comment("new", "already has new"), "already has new")


if __name__ == "__main__":
    unittest.main()
