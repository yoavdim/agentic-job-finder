#!/usr/bin/env python3
"""Tests for saved_sync — the push-then-pull planner for applied.md's ## Saved.

The classes below map onto the invariants that make this safe rather than onto functions:

  PrecedenceTests      one decision per row, in the agreed priority order
  StaleAutoRejectTests the automatic rule, including what it must NOT touch
  GuardTests           what refuses to run without --force
  ExecutionTests       the remote-call layer, driven by a fake (never the network)
  MirrorRebuildTests   new Saved = snapshot - removed - promoted, failures retained
  OrderingTests        the hazards that motivated classify-once

No network: every remote interaction goes through a scripted fake.
"""
import datetime
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location("saved_sync", SCRIPTS / "saved_sync.py")
ss = importlib.util.module_from_spec(_spec)
sys.modules["saved_sync"] = ss
_spec.loader.exec_module(ss)

TODAY = datetime.date(2026, 8, 1)


def snap(company, *, saved="2026-07-20", applied="", app_id=None, role="Engineer"):
    key = (company.lower(), role.lower())
    return {"key": key, "company": company, "title": role, "location": "Toronto",
            "saved": saved, "applied": applied, "url": f"https://x.test/{company}",
            "id": company.lower() if app_id is None else app_id}


def local(company, *, status="", comment="", role="Engineer"):
    return {(company.lower(), role.lower()): {"status": status, "comment": comment}}


class FakeActions:
    """Scripted remote. states: app_id -> 'saved'|'applied'|'unknown'."""

    def __init__(self, states=None, fail=()):
        self.states = states or {}
        self.fail = set(fail)
        self.deleted, self.marked, self.inspected = [], [], []

    def current_state(self, app_id):
        self.inspected.append(app_id)
        return self.states.get(app_id, "saved")

    def applied_date(self, app_id):
        return "2026-07-30"

    def mark_applied(self, app_id):
        if app_id in self.fail:
            return {"ok": False, "detail": "boom"}
        self.marked.append(app_id)
        return {"ok": True}

    def delete(self, app_id):
        if app_id in self.fail:
            return {"ok": False, "detail": "boom"}
        self.deleted.append(app_id)
        return {"ok": True}


def one(rows, locals_=None, today=TODAY, **kw):
    return ss.plan(rows, locals_ or {}, today, **kw)[0]


class StatusNormalizationTests(unittest.TestCase):
    def test_blank_and_unknown_degrade_to_saved(self):
        # the fallback must be the option that does NOTHING, so a typo can't delete
        for raw in ("", None, "   ", "rejcted", "whatever", "APPLIED?"):
            with self.subTest(raw=raw):
                self.assertEqual(ss.normalize_status(raw), ss.STATUS_SAVED)

    def test_known_statuses_are_recognized_case_insensitively(self):
        self.assertEqual(ss.normalize_status("Rejected"), ss.STATUS_REJECTED)
        self.assertEqual(ss.normalize_status("  APPLIED "), ss.STATUS_APPLIED)

    def test_bracketed_cell_is_tolerated(self):
        self.assertEqual(ss.normalize_status("[rejected]"), ss.STATUS_REJECTED)


class PrecedenceTests(unittest.TestCase):
    def test_remote_applied_beats_every_local_status(self):
        for status in ("", "saved", "applied", "rejected"):
            with self.subTest(status=status):
                d = one([snap("A", applied="2026-07-25")], local("A", status=status))
                self.assertEqual(d.action, ss.ACT_PROMOTE)
                self.assertEqual(d.applied_date, "2026-07-25")

    def test_remote_applied_beats_staleness(self):
        d = one([snap("A", saved="2025-01-01", applied="2026-07-25")])
        self.assertEqual(d.action, ss.ACT_PROMOTE)

    def test_local_applied_pushes_mark_applied(self):
        d = one([snap("A")], local("A", status="applied"))
        self.assertEqual(d.action, ss.ACT_MARK_APPLIED)

    def test_local_rejected_deletes_with_the_users_reason(self):
        d = one([snap("A")], local("A", status="rejected",
                                   comment="sketchy-site — scraper farm"))
        self.assertEqual(d.action, ss.ACT_DELETE)
        self.assertEqual(d.reason, "sketchy-site")
        self.assertFalse(d.auto)

    def test_explicit_rejection_outranks_the_stale_rule(self):
        # a row that is BOTH rejected and stale must keep the user's reason, not be
        # relabelled `too-old` by the automatic rule
        d = one([snap("A", saved="2025-01-01")],
                local("A", status="rejected", comment="not-qualified — wants 8 yrs"))
        self.assertEqual(d.reason, "not-qualified")
        self.assertFalse(d.auto)

    def test_untouched_recent_row_is_kept(self):
        d = one([snap("A")])
        self.assertEqual(d.action, ss.ACT_KEEP)

    def test_exactly_one_decision_per_snapshot_row(self):
        rows = [snap("A"), snap("B", saved="2025-01-01"), snap("C", applied="2026-07-25")]
        self.assertEqual(len(ss.plan(rows, {}, TODAY)), 3)

    def test_rows_absent_from_the_snapshot_are_not_decided(self):
        # gone from Simplify means the user removed it; the mirror simply drops it
        decisions = ss.plan([snap("A")], local("Ghost", status="rejected"), TODAY)
        self.assertEqual([d.company for d in decisions], ["A"])


class ReasonClassificationTests(unittest.TestCase):
    def test_leading_reason_code_is_used(self):
        for code in ("link-broken", "listing-removed", "not-qualified", "sketchy-site"):
            with self.subTest(code=code):
                self.assertEqual(ss.classify_reason(f"{code} — because")[0], code)

    def test_freeform_comment_becomes_a_judgment_rejection(self):
        # NOT a liveness code: a liveness reason would make the role re-suggestable, the
        # wrong default for something the user deliberately rejected
        reason, verbatim = ss.classify_reason("just dont like them")
        self.assertEqual(reason, "not-interested")
        self.assertEqual(verbatim, "just dont like them")

    def test_empty_comment_still_records_a_judgment(self):
        self.assertEqual(ss.classify_reason("")[0], "not-interested")


class StaleAutoRejectTests(unittest.TestCase):
    def test_row_past_the_cutoff_is_auto_rejected_as_too_old(self):
        d = one([snap("A", saved="2026-01-01")])
        self.assertEqual(d.action, ss.ACT_DELETE)
        self.assertEqual(d.reason, ss.REASON_TOO_OLD)
        self.assertTrue(d.auto)

    def test_auto_comment_is_marked_auto(self):
        d = one([snap("A", saved="2026-01-01")])
        self.assertIn("(auto)", d.comment)

    def test_too_old_is_a_liveness_reason_so_the_role_can_return(self):
        sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts" / "lib"))
        from reasons import classify_bucket
        self.assertEqual(classify_bucket(ss.REASON_TOO_OLD), "rejected-liveness")

    def test_row_inside_the_cutoff_is_untouched(self):
        self.assertEqual(one([snap("A", saved="2026-07-20")]).action, ss.ACT_KEEP)

    def test_boundary_is_exclusive(self):
        # exactly at the cutoff survives; one day past does not
        at = TODAY - datetime.timedelta(days=60)
        past = TODAY - datetime.timedelta(days=61)
        self.assertEqual(one([snap("A", saved=at.isoformat())]).action, ss.ACT_KEEP)
        self.assertEqual(one([snap("A", saved=past.isoformat())]).action, ss.ACT_DELETE)

    def test_cutoff_is_configurable(self):
        d = one([snap("A", saved="2026-07-20")], max_saved_age_days=5)
        self.assertEqual(d.action, ss.ACT_DELETE)

    def test_undated_row_is_never_auto_rejected(self):
        # never guess an age; an undated row is left alone
        self.assertEqual(one([snap("A", saved="")]).action, ss.ACT_KEEP)
        self.assertEqual(one([snap("A", saved="not a date")]).action, ss.ACT_KEEP)


class GuardTests(unittest.TestCase):
    def test_too_many_auto_deletes_is_refused(self):
        rows = [snap(f"C{i}", saved="2025-01-01") for i in range(6)]
        errs = ss.guard_errors(ss.plan(rows, {}, TODAY), max_auto_delete=5)
        self.assertTrue(any("auto-delete" in e for e in errs))

    def test_manual_rejections_are_not_capped(self):
        rows = [snap(f"C{i}") for i in range(20)]
        locals_ = {}
        for i in range(20):
            locals_.update(local(f"C{i}", status="rejected", comment="not-interested — no"))
        errs = ss.guard_errors(ss.plan(rows, locals_, TODAY), max_auto_delete=5)
        self.assertEqual([e for e in errs if "auto-delete" in e], [])

    def test_push_without_a_tracker_id_is_refused(self):
        errs = ss.guard_errors(
            ss.plan([snap("A", app_id="")], local("A", status="rejected"), TODAY))
        self.assertTrue(any("no tracker id" in e for e in errs))

    def test_a_clean_plan_has_no_guard_errors(self):
        self.assertEqual(ss.guard_errors(ss.plan([snap("A")], {}, TODAY)), [])

    def test_needs_push_detects_unpushed_intent(self):
        self.assertFalse(ss.needs_push(ss.plan([snap("A")], {}, TODAY)))
        self.assertTrue(ss.needs_push(
            ss.plan([snap("A")], local("A", status="rejected"), TODAY)))


class ExecutionTests(unittest.TestCase):
    def test_delete_reverifies_and_refuses_when_remote_says_applied(self):
        # the snapshot may be stale; the last-moment check is what protects a real
        # application from being deleted
        d = ss.plan([snap("A")], local("A", status="rejected"), TODAY)
        acts = FakeActions(states={"a": "applied"})
        out = ss.execute(d, acts)
        self.assertEqual(out[0].status, ss.OUTCOME_APPLIED_WINS)
        self.assertEqual(acts.deleted, [])

    def test_delete_is_blocked_when_remote_status_is_unverifiable(self):
        d = ss.plan([snap("A")], local("A", status="rejected"), TODAY)
        out = ss.execute(d, FakeActions(states={"a": "unknown"}))
        self.assertEqual(out[0].status, ss.OUTCOME_BLOCKED)

    def test_delete_fires_only_when_remote_says_saved(self):
        d = ss.plan([snap("A")], local("A", status="rejected"), TODAY)
        acts = FakeActions(states={"a": "saved"})
        self.assertTrue(ss.execute(d, acts)[0].ok)
        self.assertEqual(acts.deleted, ["a"])

    def test_failure_is_recorded_not_raised(self):
        d = ss.plan([snap("A")], local("A", status="rejected"), TODAY)
        out = ss.execute(d, FakeActions(states={"a": "saved"}, fail={"a"}))
        self.assertEqual(out[0].status, ss.OUTCOME_FAILED)

    def test_missing_id_blocks_before_any_remote_call(self):
        d = ss.plan([snap("A", app_id="")], local("A", status="rejected"), TODAY)
        acts = FakeActions()
        out = ss.execute(d, acts)
        self.assertEqual(out[0].status, ss.OUTCOME_BLOCKED)
        self.assertEqual(acts.inspected, [])

    def test_keep_and_promote_need_no_remote_call(self):
        d = ss.plan([snap("A"), snap("B", applied="2026-07-25")], {}, TODAY)
        acts = FakeActions()
        out = ss.execute(d, acts)
        self.assertTrue(all(o.ok for o in out))
        self.assertEqual((acts.deleted, acts.marked, acts.inspected), ([], [], []))


class MirrorRebuildTests(unittest.TestCase):
    def _run(self, rows, locals_, states=None, fail=()):
        d = ss.plan(rows, locals_, TODAY)
        outcomes = ss.execute(d, FakeActions(states or {}, fail))
        return ss.mirror_rows(outcomes)

    def test_successful_delete_leaves_saved_and_lands_in_rejected(self):
        keep, applied, rejected = self._run(
            [snap("A")], local("A", status="rejected"), {"a": "saved"})
        self.assertEqual([o.decision.company for o in rejected], ["A"])
        self.assertEqual(keep, [])

    def test_successful_mark_applied_moves_to_applied(self):
        keep, applied, rejected = self._run(
            [snap("A")], local("A", status="applied"), {"a": "saved"})
        self.assertEqual([o.decision.company for o in applied], ["A"])
        self.assertEqual(keep, [])

    def test_failed_push_is_retained_in_saved(self):
        keep, applied, rejected = self._run(
            [snap("A")], local("A", status="rejected"), {"a": "saved"}, fail={"a"})
        self.assertEqual([o.decision.company for o in keep], ["A"])
        self.assertEqual((applied, rejected), ([], []))

    def test_retained_row_keeps_the_intent_that_failed(self):
        keep, _a, _r = self._run([snap("A")], local("A", status="rejected"),
                                 {"a": "saved"}, fail={"a"})
        self.assertEqual(ss.retained_status(keep[0]), ss.STATUS_REJECTED)

    def test_retained_failed_mark_applied_keeps_applied_intent(self):
        keep, _a, _r = self._run([snap("A")], local("A", status="applied"),
                                 {"a": "saved"}, fail={"a"})
        self.assertEqual(ss.retained_status(keep[0]), ss.STATUS_APPLIED)

    def test_still_saved_row_reverts_to_a_blank_status(self):
        keep, _a, _r = self._run([snap("A")], {})
        self.assertEqual(ss.retained_status(keep[0]), "")

    def test_applied_wins_row_becomes_applied_not_rejected(self):
        keep, applied, rejected = self._run(
            [snap("A")], local("A", status="rejected"), {"a": "applied"})
        self.assertEqual([o.decision.company for o in applied], ["A"])
        self.assertEqual(rejected, [])

    def test_blocked_row_stays_for_the_next_pass(self):
        keep, applied, rejected = self._run(
            [snap("A", app_id="")], local("A", status="rejected"))
        self.assertEqual([o.decision.company for o in keep], ["A"])


class OrderingTests(unittest.TestCase):
    """The hazards that forced classify-once-from-a-snapshot."""

    def test_a_row_is_never_acted_on_twice(self):
        # both rejected and stale: two independent passes would delete it, then fail
        # deleting a dead id and mark it "retry forever"
        rows = [snap("A", saved="2025-01-01")]
        decisions = ss.plan(rows, local("A", status="rejected", comment="not-interested — no"),
                            TODAY)
        self.assertEqual(len(decisions), 1)
        acts = FakeActions({"a": "saved"})
        ss.execute(decisions, acts)
        self.assertEqual(acts.deleted, ["a"], "exactly one delete for one row")

    def test_every_removed_row_carries_its_reason_for_the_local_record(self):
        # anything deleted without being classified first would vanish from the next
        # capture with no ## Rejected entry, and get re-suggested later
        rows = [snap("A", saved="2025-01-01"), snap("B")]
        decisions = ss.plan(rows, local("B", status="rejected", comment="sketchy-site — no"),
                            TODAY)
        _k, _a, rejected = ss.mirror_rows(
            ss.execute(decisions, FakeActions({"a": "saved", "b": "saved"})))
        self.assertEqual(len(rejected), 2)
        for o in rejected:
            self.assertTrue(o.decision.reason, "a removed row must carry a reason")

    def test_plan_is_pure(self):
        rows = [snap("A", saved="2025-01-01")]
        locals_ = local("A", status="rejected")
        rows_snapshot = [dict(r) for r in rows]
        locals_snapshot = {k: dict(v) for k, v in locals_.items()}
        ss.plan(rows, locals_, TODAY)
        self.assertEqual(rows, rows_snapshot)
        self.assertEqual(locals_, locals_snapshot)

    def test_plan_is_deterministic_for_a_fixed_today(self):
        rows = [snap("A", saved="2026-01-01"), snap("B")]
        a = [d.as_dict() for d in ss.plan(rows, {}, TODAY)]
        b = [d.as_dict() for d in ss.plan(rows, {}, TODAY)]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
