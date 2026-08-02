#!/usr/bin/env python3
"""Run the `no-llm-sweep` profile from run-config.md.

Stage list comes from run-config.md itself (via run_config_check.parse_deps), not a
hardcoded copy, so adding/removing a stage there is all that's needed here too.

APPLIES BY DEFAULT — this is the one script in the workspace that deviates from the
dry-run-by-default convention, on purpose: it exists so the fully-scripted stages (no LLM
judgment involved) stay current without a human remembering to add --apply every time,
which is what "drift" means here. Each stage script's own safety guards (mirror-drop
protection, max-auto-delete cap, the Simplify tracker-tab check) are unaffected — this only
removes the need to type --apply, not any of the checks that gate what it's allowed to do.

--dry-run is NOT a no-network preview. 0b (saved_sync_cli.py) makes a real, live GET
against api.simplify.jobs to capture the current tracker list UNCONDITIONALLY, before it
even checks --apply — that read has to happen either way to know what the plan even is.
--dry-run only withholds the WRITE side: pushing mark_applied/delete to Simplify's backend
and writing applied.md/shortlist.md locally. So on Simplify's side, dry-run means "no
mutation, but yes I did just read your tracker list" — not "nothing happened."

Usage:
    no_llm_sweep.py             # applies for real
    no_llm_sweep.py --dry-run   # preview only
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parent.parent
SKILL = WORKSPACE / ".kiro" / "skills" / "simplify-tracker-sync" / "scripts"
RC = HERE / "run_config_check.py"

# stage id -> (script, args). Run order = this dict's order (0b before 0d: 0d's dedup
# should see what 0b just did; 0e last since it deletes shortlist rows).
STAGE_SCRIPTS = {
    "0b": (SKILL / "saved_sync_cli.py", ["--applied", "applied.md"]),
    "0d": (HERE / "migrate_resolved.py", ["--shortlist", "shortlist.md", "--applied", "applied.md"]),
    "0e": (HERE / "liveness_sweep.py", ["--shortlist", "shortlist.md", "--applied", "applied.md"]),
}

sys.path.insert(0, str(HERE))
import run_config_check as RCC


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    apply_ = "--dry-run" not in args  # default ON; each stage's own guards still apply
    cfg = (WORKSPACE / ".kiro" / "steering" / "run-config.md").read_text(encoding="utf-8")
    members = RCC.parse_deps(cfg).get("profiles", {}).get("no-llm-sweep", [])
    unknown = [s for s in members if s not in STAGE_SCRIPTS]
    if unknown:
        print(f"no_llm_sweep.py doesn't know how to run stage(s): {unknown} "
              f"(add to STAGE_SCRIPTS)", file=sys.stderr)
        return 2

    ok = True
    for sid, (script, script_args) in STAGE_SCRIPTS.items():
        if sid not in members:
            continue
        cmd = [sys.executable, str(script)] + script_args + (["--apply"] if apply_ else [])
        print(f"\n=== {sid} ===", file=sys.stderr)
        res = subprocess.run(cmd, cwd=str(WORKSPACE))
        ok = ok and res.returncode in (0, 2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
