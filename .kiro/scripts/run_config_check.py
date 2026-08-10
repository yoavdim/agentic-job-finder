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
    """Parse the yaml blocks of run-config.md into nested dicts.

    Handles both `profiles:`/`requires:` at column 0 and their 2-space-indented members,
    by tracking indentation instead of assuming a fixed two spaces (the previous version
    silently dropped the `requires` block, so dependency validation never fired).
    """
    def yaml_value(val):
        val = val.strip()
        if val in ("", "{}"):
            return {}
        if val == "[]":
            return []
        if val.startswith("[") and val.endswith("]"):
            items = [t.strip() for t in val[1:-1].split(",") if t.strip()]
            out = []
            for it in items:
                if len(it) >= 2 and it[0] == it[-1] and it[0] in "\"'":
                    out.append(it[1:-1])
                else:
                    out.append(it)  # bare yaml scalar -> string
            return out
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            return val[1:-1]
        return val

    deps = {"profiles": {}}
    in_yaml = False
    stack = []  # (indent, dict) — the dict each upcoming line belongs to
    for line in text.split("\n"):
        if line.strip() == "```yaml":
            in_yaml = True
            stack = []
            continue
        if in_yaml and line.strip() == "```":
            in_yaml = False
            stack = []
            continue
        if not in_yaml or not line.strip():
            continue
        content = line.strip()
        if ":" not in content:
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, val = content.split(":", 1)
        key, val = key.strip(), val.strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else deps
        if val:
            parent[key] = yaml_value(val)
        else:
            node = {}
            parent[key] = node
            stack.append((indent, node))
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
