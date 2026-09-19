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
    # TS.query is stubbed to {} throughout so these keep testing the eval path they were
    # written for. Without the stub they reach the REAL /query over HTTP (and a 404 when
    # the extension predates it), which breaks this file's no-live-browser contract.
    def test_returns_count_and_items(self):
        ci = ChromeInterface()
        res = {"count": 2, "items": [{"text": "a", "href": "/j/1"},
                                     {"text": "b", "href": "/j/2"}]}
        with patch("chrome_interface.TS.query", return_value={}), \
             patch.object(ci, "eval", return_value=res):
            out = ci.extract_elements(9, "a.job")
        self.assertEqual(out["count"], 2)
        self.assertEqual(out["items"][0]["text"], "a")

    def test_empty_selector_never_hits_the_browser(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.query") as q, patch.object(ci, "eval") as ev:
            out = ci.extract_elements(9, "")
        self.assertEqual(out["error"], "empty selector")
        q.assert_not_called()
        ev.assert_not_called()

    def test_eval_failure_is_reported(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.query", return_value={}), \
             patch.object(ci, "eval", return_value=None):
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
        # scroll/close_modals are patched because probe() drives them before extracting;
        # unpatched they reach the real Tab Share over HTTP.
        ci = ChromeInterface()
        with patch.object(ci, "is_up", return_value=True), \
             patch.object(ci, "open_loaded", return_value=5), \
             patch.object(ci, "scroll"), \
             patch.object(ci, "close_modals"), \
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
    """The eval FALLBACK path: used only when /query and /scroll are unavailable (an
    extension predating them), so these still assert the snippet-building behaviour."""

    def test_extract_elements_js_survives_js_braces(self):
        # the snippets contain JS object braces; a .format() build would raise, so
        # they must be substituted with .replace(). This proves the call goes through.
        ci = ChromeInterface()
        with patch("chrome_interface.TS.query", return_value={}), \
             patch.object(ci, "eval") as ev:
            ci.extract_elements(9, "a.job")
        code = ev.call_args.args[1]
        self.assertIn("querySelectorAll", code)
        self.assertIn("a.job", code)

    def test_scroll_container_and_wiggle_js_survive_js_braces(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.scroll", return_value={}), \
             patch.object(ci, "eval") as ev:
            ci.scroll_container(9, "[data-testid=job-card]")
            ci.scroll_wiggle(9, "[data-testid=job-card]")
        for call in ev.call_args_list:
            code = call.args[1]
            self.assertIn("scrollHeight", code)
            self.assertIn("job-card", code)


class QueryEndpointTests(unittest.TestCase):
    """/query is preferred over /eval because it works on pages whose CSP forbids eval."""

    def test_extract_elements_uses_query_and_never_evals(self):
        ci = ChromeInterface()
        res = {"count": 1, "items": [{"text": "SWE", "href": "/j/1"}], "ready": "complete"}
        with patch("chrome_interface.TS.query", return_value=res) as q, \
             patch.object(ci, "eval") as ev:
            out = ci.extract_elements(9, "a.job")
        self.assertEqual(out["count"], 1)
        q.assert_called_once()
        self.assertEqual(q.call_args.args[0], "a.job")
        ev.assert_not_called()

    def test_extract_elements_falls_back_to_eval_when_query_unavailable(self):
        # An extension that predates /query returns {} (404 -> post() yields {}), so the
        # old path must still run rather than reporting "no matches".
        ci = ChromeInterface()
        res = {"count": 2, "items": [{"text": "a", "href": "/1"}, {"text": "b", "href": "/2"}]}
        with patch("chrome_interface.TS.query", return_value={}), \
             patch.object(ci, "eval", return_value=res) as ev:
            out = ci.extract_elements(9, "a.job")
        self.assertEqual(out["count"], 2)
        ev.assert_called_once()

    def test_extract_elements_reports_when_both_paths_fail(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.query", return_value={}), \
             patch.object(ci, "eval", return_value=None):
            out = ci.extract_elements(9, "a.job")
        self.assertIn("error", out)

    def test_empty_selector_never_hits_the_browser_at_all(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.query") as q, patch.object(ci, "eval") as ev:
            out = ci.extract_elements(9, "")
        self.assertEqual(out["error"], "empty selector")
        q.assert_not_called()
        ev.assert_not_called()

    def test_ready_reads_query_ready_without_eval(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.query", return_value={"count": 1, "ready": "complete"}), \
             patch.object(ci, "eval") as ev:
            self.assertEqual(ci.ready(9), "complete")
        ev.assert_not_called()

    def test_ready_falls_back_to_eval_when_query_unavailable(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.query", return_value={}), \
             patch.object(ci, "eval", return_value="loading") as ev:
            self.assertEqual(ci.ready(9), "loading")
        ev.assert_called_once()


class ScrollEndpointTests(unittest.TestCase):
    def test_window_scroll_sends_no_selector(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.scroll", return_value={"ok": True, "position": "window"}) as s:
            ci.scroll(9, steps=1, pause=0)
        self.assertIsNone(s.call_args.kwargs.get("selector"))
        self.assertIsNone(s.call_args.kwargs.get("mode"))

    def test_scroll_steps_issues_one_call_each(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.scroll", return_value={"ok": True, "position": "window"}) as s:
            ci.scroll(9, steps=3, pause=0)
        self.assertEqual(s.call_count, 3)

    def test_container_sends_selector_and_no_mode(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.scroll",
                   return_value={"ok": True, "position": "900/1800"}) as s:
            pos = ci.scroll_container(9, "[data-testid=job-card]")
        self.assertEqual(pos, "900/1800")
        self.assertEqual(s.call_args.kwargs["selector"], "[data-testid=job-card]")
        self.assertIsNone(s.call_args.kwargs.get("mode"))

    def test_wiggle_sends_wiggle_mode(self):
        # container vs wiggle differ on purpose: wiggle scrolls unconditionally to jolt a
        # stalled loader, container only scrolls a genuinely scrollable ancestor.
        ci = ChromeInterface()
        with patch("chrome_interface.TS.scroll",
                   return_value={"ok": True, "position": "900/1800"}) as s:
            ci.scroll_wiggle(9, "[data-testid=job-card]")
        self.assertEqual(s.call_args.kwargs["mode"], "wiggle")

    def test_no_cards_position_is_passed_through(self):
        ci = ChromeInterface()
        with patch("chrome_interface.TS.scroll",
                   return_value={"ok": True, "position": "no-cards"}):
            self.assertEqual(ci.scroll_container(9, ".nope"), "no-cards")


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
