#!/usr/bin/env python3
"""Tests for url_identity — company/role from URL STRUCTURE only.

The negative cases matter most. This module exists in a narrow form on purpose: page-title
parsing was tried and rejected because four observed hosts produced four different title
shapes, one of which ("Thank you for applying") isn't a job title at all. The tests below
pin that boundary so title-parsing can't creep back in, and so "returns nothing" stays a
first-class, intended outcome rather than looking like a bug to be fixed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import url_identity as UI


class OrgTests(unittest.TestCase):
    def test_subdomain_hosts(self):
        cases = {
            "https://xanadu.applytojob.com/apply/AbC/Role": "xanadu",
            "https://huaweicanada.recruitee.com/o/embedded-dev": "huaweicanada",
            "https://lseg.wd3.myworkdayjobs.com/en-US/Careers/userHome": "lseg",
        }
        for url, org in cases.items():
            with self.subTest(url=url):
                self.assertEqual(UI.ats_org(url), org)

    def test_path_hosts(self):
        cases = {
            "https://job-boards.greenhouse.io/doordashcanada/jobs/4567": "doordashcanada",
            "https://boards.greenhouse.io/cerebrassystems/jobs/1": "cerebrassystems",
            "https://jobs.ashbyhq.com/cohere/abc-123": "cohere",
            "https://jobs.lever.co/kepler/xyz": "kepler",
        }
        for url, org in cases.items():
            with self.subTest(url=url):
                self.assertEqual(UI.ats_org(url), org)

    def test_aggregators_have_no_employer_org(self):
        for url in ("https://www.linkedin.com/jobs/view/4438904097/",
                    "https://builtintoronto.com/job/animation-tools/10173309",
                    "https://ca.indeed.com/viewjob?jk=abc",
                    "https://simplify.jobs/p/xyz"):
            with self.subTest(url=url):
                self.assertEqual(UI.ats_org(url), "")

    def test_unknown_host_yields_nothing(self):
        # a company's own careers site has no structural convention we can rely on
        self.assertEqual(UI.ats_org("https://careers.amd.com/careers-home/jobs/88614"), "")
        self.assertEqual(UI.ats_org("https://taalas.com/position/digital-design-engineer/"), "")

    def test_ats_marketing_subdomains_are_not_orgs(self):
        self.assertEqual(UI.ats_org("https://www.applytojob.com/apply/x/y"), "")

    def test_garbage_input(self):
        for bad in ("", None, "not a url", "ftp://x.test/a"):
            with self.subTest(bad=bad):
                self.assertEqual(UI.ats_org(bad), "")


class SlugRoleTests(unittest.TestCase):
    def test_applytojob_listing_path_encodes_the_title(self):
        self.assertEqual(
            UI.slug_role("https://xanadu.applytojob.com/apply/AbC/Systems-Software-Engineer"),
            "Systems Software Engineer")

    def test_applytojob_confirm_path_has_no_title(self):
        self.assertEqual(
            UI.slug_role("https://xanadu.applytojob.com/apply/confirm/AbC"), "")

    def test_recruitee_slug(self):
        self.assertEqual(
            UI.slug_role("https://huaweicanada.recruitee.com/o/embedded-linux-developer"),
            "Embedded linux developer")

    def test_opaque_ids_are_not_titles(self):
        for url in (
            "https://job-boards.greenhouse.io/acme/jobs/4567",
            "https://jobs.ashbyhq.com/cohere/1234567890abcdef-aaaa",
            "https://www.linkedin.com/jobs/view/4438904097/",
            "https://job-boards.greenhouse.io/a/job_application_requests/t3b9vphmocbvtm4c7u80wwh9ztk3q1us",
        ):
            with self.subTest(url=url):
                self.assertEqual(UI.slug_role(url), "")


class SlugToWordsTests(unittest.TestCase):
    def test_words_are_split_and_case_preserved(self):
        self.assertEqual(UI.slug_to_words("Systems-Software-Engineer"),
                         "Systems Software Engineer")

    def test_only_the_first_character_is_lifted(self):
        # word-level re-casing would invent things ("For", "Ai"), so it isn't attempted
        self.assertEqual(UI.slug_to_words("computer-network-for-ai-research"),
                         "Computer network for ai research")

    def test_ids_and_hashes_are_rejected(self):
        for bad in ("4567", "t3b9vphmocbvtm4c7u80wwh9ztk3q1us", "abcdef0123456789",
                    "", "jobs", "confirm", "apply"):
            with self.subTest(bad=bad):
                self.assertEqual(UI.slug_to_words(bad), "")

    def test_a_real_single_word_is_kept(self):
        self.assertEqual(UI.slug_to_words("engineer"), "Engineer")


class IdentifyTests(unittest.TestCase):
    def test_complete_only_when_both_fields_come_from_structure(self):
        i = UI.identify("https://xanadu.applytojob.com/apply/AbC/Systems-Software-Engineer")
        self.assertTrue(i.complete)
        self.assertEqual((i.company, i.role), ("Xanadu", "Systems Software Engineer"))

    def test_company_without_role_is_incomplete(self):
        i = UI.identify("https://job-boards.greenhouse.io/doordashcanada/jobs/4567")
        self.assertFalse(i.complete)
        self.assertEqual(i.company, "Doordashcanada")
        self.assertEqual(i.missing(), ["role"])

    def test_nothing_derivable_lists_both_as_missing(self):
        i = UI.identify("https://careers.amd.com/careers-home/jobs/88614")
        self.assertFalse(i.complete)
        self.assertEqual(sorted(i.missing()), ["company", "role"])

    def test_raw_title_is_stored_verbatim_and_never_parsed(self):
        title = "AI and Automation Software Engineer in MARKHAM, Canada - Advanced Micro Devices, Inc"
        i = UI.identify("https://careers.amd.com/careers-home/jobs/88614", raw_title=title)
        self.assertEqual(i.raw_title, title)
        # crucially: none of it leaked into company/role
        self.assertEqual(i.company, "")
        self.assertEqual(i.role, "")

    def test_a_misleading_title_cannot_produce_a_role(self):
        # the greenhouse confirmation page's title looks plausible and means nothing
        i = UI.identify("https://job-boards.greenhouse.io/a/job_application_requests/xyz123",
                        raw_title="Thank you for applying")
        self.assertEqual(i.role, "")

    def test_company_from_org_only_adjusts_casing(self):
        # mapping a slug to a brand name ("doordashcanada" -> "DoorDash Canada") is judgment
        self.assertEqual(UI.company_from_org("doordashcanada"), "Doordashcanada")
        self.assertEqual(UI.company_from_org("advanced-micro-devices"),
                         "Advanced Micro Devices")
        self.assertEqual(UI.company_from_org(""), "")

    def test_source_records_which_rule_fired(self):
        i = UI.identify("https://xanadu.applytojob.com/apply/AbC/Some-Role")
        self.assertEqual(i.source, "applytojob-slug")

    def test_no_network_or_title_fetching_exists(self):
        """Guard against reintroducing page-title parsing, which was tried and rejected.

        Bans network-capable imports specifically. `urllib.parse` is fine — that's URL
        decoding, which is exactly this module's job; `urllib.request` is not.
        """
        src = (Path(__file__).resolve().parents[1] / "lib" / "url_identity.py").read_text(
            encoding="utf-8")
        for banned in ("urllib.request", "urllib.error", "import requests",
                       "http.client", "import socket"):
            self.assertNotIn(banned, src,
                             f"url_identity must stay offline and structure-only ({banned})")


if __name__ == "__main__":
    unittest.main()
