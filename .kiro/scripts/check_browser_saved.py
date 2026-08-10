#!/usr/bin/env python3
"""Verify no open tracker.html tab has unsaved edits, before a scripted run that reads
tracker data from disk.

tracker.html only writes its .md files to disk when the user clicks Save changes — edits
still in the browser are invisible to CLI scripts, and a script's own writes can clobber
them. This finds every open tracker.html tab through the Tab Share extension, asks the page
directly (via /eval) whether its Save button is enabled (enabled <=> unsaved edits), and if
any tab is dirty prompts the user to save in the browser and waits for confirmation.

Exit codes:
    0  every open tracker.html tab is saved (or none is open), or the user confirmed
    2  aborted: can't verify (Tab Share offline / non-interactive stdin) or user declined

Usage:
    check_browser_saved.py             # check; prompt+wait if any tab has unsaved edits
    check_browser_saved.py --yes       # acknowledge edits are saved; skip the check
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
from chrome_interface import ChromeInterface

# Chromium first (DEFAULT_BASE), then Firefox. Tab Share listens on 127.0.0.1:8766/8765.
_BASES = ("http://127.0.0.1:8766", "http://127.0.0.1:8765")

_CHECK_JS = (
    "(function () {"
    "var b = document.getElementById('save');"
    "var st = document.getElementById('status');"
    "return b ? {dirty: !b.disabled, status: st ? st.textContent : ''} : null;"
    "})()"
)


def tracker_dirty_tabs(bases=_BASES):
    """Find open tracker.html tabs and ask each whether it has unsaved edits.

    Returns (dirty, err). `dirty` is a list of {"title","url","status"} for every tracker
    tab whose Save button is enabled (browser edits not yet written to disk). A tracker tab
    we cannot read (still loading, error page) counts as dirty: we may not prove it is
    clean. `err` is None normally, or a description when no Tab Share instance is reachable
    (then `dirty` is None).
    """
    base = next((b for b in bases if ChromeInterface(base=b).is_up()), None)
    if base is None:
        return None, "Tab Share not reachable on " + ", ".join(bases)
    ci = ChromeInterface(base=base)
    dirty = []
    for t in ci.tabs():
        url = t.get("url") or ""
        if "tracker.html" not in url:
            continue
        st = ci.eval(t.get("id"), _CHECK_JS)
        if st is None or st.get("dirty"):
            dirty.append({"title": t.get("title") or url, "url": url,
                          "status": (st or {}).get("status", "")})
    return dirty, None


def confirm_browser_saved(argv, input_fn=input):
    """Pre-flight gate for scripts that read the .md files the tracker writes.

    Returns True to proceed. --yes/-y acknowledges for scripted runs. Otherwise: if Tab
    Share says every open tracker.html tab is saved (or none is open), proceed; if any tab
    has unsaved edits, list it and wait for the user to save in the browser, re-checking
    each time they press Enter. "Wait until you save? [Y/n]" defaults to yes — Enter keeps
    waiting until the tabs are saved; only an explicit `n` proceeds despite unsaved edits.
    On non-interactive stdin with dirty tabs, refuse rather than hang on input() or
    silently proceed.
    """
    if "--yes" in argv or "-y" in argv:
        return True
    dirty, err = tracker_dirty_tabs()
    if err:
        print(f"check_browser_saved: {err} — can't verify no tracker.html tab has unsaved "
              "edits. Start Tab Share, or pass --yes to proceed.", file=sys.stderr)
        return False
    if not dirty:
        return True
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError):
        interactive = False
    if not interactive:
        print("check_browser_saved: unsaved edits in tracker.html tab(s) but stdin is not "
              "interactive — can't wait for you to save them. Pass --yes to proceed.",
              file=sys.stderr)
        return False
    for t in dirty:
        print(f"  unsaved edits in tracker.html tab: {t['title']}  ({t['url']})",
              file=sys.stderr)
    while True:
        try:
            input_fn("Save them in the browser (tracker.html -> Save changes), then press "
                     "Enter: ")
        except (EOFError, KeyboardInterrupt):
            return False
        prev_dirty = dirty
        dirty, err = tracker_dirty_tabs()
        if err:
            print(f"check_browser_saved: {err} during re-check — treating tabs as still "
                  "unsaved.", file=sys.stderr)
            dirty = prev_dirty
        if not dirty:
            return True
        for t in dirty:
            print(f"  still unsaved: {t['title']}  ({t['url']})", file=sys.stderr)
        try:
            ans = input_fn("Wait until you save? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if ans in ("n", "no"):
            print("check_browser_saved: proceeding despite unsaved edits.", file=sys.stderr)
            return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Verify no open tracker.html tab has unsaved edits. Exits 0 when all "
                    "are saved (or none open) or the user confirms; 2 otherwise.")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="acknowledge edits are saved; skip the check")
    args = ap.parse_args(argv)
    if confirm_browser_saved(["--yes"] if args.yes else []):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
