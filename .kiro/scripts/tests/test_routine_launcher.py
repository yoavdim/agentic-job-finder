"""Tests for routine_launcher.py — the one thing worth pinning: an illegal run-config
selection is blocked (a checked stage whose `requires` aren't all checked is a problem),
plus the pure helpers for the view-in-chrome routine (tracker URL, browser resolution,
and the open/focus flow). The prompt-wording and UI are deliberately not tested."""
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))  # workspace root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import routine_launcher as RL

REQUIRES = {
    "0a": [],
    "0b": ["0a"],
    "0d": ["0a"],
    "1": [],
    "1a": ["1"],
    "1b": ["1"],
    "1c": ["1"],
    "1g": ["1"],
    "1h": ["1g"],
    "2a": ["1h"],
    "2b": ["1", "0b"],
}


class IllegalRunConfigBlockingTests(unittest.TestCase):
    def test_complete_plan_is_legal(self):
        self.assertEqual(RL.validate_plan(REQUIRES, {"0a", "0b", "1", "1g", "1h"}), [])

    def test_missing_requirement_is_blocked(self):
        issues = RL.validate_plan(REQUIRES, {"0b"})
        self.assertEqual(issues,
                         ["`0b` requires `0a`, but `0a` is not in the plan"])

    def test_master_stage_required_by_its_children(self):
        issues = RL.validate_plan(REQUIRES, {"1a"})
        self.assertEqual(issues,
                         ["`1a` requires `1`, but `1` is not in the plan"])

    def test_unchecked_stages_are_not_validated(self):
        # 1h isn't in the plan, so its (satisfied) dependency on 1g is irrelevant.
        self.assertEqual(RL.validate_plan(REQUIRES, {"1", "1g"}), [])

    def test_multiple_missing_requirements_all_reported(self):
        issues = RL.validate_plan(REQUIRES, {"1g", "2a"})
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("`1g` requires `1`" in i for i in issues))
        self.assertTrue(any("`2a` requires `1h`" in i for i in issues))

    def test_multi_requirement_stage_checks_all(self):
        issues = RL.validate_plan(REQUIRES, {"0a", "0b", "2b"})
        self.assertEqual(len(issues), 1)  # `1` missing; `0b` is checked
        self.assertIn("`2b` requires `1`", issues[0])

    def test_warnings_surface_in_the_search_prompt(self):
        prompt = RL.search_prompt(
            [("0a", "Fold thoughts.md into prefs", True, 0),
             ("0b", "Sync Simplify tracker", True, 0)],
            REQUIRES, {"0b"})
        self.assertIn("`0b` requires `0a`, but `0a` is not in the plan", prompt)




class ViewInChromeHelperTests(unittest.TestCase):
    def test_tracker_html_url_is_a_file_uri_in_this_workspace(self):
        url = RL.tracker_html_url()
        self.assertTrue(url.startswith("file://"))
        self.assertTrue(url.endswith("/tracker.html"))
        self.assertIn("Job%20Search", url)  # spaces percent-encoded

    def test_no_prompt_routines_return_empty_prompt(self):
        self.assertEqual(RL.build_prompt("view-in-chrome", [], {}, set()), "")
        self.assertIn("view-in-chrome", RL.NO_PROMPT_ROUTINES)

    def test_chromium_bin_respects_env_override(self):
        with mock.patch.dict("os.environ", {"KIRO_CHROMIUM": "/usr/bin/foo"}):
            self.assertEqual(RL.chromium_bin(), "/usr/bin/foo")

    @mock.patch("routine_launcher.subprocess.Popen")
    @mock.patch("routine_launcher.TS.find_tab")
    @mock.patch("routine_launcher.TS.is_up")
    def test_existing_tab_is_focused_not_reopened(self, is_up, find_tab, popen):
        is_up.return_value = True
        find_tab.return_value = {"id": 42, "url": RL.tracker_html_url()}
        ok, msg = RL.open_tracker_in_chrome()
        self.assertTrue(ok)
        self.assertIn("focused", msg)
        popen.assert_called_once_with(
            [RL.chromium_bin(), "--activate-on-launch"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @mock.patch("routine_launcher.subprocess.Popen")
    @mock.patch("routine_launcher.TS.find_tab")
    @mock.patch("routine_launcher.TS.is_up")
    def test_no_tab_and_share_down_falls_back_to_cli(self, is_up, find_tab, popen):
        is_up.return_value = False
        ok, msg = RL.open_tracker_in_chrome()
        self.assertTrue(ok)
        self.assertIn("new tab", msg)
        popen.assert_called_once_with(
            [RL.chromium_bin(), RL.tracker_html_url()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
