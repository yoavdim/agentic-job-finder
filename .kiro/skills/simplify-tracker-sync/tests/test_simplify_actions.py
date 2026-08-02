#!/usr/bin/env python3
"""Tests for simplify_actions.py — Tab Share API integration.

These tests verify the action planning logic. They do NOT make real HTTP calls;
instead they mock the Tab Share responses.
"""
import json
import unittest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
WORKSPACE_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(WORKSPACE_SCRIPTS))

import simplify_actions
import tab_share


class TestPlanAction(unittest.TestCase):
    def test_plan_inspect_uses_get(self):
        plan = simplify_actions.plan_action("inspect", application_id="abc-123")
        self.assertEqual(plan["action"], "inspect")
        self.assertEqual(plan["application_id"], "abc-123")
        self.assertEqual(plan["fetch"]["method"], "GET")
        self.assertIn("/tracker/abc-123/detail", plan["fetch"]["url"])

    def test_plan_mark_applied_uses_put_with_correct_payload(self):
        plan = simplify_actions.plan_action(
            "mark_applied", application_id="abc-123",
            simplify_url="https://simplify.jobs/tracker?id=abc-123"
        )
        self.assertEqual(plan["action"], "mark_applied")
        self.assertEqual(plan["fetch"]["method"], "PUT")
        self.assertIn("/tracker/abc-123", plan["fetch"]["url"])
        self.assertIn("status_events", plan["fetch"]["body"])
        self.assertEqual(
            plan["fetch"]["body"]["status_events"][0]["status"],
            simplify_actions.STATUS_APPLIED
        )
        # app-shaped events carry an ISO timestamp
        self.assertRegex(
            plan["fetch"]["body"]["status_events"][0]["timestamp"],
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"
        )

    def test_plan_mark_applied_preserves_prior_status_events(self):
        events = [
            {"id": "ev-1", "status": simplify_actions.STATUS_SAVED,
             "timestamp": "2026-07-28T22:00:10.423867"},
        ]
        plan = simplify_actions.plan_action(
            "mark_applied", application_id="abc-123", current_events=events)
        body = plan["fetch"]["body"]["status_events"]
        self.assertEqual(len(body), 2)
        # Saved history preserved verbatim, Applied appended
        self.assertEqual(body[0]["id"], "ev-1")
        self.assertEqual(body[0]["status"], simplify_actions.STATUS_SAVED)
        self.assertEqual(body[1]["status"], simplify_actions.STATUS_APPLIED)

    def test_plan_mark_applied_keeps_existing_applied_event(self):
        events = [
            {"id": "ev-1", "status": simplify_actions.STATUS_SAVED,
             "timestamp": "2026-07-28T22:00:10.423867"},
            {"id": "ev-2", "status": simplify_actions.STATUS_APPLIED,
             "timestamp": "2026-07-29T10:00:00.000000"},
        ]
        body = simplify_actions.build_mark_applied_body(events)["status_events"]
        self.assertEqual(len(body), 2)
        self.assertEqual(body[1]["id"], "ev-2")  # existing Applied kept, not duplicated

    def test_plan_delete_saved_uses_delete(self):
        plan = simplify_actions.plan_action("delete_saved", application_id="abc-123")
        self.assertEqual(plan["action"], "delete_saved")
        self.assertEqual(plan["application_id"], "abc-123")
        self.assertEqual(plan["fetch"]["method"], "DELETE")
        self.assertIn("/tracker/abc-123", plan["fetch"]["url"])
        self.assertNotIn("/detail", plan["fetch"]["url"])
        self.assertNotIn("body", plan["fetch"])

    def test_invalid_action_raises(self):
        with self.assertRaises(ValueError):
            simplify_actions.plan_action("invalid", application_id="abc")


class TestStatusHelpers(unittest.TestCase):
    def test_current_status_is_the_last_event(self):
        body = {"status_events": [
            {"id": "ev-1", "status": simplify_actions.STATUS_SAVED,
             "timestamp": "2026-07-28T22:00:10"},
            {"id": "ev-2", "status": simplify_actions.STATUS_APPLIED,
             "timestamp": "2026-07-29T10:00:00"},
        ]}
        self.assertEqual(simplify_actions.current_status(body),
                         simplify_actions.STATUS_APPLIED)

    def test_current_status_saved_only(self):
        body = {"status_events": [
            {"id": "ev-1", "status": simplify_actions.STATUS_SAVED,
             "timestamp": "2026-07-28T22:00:10"},
        ]}
        self.assertEqual(simplify_actions.current_status(body),
                         simplify_actions.STATUS_SAVED)

    def test_current_status_unknown_when_no_events(self):
        self.assertIsNone(simplify_actions.current_status({}))
        self.assertIsNone(simplify_actions.current_status({"status_events": []}))
        self.assertIsNone(simplify_actions.current_status(
            {"status_events": [{"timestamp": "2026-07-28T22:00:10"}]}))
        self.assertIsNone(simplify_actions.current_status(None))

    def test_rejection_outcome_delete_when_saved(self):
        self.assertEqual(
            simplify_actions.rejection_outcome(simplify_actions.STATUS_SAVED), "delete")

    def test_rejection_outcome_applied_wins_over_rejection(self):
        self.assertEqual(
            simplify_actions.rejection_outcome(simplify_actions.STATUS_APPLIED), "applied")

    def test_rejection_outcome_blocks_on_unknown(self):
        self.assertEqual(simplify_actions.rejection_outcome(None), "block")
        self.assertEqual(simplify_actions.rejection_outcome(0), "block")
        self.assertEqual(simplify_actions.rejection_outcome(99), "block")


class TestExecuteViaTabShare(unittest.TestCase):
    def _resp_ctx(self, bytes_):
        mock_resp = MagicMock()
        mock_resp.read.return_value = bytes_
        ctx = MagicMock()
        ctx.__enter__.return_value = mock_resp
        return ctx

    def _mock_urlopen(self, mock_urlopen, eval_resp):
        tabs_json = json.dumps({
            "tabs": [{"id": 42, "url": "https://simplify.jobs/tracker"}],
        }).encode("utf-8")
        mock_urlopen.side_effect = [
            self._resp_ctx(tabs_json),
            self._resp_ctx(json.dumps(eval_resp).encode("utf-8")),
        ]

    def test_dry_run_skips_execution(self):
        plan = simplify_actions.plan_action("mark_applied", application_id="abc-123")
        result = simplify_actions.execute_via_tab_share(
            plan, tab_share_url="http://localhost:8766", dry_run=True
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["action"], "mark_applied")
        self.assertTrue(result["dry_run"])

    @patch("tab_share.urllib.request.urlopen")
    def test_execute_returns_success_on_ok_response(self, mock_urlopen):
        # Mock the /tabs resolution and the Tab Share /eval response
        self._mock_urlopen(mock_urlopen, {
            "result": {
                "ok": True,
                "value": {"status": 200, "ok": True, "body": {"status": 2}}
            }
        })

        plan = simplify_actions.plan_action("mark_applied", application_id="abc-123")
        result = simplify_actions.execute_via_tab_share(
            plan, tab_share_url="http://localhost:8766", dry_run=False
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["response"]["body"]["status"], 2)
        self.assertEqual(mock_urlopen.call_count, 2)
        # the eval call targets the resolved tracker tab explicitly
        req = mock_urlopen.call_args_list[1][0][0]
        self.assertIn(b'"tabId": 42', req.data)

    @patch("tab_share.urllib.request.urlopen")
    def test_execute_reports_error_on_fetch_failure(self, mock_urlopen):
        self._mock_urlopen(mock_urlopen, {
            "result": {
                "ok": True,
                "value": {"status": 500, "ok": False, "body": {"error": "Server error"}}
            }
        })

        plan = simplify_actions.plan_action("mark_applied", application_id="abc-123")
        result = simplify_actions.execute_via_tab_share(
            plan, tab_share_url="http://localhost:8766", dry_run=False
        )

        self.assertEqual(result["status"], "failed")

    @patch("tab_share.urllib.request.urlopen")
    def test_execute_fails_loudly_without_tracker_tab(self, mock_urlopen):
        mock_urlopen.side_effect = [
            self._resp_ctx(json.dumps({"tabs": []}).encode("utf-8")),
        ]
        plan = simplify_actions.plan_action("mark_applied", application_id="abc-123")
        result = simplify_actions.execute_via_tab_share(
            plan, tab_share_url="http://localhost:8766", dry_run=False
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("tracker", result["error"])
        self.assertEqual(mock_urlopen.call_count, 1)  # never reached the eval call


if __name__ == "__main__":
    unittest.main()
