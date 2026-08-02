#!/usr/bin/env python3
"""Tests for simplify_search.py — saved-search harvest over Tab Share.

Pure parsing/merging is unit-tested here; the Tab Share /eval calls are NOT
made (the mockable harness mirrors the live probes in the skill's SKILL.md).
"""
import datetime
import json
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import simplify_search


class TestParseCard(unittest.TestCase):
    def test_card_with_salary(self):
        text = ("Robinhood\nQuality Engineer - iOS/Android\nFull-Time\n"
                "CA$80.8k - CA$95k/yr\nToronto, ON, Canada\nIn Person")
        j = simplify_search.parse_card(text)
        self.assertEqual(j["company"], "Robinhood")
        self.assertEqual(j["title"], "Quality Engineer - iOS/Android")
        self.assertEqual(j["job_type"], "Full-Time")
        self.assertEqual(j["location"], "Toronto, ON, Canada")
        self.assertEqual(j["work_arrangement"], "In Person")
        self.assertEqual(j["id"], "")  # cards don't expose ids

    def test_card_without_salary(self):
        text = ("Tesla\nMechanical Design Engineer - Automated Test Equipment\n"
                "Full-Time\nPalo Alto, CA, USA + 3 more\nIn Person")
        j = simplify_search.parse_card(text)
        self.assertEqual(j["company"], "Tesla")
        self.assertEqual(j["location"], "Palo Alto, CA, USA + 3 more")
        self.assertEqual(j["work_arrangement"], "In Person")

    def test_card_too_short(self):
        self.assertIsNone(simplify_search.parse_card("Only one line"))
        self.assertIsNone(simplify_search.parse_card("Same\nSame"))

    def test_card_hybrid_arrangement(self):
        text = ("Spotify\nSoftware Engineer\nFull-Time\nCA$100k - CA$140k/yr\n"
                "Toronto, ON, Canada\nHybrid")
        j = simplify_search.parse_card(text)
        self.assertEqual(j["work_arrangement"], "Hybrid")


class TestParseMultiSearchDoc(unittest.TestCase):
    def test_doc_extracts_posting_id_and_first_location(self):
        doc = {
            "posting_id": "bce05e11-1835-4132-ba69-3e789872a0ca",
            "company_name": "Royal Bank of Canada",
            "title": "Banking Advisor",
            "locations": ["Toronto, ON, Canada", "Vaughan, ON, Canada"],
            "type": "Full-Time",
            "travel_requirements": "In Person",
            "experience_level": ["Entry Level/New Grad", "Internship"],
            "start_date": 1782936000,
        }
        j = simplify_search.parse_multi_search_doc(doc)
        self.assertEqual(j["id"], "bce05e11-1835-4132-ba69-3e789872a0ca")
        self.assertEqual(j["company"], "Royal Bank of Canada")
        self.assertEqual(j["location"], "Toronto, ON, Canada")
        self.assertEqual(j["experience"], "Entry Level/New Grad; Internship")
        self.assertEqual(j["posted"], simplify_search.epoch_to_iso(1782936000))

    def test_doc_missing_fields_defaults(self):
        j = simplify_search.parse_multi_search_doc({})
        self.assertEqual(j["id"], "")
        self.assertEqual(j["location"], "")
        self.assertEqual(j["experience"], "")
        self.assertEqual(j["posted"], "")

    def test_epoch_to_iso(self):
        self.assertEqual(simplify_search.epoch_to_iso(0), "1970-01-01")
        self.assertEqual(simplify_search.epoch_to_iso("garbage"), "")
        self.assertEqual(simplify_search.epoch_to_iso(None), "")
        self.assertEqual(simplify_search.epoch_to_iso(1778018869), "2026-05-05")

    def test_doc_non_dict_returns_none(self):
        # the /eval round-trip can occasionally yield a stray string element
        self.assertIsNone(simplify_search.parse_multi_search_doc("weird"))
        self.assertIsNone(simplify_search.parse_multi_search_doc(None))


class TestFindSavedQuery(unittest.TestCase):
    def test_finds_query_by_label(self):
        ls = json.dumps([{
            "userId": "u1",
            "searches": [{"id": "x", "label": "Other", "query": "state=A"},
                         {"id": "ab7c9cad", "label": "Toronto",
                          "query": "state=Toronto%2C+ON%2C+Canada&points=1"}],
        }])
        self.assertEqual(
            simplify_search.find_saved_query(ls, "Toronto"),
            "state=Toronto%2C+ON%2C+Canada&points=1")

    def test_label_missing_returns_none(self):
        ls = json.dumps([{"userId": "u1", "searches": [{"label": "A", "query": "q"}]}])
        self.assertIsNone(simplify_search.find_saved_query(ls, "B"))

    def test_malformed_ls_returns_none(self):
        self.assertIsNone(simplify_search.find_saved_query("not json", "Toronto"))
        self.assertIsNone(simplify_search.find_saved_query(None, "Toronto"))
        self.assertIsNone(simplify_search.find_saved_query(json.dumps({"a": 1}), "Toronto"))


class TestMergeCardsAndIds(unittest.TestCase):
    @staticmethod
    def _cards(titles, start=0):
        return [{"id": "", "company": "c%d" % (i + start), "title": t, "location": "Toronto",
                 "job_type": "Full-Time", "work_arrangement": "Hybrid", "experience": "",
                 "posted": ""}
                for i, t in enumerate(titles)]

    @staticmethod
    def _page1(n=24):
        return TestMergeCardsAndIds._cards(["P%d" % i for i in range(n)])

    @staticmethod
    def _hook(titles, start=0, experience=""):
        return [{"id": "id%d" % (i + start), "title": t, "company": "", "location": "",
                 "job_type": "", "work_arrangement": "", "experience": experience}
                for i, t in enumerate(titles)]

    def test_ids_attached_by_position(self):
        cards = self._page1() + self._cards(["SWE", "Dev"], start=24)
        hooked = self._hook(["SWE", "Dev"], experience="Mid Level")
        out = simplify_search.merge_cards_and_ids(cards, hooked)
        self.assertEqual(out[24]["id"], "id0")        # aligned after page 1
        self.assertEqual(out[25]["id"], "id1")
        self.assertEqual(out[24]["experience"], "Mid Level")  # hook enriches
        self.assertEqual(out[24]["company"], "c24")            # card fields kept
        self.assertEqual(out[0]["id"], "")                     # page 1: no ids

    def test_posted_date_carries_from_hook_to_card(self):
        cards = self._page1() + self._cards(["SWE"], start=24)
        hooked = self._hook(["SWE"])
        hooked[0]["posted"] = "2026-07-29"
        out = simplify_search.merge_cards_and_ids(cards, hooked)
        self.assertEqual(out[24]["posted"], "2026-07-29")
        self.assertEqual(out[0]["posted"], "")        # page 1 has no date

    def test_sparse_hook_doc_still_supplies_id(self):
        cards = self._page1() + self._cards(["A PM", "Semperis PM"], start=24)
        hooked = self._hook(["A PM", "Semperis PM"])
        out = simplify_search.merge_cards_and_ids(cards, hooked)
        self.assertEqual(out[0]["id"], "")            # page-1 card: empty id
        self.assertEqual(out[25]["id"], "id1")        # sparse doc matched by position
        self.assertEqual(out[25]["company"], "c25")   # card fields survive

    def test_duplicate_titles_do_not_cross(self):
        cards = self._page1() + self._cards(["Tesla SWE", "A SWE", "B SWE"], start=24)
        hooked = self._hook(["Tesla SWE", "A SWE", "B SWE"])
        out = simplify_search.merge_cards_and_ids(cards, hooked)
        # ids stay positional even though all three titles are equal
        self.assertEqual([j["id"] for j in out[24:]], ["id0", "id1", "id2"])

    def test_best_offset_finds_page1_boundary(self):
        cards = self._page1() + self._cards(["dup", "sw", "pm"], start=24)
        hooked = self._hook(["dup", "sw", "pm"])
        self.assertEqual(simplify_search.best_offset(cards, hooked, 16, 40), 24)
        out = simplify_search.merge_cards_and_ids(cards, hooked)
        self.assertEqual([j["id"] for j in out if j["id"]], ["id0", "id1", "id2"])

    def test_positional_match_respects_used(self):
        # Two adjacent cards with the same title but different postings.
        # Card A misses its positional partner and consumes the first hooked
        # doc by fallback; card B's own positional partner must NOT be reused.
        cards = self._page1() + self._cards(["MLE-OT", "MLE-OT", "Other"], start=24)
        hooked = self._hook(["P19", "P20", "P21"]) + self._hook(["MLE-OT", "MLE-OT", "Other"], start=3)
        # cards[24] positional partner hooked[0] ("P19") title-mismatches, so it
        # falls back to the first unused "MLE-OT" = hooked[3] (id id3). cards[25]
        # positional partner = hooked[4] (id id4, same title) — must be used.
        out = simplify_search.merge_cards_and_ids(cards, hooked)
        self.assertEqual(out[24]["id"], "id3")
        self.assertEqual(out[25]["id"], "id4")
        self.assertNotEqual(out[24]["id"], out[25]["id"])

    def test_unmatched_hooked_docs_appended(self):
        cards = self._page1() + self._cards(["A X"], start=24)
        hooked = self._hook(["A X"]) + self._hook(["Orphan"], start=1)
        out = simplify_search.merge_cards_and_ids(cards, hooked)
        self.assertEqual(out[24]["id"], "id0")        # aligned
        self.assertEqual(out[25]["id"], "id1")        # orphan appended with its id


class TestFilterByAge(unittest.TestCase):
    @staticmethod
    def _jobs(posted_list):
        return [{"id": "id%d" % i, "company": "", "title": "t%d" % i,
                 "location": "", "job_type": "", "work_arrangement": "",
                 "experience": "", "posted": p}
                for i, p in enumerate(posted_list)]

    def test_drops_only_definitively_old(self):
        jobs = self._jobs(["2026-07-30", "2026-07-01", "2025-01-01", "", None])
        kept, dropped, nodate = simplify_search.filter_by_age(
            jobs, 7, today=datetime.date(2026, 7, 31))
        self.assertEqual([j["posted"] for j in kept],
                         ["2026-07-30", "", None])
        self.assertEqual([j["posted"] for j in dropped],
                         ["2026-07-01", "2025-01-01"])
        self.assertEqual(nodate, 2)

    def test_no_max_age_returns_all(self):
        jobs = self._jobs(["2025-01-01", ""])
        kept, dropped, nodate = simplify_search.filter_by_age(jobs, None)
        self.assertEqual(kept, jobs)
        self.assertEqual(dropped, [])
        self.assertEqual(nodate, 0)

    def test_cutoff_inclusive(self):
        jobs = self._jobs(["2026-07-24", "2026-07-25"])
        kept, _, _ = simplify_search.filter_by_age(
            jobs, 7, today=datetime.date(2026, 7, 31))
        # cutoff is 2026-07-24; the job posted exactly on it is kept
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
