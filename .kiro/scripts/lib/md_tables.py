#!/usr/bin/env python3
"""Shared markdown-table surgery for the job-search workspace.

The tracker files (`shortlist.md`, `applied.md`) are hand-editable markdown tables that
must survive round-trips: comments, prose sections, and untouched rows stay byte-identical.
Every script that edits them goes through this module so column resolution happens by
HEADER NAME, never by hard-coded index. Hard-coded indices are how the Tier-6 writer ended
up shifting every cell by one.

Canonical field names (resolved via ALIASES) — the same code works on shortlist tiers,
`## Applied`, `## Saved`, and `## Rejected` despite their differing headers:
    status date company role raw location apply notes reason comment
"""
import json
import re
from pathlib import Path

SEP_RE = re.compile(r"^\s*\|(\s*:?-+:?\s*\|)+\s*$")
ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# canonical name -> header spellings seen across the files, in priority order.
# "" is the blank-header status-box column in shortlist tables.
ALIASES = {
    # "" is the blank-header status-box column in shortlist tiers ([ ] / [x] / [nope]);
    # "status" is applied.md's Saved table, where the cell holds transient intent
    # (saved / applied / rejected). The blank spelling is tried first so a shortlist row
    # always resolves to its box even if a "Status" column is ever added alongside.
    "status":   ["", "status"],
    "date":     ["added", "applied", "saved", "rejected", "date"],
    "company":  ["company"],
    "role":     ["role", "title"],
    "raw":      ["raw"],
    "location": ["location"],
    "apply":    ["apply link", "apply", "link"],
    "notes":    ["notes", "note"],
    "simplify": ["simplify"],
    "reason":   ["reason"],
    "comment":  ["comment", "comments"],
}


_CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")


def split_cells(line):
    """'| a | b |' -> ['a','b'] (outer pipes stripped, cells trimmed).

    Splits on UNESCAPED pipes only. `esc()` writes a literal pipe inside a cell as `\\|`
    (a multi-location job like "Toronto | Vancouver" is the common case), and splitting
    naively on every `|` tore those rows into extra columns — reading every following cell
    from the wrong place and tripping the ragged-row check. The escaped form is kept in the
    cell as-is so a read/render round-trip is byte-stable; `esc()` is idempotent so a
    re-written value doesn't double-escape.
    """
    t = line.strip()
    return [c.strip() for c in _CELL_SPLIT_RE.split(t[1:-1])]


def row_md(cells):
    return "| " + " | ".join(cells) + " |"


def esc(s):
    """Escape a value for a single-line, pipe-delimited markdown cell.

    Idempotent on the pipe: an already-escaped `\\|` is left alone rather than becoming
    `\\\\|`, so a value read from a cell (which keeps its escaped form, see `split_cells`)
    and written back stays stable.
    """
    s = (s or "").replace("\n", " ")
    s = re.sub(r"(?<!\\)\|", r"\\|", s)
    return s.strip()


class Row:
    __slots__ = ("line_idx", "cells", "table")

    def __init__(self, line_idx, cells, table):
        self.line_idx = line_idx
        self.cells = cells
        self.table = table

    def get(self, field, default=""):
        i = self.table.col(field)
        if i is None or i >= len(self.cells):
            return default
        return self.cells[i]

    def set(self, field, value):
        i = self.table.col(field)
        if i is None:
            raise KeyError(f"table {self.table.heading!r} has no {field!r} column")
        while len(self.cells) <= i:
            self.cells.append("")
        self.cells[i] = esc(value)

    def render(self):
        return row_md(self.cells)

    def __repr__(self):
        return f"<Row {self.get('company')!r} {self.get('role')!r} @{self.line_idx}>"


class Table:
    def __init__(self, heading, header_idx, sep_idx, headers, rows_raw, lines):
        self.heading = heading
        self.header_idx = header_idx
        self.sep_idx = sep_idx
        self.headers = headers
        self._cols = {h.strip().lower(): i for i, h in enumerate(headers)}
        self.rows = [Row(idx, cells, self) for idx, cells in rows_raw]
        self.ncol = len(headers)
        self._lines = lines

    def ragged_rows(self):
        """Rows whose cell count doesn't match the header: [(line_idx, n_cells)].

        Resolving columns by name stops this module from WRITING a shifted row, but it
        can't detect one that is already shifted on disk — `Row.get` just returns "" past
        the end, so a corrupted row reads as partially empty instead of raising. Callers
        surface this so the corruption is visible rather than silently absorbed.
        """
        return [(r.line_idx, len(r.cells)) for r in self.rows if len(r.cells) != self.ncol]

    def col(self, field):
        """Resolve a canonical field name to a column index, or None."""
        for spelling in ALIASES.get(field, [field]):
            if spelling in self._cols:
                return self._cols[spelling]
        return None

    def has(self, field):
        return self.col(field) is not None

    @property
    def first_data_idx(self):
        return self.sep_idx + 1

    @property
    def end_idx(self):
        """Exclusive line index where the table's data rows stop."""
        return self.rows[-1].line_idx + 1 if self.rows else self.sep_idx + 1

    def build(self, **fields):
        """Build a cell list for this table from canonical field names.
        Fields the table has no column for are silently dropped, so one call site
        can feed tables with different schemas."""
        cells = [""] * self.ncol
        for field, val in fields.items():
            i = self.col(field)
            if i is not None:
                cells[i] = val if field == "apply" else esc(val)
        return cells

    def __repr__(self):
        return f"<Table {self.heading!r} rows={len(self.rows)}>"


def parse_tables(lines):
    """Parse every markdown table in `lines`, tagging each with its nearest heading."""
    tables = []
    last_heading = ""
    i = 0
    while i < len(lines):
        m = HEADING_RE.match(lines[i])
        if m:
            last_heading = m.group(2).strip()
        if SEP_RE.match(lines[i]) and i > 0 and ROW_RE.match(lines[i - 1]):
            header_idx = i - 1
            headers = split_cells(lines[header_idx])
            rows_raw = []
            j = i + 1
            while j < len(lines) and ROW_RE.match(lines[j]) and not SEP_RE.match(lines[j]):
                rows_raw.append((j, split_cells(lines[j])))
                j += 1
            tables.append(Table(last_heading, header_idx, i, headers, rows_raw, lines))
            i = j
            continue
        i += 1
    return tables


def find_table(lines, heading_prefix):
    """First table whose heading starts with `heading_prefix` (case-insensitive)."""
    p = heading_prefix.lstrip("#").strip().lower()
    for t in parse_tables(lines):
        if t.heading.lower().startswith(p):
            return t
    return None


def find_tables(lines, heading_re):
    """All tables whose heading matches `heading_re`."""
    rx = re.compile(heading_re, re.I)
    return [t for t in parse_tables(lines) if rx.match(t.heading)]


# ---------- mutation ----------
# Line indices shift on insert/delete, so mutations are applied in one batch and the
# caller re-parses afterwards. Never hold a Row across an apply().

def delete_lines(lines, indices):
    """Delete the given line indices. Returns a new list."""
    drop = set(indices)
    return [l for i, l in enumerate(lines) if i not in drop]


def insert_rows(lines, heading_prefix, new_rows, newest_first=True):
    """Insert rendered row strings into the table under `heading_prefix`.

    newest_first=True inserts at the top of the data block (the convention in
    `## Applied` / shortlist tiers); False appends after the last existing row.
    """
    if not new_rows:
        return lines
    t = find_table(lines, heading_prefix)
    if t is None:
        raise KeyError(f"no table found under heading {heading_prefix!r}")
    at = t.first_data_idx if newest_first else t.end_idx
    return lines[:at] + list(new_rows) + lines[at:]


# ---------- URL / dedup helpers ----------

# Markdown allows both `[text](url)` and `[text](<url>)`; tracker.html writes the angle
# form. The brackets are delimiters, not part of the URL — leaving a trailing `>` attached
# meant the same link written two ways produced two different dedup keys.
URL_MD_RE = re.compile(r"\[[^\]]*\]\(\s*<?(https?://[^)>\s]+)>?\s*\)")
BARE_URL_RE = re.compile(r"<?(https?://[^\s)>]+)>?")

TRACKING_PARAMS = ("ref", "gh_src", "utm_source", "utm_medium", "utm_campaign", "src")


def extract_url(cell):
    """Pull a URL out of a markdown cell ('[Apply](url)' or a bare URL)."""
    if not cell:
        return ""
    m = URL_MD_RE.search(cell) or BARE_URL_RE.search(cell)
    return m.group(1) if m else ""


def clean_url(cell_or_url):
    """Normalize a cell or URL into a bare comparable URL (no query, no fragment, no
    trailing slash), so the same link written two ways keys identically."""
    raw = (cell_or_url or "").strip()
    if not raw:
        return ""
    # A markdown cell ('[Apply](url)') or any text with a URL embedded in it needs the
    # URL pulled out first; an already-bare URL is used as-is.
    url = raw if raw.startswith("http") else extract_url(raw)
    if not url:
        return ""
    # A bare URL can still arrive wrapped in angle brackets (pasted from markdown).
    url = url.strip().lstrip("<").rstrip(">")
    return re.split(r"[?#]", url, maxsplit=1)[0].rstrip("/")


# A stable ATS identifier survives confirm-vs-listing paths and tracking params, so it
# beats the full URL as a dedup key.
#
# Codes are NAMESPACED (`greenhouse:lyft:123`, not `123`). A bare per-ATS id collides
# across orgs — greenhouse job 123 at two companies, or every NVIDIA Workday link
# reducing to the site name because the req id lives in the query string. A collision
# here is silent and expensive: it makes one role block an unrelated one from being
# added, and makes crossref mark the wrong row applied.
#
# Each entry is (ats-name, regex, group-count). Every capture group participates in the
# code, so the org/tenant is part of the key.
ATS_CODE_PATTERNS = [
    ("applytojob", r"([A-Za-z0-9]+)\.applytojob\.com/apply/(?:confirm/)?([A-Za-z0-9]+)"),
    ("applytojob", r"applytojob\.com/apply/(?:confirm/)?([A-Za-z0-9]+)"),
    ("greenhouse", r"greenhouse\.io/([^/]+)/jobs/(\d+)"),
    ("greenhouse", r"[?&]gh_jid=(\d+)"),
    ("ashby",      r"ashbyhq\.com/([^/]+)/([0-9a-f-]{16,})"),
    ("recruitee",  r"([A-Za-z0-9-]+)\.recruitee\.com/o/([a-z0-9-]+)"),
    ("recruitee",  r"recruitee\.com/o/([a-z0-9-]+)"),
    ("lever",      r"lever\.co/([^/]+)/([0-9a-f-]{16,})"),
    ("linkedin",   r"linkedin\.com/jobs/view/(\d+)"),
    ("builtin",    r"builtin(?:[a-z]*)\.com/job/[a-z0-9-]+/(\d+)"),
    # Workday keeps the requisition id in the query string (?q=JR123 / ?jobId=...), so
    # stripping the query left only the tenant/site name — one code for every role.
    ("workday",    r"([a-z0-9-]+)\.(?:wd\d+\.)?myworkdayjobs\.com.*?\b(JR-?\d+)"),
    ("workday",    r"([a-z0-9-]+)\.(?:wd\d+\.)?myworkdayjobs\.com.*?[?&](?:q|jobId)=([\w-]+)"),
    ("workday",    r"myworkdayjobs\.com/.*/([\w-]+/[\w-]+)$"),
]


def ats_code(cell_or_url):
    """Stable, org-namespaced ATS code for dedup; falls back to the cleaned URL.

    Returns e.g. "greenhouse:lyft:123", "workday:nvidia:JR1998773". The fallback is the
    cleaned URL, which means two roles sharing one landing page (a `[Careers site](...)`
    link, per playbook §4) still collide — so callers must not treat a fallback code as
    proof of identity. `is_strong_code()` reports whether a real ATS id was recognised.
    """
    raw = extract_url(cell_or_url) or (cell_or_url or "").strip()
    if not raw:
        return ""
    bare = re.split(r"[?#]", raw, maxsplit=1)[0]
    for name, pat in ATS_CODE_PATTERNS:
        m = re.search(pat, raw, re.I) or re.search(pat, bare, re.I)
        if m:
            parts = [g for g in m.groups() if g]
            return ":".join([name] + [p.lower() for p in parts])
    return bare.rstrip("/")


def is_strong_code(code):
    """True when `code` is a recognised ATS id rather than a bare-URL fallback.

    A fallback code is just a URL, so it can be shared by different roles at the same
    company; only a strong code is safe to treat as a unique role identity.
    """
    if not code:
        return False
    return not code.startswith("http")


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def norm_key(company, title):
    return (norm(company), norm(title))


def row_keys(row):
    """Dedup keys for a row: ({company+role, company+raw}, ats_code).

    The returned code is a STRONG code only. A bare-URL fallback is deliberately dropped:
    the playbook links roles with no individual posting page as `[Careers site](url)`, so
    several distinct roles at one company can share that URL, and using it as an identity
    key made one of them shadow the others. Those rows fall through to company+title
    matching instead, which is the correct granularity for them.
    """
    keys = set()
    co = row.get("company")
    for t in (row.get("role"), row.get("raw")):
        if co and t:
            keys.add(norm_key(co, t))
    code = ats_code(row.get("apply"))
    return keys, (code if is_strong_code(code) else "")


# ---------- status box ----------

def is_applied(cell):
    return bool(re.search(r"\[\s*x\s*\]", cell or "", re.I))


def is_rejected(cell):
    return bool(re.search(r"\[\s*nope\s*\]", cell or "", re.I))


def is_open(cell):
    c = cell or ""
    return not is_applied(c) and not is_rejected(c) and "[" in c


def has_no_status(cell):
    """True when the status cell holds no box at all.

    Such a row is invisible to every stage: `is_open` is False so the liveness sweep skips
    it, and `is_applied`/`is_rejected` are False so migrate_resolved skips it too. It sits
    in the shortlist forever, never checked and never resolved. Callers use this to WARN
    rather than silently pass over it.
    """
    return "[" not in (cell or "")


def read_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().split("\n")


# ---------- backups ----------
# The tracker data files (shortlist.md, applied.md, thoughts.md, manual.md) are all
# gitignored, so git is NOT an undo for them: a wrong `--apply` is unrecoverable. Every
# mutator therefore snapshots the file it is about to overwrite. Cheap insurance — these
# files are a few KB — and it turns "the dry run looked right" into "I can get it back
# either way".
BACKUP_DIR = ".kiro/backups"
BACKUP_KEEP = 20


def backup_file(path, backup_dir=BACKUP_DIR, keep=BACKUP_KEEP, stamp=None):
    """Copy `path` to `<backup_dir>/<name>.<UTC timestamp>.bak`. Returns the backup path,
    or "" when there is nothing to back up (file absent).

    Keeps the newest `keep` snapshots per file and prunes older ones so the directory
    can't grow without bound.
    """
    import shutil
    from datetime import datetime, timezone

    src = Path(path)
    if not src.exists():
        return ""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Names are built so that a plain lexicographic sort is chronological, because the
    # prune below relies on that ordering — sorting wrongly would delete the NEWEST
    # snapshots, the opposite of the intent. Hence a fixed-width zero-padded sequence
    # rather than a bare "-2" suffix ("-10" sorts before "-2"), and the sequence is always
    # present so every name has the same shape. Second-resolution stamps collide whenever a
    # pass writes one file twice in a second, so the sequence is what keeps those distinct.
    # The sequence must be allocated as (highest existing for this stamp) + 1, NOT as the
    # first free slot: pruning removes the lowest-numbered files, so a first-free search
    # would reuse a just-freed low number and the snapshots would cycle, overwriting each
    # other and scrambling chronological order.
    prefix = f"{src.name}.{stamp}."
    used = []
    for p in dest_dir.glob(f"{prefix}*.bak"):
        part = p.name[len(prefix):-len(".bak")]
        if part.isdigit():
            used.append(int(part))
    seq = max(used) + 1 if used else 0
    if seq > 999:
        return ""              # absurd; skip the snapshot rather than spin
    dest = dest_dir / f"{src.name}.{stamp}.{seq:03d}.bak"
    shutil.copy2(src, dest)

    if keep and keep > 0:
        existing = sorted(dest_dir.glob(f"{src.name}.*.bak"))
        for old in existing[:-keep]:
            try:
                old.unlink()
            except OSError:
                pass
    return str(dest)


def write_lines(path, lines, backup=True, backup_dir=BACKUP_DIR):
    """Overwrite `path` with `lines`, snapshotting the previous contents first.

    Pass backup=False only when the caller has already taken its own snapshot (e.g. a
    multi-file write that backs everything up before touching any of it).
    """
    if backup:
        backup_file(path, backup_dir=backup_dir)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_json(path, obj):
    """Write `obj` as JSON to `path`, or to stdout when `path` is '-'.

    Scripts keep their human prose on stderr and use this for the structured
    plan/results contract: `--json plan.json` for a durable artifact, or
    `--json -` to stream JSON to stdout for an LLM/pipe consumer.
    """
    if path == "-":
        print(json.dumps(obj, indent=1))
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
