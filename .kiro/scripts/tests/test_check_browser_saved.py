import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_browser_saved as CBS

TRACKER = {"title": "tracker", "url": "file:///ws/tracker.html",
           "status": "Unsaved changes"}


def stdin_mock(isatty_value):
    return mock.patch.object(sys, "stdin", mock.Mock(isatty=lambda: isatty_value))


class ConfirmBrowserSavedTests(unittest.TestCase):

    def test_yes_bypasses_the_check_entirely(self):
        for flag in ("--yes", "-y"):
            with self.subTest(flag=flag), mock.patch.object(CBS, "tracker_dirty_tabs") as td:
                self.assertTrue(CBS.confirm_browser_saved([flag]))
                td.assert_not_called()

    def test_no_tracker_tabs_or_all_saved_proceeds(self):
        with mock.patch.object(CBS, "tracker_dirty_tabs", return_value=([], None)) as td:
            self.assertTrue(CBS.confirm_browser_saved([]))
        td.assert_called_once()

    def test_tab_share_offline_refuses(self):
        with mock.patch.object(CBS, "tracker_dirty_tabs",
                               return_value=(None, "Tab Share not reachable")):
            self.assertFalse(CBS.confirm_browser_saved([]))

    def test_clean_noninteractive_proceeds(self):
        with mock.patch.object(CBS, "tracker_dirty_tabs", return_value=([], None)), \
             stdin_mock(False):
            self.assertTrue(CBS.confirm_browser_saved([]))

    def test_dirty_but_noninteractive_refuses(self):
        with mock.patch.object(CBS, "tracker_dirty_tabs",
                               return_value=([dict(TRACKER)], None)), \
             stdin_mock(False):
            self.assertFalse(CBS.confirm_browser_saved([]))

    def test_unsaved_tab_prompts_and_proceeds_after_user_saves(self):
        replies = [""]
        prompts = []

        def fake_input(prompt):
            prompts.append(prompt)
            return replies.pop(0)

        calls = [([dict(TRACKER)], None), ([], None)]
        with mock.patch.object(CBS, "tracker_dirty_tabs", side_effect=calls), \
             stdin_mock(True):
            self.assertTrue(CBS.confirm_browser_saved([], input_fn=fake_input))
        self.assertEqual(len(prompts), 1)
        self.assertIn("Save them in the browser", prompts[0])

    def test_still_dirty_proceed_anyway_with_n(self):
        replies = ["", "n"]
        prompts = []

        def fake_input(prompt):
            prompts.append(prompt)
            return replies.pop(0)

        dirty = [dict(TRACKER)]
        with mock.patch.object(CBS, "tracker_dirty_tabs", return_value=(dirty, None)), \
             stdin_mock(True):
            self.assertTrue(CBS.confirm_browser_saved([], input_fn=fake_input))
        self.assertIn("Wait until you save?", prompts[-1])

    def test_wait_until_save_yes_is_default_and_loops_until_saved(self):
        replies = ["", "", ""]
        calls = [([dict(TRACKER)], None), ([dict(TRACKER)], None), ([], None)]
        with mock.patch.object(CBS, "tracker_dirty_tabs", side_effect=calls), \
             stdin_mock(True):
            self.assertTrue(CBS.confirm_browser_saved([], input_fn=lambda p: replies.pop(0)))

    def test_eof_on_prompt_aborts(self):
        def boom(prompt):
            raise EOFError

        with mock.patch.object(CBS, "tracker_dirty_tabs",
                               return_value=([dict(TRACKER)], None)), \
             stdin_mock(True):
            self.assertFalse(CBS.confirm_browser_saved([], input_fn=boom))

    def test_recheck_failure_treats_tabs_as_still_unsaved(self):
        import io
        from contextlib import redirect_stderr

        def fake_input(prompt):
            return "n"

        calls = [([dict(TRACKER)], None), (None, "Tab Share went away")]
        buf = io.StringIO()
        with mock.patch.object(CBS, "tracker_dirty_tabs", side_effect=calls), \
             stdin_mock(True), redirect_stderr(buf):
            self.assertTrue(CBS.confirm_browser_saved([], input_fn=fake_input))
        self.assertIn("still unsaved", buf.getvalue())


class TrackerDirtyTabsTests(unittest.TestCase):

    def test_finds_only_tracker_tabs_and_asks_save_button(self):
        tabs = [
            {"id": 1, "url": "file:///ws/tracker.html", "title": "tracker"},
            {"id": 2, "url": "https://simplify.jobs/x", "title": "other"},
        ]

        def fake_eval(_self, tab_id, code, timeout=30):
            self.assertIn("getElementById('save')", code)
            self.assertEqual(tab_id, 1)
            return {"dirty": True, "status": "Unsaved changes"}

        with mock.patch.object(CBS.ChromeInterface, "is_up", return_value=True), \
             mock.patch.object(CBS.ChromeInterface, "tabs", return_value=tabs), \
             mock.patch.object(CBS.ChromeInterface, "eval", autospec=True,
                               side_effect=fake_eval):
            dirty, err = CBS.tracker_dirty_tabs()
        self.assertIsNone(err)
        self.assertEqual(len(dirty), 1)
        self.assertEqual(dirty[0]["title"], "tracker")
        self.assertEqual(dirty[0]["status"], "Unsaved changes")

    def test_clean_save_button_not_dirty(self):
        tabs = [{"id": 1, "url": "file:///ws/tracker.html", "title": "tracker"}]
        with mock.patch.object(CBS.ChromeInterface, "is_up", return_value=True), \
             mock.patch.object(CBS.ChromeInterface, "tabs", return_value=tabs), \
             mock.patch.object(CBS.ChromeInterface, "eval",
                               return_value={"dirty": False, "status": "Saved"}):
            dirty, err = CBS.tracker_dirty_tabs()
        self.assertIsNone(err)
        self.assertEqual(dirty, [])

    def test_unreadable_tab_counts_as_dirty(self):
        tabs = [{"id": 1, "url": "file:///ws/tracker.html", "title": "tracker"}]
        with mock.patch.object(CBS.ChromeInterface, "is_up", return_value=True), \
             mock.patch.object(CBS.ChromeInterface, "tabs", return_value=tabs), \
             mock.patch.object(CBS.ChromeInterface, "eval", return_value=None):
            dirty, err = CBS.tracker_dirty_tabs()
        self.assertIsNone(err)
        self.assertEqual(len(dirty), 1)

    def test_tab_share_down_reports_err(self):
        with mock.patch.object(CBS.ChromeInterface, "is_up", return_value=False):
            dirty, err = CBS.tracker_dirty_tabs()
        self.assertIsNone(dirty)
        self.assertIn("not reachable", err)

    def test_firefox_base_tried_after_chromium(self):
        with mock.patch.object(CBS.ChromeInterface, "is_up", side_effect=[False, True]) as up:
            with mock.patch.object(CBS.ChromeInterface, "tabs", return_value=[]):
                with mock.patch.object(CBS.ChromeInterface, "eval", return_value=None):
                    dirty, err = CBS.tracker_dirty_tabs()
        self.assertIsNone(err)
        self.assertEqual(dirty, [])
        # one is_up probe per base (both tried), and the winner (8765) is used
        self.assertEqual(up.call_count, 2)

    def test_check_js_is_an_iife_not_a_top_level_return(self):
        # Tab Share's /eval wraps the code, so a top-level `return` is a SyntaxError
        # (verified live: "Illegal return statement"). It must be a standalone expression.
        self.assertTrue(CBS._CHECK_JS.startswith("(function () {"))
        self.assertTrue(CBS._CHECK_JS.endswith("})()"))
        self.assertIn("getElementById('save')", CBS._CHECK_JS)


if __name__ == "__main__":
    unittest.main()
