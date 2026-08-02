#!/usr/bin/env python3
"""Subprocess-level smoke tests: every script runs as a real CLI, on a real fixture pass.

WHY THESE EXIST
---------------
The suite had 244 passing unit tests while `parse_tracker.py --sync-saved` was a guaranteed
NameError, because the entry point sat above the functions it called. Nothing caught it:
the only CLI-ish test imported the module first, which binds every name and hides the
ordering entirely. In-process `main()` calls cannot see that class of bug.

So these tests shell out. They assert the boring properties that unit tests structurally
cannot:
  - the script is importable AND runnable as `python3 <script>`
  - `--help` works (argparse wiring is intact)
  - a dry run touches nothing on disk
  - `--apply` writes what the dry run promised
  - exit codes are meaningful

There is also a full stage 0a->2b pass over a fixture workspace, because the scripts hand
plans to each other (liveness_sweep builds a migrate_resolved-shaped dict) and the seams
are where the bugs live.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SKILL_SCRIPTS = SCRIPTS.parent / "skills" / "simplify-tracker-sync" / "scripts"

TODAY = "2026-08-01"

APPLIED = """# Applied / In-Motion Tracker

**Last synced from Simplify:** 2026-07-20 · 1 applied · 0 saved (not yet applied)

## Applied

| Applied | Company | Role | Raw | Location | Apply | Comment |
|---|---|---|---|---|---|---|
| 2026-07-18 | Tenstorrent | Kernel Engineer | Kernel Engineer | Toronto | [Apply](https://job-boards.greenhouse.io/tenstorrent/jobs/900) |  |

## Saved (not yet applied)

| Saved | Company | Role | Raw | Location | Apply | Simplify | Comment |
|---|---|---|---|---|---|---|---|

## Rejected

| Rejected | Company | Role | Raw | Location | Apply | Reason | Comment |
|---|---|---|---|---|---|---|---|
"""

SHORTLIST = """# Job Shortlist

**Last searched the web:** 2026-07-01

## Tier 1 — Best fit

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|
| [x] | 2026-07-25 | Xanadu | Systems Software Engineer | Toronto | [Apply](https://xanadu.applytojob.com/apply/AbC123/systems) | ✅ embedded | great chat |
| [nope] | 2026-07-26 | Sketchy Co | SWE | Toronto | [Apply](https://sketchy.test/j/1) | ⚠️ verify | sketchy-site — looks like a scraper farm |
| [ ] | 2026-07-28 | Live Co | Embedded Engineer | Toronto | [Apply](https://job-boards.greenhouse.io/liveco/jobs/500) | ✅ fit · 📅 posted 2026-07-27 |  |
| [ ] | 2026-01-05 | Ancient Co | Firmware Dev | Toronto | [Apply](https://job-boards.greenhouse.io/ancient/jobs/1) | ✅ fit · 📅 posted 2026-01-04 |  |
| | 2026-07-28 | Ghost Co | No Status Box | Toronto | [Apply](https://job-boards.greenhouse.io/ghost/jobs/9) | ✅ fit |  |

## Tier 2 — General SWE

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|

## Referral leads

|  | Added | Company | Role | Location | Apply link | Notes | Comment |
|---|---|---|---|---|---|---|---|

## Notes

## Excluded (and why)

- **Remote-only** (against in-office pref): Groq, Coinbase.
"""

THOUGHTS = "# Thoughts\n\nCapture buffer.\n"
MANUAL = ("# Manual URL additions\n\nInbox.\n\n## Entries\n\n"
          "| Added | URL | Status |\n|---|---|---|\n")


def run(args, cwd, expect_rc=None):
    """Run a CLI and return (rc, stdout, stderr)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable] + [str(a) for a in args], cwd=str(cwd),
                       capture_output=True, text=True, env=env, timeout=120)
    if expect_rc is not None and p.returncode != expect_rc:
        raise AssertionError(
            f"{args!r} exited {p.returncode}, expected {expect_rc}\n"
            f"--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}")
    return p.returncode, p.stdout, p.stderr


class WorkspaceCase(unittest.TestCase):
    """A throwaway workspace with the four data files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        (self.ws / "applied.md").write_text(APPLIED, encoding="utf-8")
        (self.ws / "shortlist.md").write_text(SHORTLIST, encoding="utf-8")
        (self.ws / "thoughts.md").write_text(THOUGHTS, encoding="utf-8")
        (self.ws / "manual.md").write_text(MANUAL, encoding="utf-8")

    def read(self, name):
        return (self.ws / name).read_text(encoding="utf-8")

    def snapshot(self):
        return {p.name: p.read_text(encoding="utf-8") for p in self.ws.glob("*.md")}


LIB = SCRIPTS / "lib"

ALL_SCRIPTS = [
    LIB / "md_tables.py",
    LIB / "jobdates.py",
    LIB / "reasons.py",
    LIB / "candidate_lint.py",
    SCRIPTS / "dedup_index.py",
    SCRIPTS / "shortlist_add.py",
    SCRIPTS / "crossref.py",
    SCRIPTS / "migrate_resolved.py",
    SCRIPTS / "liveness_sweep.py",
    SCRIPTS / "housekeeping.py",
    SCRIPTS / "ensure_data_files.py",
    SCRIPTS / "run_config_check.py",
    SKILL_SCRIPTS / "parse_tracker.py",
    SKILL_SCRIPTS / "simplify_actions.py",
]


class ImportAndHelpTests(unittest.TestCase):
    """Catches the class of bug unit tests cannot: a script that imports fine but crashes
    when RUN, e.g. an entry point placed above the functions it calls."""

    def test_every_script_imports_as_a_module(self):
        for path in ALL_SCRIPTS:
            with self.subTest(script=path.name):
                rc, _o, err = run(["-c", f"import importlib.util,sys;"
                                         f"spec=importlib.util.spec_from_file_location('m',r'{path}');"
                                         f"m=importlib.util.module_from_spec(spec);"
                                         f"sys.modules['m']=m;spec.loader.exec_module(m)"],
                                  cwd=SCRIPTS)
                self.assertEqual(rc, 0, f"{path.name} failed to import:\n{err}")

    def test_every_cli_script_answers_help(self):
        # --help exercises argparse construction and, crucially, runs main() far enough to
        # prove the entry point can resolve the names it references.
        cli = [p for p in ALL_SCRIPTS
               if p.name not in ("md_tables.py", "jobdates.py", "reasons.py",
                                 "candidate_lint.py", "simplify_actions.py")]
        for path in cli:
            with self.subTest(script=path.name):
                rc, out, err = run([path, "--help"], cwd=SCRIPTS)
                self.assertEqual(rc, 0, f"{path.name} --help failed:\n{err}")
                self.assertIn("usage", (out + err).lower())


class DryRunTests(WorkspaceCase):
    """Every mutator is dry-run by default and must not touch the disk."""

    def test_migrate_resolved_dry_run_writes_nothing(self):
        before = self.snapshot()
        run([SCRIPTS / "migrate_resolved.py", "--shortlist", "shortlist.md",
             "--applied", "applied.md", "--today", TODAY],
            cwd=self.ws, expect_rc=0)
        self.assertEqual(self.snapshot(), before)

    def test_crossref_dry_run_writes_nothing(self):
        before = self.snapshot()
        run([SCRIPTS / "crossref.py", "--shortlist", "shortlist.md",
             "--applied", "applied.md"], cwd=self.ws, expect_rc=0)
        self.assertEqual(self.snapshot(), before)

    def test_liveness_sweep_dry_run_writes_nothing(self):
        before = self.snapshot()
        run([SCRIPTS / "liveness_sweep.py", "--shortlist", "shortlist.md",
             "--applied", "applied.md", "--today", TODAY, "--age-only"],
            cwd=self.ws, expect_rc=0)
        self.assertEqual(self.snapshot(), before)

    def test_shortlist_add_dry_run_writes_nothing(self):
        (self.ws / "c.json").write_text(json.dumps([{
            "company": "Fresh Co", "role": "Embedded Engineer", "location": "Toronto",
            "url": "https://job-boards.greenhouse.io/fresh/jobs/1", "tier": 1,
            "notes": "✅ fit", "posted": "2 days ago",
            "evidence": "Responsibilities: C firmware. Requirements: 2-4 yrs, BSc."}]),
            encoding="utf-8")
        before = self.snapshot()
        run([SCRIPTS / "shortlist_add.py", "--shortlist", "shortlist.md",
             "--applied", "applied.md", "--candidates", "c.json", "--today", TODAY],
            cwd=self.ws, expect_rc=0)
        self.assertEqual(self.snapshot(), before)

    def test_ensure_data_files_dry_run_creates_nothing(self):
        with tempfile.TemporaryDirectory() as empty:
            run([SCRIPTS / "ensure_data_files.py"], cwd=empty, expect_rc=0)
            self.assertEqual(list(Path(empty).glob("*.md")), [])


class ParseTrackerCliTests(unittest.TestCase):
    """The regression suite for blocker 1: --sync-saved crashed with NameError AFTER
    applied.md had been written, leaving a half-applied run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name)
        (self.ws / "applied.md").write_text(APPLIED, encoding="utf-8")
        (self.ws / "shortlist.md").write_text(SHORTLIST, encoding="utf-8")
        (self.ws / "tracker.txt").write_text(
            "Firmware Engineer\nAcme\nToronto\nSaved\n7/28/26\nApplied\n"
            "Screen\nInterview\nOffer\nStatus\n", encoding="utf-8")

    def _args(self, *extra):
        return [SKILL_SCRIPTS / "parse_tracker.py", "tracker.txt", "--md", "applied.md",
                "--date", TODAY, *extra]

    def test_runs_as_a_cli(self):
        # in-process main() calls could not catch an entry point that references names
        # defined below it: importing binds every name and hides the ordering
        rc, _o, err = run(self._args(), cwd=self.ws)
        self.assertEqual(rc, 0, f"parse_tracker crashed:\n{err}")
        self.assertNotIn("NameError", err)
        self.assertNotIn("Traceback", err)

    def test_dry_run_leaves_both_files_untouched(self):
        before = {p.name: p.read_text(encoding="utf-8") for p in self.ws.glob("*.md")}
        run(self._args(), cwd=self.ws, expect_rc=0)
        after = {p.name: p.read_text(encoding="utf-8") for p in self.ws.glob("*.md")}
        self.assertEqual(after, before)

    def test_apply_writes_applied_and_leaves_the_shortlist_alone(self):
        before = (self.ws / "shortlist.md").read_text(encoding="utf-8")
        run(self._args("--apply"), cwd=self.ws, expect_rc=0)
        self.assertIn("Acme", (self.ws / "applied.md").read_text(encoding="utf-8"))
        # the sync owns ## Saved only; the user-curated tiers are never written
        self.assertEqual((self.ws / "shortlist.md").read_text(encoding="utf-8"), before)

    def test_truncated_capture_is_refused_with_exit_2(self):
        rc, _o, err = run(self._args("--apply", "--total-jobs", "40"), cwd=self.ws)
        self.assertEqual(rc, 2)
        self.assertIn("truncated", err)

    def test_force_overrides_the_guard(self):
        run(self._args("--apply", "--total-jobs", "40", "--force"), cwd=self.ws,
            expect_rc=0)

    def test_migrate_schema_is_idempotent_via_cli(self):
        for _ in range(2):
            run([SKILL_SCRIPTS / "parse_tracker.py", "--migrate-schema", "--md",
                 "applied.md"], cwd=self.ws, expect_rc=0)
        text = (self.ws / "applied.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("| Saved | Company | Role | Raw | Location | Apply | "
                                    "Simplify | Status | Comment |"), 1)


class MigrateResolvedCliTests(WorkspaceCase):
    """Regression for blocker 3: the [x] row's comment must reach applied.md."""

    def test_applied_row_comment_lands_on_the_right_row(self):
        run([SCRIPTS / "migrate_resolved.py", "--shortlist", "shortlist.md",
             "--applied", "applied.md", "--today", TODAY, "--apply"],
            cwd=self.ws, expect_rc=0)
        applied = self.read("applied.md")
        # Xanadu was [x] and not already in Applied -> new row carrying its comment
        xanadu = [l for l in applied.splitlines() if "Xanadu" in l]
        self.assertEqual(len(xanadu), 1, applied)
        self.assertIn("great chat", xanadu[0])
        # the pre-existing Tenstorrent row must be untouched
        tt = [l for l in applied.splitlines() if "Tenstorrent" in l]
        self.assertEqual(len(tt), 1)
        self.assertTrue(tt[0].rstrip().endswith("|  |"), tt[0])

    def test_nope_row_is_classified_and_migrated(self):
        run([SCRIPTS / "migrate_resolved.py", "--shortlist", "shortlist.md",
             "--applied", "applied.md", "--today", TODAY, "--apply"],
            cwd=self.ws, expect_rc=0)
        rejected = self.read("applied.md").split("## Rejected", 1)[1]
        self.assertIn("Sketchy Co", rejected)
        self.assertIn("sketchy-site", rejected)
        self.assertNotIn("Sketchy Co", self.read("shortlist.md"))

    def test_reports_no_simplify_work_at_all(self):
        # Simplify push-back moved to the skill's saved_sync; this script must not mention it
        _rc, _o, err = run([SCRIPTS / "migrate_resolved.py", "--shortlist", "shortlist.md",
                            "--applied", "applied.md", "--today", TODAY],
                           cwd=self.ws, expect_rc=0)
        self.assertNotIn("Simplify", err)

    def test_row_with_no_status_box_is_warned_about(self):
        _rc, _o, err = run([SCRIPTS / "migrate_resolved.py", "--shortlist", "shortlist.md",
                            "--applied", "applied.md", "--today", TODAY],
                           cwd=self.ws, expect_rc=0)
        self.assertIn("Ghost Co", err)
        self.assertIn("no status box", err)


class LivenessSweepCliTests(WorkspaceCase):
    def test_stale_row_is_cut_and_names_its_date_source(self):
        _rc, _o, err = run([SCRIPTS / "liveness_sweep.py", "--shortlist", "shortlist.md",
                            "--applied", "applied.md", "--today", TODAY, "--age-only"],
                           cwd=self.ws, expect_rc=0)
        self.assertIn("Ancient Co", err)
        self.assertIn("too-old", err)
        # the audit trail must say the date came from the LISTING, not from `Added`
        self.assertIn("posting dated 2026-01-04", err)

    def test_ghost_row_is_warned_not_silently_skipped(self):
        _rc, _o, err = run([SCRIPTS / "liveness_sweep.py", "--shortlist", "shortlist.md",
                            "--applied", "applied.md", "--today", TODAY, "--age-only"],
                           cwd=self.ws, expect_rc=0)
        self.assertIn("no status box", err)

    def test_json_report_has_results_and_anomalies(self):
        run([SCRIPTS / "liveness_sweep.py", "--shortlist", "shortlist.md",
             "--applied", "applied.md", "--today", TODAY, "--age-only",
             "--json", "out.json"], cwd=self.ws, expect_rc=0)
        data = json.loads(self.read("out.json"))
        self.assertIn("results", data)
        self.assertIn("anomalies", data)


class FullPassTests(WorkspaceCase):
    """Stage 0a -> 2b over a fixture workspace. The scripts hand plans to each other, so
    the seams need exercising as a sequence, not just individually."""

    def test_stage_0_through_2_runs_clean_and_is_idempotent(self):
        common = ["--shortlist", "shortlist.md", "--applied", "applied.md"]

        # 0: bootstrap must be a no-op on an existing workspace
        run([SCRIPTS / "ensure_data_files.py", "--apply"], cwd=self.ws, expect_rc=0)

        # config validity gates the whole pass
        run([SCRIPTS / "run_config_check.py", "--playbook",
             SCRIPTS.parent / "steering" / "search-playbook.md"], cwd=self.ws, expect_rc=0)

        # 0b: cross-reference, then 0d: migrate resolved rows
        run([SCRIPTS / "crossref.py", *common, "--apply"], cwd=self.ws, expect_rc=0)
        run([SCRIPTS / "migrate_resolved.py", *common, "--today", TODAY, "--apply"], cwd=self.ws, expect_rc=0)

        # 0e: liveness sweep
        run([SCRIPTS / "liveness_sweep.py", *common, "--today", TODAY, "--age-only",
             "--apply"], cwd=self.ws, expect_rc=0)

        # 1g: add a new find
        (self.ws / "c.json").write_text(json.dumps([{
            "company": "Fresh Co", "role": "Embedded Engineer", "location": "Toronto",
            "url": "https://job-boards.greenhouse.io/fresh/jobs/1", "tier": 1,
            "notes": "✅ fit", "posted": "2 days ago",
            "evidence": "Responsibilities: C firmware, RTOS. Requirements: 2-4 yrs, BSc."}]),
            encoding="utf-8")
        run([SCRIPTS / "shortlist_add.py", *common, "--candidates", "c.json",
             "--today", TODAY, "--apply"], cwd=self.ws, expect_rc=0)

        # 2b: header bumps
        run([SCRIPTS / "housekeeping.py", *common, "--date", TODAY,
             "--bump-searched", "--sync-header"], cwd=self.ws, expect_rc=0)

        shortlist, applied = self.read("shortlist.md"), self.read("applied.md")

        # resolved rows left the shortlist and landed in applied.md
        self.assertNotIn("Xanadu", shortlist)
        self.assertNotIn("Sketchy Co", shortlist)
        self.assertNotIn("Ancient Co", shortlist)
        self.assertIn("Xanadu", applied)
        self.assertIn("great chat", applied)
        self.assertIn("Sketchy Co", applied)
        # the still-live row and the new find remain
        self.assertIn("Live Co", shortlist)
        self.assertIn("Fresh Co", shortlist)
        # headers bumped
        self.assertIn(f"**Last searched the web:** {TODAY}", shortlist)
        self.assertIn(f"**Last synced from Simplify:** {TODAY}", applied)

        # every table still parses and no row is ragged
        rc, out, err = run(["-c",
                            "import sys;sys.path.insert(0,r'%s');import md_tables as M;"
                            "bad=[];"
                            "[bad.extend(t.ragged_rows()) for t in "
                            " M.parse_tables(M.read_lines('shortlist.md'))];"
                            "[bad.extend(t.ragged_rows()) for t in "
                            " M.parse_tables(M.read_lines('applied.md'))];"
                            "print('RAGGED', bad)" % LIB], cwd=self.ws)
        self.assertEqual(rc, 0, err)
        self.assertIn("RAGGED []", out)

        # re-running the whole pass changes nothing further
        before = self.snapshot()
        run([SCRIPTS / "crossref.py", *common, "--apply"], cwd=self.ws, expect_rc=0)
        run([SCRIPTS / "migrate_resolved.py", *common, "--today", TODAY, "--apply"], cwd=self.ws, expect_rc=0)
        run([SCRIPTS / "housekeeping.py", *common, "--date", TODAY,
             "--bump-searched", "--sync-header"], cwd=self.ws, expect_rc=0)
        self.assertEqual(self.snapshot(), before, "second pass was not idempotent")

    def test_a_fresh_workspace_bootstraps_and_accepts_a_row(self):
        with tempfile.TemporaryDirectory() as fresh:
            run([SCRIPTS / "ensure_data_files.py", "--apply", "--date", TODAY],
                cwd=fresh, expect_rc=0)
            for name in ("applied.md", "shortlist.md", "thoughts.md", "manual.md"):
                self.assertTrue((Path(fresh) / name).exists(), f"{name} not created")
            (Path(fresh) / "c.json").write_text(json.dumps([{
                "company": "Fresh Co", "role": "Embedded Engineer", "location": "Toronto",
                "url": "https://job-boards.greenhouse.io/fresh/jobs/1", "tier": 1,
                "notes": "✅ fit", "posted": "1 day ago",
                "evidence": "Responsibilities: C firmware. Requirements: 2 yrs, BSc."}]),
                encoding="utf-8")
            run([SCRIPTS / "shortlist_add.py", "--shortlist", "shortlist.md",
                 "--applied", "applied.md", "--candidates", "c.json", "--today", TODAY,
                 "--apply"], cwd=fresh, expect_rc=0)
            self.assertIn("Fresh Co",
                          (Path(fresh) / "shortlist.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
