#!/usr/bin/env python3
"""Tests for candidate_lint — the structural check on candidate rows.

The most important tests here are the NEGATIVE ones: candidate_lint must not second-guess
the LLM's reading of a listing. An earlier version encoded seniority / DV-vs-FV /
degree-gate rules in regex and blocked good roles, which is the same failure mode as the
`## Excluded` prose matching. The "judgment is not second-guessed" class below pins that
down so the heuristics can't creep back in.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import candidate_lint as CL

TODAY = "2026-08-01"
EVIDENCE = "Responsibilities: Linux kernel drivers in C. Requirements: 2-5 yrs, BSc."


def row(**kw):
    base = {"company": "Acme", "role": "Kernel Engineer", "location": "Toronto",
            "notes": "✅ embedded", "evidence": EVIDENCE}
    base.update(kw)
    return base


def codes(cand, **kw):
    f = CL.lint_candidate(cand, today=TODAY, **kw)
    return [x["code"] for x in CL.blocking(f)]


def advisory_codes(cand, **kw):
    f = CL.lint_candidate(cand, today=TODAY, **kw)
    return [x["code"] for x in CL.advisory(f)]


class CompleteRowTests(unittest.TestCase):
    def test_a_complete_row_has_no_findings(self):
        self.assertEqual(CL.lint_candidate(row(posted="3 days ago"), today=TODAY), [])

    def test_severity_split_partitions_every_finding(self):
        f = CL.lint_candidate({"company": "A", "role": "R", "location": "Montreal, QC"},
                              today=TODAY)
        self.assertEqual(len(f), len(CL.blocking(f)) + len(CL.advisory(f)))


class EvidenceTests(unittest.TestCase):
    def test_missing_evidence_blocks(self):
        self.assertIn("missing-evidence", codes(row(evidence="")))

    def test_absent_evidence_key_blocks(self):
        cand = row()
        del cand["evidence"]
        self.assertIn("missing-evidence", codes(cand))

    def test_stub_evidence_blocks(self):
        self.assertIn("missing-evidence", codes(row(evidence="looks good")))

    def test_substantive_evidence_passes(self):
        self.assertNotIn("missing-evidence", codes(row(evidence=EVIDENCE)))

    def test_no_strict_drops_only_the_evidence_requirement(self):
        self.assertEqual(codes(row(evidence=""), strict=False), [])
        # the notes check still applies
        self.assertIn("missing-notes-flag",
                      codes(row(evidence="", notes="plain text"), strict=False))


class NotesFlagTests(unittest.TestCase):
    def test_missing_legend_marker_blocks(self):
        self.assertIn("missing-notes-flag", codes(row(notes="embedded work")))

    def test_empty_notes_blocks(self):
        self.assertIn("missing-notes-flag", codes(row(notes="")))

    def test_each_legend_marker_is_accepted(self):
        for flag in CL.NOTES_LEGEND:
            with self.subTest(flag=flag):
                self.assertNotIn("missing-notes-flag", codes(row(notes=f"{flag} note")))

    def test_the_ds_rule_is_enforced_structurally_not_by_title(self):
        # prefs: a DS role must ALWAYS be surfaced flagged so the user decides per-role.
        # candidate_lint checks the FLAG is present; it never tries to detect DS-ness.
        self.assertEqual(codes(row(role="Data Scientist", notes="⚠️ DS — your call")), [])
        self.assertIn("missing-notes-flag",
                      codes(row(role="Data Scientist", notes="strong match")))


class StalePostingTests(unittest.TestCase):
    def test_old_posting_blocks(self):
        self.assertIn("stale-posting", codes(row(posted="2026-05-01")))

    def test_fresh_posting_passes(self):
        self.assertEqual(codes(row(posted="3 days ago")), [])

    def test_undated_posting_is_not_penalized(self):
        # prefs: "No date is fine — don't penalize"
        self.assertEqual(codes(row(posted="")), [])
        self.assertEqual(codes(row()), [])

    def test_unparseable_date_is_not_penalized(self):
        self.assertEqual(codes(row(posted="sometime last quarter maybe")), [])

    def test_cutoff_is_configurable(self):
        self.assertIn("stale-posting", codes(row(posted="2026-07-20"), max_age_days=5))
        self.assertNotIn("stale-posting", codes(row(posted="2026-07-20"), max_age_days=60))

    def test_boundary_is_inclusive_of_the_cutoff(self):
        # exactly max_age_days old is still acceptable; one day past is not
        self.assertEqual(codes(row(posted="2026-07-01"), max_age_days=31), [])
        self.assertIn("stale-posting", codes(row(posted="2026-06-30"), max_age_days=31))


class LocationAdvisoryTests(unittest.TestCase):
    def test_out_of_region_is_advisory_never_blocking(self):
        for loc in ["Montreal, QC", "Ottawa, ON", "Québec City", "Remote"]:
            with self.subTest(loc=loc):
                self.assertEqual(codes(row(location=loc)), [],
                                 "location must never block — it's a heuristic on prose")
                self.assertIn("location-review", advisory_codes(row(location=loc)))

    def test_a_legitimate_mixed_location_still_only_warns(self):
        # "Toronto, occasional travel to Montreal" is a real Toronto role
        cand = row(location="Toronto, ON (occasional travel to Montreal)")
        self.assertEqual(codes(cand), [])

    def test_plain_toronto_is_silent(self):
        self.assertEqual(advisory_codes(row(location="Toronto, ON")), [])


class JudgmentIsNotSecondGuessedTests(unittest.TestCase):
    """candidate_lint must stay out of reading comprehension. Each row here reflects a
    judgment call the LLM is responsible for; none may be blocked."""

    def test_verification_role_is_left_to_the_caller(self):
        # "Verification Engineer" is usually DV (the hard no) but sometimes FV (a keeper).
        # Only the listing settles it, so the lint must not guess either way.
        self.assertEqual(codes(row(role="Verification Engineer",
                                   notes="⚠️ formal verification per JD, lowest tier")), [])
        self.assertEqual(codes(row(role="Design Verification Engineer",
                                   notes="⚠️ confirmed DV — logging as excluded")), [])

    def test_assertion_based_work_is_not_classified(self):
        # assertion-based verification is a formal technique that DV testbenches also use
        self.assertEqual(codes(row(role="Hardware Engineer",
                                   evidence="Writes SVA assertions; assertion-based "
                                            "verification of datapath. 2-4 yrs.")), [])

    def test_seniority_wording_is_left_to_the_caller(self):
        # "Lead" here describes the product area, not the level
        self.assertEqual(codes(row(role="Junior Software Engineer, Storage Lead")), [])
        self.assertEqual(codes(row(role="Software Engineer II")), [])

    def test_degree_wording_is_left_to_the_caller(self):
        self.assertEqual(codes(row(role="ML Engineer",
                                   evidence="Requirements: BSc required, PhD a plus. 2 yrs.")),
                         [])

    def test_no_regex_examines_the_role_title_for_fit(self):
        # a deliberately awful-sounding title with a complete row still passes: fit is the
        # caller's call, and this test fails loudly if fit heuristics are reintroduced
        self.assertEqual(codes(row(role="Senior Staff Principal DV Architect III",
                                   notes="⚠️ kept deliberately, see comment")), [])


class BatchTests(unittest.TestCase):
    def test_lint_batch_indexes_only_rows_with_findings(self):
        out = CL.lint_batch([row(), row(notes="no marker"), row()], today=TODAY)
        self.assertEqual(list(out), [1])

    def test_lint_batch_is_empty_when_all_rows_are_clean(self):
        self.assertEqual(CL.lint_batch([row(), row()], today=TODAY), {})


if __name__ == "__main__":
    unittest.main()
