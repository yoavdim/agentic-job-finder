#!/usr/bin/env python3
"""Tests for md_tables.py — the shared table-surgery layer."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import md_tables as M


SHORTLIST = """# Job Shortlist

**Last searched the web:** 2026-07-22

## Tier 1 — Best fit (Toronto, junior)

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [ ] | 2026-07-15 | DoorDash | Software Engineer, Backend | Toronto | [Apply](https://www.linkedin.com/jobs/view/4438904097/) | ✅ backend · 📅 posted 2026-07-15 |  |
| [x] | 2026-07-01 | Acme | Embedded Dev | Markham | [Apply](https://job-boards.greenhouse.io/acme/jobs/123) | ✅ embedded | applied last week |

## Tier 4 — Markham

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [nope] | 2026-06-22 | Huawei Canada | Compiler Engineer | Markham | [Apply](https://huaweicanada.recruitee.com/o/compiler-engineer-2-16) | ✅ compiler | not-interested — too far |

## Notes
Prose section, no table.
"""

APPLIED = """# Applied

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-15 | Tenstorrent | Software Engineer, TT-Fabric | Software Engineer, TT-Fabric | Toronto | [Apply](https://www.linkedin.com/jobs/view/4439917871/) |  |

## Rejected (migrated from shortlist `[nope]` rows)

| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |
|---|---|---|---|---|---|---|---|
"""


class ParseTests(unittest.TestCase):
    def test_finds_every_table_with_its_heading(self):
        tables = M.parse_tables(SHORTLIST.split("\n"))
        self.assertEqual(len(tables), 2)
        self.assertTrue(tables[0].heading.startswith("Tier 1"))
        self.assertTrue(tables[1].heading.startswith("Tier 4"))

    def test_prose_section_is_not_parsed_as_a_table(self):
        headings = [t.heading for t in M.parse_tables(SHORTLIST.split("\n"))]
        self.assertNotIn("Notes", headings)

    def test_find_tables_by_regex(self):
        tiers = M.find_tables(SHORTLIST.split("\n"), r"Tier \d+")
        self.assertEqual(len(tiers), 2)

    def test_resolves_columns_by_name_not_index(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        # shortlist: blank status col at 0, so company is 2 (NOT 1 as in applied.md)
        self.assertEqual(t.col("status"), 0)
        self.assertEqual(t.col("date"), 1)
        self.assertEqual(t.col("company"), 2)
        self.assertEqual(t.col("role"), 3)
        self.assertEqual(t.col("apply"), 5)      # header is "Apply link"
        self.assertEqual(t.col("notes"), 6)
        self.assertIsNone(t.col("raw"))          # shortlist has no Raw column

    def test_applied_table_has_a_different_shape(self):
        t = M.find_table(APPLIED.split("\n"), "## Applied")
        self.assertEqual(t.col("date"), 0)       # date at 0, no status box
        self.assertEqual(t.col("company"), 1)
        self.assertEqual(t.col("raw"), 3)
        self.assertIsNone(t.col("status"))

    def test_row_get_reads_by_field_name(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        row = t.rows[0]
        self.assertEqual(row.get("company"), "DoorDash")
        self.assertEqual(row.get("role"), "Software Engineer, Backend")
        self.assertEqual(row.get("date"), "2026-07-15")
        self.assertEqual(row.get("raw"), "")     # absent column -> default

    def test_build_drops_fields_the_table_lacks(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        cells = t.build(status="[ ]", date="2026-07-29", company="Foo", role="Bar",
                        raw="ignored — no Raw column here", location="Toronto",
                        apply="[Apply](https://x.test/1)", notes="✅", comment="")
        self.assertEqual(len(cells), 8)
        self.assertEqual(cells[0], "[ ]")
        self.assertEqual(cells[2], "Foo")
        self.assertNotIn("ignored", " ".join(cells))

    def test_build_preserves_apply_markdown(self):
        t = M.find_table(APPLIED.split("\n"), "## Applied")
        cells = t.build(apply="[Apply](https://x.test/1)")
        self.assertIn("[Apply](https://x.test/1)", cells)


class StatusBoxTests(unittest.TestCase):
    def test_recognizes_the_three_states(self):
        self.assertTrue(M.is_open("[ ]"))
        self.assertTrue(M.is_applied("[x]"))
        self.assertTrue(M.is_applied("[X]"))
        self.assertTrue(M.is_rejected("[nope]"))
        self.assertFalse(M.is_open("[x]"))
        self.assertFalse(M.is_open("[nope]"))


class UrlTests(unittest.TestCase):
    def test_extracts_url_from_markdown_cell(self):
        self.assertEqual(M.extract_url("[Apply](https://x.test/a?ref=1)"),
                         "https://x.test/a?ref=1")

    def test_clean_url_strips_query_and_trailing_slash(self):
        self.assertEqual(M.clean_url("[Apply](https://x.test/a/?ref=Simplify)"),
                         "https://x.test/a")

    def test_ats_code_survives_confirm_vs_listing_paths(self):
        listing = "https://xanadu.applytojob.com/apply/LxwHAxMdlE/Systems-Software-Engineer"
        confirm = "https://xanadu.applytojob.com/apply/confirm/LxwHAxMdlE"
        self.assertEqual(M.ats_code(listing), M.ats_code(confirm))

    def test_ats_code_handles_greenhouse_linkedin_builtin(self):
        self.assertEqual(M.ats_code("https://job-boards.greenhouse.io/acme/jobs/123"),
                         "greenhouse:acme:123")
        self.assertEqual(M.ats_code("https://www.linkedin.com/jobs/view/4438904097/"),
                         "linkedin:4438904097")
        self.assertEqual(
            M.ats_code("https://builtintoronto.com/job/animation-tools/10173309"),
            "builtin:10173309")

    def test_ats_code_namespaces_by_org_so_ids_cannot_collide(self):
        # greenhouse ids are only unique within an org
        self.assertNotEqual(M.ats_code("https://job-boards.greenhouse.io/lyft/jobs/123"),
                            M.ats_code("https://job-boards.greenhouse.io/cohere/jobs/123"))

    def test_workday_code_uses_the_requisition_id_not_the_site_name(self):
        a = M.ats_code("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite?q=JR1998773")
        b = M.ats_code("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite?q=JR2001234")
        # the req id lives in the query string; stripping it collapsed every NVIDIA role
        # onto the shared site name
        self.assertNotEqual(a, b)
        self.assertIn("jr1998773", a)

    def test_bare_careers_url_is_a_weak_code(self):
        # the playbook links search-only roles as [Careers site](url), so several distinct
        # roles can share one URL — it must not be usable as an identity key
        code = M.ats_code("https://acme.test/careers")
        self.assertFalse(M.is_strong_code(code))
        self.assertTrue(M.is_strong_code(M.ats_code("https://job-boards.greenhouse.io/a/jobs/1")))

    def test_row_keys_yields_company_title_and_code(self):
        t = M.find_tables(SHORTLIST.split("\n"), r"Tier 1")[0]
        keys, code = M.row_keys(t.rows[1])
        self.assertIn(("acme", "embedded dev"), keys)
        self.assertEqual(code, "greenhouse:acme:123")

    def test_row_keys_drops_a_weak_code(self):
        lines = [
            "## Tier 9 — x", "",
            "|  | Added | Company | Role | Location | Apply link | Notes | Comment |",
            "|---|---|---|---|---|---|---|---|",
            "| [ ] | 2026-07-01 | Acme | Role A | Toronto | [Careers site](https://acme.test/careers) |  |  |",
        ]
        t = M.find_tables(lines, r"Tier 9")[0]
        keys, code = M.row_keys(t.rows[0])
        self.assertEqual(code, "")
        self.assertIn(("acme", "role a"), keys)


class EscapedPipeTests(unittest.TestCase):
    """A literal pipe in a cell (multi-location jobs) must survive read/write intact.

    Writers escape `|` -> `\\|`; the reader used to split on every `|`, tearing those rows
    into extra columns and shifting every following cell. Real data surfaced it: a job
    located "Toronto, ON | Vancouver, BC".
    """

    def test_split_respects_escaped_pipe(self):
        cells = M.split_cells(r"| a | Toronto \| Vancouver | c |")
        self.assertEqual(len(cells), 3)
        self.assertEqual(cells[1], r"Toronto \| Vancouver")

    def test_esc_escapes_a_literal_pipe(self):
        self.assertEqual(M.esc("Toronto | Vancouver"), r"Toronto \| Vancouver")

    def test_esc_is_idempotent_on_the_pipe(self):
        once = M.esc("Toronto | Vancouver")
        self.assertEqual(M.esc(once), once, "re-escaping must not double to \\\\|")

    def test_a_multi_location_row_round_trips(self):
        lines = [
            "## Saved", "",
            "| Saved | Company | Role | Raw | Location | Apply | Simplify | Status | Comment |",
            "|---|---|---|---|---|---|---|---|---|",
            r"| 2026-07-27 | Motorola | SWE | SWE | Toronto, ON \| Vancouver, BC | "
            r"[Apply](https://x.test/1) |  |  |  |",
        ]
        t = M.find_table(lines, "## Saved")
        self.assertEqual(t.ragged_rows(), [], "escaped-pipe row must not read as ragged")
        row = t.rows[0]
        self.assertEqual(row.get("location"), r"Toronto, ON \| Vancouver, BC")
        # re-render is byte-stable
        self.assertEqual(row.render(), lines[4])

    def test_build_escapes_a_pipe_in_a_field(self):
        lines = ["## X", "",
                 "|  | Added | Company | Role | Location | Apply link | Notes | Comment |",
                 "|---|---|---|---|---|---|---|---|"]
        t = M.parse_tables(lines)[0]
        cells = t.build(company="A", role="R", location="Toronto | Remote")
        self.assertIn(r"Toronto \| Remote", cells)


class BackupTests(unittest.TestCase):
    """The data files are gitignored, so these snapshots are the only undo for --apply."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.f = self.root / "applied.md"
        self.bdir = self.root / "backups"

    def _snaps(self):
        return sorted(self.bdir.glob("applied.md.*.bak"))

    def _contents(self):
        return [p.read_text(encoding="utf-8").strip() for p in self._snaps()]

    def test_write_lines_snapshots_the_previous_contents(self):
        self.f.write_text("before\n", encoding="utf-8")
        M.write_lines(str(self.f), ["after"], backup_dir=str(self.bdir))
        self.assertEqual(self.f.read_text(encoding="utf-8").strip(), "after")
        self.assertEqual(self._contents(), ["before"])

    def test_backup_can_be_skipped(self):
        self.f.write_text("before\n", encoding="utf-8")
        M.write_lines(str(self.f), ["after"], backup=False, backup_dir=str(self.bdir))
        self.assertFalse(self.bdir.exists())

    def test_absent_file_is_not_backed_up(self):
        self.assertEqual(M.backup_file(str(self.root / "nope.md"),
                                      backup_dir=str(self.bdir)), "")

    def test_same_second_writes_do_not_overwrite_each_other(self):
        # a fixed stamp forces the collision that real sub-second writes produce
        self.f.write_text("one\n", encoding="utf-8")
        M.backup_file(str(self.f), backup_dir=str(self.bdir), stamp="20260801T000000Z")
        self.f.write_text("two\n", encoding="utf-8")
        M.backup_file(str(self.f), backup_dir=str(self.bdir), stamp="20260801T000000Z")
        self.assertEqual(self._contents(), ["one", "two"])

    def test_snapshots_sort_chronologically(self):
        # names must sort chronologically because the prune relies on that order;
        # a bare "-10"/"-2" suffix would sort wrongly and delete the newest
        for i in range(12):
            self.f.write_text(f"v{i}\n", encoding="utf-8")
            M.backup_file(str(self.f), backup_dir=str(self.bdir), stamp="20260801T000000Z")
        self.assertEqual(self._contents(), [f"v{i}" for i in range(12)])

    def test_prune_keeps_the_newest_and_does_not_recycle_slots(self):
        # allocating the first FREE slot would reuse numbers the prune just freed, so
        # snapshots would cycle and overwrite; allocation must be monotonic
        for i in range(30):
            self.f.write_text(f"v{i}\n", encoding="utf-8")
            M.backup_file(str(self.f), backup_dir=str(self.bdir), keep=5,
                          stamp="20260801T000000Z")
        self.assertEqual(len(self._snaps()), 5)
        self.assertEqual(self._contents(), [f"v{i}" for i in range(25, 30)])

    def test_keep_zero_disables_pruning(self):
        for i in range(7):
            self.f.write_text(f"v{i}\n", encoding="utf-8")
            M.backup_file(str(self.f), backup_dir=str(self.bdir), keep=0,
                          stamp="20260801T000000Z")
        self.assertEqual(len(self._snaps()), 7)

    def test_backups_of_different_files_are_pruned_independently(self):
        other = self.root / "shortlist.md"
        for i in range(8):
            self.f.write_text(f"a{i}\n", encoding="utf-8")
            other.write_text(f"b{i}\n", encoding="utf-8")
            M.backup_file(str(self.f), backup_dir=str(self.bdir), keep=3,
                          stamp="20260801T000000Z")
            M.backup_file(str(other), backup_dir=str(self.bdir), keep=3,
                          stamp="20260801T000000Z")
        self.assertEqual(len(self._snaps()), 3)
        self.assertEqual(len(sorted(self.bdir.glob("shortlist.md.*.bak"))), 3)


class MutationTests(unittest.TestCase):
    def test_insert_rows_newest_first_lands_above_existing_data(self):
        lines = APPLIED.split("\n")
        t = M.find_table(lines, "## Applied")
        new = M.row_md(t.build(date="2026-07-29", company="Foo", role="Bar"))
        out = M.insert_rows(lines, "## Applied", [new], newest_first=True)
        t2 = M.find_table(out, "## Applied")
        self.assertEqual(t2.rows[0].get("company"), "Foo")
        self.assertEqual(t2.rows[1].get("company"), "Tenstorrent")

    def test_insert_into_empty_table_works(self):
        lines = APPLIED.split("\n")
        t = M.find_table(lines, "## Rejected")
        self.assertEqual(len(t.rows), 0)
        new = M.row_md(t.build(date="2026-07-29", company="Foo", role="Bar",
                               reason="listing-removed", comment="gone (auto)"))
        out = M.insert_rows(lines, "## Rejected", [new])
        t2 = M.find_table(out, "## Rejected")
        self.assertEqual(len(t2.rows), 1)
        self.assertEqual(t2.rows[0].get("reason"), "listing-removed")

    def test_delete_lines_removes_only_the_targets(self):
        lines = SHORTLIST.split("\n")
        t = M.find_tables(lines, r"Tier 1")[0]
        victim = t.rows[1].line_idx
        out = M.delete_lines(lines, [victim])
        self.assertEqual(len(out), len(lines) - 1)
        self.assertNotIn("Embedded Dev", "\n".join(out))
        self.assertIn("DoorDash", "\n".join(out))

    def test_untouched_content_is_byte_identical(self):
        lines = SHORTLIST.split("\n")
        t = M.find_tables(lines, r"Tier 1")[0]
        out = M.delete_lines(lines, [t.rows[1].line_idx])
        # prose + header lines survive verbatim
        self.assertIn("**Last searched the web:** 2026-07-22", out)
        self.assertIn("Prose section, no table.", out)

    def test_esc_neutralizes_pipes(self):
        self.assertEqual(M.esc("a | b"), "a \\| b")


if __name__ == "__main__":
    unittest.main()
