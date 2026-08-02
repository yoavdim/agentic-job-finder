import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("parse_tracker", SCRIPT_DIR / "parse_tracker.py")
parse_tracker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = parse_tracker
SPEC.loader.exec_module(parse_tracker)


APPLIED = """# Applied

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|

## Saved

| Saved | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-28 | Kepler Communications | Embedded Software Designer | Embedded Software Designer | Toronto | [Apply](https://jobs.lever.co/kepler/example) | verify level |
"""

SHORTLIST = """# Shortlist

## Tier 1 — Best fit

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|

## Referral leads

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
"""


def saved_record():
    return {
        "title": "Embedded Software Designer",
        "company": "Kepler Communications",
        "company_raw": "Kepler Communications",
        "location": "Toronto, ON, Canada",
        "saved": "7/28/26",
        "applied": "",
    }


def metadata():
    return {
        parse_tracker.norm_key("Kepler Communications", "Embedded Software Designer"): {
            "id": "4d8e6472-779d-4c7f-84fd-e4eaf6d8340c",
            "url": "https://jobs.lever.co/kepler/accae2f6-7ffe-49a8-a485-ee9c13421c95",
        }
    }


class SchemaMigrationTests(unittest.TestCase):
    def test_fmt_simplify_and_track(self):
        self.assertEqual(parse_tracker.fmt_simplify("abc"),
                         "[Simplify](https://simplify.jobs/tracker?id=abc)")
        self.assertEqual(parse_tracker.fmt_simplify(""), "")
        self.assertEqual(parse_tracker.fmt_track("https://x.test/j", "abc"),
                         "[Apply](https://x.test/j) · [Simplify](https://simplify.jobs/tracker?id=abc)")
        self.assertEqual(parse_tracker.fmt_track("https://x.test/j", ""),
                         "[Apply](https://x.test/j)")
        self.assertEqual(parse_tracker.fmt_track("", "abc"),
                         "[Simplify](https://simplify.jobs/tracker?id=abc)")
        self.assertEqual(parse_tracker.fmt_track("", ""), "")

    def test_migrate_schema_adds_simplify_and_status_without_renaming_apply(self):
        text = "\n".join(parse_tracker.migrate_schema(APPLIED.split("\n")))
        # the Applied header keeps its 'Apply' name
        self.assertIn("| Applied | Company | Role | Raw | Location | Apply | Comment |", text)
        # the Saved table gains Simplify + Status, both before Comment
        self.assertIn("| Saved | Company | Role | Raw | Location | Apply | Simplify | Status | Comment |", text)
        # existing Saved row keeps its data; the two new cells are empty
        self.assertIn(
            "| 2026-07-28 | Kepler Communications | Embedded Software Designer | Embedded Software Designer "
            "| Toronto | [Apply](https://jobs.lever.co/kepler/example) |  |  | verify level |", text)

    def test_migrate_schema_leaves_no_ragged_rows(self):
        lines = parse_tracker.migrate_schema(APPLIED.split("\n"))
        loc = parse_tracker.find_section(lines, "## Saved")
        _, sep, first, end = loc
        ncol = len(parse_tracker.split_md_row(lines[sep]))
        for i in range(first, end):
            cells = parse_tracker.split_md_row(lines[i])
            self.assertEqual(len(cells), ncol, f"row {i} is ragged: {lines[i]}")

    def test_migrate_schema_adds_status_to_an_already_simplify_migrated_table(self):
        # the real applied.md had already been migrated for Simplify before Status existed,
        # so the second column has to be addable on its own without duplicating the first
        once = parse_tracker.migrate_schema(APPLIED.split("\n"))
        lines = parse_tracker.migrate_schema(once)
        loc = parse_tracker.find_section(lines, "## Saved")
        header = parse_tracker.split_md_row(lines[loc[1] - 1])
        self.assertEqual(header.count("Status"), 1)
        self.assertEqual(header.count("Simplify"), 1)

    def test_migrate_schema_is_idempotent(self):
        once = "\n".join(parse_tracker.migrate_schema(APPLIED.split("\n")))
        twice = "\n".join(parse_tracker.migrate_schema(once.split("\n")))
        self.assertEqual(once, twice)


MIGRATED_DOC = """# Applied

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|

## Saved

| Saved | Company | Role | Raw | Location | Apply | Simplify | Comment |
|---|---|---|---|---|---|---|---|
| 2026-07-28 | Kepler Communications | Embedded Software Designer | Embedded Software Designer | Toronto | [Apply](https://jobs.lever.co/kepler/old) | [Simplify](https://simplify.jobs/tracker?id=4d8e6472-779d-4c7f-84fd-e4eaf6d8340c) | verify level |
"""


class MergeWithSimplifyTests(unittest.TestCase):
    def test_merge_new_rows_carry_simplify_id(self):
        recs = [
            {"title": "Firmware Engineer", "company": "Acme", "location": "Toronto",
             "saved": "7/28/26", "applied": ""},
            {"title": "Kepler Embedded", "company": "Kepler Communications", "location": "Toronto",
             "saved": "7/15/26", "applied": "7/20/26"},
        ]
        metadata = {
            parse_tracker.norm_key("Acme", "Firmware Engineer"): {
                "id": "11111111-2222-3333-4444-555555555555",
                "url": "https://job-boards.greenhouse.io/acme/jobs/123",
            },
            parse_tracker.norm_key("Kepler Communications", "Kepler Embedded"): {
                "id": "4d8e6472-779d-4c7f-84fd-e4eaf6d8340c",
                "url": "https://jobs.lever.co/kepler/apply",
            },
        }
        warn = []
        out, stats = parse_tracker.merge(MIGRATED_DOC.split("\n"), recs, warn, metadata=metadata)
        text = "\n".join(out)
        # Saved row: Simplify link goes in the dedicated Simplify column
        self.assertIn(
            "| 2026-07-28 | Acme | Firmware Engineer | Firmware Engineer | Toronto "
            "| [Apply](https://job-boards.greenhouse.io/acme/jobs/123) "
            "| [Simplify](https://simplify.jobs/tracker?id=11111111-2222-3333-4444-555555555555) |  |",
            text)
        # Applied row: Apply + Simplify links share the Track cell
        self.assertIn(
            "| 2026-07-20 | Kepler Communications | Kepler Embedded | Kepler Embedded | Toronto "
            "| [Apply](https://jobs.lever.co/kepler/apply) · "
            "[Simplify](https://simplify.jobs/tracker?id=4d8e6472-779d-4c7f-84fd-e4eaf6d8340c) |  |",
            text)
        self.assertEqual(stats["id_linked"], 2)

    def test_promotion_carries_simplify_link_into_track_cell(self):
        recs = [{"title": "Embedded Software Designer", "company": "Kepler Communications",
                 "location": "Toronto", "saved": "7/28/26", "applied": "7/29/26"}]
        warn = []
        out, stats = parse_tracker.merge(MIGRATED_DOC.split("\n"), recs, warn)
        text = "\n".join(out)
        self.assertEqual(stats["promoted"], 1)
        self.assertIn(
            "| 2026-07-29 | Kepler Communications | Embedded Software Designer | Embedded Software Designer "
            "| Toronto | [Apply](https://jobs.lever.co/kepler/old) · "
            "[Simplify](https://simplify.jobs/tracker?id=4d8e6472-779d-4c7f-84fd-e4eaf6d8340c) "
            "| verify level |", text)
        saved_section = text.split("## Saved")[1]
        self.assertNotIn("simplify.jobs/tracker?id=", saved_section)

    def test_saved_mirror_keeps_existing_row_carrying_comment_url_id(self):
        recs = [{"title": "Embedded Software Designer", "company": "Kepler Communications",
                 "location": "Toronto", "saved": "7/28/26", "applied": ""}]
        warn = []
        out, stats = parse_tracker.merge(MIGRATED_DOC.split("\n"), recs, warn)
        text = "\n".join(out)
        self.assertEqual(stats["mirror_saved"], 1)
        self.assertEqual(stats["added_saved"], 0)
        # re-emitted verbatim: date, old Apply URL, Simplify id and comment survive
        self.assertIn(
            "| 2026-07-28 | Kepler Communications | Embedded Software Designer | Embedded Software Designer "
            "| Toronto | [Apply](https://jobs.lever.co/kepler/old) "
            "| [Simplify](https://simplify.jobs/tracker?id=4d8e6472-779d-4c7f-84fd-e4eaf6d8340c) "
            "| verify level |", text)

    def test_saved_mirror_drops_rows_no_longer_saved_on_simplify(self):
        warn = []
        out, stats = parse_tracker.merge(MIGRATED_DOC.split("\n"), [], warn)
        text = "\n".join(out)
        self.assertEqual(stats["mirror_saved"], 0)
        # the Kepler row was rejected/deleted on Simplify → gone from the Saved table
        saved_section = text.split("## Saved")[1]
        self.assertNotIn("Kepler Communications", saved_section)
        self.assertNotIn("simplify.jobs/tracker?id=", saved_section)
        # the Applied table is untouched (empty here, but must keep its header)
        self.assertIn("| Applied | Company | Role | Raw | Location | Apply | Comment |", text)

    def test_saved_mirror_rebuilds_but_applied_stays_append_only(self):
        doc = "# Applied\n\n## Applied\n\n| Applied | Company | Role | Raw | Location | Apply | Comment |\n"
        doc += "|---|---|---|---|---|---|---|\n"
        doc += "| 2026-07-20 | Kepler Communications | Kepler Embedded | Kepler Embedded | Toronto | "
        doc += "[Apply](https://jobs.lever.co/kepler/keep) · "
        doc += "[Simplify](https://simplify.jobs/tracker?id=4d8e6472-779d-4c7f-84fd-e4eaf6d8340c) | note |\n"
        doc += "\n## Saved (not yet applied)\n\n| Saved | Company | Role | Raw | Location | Apply | Simplify | Comment |\n"
        doc += "|---|---|---|---|---|---|---|---|\n"
        doc += "| 2026-06-01 | Old Co | Old Role | Old Role | Toronto | [Apply](https://jobs.lever.co/old) | | |\n"
        recs = [
            {"title": "Kepler Embedded", "company": "Kepler Communications", "location": "Toronto",
             "saved": "7/15/26", "applied": "7/20/26"},
            {"title": "New Saved Role", "company": "New Co", "location": "Toronto",
             "saved": "6/20/26", "applied": ""},
        ]
        warn = []
        out, stats = parse_tracker.merge(doc.split("\n"), recs, warn)
        text = "\n".join(out)
        # Applied row stays exactly as-is (never rebuilt/duplicated)
        self.assertIn("| 2026-07-20 | Kepler Communications | Kepler Embedded | Kepler Embedded | Toronto "
                      "| [Apply](https://jobs.lever.co/kepler/keep) · "
                      "[Simplify](https://simplify.jobs/tracker?id=4d8e6472-779d-4c7f-84fd-e4eaf6d8340c) | note |",
                      text)
        # Saved is rebuilt: Old Co dropped (no longer saved), New Co added
        saved_section = text.split("## Saved")[1]
        self.assertNotIn("Old Co", saved_section)
        self.assertIn("New Saved Role", saved_section)
        self.assertEqual(stats["added_saved"], 1)
        self.assertEqual(stats["added_applied"], 0)
        self.assertEqual(stats["mirror_saved"], 0)


TRACKER_TEXT = """Kepler Embedded
Kepler Communications
Toronto, ON, Canada
Saved
07/15/26
Applied
07/20/26
Status
Firmware Engineer
Acme
Toronto
Saved
07/28/26
Status
"""


class MainEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.applied = root / "applied.md"
        self.shortlist = root / "shortlist.md"
        self.tracker = root / "tracker.txt"
        self.urls = root / "urls.json"
        self.applied.write_text(
            "# Applied\n\n"
            "**Last synced from Simplify:** 2026-07-15 · 1 applied · 0 saved (not yet applied)\n\n"
            "## Applied\n\n| Applied | Company | Role | Raw | Location | Apply | Comment |\n"
            "|---|---|---|---|---|---|---|\n\n"
            "## Saved (not yet applied)\n\n| Saved | Company | Role | Raw | Location | Apply | Comment |\n"
            "|---|---|---|---|---|---|---|\n", encoding="utf-8")
        self.shortlist.write_text(
            "# Job Shortlist\n\n## Tier 1 — Best fit\n\n"
            "|  | Added | Company | Role | Location | Apply link | Notes | Comment |\n"
            "|---|---|---|---|---|---|---|---|\n\n"
            "## Referral leads\n\n"
            "|  | Added | Company | Role | Location | Apply link | Notes | Comment |\n"
            "|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
        self.tracker.write_text(TRACKER_TEXT, encoding="utf-8")
        self.urls.write_text(json.dumps({
            "Kepler Communications||Kepler Embedded": {
                "id": "4d8e6472-779d-4c7f-84fd-e4eaf6d8340c",
                "url": "https://jobs.lever.co/kepler/apply",
            },
            "Acme||Firmware Engineer": {
                "id": "11111111-2222-3333-4444-555555555555",
                "url": "https://job-boards.greenhouse.io/acme/jobs/123",
            },
        }), encoding="utf-8")

    def run_main(self, *extra):
        with mock.patch.object(sys, "argv", [
            "parse_tracker.py", str(self.tracker), "--md", str(self.applied),
            "--urls", str(self.urls), "--date", "2026-07-29", "--apply", *extra,
        ]):
            return parse_tracker.main()

    def test_merges_into_the_single_saved_table_and_never_touches_the_shortlist(self):
        before = self.shortlist.read_text(encoding="utf-8")
        self.run_main()
        a_text = self.applied.read_text(encoding="utf-8")
        # dict-shaped --urls drive the merge's Apply links, and the schema records the
        # Simplify id in applied.md
        self.assertIn("| Applied | Company | Role | Raw | Location | Apply | Comment |", a_text)
        self.assertIn(
            "[Apply](https://jobs.lever.co/kepler/apply) · "
            "[Simplify](https://simplify.jobs/tracker?id=4d8e6472-779d-4c7f-84fd-e4eaf6d8340c)",
            a_text)
        self.assertIn("[Apply](https://job-boards.greenhouse.io/acme/jobs/123)", a_text)
        self.assertIn("| Saved | Company | Role | Raw | Location | Apply | Simplify | Status | Comment |", a_text)
        self.assertIn(
            "[Simplify](https://simplify.jobs/tracker?id=11111111-2222-3333-4444-555555555555)",
            a_text)
        # the saved row lands ONLY in ## Saved — there is no second copy anywhere
        self.assertIn("Firmware Engineer", a_text)
        # shortlist.md is byte-identical: the sync has no business writing to the
        # user-curated tiers, and Tier 6 no longer exists
        self.assertEqual(self.shortlist.read_text(encoding="utf-8"), before)
        self.assertNotIn("Tier 6", self.shortlist.read_text(encoding="utf-8"))

    def test_migrate_schema_cli(self):
        with mock.patch.object(sys, "argv", ["parse_tracker.py", "--migrate-schema", "--md", str(self.applied)]):
            parse_tracker.main()
        a_text = self.applied.read_text(encoding="utf-8")
        self.assertIn("| Applied | Company | Role | Raw | Location | Apply | Comment |", a_text)
        self.assertIn("| Saved | Company | Role | Raw | Location | Apply | Simplify | Status | Comment |", a_text)

    def test_sync_is_idempotent(self):
        self.run_main()
        first = self.applied.read_text(encoding="utf-8")
        self.run_main()
        second = self.applied.read_text(encoding="utf-8")
        self.assertEqual(first.count("Firmware Engineer"), second.count("Firmware Engineer"))
        # the still-saved row survives the second mirror rebuild rather than being dropped
        self.assertIn("Firmware Engineer", second)

    def test_legacy_url_strings_still_work(self):
        self.urls.write_text(json.dumps({
            "Acme||Firmware Engineer": "https://job-boards.greenhouse.io/acme/jobs/123",
        }), encoding="utf-8")
        self.run_main()
        a_text = self.applied.read_text(encoding="utf-8")
        self.assertIn("[Apply](https://job-boards.greenhouse.io/acme/jobs/123)", a_text)

    def test_sync_no_longer_accepts_the_tier_six_flags(self):
        # the flags are gone, not silently ignored
        for flag in ("--sync-saved", "--shortlist"):
            with self.subTest(flag=flag):
                with mock.patch.object(sys, "argv", [
                        "parse_tracker.py", str(self.tracker), "--md", str(self.applied),
                        flag, str(self.shortlist)]):
                    with self.assertRaises(SystemExit):
                        parse_tracker.main()


# A shared corpus for the parity check below. Every shape the two implementations are
# expected to recognise belongs here, plus the fallback cases.
ATS_URL_CORPUS = [
    "https://job-boards.greenhouse.io/lyft/jobs/123",
    "https://job-boards.greenhouse.io/cohere/jobs/123",
    "https://boards.greenhouse.io/cerebrassystems/jobs/4567",
    "https://stripe.com/jobs/search?gh_jid=6543210",
    "https://xanadu.applytojob.com/apply/LxwHAxMdlE/Systems-Software-Engineer",
    "https://xanadu.applytojob.com/apply/confirm/LxwHAxMdlE",
    "https://jobs.ashbyhq.com/cohere/1234567890abcdef-aaaa",
    "https://huaweicanada.recruitee.com/o/embedded-dev",
    "https://jobs.lever.co/kepler/1234567890abcdef-bbbb",
    "https://www.linkedin.com/jobs/view/4438904097/",
    "https://builtintoronto.com/job/animation-tools/10173309",
    "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite?q=JR1998773",
    "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite?q=JR2001234",
    "https://acme.test/careers",
    "https://other.test/careers",
    "[Apply](https://job-boards.greenhouse.io/acme/jobs/999?ref=Simplify)",
    "",
]


class AtsCodeBehaviorTests(unittest.TestCase):
    """`parse_tracker.ats_code`/`clean_url`/`is_strong_code` ARE `md_tables`'s (a direct
    import, not a copy — see the import comment at the top of parse_tracker.py), so there
    is nothing left to keep in parity. This corpus is now a plain regression test over the
    ATS URL shapes the sync actually needs to dedup correctly."""

    def test_is_strong_code_is_true_only_for_recognised_ats_ids(self):
        for url in ATS_URL_CORPUS:
            with self.subTest(url=url):
                code = parse_tracker.ats_code(url)
                self.assertEqual(parse_tracker.is_strong_code(code), bool(code) and not code.startswith("http"))

    def test_the_corpus_has_no_unintended_collisions(self):
        seen = {}
        for url in ATS_URL_CORPUS:
            if not url:
                continue
            code = parse_tracker.ats_code(url)
            seen.setdefault(code, []).append(url)
        collisions = {c: u for c, u in seen.items() if len(u) > 1}
        # the ONLY permitted collision is the confirm-vs-listing pair for one job
        self.assertEqual(list(collisions), ["applytojob:xanadu:lxwhaxmdle"])


class MirrorGuardTests(unittest.TestCase):
    """The Saved table is rebuilt from the capture, so a truncated capture deletes rows."""

    def _saved_lines(self, n):
        rows = "\n".join(
            f"| 2026-07-2{i} | Co{i} | Role{i} | Role{i} | Toronto |  |  |  |" for i in range(n))
        return ("# Applied\n\n"
                "**Last synced from Simplify:** 2026-07-30 · 0 applied · 0 saved (not yet applied)\n\n"
                "## Applied\n\n"
                "| Applied | Company | Role | Raw | Location | Apply | Comment |\n"
                "|---|---|---|---|---|---|---|\n\n"
                "## Saved (not yet applied)\n\n"
                "| Saved | Company | Role | Raw | Location | Apply | Simplify | Comment |\n"
                "|---|---|---|---|---|---|---|---|\n"
                + rows + "\n").split("\n")

    def test_empty_capture_against_populated_saved_is_refused(self):
        errs = parse_tracker.mirror_guard_errors(self._saved_lines(4), [])
        self.assertTrue(errs)
        self.assertIn("delete all of them", " ".join(errs))

    def test_total_jobs_mismatch_is_refused(self):
        recs = [{"company": "Co0", "title": "Role0", "saved": "7/20/26", "applied": "",
                 "location": ""}]
        errs = parse_tracker.mirror_guard_errors(self._saved_lines(4), recs, total_jobs=40)
        self.assertTrue(any("truncated" in e for e in errs))

    def test_large_drop_fraction_is_refused(self):
        recs = [{"company": "Co0", "title": "Role0", "saved": "7/20/26", "applied": "",
                 "location": ""}]
        errs = parse_tracker.mirror_guard_errors(self._saved_lines(10), recs)
        self.assertTrue(any("would drop" in e for e in errs))

    def test_a_complete_capture_passes(self):
        recs = [{"company": f"Co{i}", "title": f"Role{i}", "saved": "7/20/26",
                 "applied": "", "location": ""} for i in range(4)]
        errs = parse_tracker.mirror_guard_errors(self._saved_lines(4), recs, total_jobs=4)
        self.assertEqual(errs, [])

    def test_no_existing_saved_rows_means_nothing_to_protect(self):
        recs = [{"company": "Co0", "title": "Role0", "saved": "7/20/26", "applied": "",
                 "location": ""}]
        self.assertEqual(parse_tracker.mirror_guard_errors(self._saved_lines(0), recs), [])


class UpdateSyncHeaderTests(unittest.TestCase):
    """update_sync_header must count actual ROWS, not `parse_existing`'s dedup-key map.

    Found live: a real applied.md with 129 Applied rows (several legitimate repeat
    applications sharing one company+title key, e.g. re-applying months apart, or
    Simplify's own real duplicate rows — kept as-is per playbook §5) had its header
    written as "111 applied" — len() of the dict, which collapses same-key rows to one
    entry, silently dropped every repeat from the count.
    """

    def _lines(self, n_applied, n_saved, repeat_key=False):
        if repeat_key:
            # every Applied row shares one company+title -> the OLD dict-based count
            # would have collapsed all of them to 1
            applied_rows = "\n".join(
                f"| 2026-07-2{i%9} | Acme | Role | Role | Toronto | [Apply](https://x.test/{i}) |  |"
                for i in range(n_applied))
        else:
            applied_rows = "\n".join(
                f"| 2026-07-2{i%9} | Co{i} | Role{i} | Role{i} | Toronto | [Apply](https://x.test/{i}) |  |"
                for i in range(n_applied))
        saved_rows = "\n".join(
            f"| 2026-07-2{i%9} | SCo{i} | SRole{i} | SRole{i} | Toronto | [Apply](https://x.test/s{i}) |  |"
            for i in range(n_saved))
        return ("# Applied\n\n"
                "**Last synced from Simplify:** 2026-07-30 · 0 applied · 0 saved (not yet applied)\n\n"
                "## Applied\n\n"
                "| Applied | Company | Role | Raw | Location | Apply | Comment |\n"
                "|---|---|---|---|---|---|---|\n"
                + applied_rows + "\n\n"
                "## Saved\n\n"
                "| Saved | Company | Role | Raw | Location | Apply | Comment |\n"
                "|---|---|---|---|---|---|---|\n"
                + saved_rows + "\n").split("\n")

    def test_counts_match_distinct_rows(self):
        lines = self._lines(5, 2)
        new_lines = parse_tracker.update_sync_header(lines, "2026-08-02")
        header = next(l for l in new_lines if l.startswith("**Last synced"))
        self.assertIn("5 applied", header)
        self.assertIn("2 saved", header)

    def test_repeated_company_title_rows_are_all_counted(self):
        # the exact regression: every row shares one (company, title) key
        lines = self._lines(6, 0, repeat_key=True)
        new_lines = parse_tracker.update_sync_header(lines, "2026-08-02")
        header = next(l for l in new_lines if l.startswith("**Last synced"))
        self.assertIn("6 applied", header, "count must be the row total, not the "
                      "distinct-key count (would wrongly read '1 applied')")

    def test_empty_tables_count_as_zero(self):
        lines = self._lines(0, 0)
        new_lines = parse_tracker.update_sync_header(lines, "2026-08-02")
        header = next(l for l in new_lines if l.startswith("**Last synced"))
        self.assertIn("0 applied", header)
        self.assertIn("0 saved", header)

    def test_date_is_updated(self):
        lines = self._lines(1, 1)
        new_lines = parse_tracker.update_sync_header(lines, "2099-01-01")
        header = next(l for l in new_lines if l.startswith("**Last synced"))
        self.assertIn("2099-01-01", header)

    def test_missing_header_line_is_a_noop(self):
        lines = ["# Applied", "", "## Applied", "", "| a |", "|---|"]
        self.assertEqual(parse_tracker.update_sync_header(lines, "2026-08-02"), lines)


if __name__ == "__main__":
    unittest.main()
