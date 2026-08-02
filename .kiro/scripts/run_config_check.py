#!/usr/bin/env python3
"""Validate run-config.md.

Checks:
1. Profile `no-llm-*` contains only 🔧 stages
2. No duplicate stages
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CFG = HERE.parent / "steering" / "run-config.md"

STAGE_RE = re.compile(r"^(\s*)- \[([ xX])\] `([a-z0-9]+)` \[([^\]]+)\]\(([^)#]*)#(stage-[a-z0-9-]+)\)(?P<rest>.*)$")
MARK_SCRIPT = "🔧"


def parse_stages(text):
    stages = {}
    for line in text.split("\n"):
        m = STAGE_RE.match(line)
        if m:
            indent, checked, sid, _text, _href, _anchor = m.groups()[:6]
            rest = m.group("rest") or ""
            stages[sid] = {"checked": checked in "xX", "scripted": MARK_SCRIPT in rest}
    return stages


def parse_deps(text):
    deps = {"profiles": {}}
    in_yaml = False
    for line in text.split("\n"):
        if line.strip() == "```yaml":
            in_yaml = True
            continue
        if in_yaml and line.strip() == "```":
            in_yaml = False
            continue
        if not in_yaml:
            continue
        if line.startswith("  ") and ":" in line:
            key, val = line.strip().split(":", 1)
            key = key.strip()
            val = val.strip()
            if key in ("requires", "auto_enable", "warn"):
                deps[key] = eval(val) if val else {}
            elif key.startswith("no-llm"):
                deps["profiles"][key] = eval(val) if val else []
    return deps


def validate(text):
    issues = []
    stages = parse_stages(text)
    deps = parse_deps(text)

    # Check profiles
    for pname, members in deps.get("profiles", {}).items():
        if "no-llm" not in pname:
            continue
        for sid in members:
            if sid not in stages:
                issues.append(f"profile `{pname}` names unknown stage `{sid}`")
            elif not stages[sid]["scripted"]:
                issues.append(f"profile `{pname}` contains `{sid}` which is not 🔧")

    return issues


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CFG))
    ap.add_argument("--playbook", help="ignored (for backward compatibility)")
    args = ap.parse_args(argv)

    text = Path(args.config).read_text(encoding="utf-8")
    issues = validate(text)

    if issues:
        for i in issues:
            print(f"FAIL  {i}", file=sys.stderr)
        print(f"run-config: INVALID ({len(issues)} issue(s))", file=sys.stderr)
        return 1

    stages = parse_stages(text)
    print(f"run-config: VALID — {len(stages)} stages, {len(parse_deps(text).get('profiles', {}))} profile(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
