"""Tests for the shared ChromeInterface (lib/chrome_interface.py). No live browser:
Tab Share is mocked at the module level, exactly like the per-script tests."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from chrome_interface import ChromeInterface


class ExtractTextTests(unittest.TestCase):
    def test_extract_text_opens_extracts_by_tab_id_and_closes(self):
        ci = ChromeInterface()
        with patch.object(ci, "open", return_value=7) as op, \
             patch.object(ci, "extract",
                          return_value={"text": "hello", "title": "Job Title"}) as ex, \
             patch.object(ci, "close") as cl:
            text, err = ci.extract_text("https://x.test/job/1")
        self.assertIsNone(err)
        self.assertIn("hello", text)
        self.assertIn("Job Title", text)
        op.assert_called_once_with("https://x.test/job/1")
        ex.assert_called_once_with(7, timeout=45)
        cl.assert_called_once_with([7], expect_host="x.test")

    def test_extract_text_short_circuits_when_open_fails(self):
        ci = ChromeInterface()
        with patch.object(ci, "open", return_value=None), \
             patch.object(ci, "extract") as ex, \
             patch.object(ci, "close") as cl:
            text, err = ci.extract_text("https://x.test/job/1")
        self.assertEqual(text, "")
        self.assertIn("could not open", err)
        ex.assert_not_called()
        cl.assert_not_called()

    def test_extract_text_closes_even_when_extract_fails(self):
        ci = ChromeInterface()
        with patch.object(ci, "open", return_value=7), \
             patch.object(ci, "extract", return_value=None), \
             patch.object(ci, "close") as cl:
            text, err = ci.extract_text("https://x.test/job/1")
        self.assertEqual(text, "")
        self.assertIn("extract failed", err)
        cl.assert_called_once_with([7], expect_host="x.test")


class ExtractElementsTests(unittest.TestCase):
    def test_returns_count_and_items(self):
        ci = ChromeInterface()
        res = {"count": 2, "items": [{"text": "a", "href": "/j/1"},
                                     {"text": "b", "href": "/j/2"}]}
        with patch.object(ci, "eval", return_value=res):
            out = ci.extract_elements(9, "a.job")
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["items"][0]["text"], "a")

    def test_empty_selector_never_hits_the_browser(self):
        ci = ChromeInterface()
        with patch.object(ci, "eval") as ev:
            out = ci.extract_elements(9, "")
        self.assertEqual(out["error"], "empty selector")
        ev.assert_not_called()

    def test_eval_failure_is_reported(self):
        ci = ChromeInterface()
        with patch.object(ci, "eval", return_value=None):
            out = ci.extract_elements(9, "a.job")
        self.assertIn("error", out)


class ProbeTests(unittest.TestCase):
    def test_probe_returns_samples_and_closes(self):
        ci = ChromeInterface()
        res = {"count": 3, "items": [{"text": "t1", "href": "/a"},
                                     {"text": "t2", "href": "/b"},
                                     {"text": "t3", "href": "/c"}]}
        with patch.object(ci, "is_up", return_value=True), \
             patch.object(ci, "open_loaded", return_value=5), \
             patch.object(ci, "scroll"), \
             patch.object(ci, "close_modals"), \
             patch.object(ci, "extract_elements", return_value=res), \
             patch.object(ci, "close") as cl:
            out = ci.probe("https://x.test", "a.job")
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 3)
        self.assertEqual(len(out["samples"]), 3)
        cl.assert_called_once_with([5], expect_host="*")

    def test_probe_surfaces_selector_error(self):
        ci = ChromeInterface()
        with patch.object(ci, "is_up", return_value=True), \
             patch.object(ci, "open_loaded", return_value=5), \
             patch.object(ci, "extract_elements", return_value={"error": "invalid selector"}), \
             patch.object(ci, "close") as cl:
            out = ci.probe("https://x.test", "a[")
        self.assertFalse(out["ok"])
        self.assertIn("invalid selector", out["error"])
        cl.assert_called_once_with([5], expect_host="*")

    def test_probe_never_opens_when_tab_share_down(self):
        ci = ChromeInterface()
        with patch.object(ci, "is_up", return_value=False), \
             patch.object(ci, "open_loaded") as op:
            out = ci.probe("https://x.test", "a.job")
        self.assertFalse(out["ok"])
        op.assert_not_called()


class ScrollAndExtractSnippetTests(unittest.TestCase):
    def test_extract_elements_js_survives_js_braces(self):
        # the snippets contain JS object braces; a .format() build would raise, so
        # they must be substituted with .replace(). This proves the call goes through.
        ci = ChromeInterface()
        with patch.object(ci, "eval") as ev:
            ci.extract_elements(9, "a.job")
        code = ev.call_args.args[1]
        self.assertIn("querySelectorAll", code)
        self.assertIn("a.job", code)

    def test_scroll_container_and_wiggle_js_survive_js_braces(self):
        ci = ChromeInterface()
        with patch.object(ci, "eval") as ev:
            ci.scroll_container(9, "[data-testid=job-card]")
            ci.scroll_wiggle(9, "[data-testid=job-card]")
        for call in ev.call_args_list:
            code = call.args[1]
            self.assertIn("scrollHeight", code)
            self.assertIn("job-card", code)


class UrlHelperTests(unittest.TestCase):
    def test_host_of_strips_scheme_port_path(self):
        from chrome_interface import host_of
        self.assertEqual(host_of("https://builtintoronto.com/job/a"), "builtintoronto.com")
        self.assertEqual(host_of("http://127.0.0.1:8766/x"), "127.0.0.1")
        self.assertEqual(host_of(""), "")
        self.assertEqual(host_of("not a url"), "")

    def test_resolve_href_resolves_relative_and_absolute(self):
        from chrome_interface import resolve_href
        origin = "https://builtintoronto.com"
        self.assertEqual(resolve_href("/job/a/1", origin), "https://builtintoronto.com/job/a/1")
        self.assertEqual(resolve_href("https://other.com/j/2", origin), "https://other.com/j/2")
        self.assertEqual(resolve_href("job/x", origin), "https://builtintoronto.com/job/x")
        self.assertEqual(resolve_href("", origin), "")
        self.assertEqual(resolve_href("#top", origin), "")


if __name__ == "__main__":
    unittest.main()
