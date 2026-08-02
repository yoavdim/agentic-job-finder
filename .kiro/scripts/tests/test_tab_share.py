#!/usr/bin/env python3
"""Tests for tab_share — the one Tab Share HTTP client (see its module docstring for why
six independent copies existed before this). No real network calls: urllib is mocked."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tab_share as TS


def resp_ctx(obj):
    ctx = MagicMock()
    m = MagicMock()
    m.read.return_value = json.dumps(obj).encode("utf-8")
    ctx.__enter__.return_value = m
    return ctx


class PostTests(unittest.TestCase):
    @patch("tab_share.urllib.request.urlopen")
    def test_post_returns_decoded_json(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"ok": True})
        self.assertEqual(TS.post("/eval", {"code": "1"}), {"ok": True})

    @patch("tab_share.urllib.request.urlopen")
    def test_post_retries_then_gives_up_as_empty_dict(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("boom")
        with patch("tab_share.time.sleep"):
            self.assertEqual(TS.post("/eval", {}, retries=2), {})
        self.assertEqual(mock_urlopen.call_count, 3)  # 1 try + 2 retries

    @patch("tab_share.urllib.request.urlopen")
    def test_post_succeeds_after_a_retry(self, mock_urlopen):
        mock_urlopen.side_effect = [Exception("boom"), resp_ctx({"ok": True})]
        with patch("tab_share.time.sleep"):
            self.assertEqual(TS.post("/eval", {}, retries=2), {"ok": True})


class PostRawTests(unittest.TestCase):
    @patch("tab_share.urllib.request.urlopen")
    def test_success_returns_response_and_no_error(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"result": {"ok": True}})
        resp, err = TS.post_raw("/eval", {})
        self.assertEqual(resp, {"result": {"ok": True}})
        self.assertIsNone(err)

    @patch("tab_share.urllib.request.urlopen")
    def test_failure_surfaces_an_error_string_not_an_empty_dict(self, mock_urlopen):
        # this is the whole reason post_raw exists: a caller (housekeeping.py) that must
        # tell "it errored" apart from "it came back empty" needs the error text, not {}
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        resp, err = TS.post_raw("/eval", {})
        self.assertEqual(resp, {})
        self.assertIsNotNone(err)
        self.assertIn("Tab Share connection failed", err)


class EvalValueTests(unittest.TestCase):
    @patch("tab_share.urllib.request.urlopen")
    def test_unwraps_ok_result(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"result": {"ok": True, "value": 42}})
        self.assertEqual(TS.eval_value("1+1"), 42)

    @patch("tab_share.urllib.request.urlopen")
    def test_not_ok_result_is_none(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"result": {"ok": False, "value": "err"}})
        self.assertIsNone(TS.eval_value("1+1"))

    @patch("tab_share.urllib.request.urlopen")
    def test_tab_id_is_included_when_given(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"result": {"ok": True, "value": 1}})
        TS.eval_value("1", tab_id=42)
        req = mock_urlopen.call_args[0][0]
        self.assertIn(b'"tabId": 42', req.data)

    @patch("tab_share.urllib.request.urlopen")
    def test_no_tab_id_omits_the_field(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"result": {"ok": True, "value": 1}})
        TS.eval_value("1")
        req = mock_urlopen.call_args[0][0]
        self.assertNotIn(b"tabId", req.data)


class TabsAndIsUpTests(unittest.TestCase):
    @patch("tab_share.urllib.request.urlopen")
    def test_tabs_returns_the_list(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"tabs": [{"id": 1}, {"id": 2}]})
        self.assertEqual(TS.tabs(), [{"id": 1}, {"id": 2}])

    @patch("tab_share.urllib.request.urlopen")
    def test_tabs_on_failure_is_empty_list_not_an_exception(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("down")
        self.assertEqual(TS.tabs(), [])

    @patch("tab_share.urllib.request.urlopen")
    def test_is_up_true_on_success(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"tabs": []})
        self.assertTrue(TS.is_up())

    @patch("tab_share.urllib.request.urlopen")
    def test_is_up_false_on_failure(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("down")
        self.assertFalse(TS.is_up())

    @patch("tab_share.urllib.request.urlopen")
    def test_find_tab_matches_by_substring(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx(
            {"tabs": [{"id": 1, "url": "https://a.test/x"},
                      {"id": 2, "url": "https://simplify.jobs/tracker"}]})
        t = TS.find_tab("simplify.jobs/tracker")
        self.assertEqual(t["id"], 2)

    @patch("tab_share.urllib.request.urlopen")
    def test_find_tab_none_when_no_match(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"tabs": [{"id": 1, "url": "https://a.test/x"}]})
        self.assertIsNone(TS.find_tab("nope"))


class ExtractOpenCloseTests(unittest.TestCase):
    @patch("tab_share.urllib.request.urlopen")
    def test_extract_by_tab_id(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"text": "hi", "title": "T"})
        TS.extract(tab_id=7)
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        self.assertEqual(body, {"tabId": 7})

    @patch("tab_share.urllib.request.urlopen")
    def test_extract_by_url_when_no_tab_id(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"text": "hi"})
        TS.extract(url="https://x.test", group_name="Scratch")
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        self.assertEqual(body, {"url": "https://x.test", "groupName": "Scratch"})

    @patch("tab_share.urllib.request.urlopen")
    def test_extract_prefers_tab_id_over_url(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"text": "hi"})
        TS.extract(url="https://x.test", tab_id=7)
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        self.assertEqual(body, {"tabId": 7})

    @patch("tab_share.urllib.request.urlopen")
    def test_open_tab_returns_id(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"tabId": 99})
        self.assertEqual(TS.open_tab("https://x.test", group_name="Scratch"), 99)

    @patch("tab_share.urllib.request.urlopen")
    def test_close_builds_expected_body(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"closed": [], "rejected": []})
        TS.close(tab_ids=[1, 2], expect_group="Scratch", expect_host="x.test")
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        self.assertEqual(body, {"tabId": [1, 2], "expectGroup": "Scratch",
                                "expectHost": "x.test"})

    @patch("tab_share.urllib.request.urlopen")
    def test_close_wraps_a_single_id_in_a_list(self, mock_urlopen):
        mock_urlopen.return_value = resp_ctx({"closed": [], "rejected": []})
        TS.close(tab_ids=5, expect_group="*", expect_host="x.test")
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data)
        self.assertEqual(body["tabId"], [5])


if __name__ == "__main__":
    unittest.main()
