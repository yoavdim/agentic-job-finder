#!/usr/bin/env python3
"""Routine launcher — pick a job-search routine and get the prompt that runs it.

A PyQt5 dialog that does NOT execute anything itself: it emits a *prompt* — to stdout
(what the calling agent/LLM reads back) and to the clipboard — which the caller then
carries out. `run-config.md` is read for the stage list and its `requires` graph, but is
never written.

Routines:
  search            — full pass; shows run-config.md's stage checkboxes, validated against
                      the `requires` graph, and emits the selected run plan as the prompt
  scrape            — watchlist scrape (`watchlist_scrape.py --apply`)
  no-llm sweep      — `no_llm_sweep.py` (0b + 0e + 0f, applies by default)
  stage 0 only      — full stage-0 maintenance (0a–0f, including the LLM-judgment parts)
  reject shortlist  — the shortlist's "flush" (no script exists): mark every open `[ ]`
                      row `[nope]` + a reason, then migrate via `migrate_resolved.py`
  view in chrome    — no prompt; the card's button opens tracker.html in Chromium and
                      focuses the window + tab (dedupes via Tab Share when reachable)

Usage:
    python3 routine_launcher.py [--config run-config.md]

Exit: 0 = prompt emitted (stdout + clipboard), 2 = cancelled.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / ".kiro" / "scripts"))
sys.path.insert(0, str(HERE / ".kiro" / "scripts" / "lib"))
import run_config_check as RCC  # type: ignore
import tab_share as TS  # type: ignore
from reasons import REASON_CODES  # type: ignore

DEFAULT_CONFIG = HERE / ".kiro" / "steering" / "run-config.md"

# Routines that act directly (no prompt to emit) — the card's button is the action.
NO_PROMPT_ROUTINES = {"view-in-chrome"}


def chromium_bin():
    """The Chromium executable, or '' when none can be found. Env override first."""
    env = os.environ.get("KIRO_CHROMIUM")
    if env:
        return env
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(name)
        if p:
            return p
    return "/snap/bin/chromium" if Path("/snap/bin/chromium").exists() else ""


def tracker_html_url():
    """The file:// URL of tracker.html in this workspace (spaces percent-encoded)."""
    return (HERE / "tracker.html").resolve().as_uri()


def open_tracker_in_chrome():
    """Open tracker.html in Chromium and focus the window + tab.

    Best-effort dedupe via Tab Share (the extension only sees the focused window, so a
    tracker tab in another window gets a duplicate — accepted trade-off). Returns
    (ok, message).
    """
    url = tracker_html_url()
    browser = chromium_bin()
    if not browser:
        return False, "no Chromium executable found (set KIRO_CHROMIUM)"
    if TS.is_up():
        tab = TS.find_tab("tracker.html")
        if tab:
            tid = tab.get("id")
            TS.navigate(tid, url)  # activates the existing tab; same-URL nav doesn't reload
            subprocess.Popen([browser, "--activate-on-launch"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True, "focused the existing tracker.html tab and raised Chromium"
    subprocess.Popen([browser, url],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True, "opened tracker.html in Chromium (new tab)"

# routine key -> (display name, accent color, tag, help text)
ROUTINES = [
    ("search", "Search", "#5b9dff", "Full pass — run-config stage plan",
     "Full search per the playbook. Pick which run-config.md stages to execute"),
    ("scrape", "Watchlist scrape", "#a78bfa", "watchlist_scrape.py",
     "The watchlist scrape routine — separate from the 1c search stage: "
     "`watchlist_scrape.py --apply` appends new postings from the "
     "watched companies to `## Scraped (watchlist)` (selector pass is the precondition)."),
    ("css-selectors", "Analyze watchlist", "#f6c85f", "watchlist_selectors.py — LLM-managed",
     "Fill the empty CSS Selector cells in watchlist.md's `## Companies` via the selector "
     "pass. LLM-dependent: probe candidate selectors live, iterate, then commit each "
     "winner with `watchlist_selectors.py write --apply`."),
    ("no-llm-sweep", "No-LLM sweep", "#3ecf8e", "maintenance: 0b + 0e + 0f — fully scripted",
     "`no_llm_sweep.py` — the fully-scripted maintenance stages (Simplify sync, resolved "
     "migration, liveness sweep). Applies by default; it pre-flights `check_browser_saved`."),
    ("stage0", "Stage 0 only", "#f0a850", "Maintenance",
     "The whole stage-0 maintenance pass (0a–0f)"),
    ("reject-shortlist", "Reject / flush all", "#ef5350", "The shortlist's 'flush'",
     ""),
    ("view-in-chrome", "View tracker in Chrome", "#4fc1f0", "opens tracker.html",
     "No prompt — the card's button opens tracker.html (file://) in Chromium and focuses "
     "the window + tab. Reuses an already-open tracker tab via Tab Share when reachable."),
]


def ordered_stages(text):
    """(sid, label, checked, indent) tuples in run-config.md document order.

    `indent` is 0 for top-level stages, 1 for children (indented in the md).
    Also returns section headers as (None, heading_text, False, -1).
    """
    out = []
    heading_re = re.compile(r'^##\s+(.+)')
    for line in text.split("\n"):
        hm = heading_re.match(line)
        if hm:
            heading = hm.group(1).strip()
            # skip non-stage headings (Profiles, Dependencies)
            if any(k in heading.lower() for k in ("stage", "maintenance", "search", "wrap")):
                out.append((None, heading, False, -1))
            continue
        m = RCC.STAGE_RE.match(line)
        if m:
            raw_indent, checked, sid, label, _href, _anchor = m.groups()[:6]
            depth = 1 if len(raw_indent) >= 2 else 0
            out.append((sid, label, checked in "xX", depth))
    return out


def validate_plan(requires, checked):
    """Return a list of issues for the chosen run plan, or [].

    `checked` is an iterable of stage ids; `requires` is the `requires` dict from
    run-config.md's yaml. A checked stage whose requirements aren't all checked is an
    issue.
    """
    checked = set(checked)
    issues = []
    for sid in sorted(checked):
        for need in requires.get(sid, []) or []:
            if need not in checked:
                issues.append(f"`{sid}` requires `{need}`, but `{need}` is not in the plan")
    return issues


def search_prompt(stages, requires, checked, file_issues=()):
    """The prompt for the search routine: a run plan built from the checked stages."""
    plan = [(sid, label) for sid, label, _, _d in stages if sid is not None and sid in checked]
    issues = validate_plan(requires, checked)
    lines = [
        "Run the job search pass per `.kiro/steering/search-playbook.md`.",
        "",
        "Run plan:",
    ]
    lines += [f"- `{sid}` — {label}" for sid, label in plan]
    if not plan:
        lines.append("- (none selected)")
    if issues:
        lines += ["", "Plan warnings:"] + [f"- {i}" for i in issues]
    if file_issues:
        lines += ["", "run-config.md problems:"] + [f"- {i}" for i in file_issues]
    return "\n".join(lines)


def scrape_prompt():
    return "Run the watchlist scrape per `.kiro/steering/watchlist-scraper.md`."


def no_llm_sweep_prompt(skip_liveness=False):
    if skip_liveness:
        return "Run the no-LLM sweep script with `--skip-liveness-sweep` (applies 0b + 0e, skips 0f)."
    return "Run the no-LLM sweep script (applies 0b + 0e + 0f)."


def stage0_prompt(stages, requires, checked, file_issues=()):
    plan = [(sid, label) for sid, label, _, _d in stages if sid is not None and sid in checked and sid.startswith("0")]
    issues = validate_plan(requires, checked)
    lines = [
        "Run the Stage 0 maintenance pass per `.kiro/steering/search-playbook.md`.",
        "",
        "Run plan:",
    ]
    lines += [f"- `{sid}` — {label}" for sid, label in plan]
    if not plan:
        lines.append("- (none selected)")
    if issues:
        lines += ["", "Plan warnings:"] + [f"- {i}" for i in issues]
    if file_issues:
        lines += ["", "run-config.md problems:"] + [f"- {i}" for i in file_issues]
    return "\n".join(lines)


def reject_shortlist_prompt(reason):
    return f"Reject all open shortlist rows with reason '{reason}', then migrate them."


def flush_scraped_prompt():
    return "Flush all unmarked watchlist scrape rows."


def css_selectors_prompt(companies=(), flush=False):
    if not companies:
        return ""
    
    base = "Run the CSS selector pass per `.kiro/steering/watchlist-scraper.md`."
    co_list = ", ".join(companies)
    base += f"\n\nCompanies to find selectors for: {co_list}."
    
    if flush:
        args = " ".join(f'"{c}"' if " " in c else c for c in companies)
        base += f"\n\nAfter adding the selectors, run `watchlist_scrape.py --apply --to-flush --companies {args}` to flush their first scrape."
    
    return base


PROMPT_BUILDERS = {
    "search": None,  # needs the live stage selection; handled in the dialog
    "stage0": None,  # needs the live stage selection; handled in the dialog
    "scrape": scrape_prompt,
    "css-selectors": css_selectors_prompt,
    "no-llm-sweep": no_llm_sweep_prompt,
    "reject-shortlist": lambda reason: reject_shortlist_prompt(reason),
}


def build_prompt(routine, stages, requires, checked, file_issues=(), reason="not-interested"):
    """Build the prompt for a routine. `stages`/`requires`/`checked` matter for search;
    `reason` only for reject-shortlist. Routines in NO_PROMPT_ROUTINES have no prompt."""
    if routine in NO_PROMPT_ROUTINES:
        return ""
    if routine in ("search", "stage0"):
        prompt_fn = search_prompt if routine == "search" else stage0_prompt
        return prompt_fn(stages, requires, checked, file_issues)
    if routine == "reject-shortlist":
        return PROMPT_BUILDERS[routine](reason)
    if routine == "css-selectors":
        return PROMPT_BUILDERS[routine](checked)  # `checked` reused as company set
    return PROMPT_BUILDERS[routine]()


def parse_watchlist_companies(watchlist_path):
    """Return list of (company_name, has_selector) from watchlist.md ## Companies."""
    try:
        text = Path(watchlist_path).read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^## Companies\s*\n(.*?)(?=^##|\Z)", text, re.I | re.M | re.S)
    if not m:
        return []
    rows = [r for r in m.group(1).splitlines() if r.startswith("|") and not re.match(r"^[|\- ]+$", r)]
    if len(rows) < 2:
        return []
    hdr = [h.strip().lower() for h in rows[0].strip("|").split("|")]
    try:
        ci, si = hdr.index("company"), hdr.index("css selector")
        ni = hdr.index("next page") if "next page" in hdr else -1
    except ValueError:
        return []
    result = []
    for r in rows[1:]:
        c = [x.strip() for x in r.strip("|").split("|")]
        if len(c) > ci and c[ci]:
            has_sel = bool(c[si]) if len(c) > si else False
            has_next = bool(c[ni]) if ni != -1 and len(c) > ni else False
            is_complete = has_sel and (ni == -1 or has_next)
            result.append((c[ci], is_complete))
    return result


# ---------------------------------------------------------------------------
# PyQt5 UI — premium dark theme
# ---------------------------------------------------------------------------

QSS = """
/* ── Base ── */
QDialog { background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
          stop:0 #0c0e14, stop:1 #10131a);
          border: 1px solid #1c2030; }
QWidget { font-family: 'Inter', -apple-system, 'Segoe UI', Roboto, 'Noto Sans',
          Helvetica, Arial, sans-serif;
          font-size: 13px; color: #cdd1da; }

/* ── Typography ── */
QLabel#title { font-size: 26px; font-weight: 700; color: #ffffff;
               letter-spacing: 0.4px; }
QLabel#subtitle { color: #636b7e; font-size: 13px; }
QLabel#muted { color: #636b7e; font-size: 12px; }
QLabel#card-title { font-size: 17px; font-weight: 600; color: #e8ebf0; }
QLabel#desc { color: #8892a4; font-size: 13px; }

/* ── Routine list ── */
QListWidget#routines { background: #111420; border: 1px solid #1c2030;
                       border-radius: 12px; padding: 6px; outline: none; }
QListWidget#routines::item { border-radius: 10px; margin: 2px 0; }
QListWidget#routines::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(91,157,255,0.18), stop:1 rgba(91,157,255,0.06)); }
QListWidget#routines::item:hover:!selected { background: rgba(255,255,255,0.025); }
QLabel#routine-name { font-size: 13px; font-weight: 600; color: #e0e3ea; }
QLabel#routine-tag { color: #505868; font-size: 11px; }

/* ── Cards ── */
QFrame#card { background: #111420; border: 1px solid #1c2030;
              border-radius: 12px; }

/* ── Checkboxes ── */
QCheckBox { color: #cdd1da; spacing: 10px; padding: 4px 0; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
                       border: 1.5px solid #2a3040; background: #0e1018; }
QCheckBox::indicator:hover { border-color: #4a8af4; }
QCheckBox::indicator:checked { background: #4a8af4; border-color: #4a8af4; }

/* ── Radio buttons ── */
QRadioButton { color: #cdd1da; spacing: 10px; padding: 4px 0; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px;
                          border: 1.5px solid #2a3040; background: #0e1018; }
QRadioButton::indicator:hover { border-color: #4a8af4; }
QRadioButton::indicator:checked { background: #4a8af4; border-color: #4a8af4; }
QRadioButton:checked { font-weight: 600; color: #ffffff; }

/* ── Scroll area ── */
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }

/* ── Buttons ── */
QPushButton { background: #161a24; color: #cdd1da; border: 1px solid #222838;
              border-radius: 8px; padding: 8px 18px; font-weight: 500; }
QPushButton:hover { border-color: #4a8af4; color: #e8ebf0;
                    background: #1a1f2a; }
QPushButton#chip { padding: 5px 14px; border-radius: 14px; font-size: 12px;
                   color: #636b7e; background: #111420; border: 1px solid #1c2030; }
QPushButton#chip:hover { color: #cdd1da; border-color: #4a8af4;
                         background: #161a24; }
QPushButton#primary { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                      stop:0 #4080f0, stop:1 #5b9dff);
                      color: #ffffff; font-weight: 600;
                      border: none; padding: 10px 26px; border-radius: 8px; }
QPushButton#primary:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                            stop:0 #5090f8, stop:1 #70adff); }

/* ── Prompt preview ── */
QPlainTextEdit { background: #0a0c12; color: #98a0b0; border: 1px solid #1c2030;
                 border-radius: 10px; padding: 10px;
                 font-family: 'JetBrains Mono', 'Cascadia Mono', ui-monospace,
                              Menlo, Consolas, monospace;
                 font-size: 12px; selection-background-color: #4a8af4; }

/* ── Combo box ── */
QComboBox { background: #161a24; color: #cdd1da; border: 1px solid #222838;
            border-radius: 8px; padding: 7px 12px; }
QComboBox:hover { border-color: #4a8af4; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #111420; color: #cdd1da;
                              border: 1px solid #1c2030;
                              selection-background-color: #4a8af4; }

/* ── Scrollbar ── */
QScrollBar:vertical { background: transparent; width: 7px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: #222838; border-radius: 3px;
                              min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #3a4150; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QMessageBox { background: #111420; }

/* ── Custom title bar ── */
QWidget#titlebar { background: #0a0c12; border-bottom: 1px solid #1c2030; }
QLabel#titlebar-text { color: #505868; font-size: 12px; font-weight: 500; }
QPushButton#titlebar-btn { background: transparent; border: none; color: #505868;
                           border-radius: 4px; padding: 0;
                           font-family: Arial, sans-serif; font-size: 14px; }
QPushButton#titlebar-btn:hover { background: rgba(255,255,255,0.07); color: #cdd1da; }
QPushButton#titlebar-close { background: transparent; border: none; color: #505868;
                             border-radius: 4px; padding: 0;
                             font-family: Arial, sans-serif; font-size: 14px; }
QPushButton#titlebar-close:hover { background: #ef5350; color: #ffffff; }

/* ── Context menu (title bar right-click) ── */
QMenu { background-color: #131722; border: 1px solid #1c2030;
        border-radius: 8px; padding: 6px; }
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 5px; color: #cdd1da; }
QMenu::item:selected { background: rgba(91,157,255,0.18); color: #ffffff; }
QMenu::item:checked { color: #5b9dff; font-weight: 600; }
QMenu::separator { height: 1px; background: #1c2030; margin: 5px 8px; }
QMenu::indicator { margin-left: 4px; width: 12px; height: 12px; }
QMenu::indicator:checked { background: #5b9dff; border-radius: 3px; }
QMenu::indicator:checked:selected { background: #6faeff; }
"""


def run_dialog(config_path, argv=None):
    """Show the dialog; print the chosen prompt to stdout on accept.

    Returns the exit code: 0 = prompt emitted, 2 = cancelled. Never writes run-config.md.
    """
    from PyQt5 import QtCore, QtGui, QtWidgets  # deferred so tests don't need a display

    # Toggling WindowStaysOnTopHint rebuilds the native window; on X11/Wayland Qt then
    # re-registers the display socket notifier in Python's main thread (not a QThread) and
    # logs a benign "QSocketNotifier: Can only be used with threads started with QThread"
    # during QApplication construction. Suppress just that message; everything else passes.
    def _filter_msg(_t, _ctx, msg):
        if isinstance(msg, str) and msg.startswith(
                "QSocketNotifier: Can only be used with threads"):
            return
        print(msg, file=sys.stderr)

    QtCore.qInstallMessageHandler(_filter_msg)

    cfg_text = Path(config_path).read_text(encoding="utf-8")
    stages = ordered_stages(cfg_text)
    deps = RCC.parse_deps(cfg_text)
    requires = deps.get("requires", {})
    file_issues = RCC.validate(cfg_text)

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv or [])
    app.setStyleSheet(QSS)

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Routine launcher — job search")
    dialog.setWindowFlags(dialog.windowFlags() | QtCore.Qt.FramelessWindowHint)
    dialog.resize(920, 760)
    dialog.setMinimumSize(780, 620)

    root = QtWidgets.QVBoxLayout(dialog)
    root.setContentsMargins(5, 5, 5, 5)
    root.setSpacing(0)

    # ── custom title bar (drag-to-move, minimize, close) ──
    class _TitleBar(QtWidgets.QWidget):
        def __init__(self, dlg, parent=None):
            super().__init__(parent)
            self._dlg = dlg
            self._drag_pos = None
        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton:
                wh = self._dlg.windowHandle()
                if wh and hasattr(wh, 'startSystemMove'):
                    wh.startSystemMove()          # Qt 5.15+ native WM drag
                else:
                    self._drag_pos = (event.globalPos()
                                      - self._dlg.frameGeometry().topLeft())
                event.accept()
            else:
                super().mousePressEvent(event)
        def mouseMoveEvent(self, event):
            if self._drag_pos is not None and event.buttons() & QtCore.Qt.LeftButton:
                self._dlg.move(event.globalPos() - self._drag_pos)
                event.accept()
            else:
                super().mouseMoveEvent(event)
        def mouseReleaseEvent(self, event):
            self._drag_pos = None
            super().mouseReleaseEvent(event)

    titlebar = _TitleBar(dialog)
    titlebar.setObjectName("titlebar")
    titlebar.setFixedHeight(36)
    tb_lay = QtWidgets.QHBoxLayout(titlebar)
    tb_lay.setContentsMargins(14, 0, 8, 0)
    tb_lay.setSpacing(0)
    tb_title = QtWidgets.QLabel("Routine launcher — job search")
    tb_title.setObjectName("titlebar-text")
    tb_title.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
    tb_lay.addWidget(tb_title)
    tb_lay.addStretch(1)
    min_btn = QtWidgets.QPushButton("─")
    min_btn.setObjectName("titlebar-btn")
    min_btn.setFixedSize(36, 28)
    min_btn.setCursor(QtCore.Qt.PointingHandCursor)
    min_btn.clicked.connect(dialog.showMinimized)
    tb_lay.addWidget(min_btn)
    max_btn = QtWidgets.QPushButton("□")
    max_btn.setObjectName("titlebar-btn")
    max_btn.setFixedSize(36, 28)
    max_btn.setCursor(QtCore.Qt.PointingHandCursor)
    max_btn.setToolTip("Maximize / restore")
    def toggle_maximize():
        if dialog.isMaximized():
            dialog.showNormal()
        else:
            dialog.showMaximized()
    max_btn.clicked.connect(toggle_maximize)
    tb_lay.addWidget(max_btn)
    close_btn = QtWidgets.QPushButton("✕")
    close_btn.setObjectName("titlebar-close")
    close_btn.setFixedSize(36, 28)
    close_btn.setCursor(QtCore.Qt.PointingHandCursor)
    close_btn.clicked.connect(dialog.reject)
    tb_lay.addWidget(close_btn)

    def show_window_menu(pos):
        """Right-click on the title bar: window options (minimize, maximize, close).

        NOTE: 'Always on top' was removed — toggling WindowStaysOnTopHint rebuilds the
        native window and crashes Qt5 on Wayland. See the open todo.
        """
        menu = QtWidgets.QMenu(dialog)
        menu.addAction("Minimize", dialog.showMinimized)
        max_label = "Restore" if dialog.isMaximized() else "Maximize"
        menu.addAction(max_label, toggle_maximize)
        menu.addSeparator()
        menu.addAction("Close", dialog.reject)
        menu.exec_(pos)

    for w in (min_btn, max_btn, close_btn, titlebar):
        w.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        w.customContextMenuRequested.connect(
            lambda _pos, w=w: show_window_menu(w.mapToGlobal(_pos)))
    root.addWidget(titlebar)

    # ── body (the rest of the UI, with padding) ──
    body = QtWidgets.QVBoxLayout()
    body.setContentsMargins(28, 18, 28, 22)
    body.setSpacing(14)
    root.addLayout(body, 1)

    title = QtWidgets.QLabel("Routine launcher")
    title.setObjectName("title")
    body.addWidget(title)
    subtitle = QtWidgets.QLabel(
        "Pick a routine and emit the prompt the agent runs. Nothing executes here and "
        "run-config.md is never edited.")
    subtitle.setObjectName("subtitle")
    body.addWidget(subtitle)
    body.addSpacing(4)

    mid = QtWidgets.QHBoxLayout()
    mid.setSpacing(14)
    body.addLayout(mid, 1)

    list_w = QtWidgets.QListWidget()
    list_w.setObjectName("routines")
    list_w.setFixedWidth(300)
    for _key, name, color, tag, help_ in ROUTINES:
        it = QtWidgets.QListWidgetItem()
        it.setSizeHint(QtCore.QSize(0, 60))
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(12, 9, 12, 9)
        outer.setSpacing(3)
        name_row = QtWidgets.QHBoxLayout()
        name_row.setSpacing(9)
        dot = QtWidgets.QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(
            f"background:{color}; border-radius:4px; border:none;")
        name_row.addWidget(dot, 0, QtCore.Qt.AlignVCenter)
        t = QtWidgets.QLabel(name)
        t.setObjectName("routine-name")
        name_row.addWidget(t, 1)
        outer.addLayout(name_row)
        g = QtWidgets.QLabel(tag)
        g.setObjectName("routine-tag")
        g.setContentsMargins(17, 0, 0, 0)
        outer.addWidget(g)
        it.setToolTip(help_)
        list_w.addItem(it)
        list_w.setItemWidget(it, w)
    mid.addWidget(list_w)

    stack = QtWidgets.QStackedWidget()
    mid.addWidget(stack, 1)
    stack_keys = []

    def card(routine_key):
        f = QtWidgets.QFrame()
        f.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(f)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        # Build standard header
        row = next(r for r in ROUTINES if r[0] == routine_key)
        _, name, color, _tag, help_ = row
        hdr = QtWidgets.QHBoxLayout()
        hdr.setSpacing(10)
        dot = QtWidgets.QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{color}; border-radius:5px; border:none;")
        hdr.addWidget(dot, 0, QtCore.Qt.AlignVCenter)
        t = QtWidgets.QLabel(name)
        t.setObjectName("card-title")
        hdr.addWidget(t, 1)
        lay.addLayout(hdr)
        if help_:
            lay.addSpacing(4)
            d = QtWidgets.QLabel(help_)
            d.setObjectName("desc")
            d.setWordWrap(True)
            lay.addWidget(d)

        return f, lay

    def add_run_button(lay, specs, running="Running…", done_msg="Done — exit 0."):
        """A 'Run script directly' button that runs a script via QProcess and reports
        the exit code + last stderr line in a status label under it.

        `specs` is a callable returning (script_path, args_tuple) — evaluated at click
        time so a mode toggle on the card can switch what the button runs.
        """
        run_btn = QtWidgets.QPushButton("Run script directly")
        run_btn.setObjectName("primary")
        run_btn.setCursor(QtCore.Qt.PointingHandCursor)
        lay.addWidget(run_btn)
        run_status = QtWidgets.QLabel("")
        run_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        run_status.setObjectName("muted")
        run_status.setWordWrap(True)
        lay.addWidget(run_status)

        proc = QtCore.QProcess(dialog)
        proc.setWorkingDirectory(str(HERE))
        err_tail = []

        def _collect_err():
            data = bytes(proc.readAllStandardError()).decode("utf-8", "replace")
            if data:
                err_tail[:] = (err_tail + [data])[-3:]

        def _run():
            script, args = specs()
            run_btn.setEnabled(False)
            run_status.setStyleSheet("color:#98a0b0;")
            run_status.setText(running)
            err_tail.clear()
            proc.start(sys.executable, [str(script)] + list(args))

        def _done(code, _status):
            run_btn.setEnabled(True)
            if code == 0:
                run_status.setStyleSheet("color:#3ecf8e;")
                run_status.setText(done_msg)
            else:
                tail = "".join(err_tail).strip().splitlines()
                reason = tail[-1] if tail else "see the terminal for output"
                run_status.setStyleSheet("color:#ef5350;")
                run_status.setText(f"Failed (exit {code}): {reason}")

        proc.readyReadStandardError.connect(_collect_err)
        proc.finished.connect(_done)
        run_btn.clicked.connect(_run)
        return run_btn, run_status

    # --- search: run-config stage plan ---
    stage_checks = {}
    stage_widgets = []
    search_card, search_v = card("search")
    scroller = QtWidgets.QScrollArea()
    scroller.setWidgetResizable(True)
    inner = QtWidgets.QWidget()
    inner_v = QtWidgets.QVBoxLayout(inner)
    inner_v.setContentsMargins(2, 2, 8, 2)
    inner_v.setSpacing(2)
    for sid, label, checked, depth in stages:
        if sid is None:
            # section header
            sec = QtWidgets.QLabel(label)
            sec.setObjectName("muted")
            sec.setStyleSheet("margin-top:8px; margin-bottom:2px; font-weight:600;")
            inner_v.addWidget(sec)
            stage_widgets.append((None, label, sec))
            continue
        cb = QtWidgets.QCheckBox(f"{sid} — {label}")
        cb.setChecked(checked)
        cb.stateChanged.connect(lambda _s, s=sid: refresh())
        if depth >= 1:
            cb.setStyleSheet("margin-left:22px;")
        stage_checks[sid] = cb
        inner_v.addWidget(cb)
        stage_widgets.append((sid, label, cb))
    inner_v.addStretch(1)
    scroller.setWidget(inner)
    search_v.addWidget(scroller, 1)

    def set_all(value):
        for cb in stage_checks.values():
            cb.setChecked(value)

    def reset_stages():
        for sid, _label, checked, _d in stages:
            if sid is not None:
                stage_checks[sid].setChecked(checked)

    chip_row = QtWidgets.QHBoxLayout()
    chip_row.setSpacing(8)
    for name, fn in (("All", lambda: set_all(True)),
                     ("None", lambda: set_all(False)),
                     ("Reset to run-config.md", reset_stages)):
        b = QtWidgets.QPushButton(name)
        b.setObjectName("chip")
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.clicked.connect(fn)
        chip_row.addWidget(b)
    chip_row.addStretch(1)
    search_v.addLayout(chip_row)
    validation = QtWidgets.QLabel("")
    validation.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    validation.setWordWrap(True)
    search_v.addWidget(validation)
    stack.addWidget(search_card)
    stack_keys.append("search")

    # --- reject all: mode selector (reject shortlist / flush scraped watchlist) ---
    reject_card, reject_v = card("reject-shortlist")
    mode_label = QtWidgets.QLabel("What to flush:")
    mode_label.setObjectName("muted")
    reject_v.addWidget(mode_label)
    reject_modes = {}  # key -> QRadioButton
    reject_mode_group = QtWidgets.QButtonGroup(dialog)
    for key, label in (("reject", "Reject all open shortlist rows"),
                       ("flush", "Flush scraped watchlist rows")):
        rb = QtWidgets.QRadioButton(label)
        rb.setChecked(key == "reject")
        rb.toggled.connect(lambda _c: refresh())
        reject_mode_group.addButton(rb)
        reject_modes[key] = rb
        reject_v.addWidget(rb)

    rlabel = QtWidgets.QLabel("Reason written into each rejected row's Comment:")
    rlabel.setObjectName("muted")
    reject_v.addWidget(rlabel)
    reason_grid = QtWidgets.QGridLayout()
    reason_grid.setHorizontalSpacing(18)
    reason_grid.setVerticalSpacing(2)
    reason_btns = []
    reason_group = QtWidgets.QButtonGroup(dialog)
    for i, code in enumerate(REASON_CODES):
        rb = QtWidgets.QRadioButton(code)
        rb.setChecked(code == "not-interested")
        rb.toggled.connect(lambda _c: refresh())
        reason_group.addButton(rb)
        reason_btns.append(rb)
        reason_grid.addWidget(rb, i // 2, i % 2)
    reject_v.addLayout(reason_grid)
    reject_v.addStretch(1)

    def reject_run_specs():
        if reject_modes["flush"].isChecked():
            return (HERE / ".kiro" / "scripts" / "watchlist_flush.py", ("--apply",))
        return (HERE / ".kiro" / "scripts" / "reject_shortlist.py",
                ("--yes", "--comment", "batch reject via launcher"))

    add_run_button(
        reject_v,
        reject_run_specs,
        running="Running…",
        done_msg="Done — flushed (exit 0).")
    stack.addWidget(reject_card)
    stack_keys.append("reject-shortlist")

    # --- fixed-description routines ---
    for key, name, color, tag, help_ in ROUTINES:
        if key in ("search", "reject-shortlist"):
            continue
        c, lay = card(key)

        def add_run_button(script, args=(), running="Running…", done_msg="Done — exit 0."):
            """A 'Run script directly' button that runs `script` via QProcess and reports
            the exit code + last stderr line in a status label under it."""
            run_btn = QtWidgets.QPushButton("Run script directly")
            run_btn.setObjectName("primary")
            run_btn.setCursor(QtCore.Qt.PointingHandCursor)
            lay.addWidget(run_btn)
            run_status = QtWidgets.QLabel("")
            run_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            run_status.setObjectName("muted")
            run_status.setWordWrap(True)
            lay.addWidget(run_status)

            proc = QtCore.QProcess(dialog)
            proc.setWorkingDirectory(str(HERE))
            err_tail = []

            def _collect_err():
                data = bytes(proc.readAllStandardError()).decode("utf-8", "replace")
                if data:
                    err_tail[:] = (err_tail + [data])[-3:]

            def _run():
                run_btn.setEnabled(False)
                run_status.setStyleSheet("color:#98a0b0;")
                run_status.setText(running)
                err_tail.clear()
                try:
                    current_args = args() if callable(args) else args
                    proc.start(sys.executable, [str(script)] + list(current_args))
                except Exception as exc:
                    run_btn.setEnabled(True)
                    traceback.print_exc()
                    QtWidgets.QMessageBox.critical(dialog, "Launch error", str(exc))

            def _done(code, _status):
                run_btn.setEnabled(True)
                if code == 0:
                    run_status.setStyleSheet("color:#3ecf8e;")
                    run_status.setText(done_msg)
                else:
                    tail = "".join(err_tail).strip().splitlines()
                    reason = tail[-1] if tail else "see the terminal for output"
                    run_status.setStyleSheet("color:#ef5350;")
                    run_status.setText(f"Failed (exit {code}): {reason}")

            proc.readyReadStandardError.connect(_collect_err)
            proc.finished.connect(_done)
            run_btn.clicked.connect(_run)
            return run_btn, run_status

        if key == "no-llm-sweep":
            skip_cb = QtWidgets.QCheckBox("Skip liveness sweep (--skip-liveness-sweep)")
            skip_cb.stateChanged.connect(lambda _s: refresh())
            lay.addWidget(skip_cb)
            lay.setProperty("skip_cb", skip_cb)
            lay.addSpacing(6)
            add_run_button(
                HERE / ".kiro" / "scripts" / "no_llm_sweep.py",
                lambda: ("--skip-liveness-sweep",) if skip_cb.isChecked() else (),
                running="Running no_llm_sweep.py…",
                done_msg="Done — sweep applied (exit 0).")
        elif key == "scrape":
            add_run_button(
                HERE / ".kiro" / "scripts" / "watchlist_scrape.py",
                ("--apply",),
                running="Running watchlist_scrape.py --apply…",
                done_msg="Done — scrape applied (exit 0).")
        elif key == "css-selectors":
            missing = [n for n, is_complete in parse_watchlist_companies(HERE / "watchlist.md") if not is_complete]
            selector_checks = {}
            lbl_text = "Companies with missing CSS or Next Page selectors:" if missing else "All companies have their selectors fully populated."
            lbl = QtWidgets.QLabel(lbl_text)
            lbl.setObjectName("muted"); lbl.setWordWrap(True)
            lay.addWidget(lbl)
            for name in missing:
                cb = QtWidgets.QCheckBox(name)
                cb.setChecked(True)
                cb.stateChanged.connect(lambda _s: refresh())
                lay.addWidget(cb)
                selector_checks[name] = cb
            if selector_checks:
                chip_row2 = QtWidgets.QHBoxLayout()
                chip_row2.setSpacing(8)
                for chip_name, val in (("All", True), ("None", False)):
                    b2 = QtWidgets.QPushButton(chip_name)
                    b2.setObjectName("chip"); b2.setCursor(QtCore.Qt.PointingHandCursor)
                    b2.clicked.connect(lambda _c, v=val: [cb.setChecked(v) for cb in selector_checks.values()])
                    chip_row2.addWidget(b2)
                chip_row2.addStretch(1)
                lay.addLayout(chip_row2)

            lay.addSpacing(6)
            flush_cb = QtWidgets.QCheckBox("Flush these companies after scraping (the first scrape is meaningless)")
            flush_cb.setChecked(True)
            flush_cb.stateChanged.connect(lambda _s: refresh())
            lay.addWidget(flush_cb)
            lay.setProperty("flush_cb", flush_cb)

            lay.setProperty("selector_checks", selector_checks)
        elif key == "view-in-chrome":
            open_btn = QtWidgets.QPushButton("Open tracker in Chrome")
            open_btn.setObjectName("primary")
            open_btn.setCursor(QtCore.Qt.PointingHandCursor)
            lay.addWidget(open_btn)
            open_status = QtWidgets.QLabel("")
            open_status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            open_status.setObjectName("muted")
            open_status.setWordWrap(True)
            lay.addWidget(open_status)

            def _open(_checked=False):
                ok, msg = open_tracker_in_chrome()
                open_status.setStyleSheet("color:#3ecf8e;" if ok else "color:#ef5350;")
                open_status.setText(("Opened — " if ok else "Could not open — ") + msg)

            open_btn.clicked.connect(_open)
        lay.addStretch(1)
        stack.addWidget(c)
        stack_keys.append(key)

    # --- live preview ---
    preview_label = QtWidgets.QLabel("Prompt — goes to stdout and the clipboard when emitted:")
    preview_label.setObjectName("muted")
    body.addWidget(preview_label)
    preview = QtWidgets.QPlainTextEdit()
    preview.setReadOnly(True)
    preview.setPlaceholderText("The prompt to hand to the agent appears here as you choose.")
    preview.setFixedHeight(86)
    body.addWidget(preview)

    def current_routine():
        row = list_w.currentRow()
        return ROUTINES[max(0, row)][0]

    def refresh():
        routine = current_routine()
        stack_routine = "search" if routine in ("search", "stage0") else routine
        stack.setCurrentIndex(stack_keys.index(stack_routine))
        if routine in ("search", "stage0"):
            is_stage0 = (routine == "stage0")
            t_label = search_card.findChild(QtWidgets.QLabel, "card-title")
            d_label = search_card.findChild(QtWidgets.QLabel, "desc")
            if t_label and d_label:
                row = next(r for r in ROUTINES if r[0] == routine)
                t_label.setText(row[1])
                d_label.setText(row[4])

            for sid, label, widget in stage_widgets:
                if sid is None:
                    visible = not is_stage0 or "Stage 0" in label
                else:
                    visible = not is_stage0 or sid.startswith("0")
                widget.setVisible(visible)

            checked = {sid for sid, cb in stage_checks.items() if cb.isChecked() and (not is_stage0 or sid.startswith("0"))}
            issues = validate_plan(requires, checked)
            if issues:
                validation.setText("\n".join("• " + i for i in issues))
                validation.setStyleSheet("color:#e0a458;")
            elif checked:
                validation.setText("Plan OK.")
                validation.setStyleSheet("color:#3ecf8e;")
            else:
                validation.setText("No stages selected.")
                validation.setStyleSheet("color:#98a0b0;")
            preview.setPlainText(
                build_prompt(routine, stages, requires, checked, file_issues))
        elif routine == "reject-shortlist":
            flush = reject_modes["flush"].isChecked()
            rlabel.setVisible(not flush)
            for rb in reason_btns:
                rb.setVisible(not flush)
            if flush:
                preview.setPlainText(flush_scraped_prompt())
            else:
                reason = next(rb.text() for rb in reason_btns if rb.isChecked())
                preview.setPlainText(reject_shortlist_prompt(reason))
            emit_btn.setEnabled(True)
        elif routine in NO_PROMPT_ROUTINES:
            preview.setPlainText("(no prompt — use the button on this routine's card)")
            emit_btn.setEnabled(False)
        elif routine == "css-selectors":
            emit_btn.setEnabled(True)
            # Find the css-selectors card and read its checklist
            css_card = stack.widget(stack_keys.index("css-selectors"))
            css_lay = css_card.layout()
            sel_checks = css_lay.property("selector_checks") or {}
            selected = [name for name, cb in sel_checks.items() if cb.isChecked()]
            
            flush_cb = css_lay.property("flush_cb")
            flush = flush_cb.isChecked() if flush_cb else False
            
            preview.setPlainText(css_selectors_prompt(selected, flush))
        elif routine == "no-llm-sweep":
            emit_btn.setEnabled(True)
            sweep_card = stack.widget(stack_keys.index("no-llm-sweep"))
            skip_cb = sweep_card.layout().property("skip_cb")
            skip = skip_cb.isChecked() if skip_cb else False
            preview.setPlainText(no_llm_sweep_prompt(skip))
        else:
            emit_btn.setEnabled(True)
            preview.setPlainText(PROMPT_BUILDERS[routine]())

    list_w.currentRowChanged.connect(lambda _r: refresh())

    btn_row = QtWidgets.QHBoxLayout()
    btn_row.setSpacing(10)
    btn_row.addStretch(1)
    copy_btn = QtWidgets.QPushButton("⎘ Copy")
    copy_btn.setCursor(QtCore.Qt.PointingHandCursor)
    cancel_btn = QtWidgets.QPushButton("Cancel")
    cancel_btn.setCursor(QtCore.Qt.PointingHandCursor)
    cancel_btn.clicked.connect(dialog.reject)
    emit_btn = QtWidgets.QPushButton("Emit prompt")
    emit_btn.setObjectName("primary")
    emit_btn.setDefault(True)
    emit_btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn_row.addWidget(copy_btn)
    btn_row.addWidget(cancel_btn)
    btn_row.addWidget(emit_btn)
    body.addLayout(btn_row)

    def _copy_prompt():
        text = preview.toPlainText()
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            copy_btn.setText("✓ Copied")
            QtCore.QTimer.singleShot(1500, lambda: copy_btn.setText("⎘ Copy"))

    copy_btn.clicked.connect(_copy_prompt)

    result = {"prompt": None}

    def emit():
        routine = current_routine()
        if routine in NO_PROMPT_ROUTINES:
            return  # the button is disabled; this is just a guard
        if routine in ("search", "stage0"):
            is_stage0 = (routine == "stage0")
            checked = {sid for sid, cb in stage_checks.items() if cb.isChecked() and (not is_stage0 or sid.startswith("0"))}
            if not checked:
                QtWidgets.QMessageBox.warning(dialog, "No stages",
                                              "Select at least one stage to run.")
                return
            issues = validate_plan(requires, checked)
            if issues:
                answer = QtWidgets.QMessageBox.question(
                    dialog, "Plan has warnings",
                    "The run plan is missing required stages:\n\n"
                    + "\n".join("• " + i for i in issues)
                    + "\n\nEmit the prompt with these warnings anyway?")
                if answer != QtWidgets.QMessageBox.Yes:
                    return
        result["prompt"] = preview.toPlainText()
        dialog.accept()

    emit_btn.clicked.connect(emit)

    # ── resize: edge + corner drag handles (native WM resize) ──
    grip = QtWidgets.QSizeGrip(dialog)
    grip.setFixedSize(18, 18)
    grip.setStyleSheet("QSizeGrip { background: transparent; }")

    H = 5
    class _ResizeHandle(QtWidgets.QWidget):
        def __init__(self, edges, cursor_shape):
            super().__init__(dialog)
            self._edges = edges
            self.setCursor(cursor_shape)
        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton:
                wh = dialog.windowHandle()
                if wh and hasattr(wh, "startSystemResize"):
                    wh.startSystemResize(self._edges)
                    event.accept()
                    return
            super().mousePressEvent(event)

    handles = {
        "nw": _ResizeHandle(QtCore.Qt.TopEdge | QtCore.Qt.LeftEdge,
                            QtCore.Qt.SizeFDiagCursor),
        "n":  _ResizeHandle(QtCore.Qt.TopEdge, QtCore.Qt.SizeVerCursor),
        "ne": _ResizeHandle(QtCore.Qt.TopEdge | QtCore.Qt.RightEdge,
                            QtCore.Qt.SizeBDiagCursor),
        "e":  _ResizeHandle(QtCore.Qt.RightEdge, QtCore.Qt.SizeHorCursor),
        "se": _ResizeHandle(QtCore.Qt.BottomEdge | QtCore.Qt.RightEdge,
                            QtCore.Qt.SizeFDiagCursor),
        "s":  _ResizeHandle(QtCore.Qt.BottomEdge, QtCore.Qt.SizeVerCursor),
        "sw": _ResizeHandle(QtCore.Qt.BottomEdge | QtCore.Qt.LeftEdge,
                            QtCore.Qt.SizeBDiagCursor),
        "w":  _ResizeHandle(QtCore.Qt.LeftEdge, QtCore.Qt.SizeHorCursor),
    }

    def layout_resize_handles():
        w, h = dialog.width(), dialog.height()
        for name, (x, y, ww, hh) in {
            "nw": (0, 0, H, H),
            "n":  (H, 0, w - 2 * H, H),
            "ne": (w - H, 0, H, H),
            "e":  (w - H, H, H, h - 2 * H),
            "se": (w - H, h - H, H, H),
            "s":  (H, h - H, w - 2 * H, H),
            "sw": (0, h - H, H, H),
            "w":  (0, H, H, h - 2 * H),
        }.items():
            handles[name].setGeometry(x, y, ww, hh)
        for k in handles.values():
            k.raise_()
        grip.move(w - grip.width(), h - grip.height())
        grip.raise_()

    class _ResizePos(QtCore.QObject):
        def eventFilter(self, obj, event):
            if event.type() == QtCore.QEvent.Resize:
                layout_resize_handles()
            elif event.type() == QtCore.QEvent.WindowStateChange:
                if dialog.isMaximized():
                    max_btn.setText("❐")
                    max_btn.setToolTip("Restore")
                else:
                    max_btn.setText("□")
                    max_btn.setToolTip("Maximize")
            return False
    _rp = _ResizePos(dialog)
    dialog.installEventFilter(_rp)

    refresh()
    list_w.setCurrentRow(0)
    layout_resize_handles()

    code = dialog.exec_()
    if code != QtWidgets.QDialog.Accepted or result["prompt"] is None:
        return 0

    sys.stdout.write(result["prompt"] + "\n")
    sys.stdout.flush()
    try:
        QtWidgets.QApplication.clipboard().setText(result["prompt"])
    except Exception:
        pass  # clipboard is a nice-to-have; stdout is the contract
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pick a job-search routine and emit the prompt that runs it "
                    "(reads run-config.md, never writes it)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args(argv)
    return run_dialog(args.config)


if __name__ == "__main__":
    sys.exit(main())
