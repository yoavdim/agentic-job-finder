#!/usr/bin/env python3
"""Parse rendered Simplify tracker text into structured application records.

Input : a text file containing the innerText of the Simplify tracker list
        container (see SKILL.md for how to capture it via the Tab Share
        extension /eval endpoint).
Output: JSON (stdout or --json path) and a markdown table (--md path).

Each Simplify list row renders as a block ending in the literal token
"Status", with this shape (blank lines stripped):
    <title>
    <company>            e.g. "Example Co", "<prefix> - Example Co - Career Page", "<n> Foo logo"
    <location>           (optional)
    Saved
    <saved date m/d/yy>
    Applied
    [<applied date m/d/yy>]   <- present only if actually applied
    Screen
    Interview
    Offer
    Status
"""
import argparse, json, re, sys
from datetime import datetime
from pathlib import Path

# Table primitives (esc/split/find/clean_url/ats_code/row_md/backup) live in md_tables.py.
# This used to be a synced COPY, kept "in agreement" only by a parity test — and the copies
# had already drifted (parse_tracker's clean_url didn't strip angle brackets, so a
# `[x](<url>)` link deduped differently here than everywhere else). Importing the real
# module removes the drift risk instead of just detecting it after the fact.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "lib"))
import md_tables as _M

esc = _M.esc
row_md = _M.row_md
clean_url = _M.clean_url
ats_code = _M.ats_code
is_strong_code = _M.is_strong_code
backup_file = _M.backup_file
BACKUP_DIR, BACKUP_KEEP = _M.BACKUP_DIR, _M.BACKUP_KEEP


def split_md_row(line):
    return _M.split_cells(line) if re.match(r"^\|(.+)\|\s*$", line.rstrip()) else None


def find_section(lines, heading_prefix):
    t = _M.find_table(lines, heading_prefix)
    return None if t is None else (t.header_idx, t.sep_idx, t.first_data_idx, t.end_idx)


def load_tokens(path):
    with open(path, encoding="utf-8") as fh:
        lines = [l.rstrip() for l in fh]
    # Drop the page header up to and including the "List" view toggle if present.
    if "List" in lines:
        lines = lines[lines.index("List") + 1:]
    return [l for l in lines if l.strip() != ""]


def split_blocks(tokens):
    blocks, cur = [], []
    for t in tokens:
        cur.append(t)
        if t == "Status":
            blocks.append(cur)
            cur = []
    return blocks


# Optional per-user normalizations: map a legal/alternate name that Simplify
# reports to the short name your existing rows use, e.g.
#   "example legal name inc": "Example Co",
COMPANY_ALIASES = {
}

def clean_company(c):
    c = re.sub(r"\s*-\s*Career Page$", "", c)
    c = re.sub(r"^\d+\s+", "", c)        # strip leading req numbers ("207 Example ...")
    c = re.sub(r"\s*logo$", "", c)        # strip trailing " logo"
    c = c.strip()
    return COMPANY_ALIASES.get(c.lower(), c)


def parse_date(s):
    try:
        return datetime.strptime(s, "%m/%d/%y")
    except (ValueError, TypeError):
        return None


def iso(s):
    d = parse_date(s)
    return d.strftime("%Y-%m-%d") if d else (s or "")


def parse_block(block):
    if "Saved" not in block:
        return None
    si = block.index("Saved")
    head = block[:si]
    title = head[0] if len(head) >= 1 else ""
    company = head[1].strip() if len(head) >= 2 else ""
    location = head[2] if len(head) >= 3 else ""
    saved = block[si + 1] if si + 1 < len(block) else ""
    applied = ""
    if si + 2 < len(block) and block[si + 2] == "Applied":
        nxt = block[si + 3] if si + 3 < len(block) else ""
        if re.match(r"\d+/\d+/\d+", nxt):
            applied = nxt
    return {
        "title": title.strip(),
        "company": clean_company(company),
        "company_raw": company,
        "location": location.strip(),
        "saved": saved,
        "applied": applied,
    }


def norm_key(company, title):
    """Dedup key: normalized company + title. Case-insensitive, whitespace-collapsed."""
    def n(s):
        return re.sub(r"\s+", " ", (s or "").strip().lower())
    return (n(company), n(title))


# ---------- merge into an existing applied.md ----------
# Applied : append-only. Rows already there are never touched; newly-applied records
#           are added, carrying the comment/URL/id of a matching Saved row across.
# Saved   : an EXACT MIRROR of the Simplify tracker. Rebuilt from the current records
#           on every sync — rows still saved on Simplify are re-emitted (carrying
#           their comment, Apply URL and Simplify id), and rows no longer saved on
#           Simplify are dropped. We don't track rejections locally: a rejected
#           entry just stops appearing as "saved" on the next sync (Simplify is the
#           source of truth for status).

# ---------- merge into an existing applied.md ----------
# Applied : append-only. Rows already there are never touched; newly-applied records
#           are added, carrying the comment/URL/id of a matching Saved row across.
# Saved   : an EXACT MIRROR of the Simplify tracker. Rebuilt from the current records
#           on every sync — rows still saved on Simplify are re-emitted (carrying
#           their comment, Apply URL and Simplify id), and rows no longer saved on
#           Simplify are dropped. We don't track rejections locally: a rejected
#           entry just stops appearing as "saved" on the next sync (Simplify is the
#           source of truth for status).


# Derive a human title from an apply URL's slug, when present.
# applytojob: /apply/<code>/<Title-Slug>  → "Title Slug". Confirm pages
# (/apply/confirm/<code>) have no slug → returns "" (caller keeps the shorthand).
def slug_title(u):
    bare = re.split(r"[?#]", (u or "").strip(), maxsplit=1)[0]
    m = re.search(r"applytojob\.com/apply/([A-Za-z0-9]+)/([^/]+)/?$", bare)
    if m and m.group(1) != "confirm":
        return m.group(2).replace("-", " ").strip()
    # greenhouse/ashby slugs aren't human titles; skip them
    return ""


def header_cols(lines, sep_idx):
    """Map lowercased header name -> column index for the table whose separator is sep_idx."""
    hdr = split_md_row(lines[sep_idx - 1]) or []
    return {h.strip().lower(): i for i, h in enumerate(hdr)}


def parse_existing(lines, section_prefix):
    """Return ({rawkey: row}, {code: row}, loc, cols). Each row = {cells, idx}.
    Dedup keys: rawkey = norm(company, RAW title) — always stable vs what Simplify
    re-emits; code = ATS code from the Apply URL when present."""
    loc = find_section(lines, section_prefix)
    tmap, umap, cols = {}, {}, {}
    if not loc:
        return tmap, umap, loc, cols
    _, sep, first, end = loc
    cols = header_cols(lines, sep)
    ci_co = cols.get("company", 1)
    ci_ro = cols.get("role", 2)
    ci_raw = cols.get("raw")
    ci_ap = cols.get("apply")
    for idx in range(first, end):
        cells = split_md_row(lines[idx])
        if not cells or len(cells) <= ci_ro:
            continue
        row = {"cells": cells, "idx": idx}
        # dedup by RAW title when the column exists, else fall back to Role
        rawt = cells[ci_raw] if ci_raw is not None and len(cells) > ci_raw and cells[ci_raw].strip() else cells[ci_ro]
        tmap[norm_key(cells[ci_co], rawt)] = row
        if ci_ap is not None and len(cells) > ci_ap:
            code = ats_code(cells[ci_ap])   # stable ATS code, not the full URL
            # Strong codes only: a bare-URL fallback can be shared by distinct roles at
            # one company, so indexing it would let one row shadow the others.
            if is_strong_code(code):
                umap[code] = row
    return tmap, umap, loc, cols


def fmt_apply(url):
    return f"[Apply]({url})" if url else ""


SIMPLIFY_TRACKER_URL = "https://simplify.jobs/tracker?id="
SIMPLIFY_LINK_RE = re.compile(r"\[Simplify\]\(https://simplify\.jobs/tracker\?id=([0-9a-fA-F-]{36})\)")


def fmt_simplify(simplify_id):
    """Markdown link into the Simplify tracker entry for an application."""
    return f"[Simplify]({SIMPLIFY_TRACKER_URL}{simplify_id})" if simplify_id else ""


def fmt_track(apply_url, simplify_id):
    """Applied-table cell content: the Apply link plus the Simplify tracker link
    in the same cell (the column keeps its 'Apply' header)."""
    parts = [fmt_apply(apply_url), fmt_simplify(simplify_id)]
    return " · ".join(p for p in parts if p)


def build_cells(cols, ncol, *, date="", company="", title="", raw="", location="", apply_url="", comment="", reason="", simplify_id=""):
    """Build a row's cell list positioned per the table's header columns.
    `title` = display/inferred Role; `raw` = verbatim Simplify title (dedup anchor).
    The applied table's Apply cell carries Apply + Simplify links together (no separate
    Simplify column); the Saved table splits them into Apply + Simplify columns."""
    cells = [""] * ncol
    cells[0] = date  # date/status column
    if "company" in cols: cells[cols["company"]] = esc(company)
    if "role" in cols: cells[cols["role"]] = esc(title)
    if "raw" in cols: cells[cols["raw"]] = esc(raw or title)
    if "location" in cols: cells[cols["location"]] = esc(location)
    if "apply" in cols:
        if "simplify" in cols:
            cells[cols["apply"]] = fmt_apply(apply_url)
            cells[cols["simplify"]] = fmt_simplify(simplify_id)
        else:
            cells[cols["apply"]] = fmt_track(apply_url, simplify_id)
    if "reason" in cols: cells[cols["reason"]] = reason
    if "comment" in cols: cells[cols["comment"]] = comment
    return cells

def row_md(cells):
    return "| " + " | ".join(cells) + " |"


def _saved_key_of(saved_row, s_cols):
    """The (company, raw-title) key a Saved-table row is indexed under in `s_t`.

    parse_existing() keys rows by RAW title when that column exists, so this has to
    mirror that choice exactly — otherwise mirror bookkeeping reports phantom drops.
    """
    cells = saved_row["cells"]
    get = lambda name: (cells[s_cols[name]]
                        if name in s_cols and len(cells) > s_cols[name] else "")
    raw = get("raw").strip() or get("role")
    return norm_key(get("company"), raw)


DEFAULT_MAX_DROP_FRAC = 0.30


def saved_row_count(lines):
    """How many data rows the Saved table currently holds."""
    t, _, loc, _ = parse_existing(lines, "## Saved")
    return len(t) if loc else 0


def mirror_guard_errors(lines, recs, total_jobs=None, max_drop_frac=DEFAULT_MAX_DROP_FRAC):
    """Reasons the Saved mirror should be refused, as a list of human-readable strings.

    The Saved table is rebuilt from `recs` on every sync, so a capture that under-scrolled
    the lazy-loading tracker list silently DELETES rows (and their comments, apply URLs and
    Simplify ids). Two independent checks:

      1. `total_jobs` — the "N TOTAL JOBS" count from the tracker header. Fewer parsed
         records than that means the capture is definitively incomplete.
      2. `max_drop_frac` — even without a known total, refuse when the mirror would drop
         an implausible share of the existing Saved rows.

    An empty `recs` against a non-empty Saved table is always refused: it's the signature
    of a failed capture, not of the user un-saving everything at once.
    """
    errors = []
    n = len(recs)

    if total_jobs is not None and n < total_jobs:
        errors.append(f"capture looks truncated: parsed {n} record(s) but the tracker "
                      f"header reports {total_jobs} total job(s)")

    existing_saved = saved_row_count(lines)
    if existing_saved:
        still_saved = sum(1 for r in recs if not r.get("applied"))
        if not n:
            errors.append(f"parsed 0 records while the Saved table holds {existing_saved} row(s) "
                          f"— the mirror would delete all of them")
        elif still_saved == 0:
            errors.append(f"capture contains no still-saved records while the Saved table holds "
                          f"{existing_saved} row(s) — the mirror would delete all of them")
        else:
            # An upper bound on drops: rows that can't be matched by the capture.
            dropped = max(0, existing_saved - still_saved)
            frac = dropped / existing_saved
            if frac > max_drop_frac:
                errors.append(f"mirror would drop {dropped}/{existing_saved} Saved row(s) "
                              f"({frac:.0%} > {max_drop_frac:.0%} limit)")
    return errors


def merge(lines, recs, warn, urls=None, metadata=None):
    """Merge Simplify records into applied.md.

    Applied is append-only: existing rows are never touched, and newly-applied
    records are added (carrying the comment/URL/id of a matching Saved row across).

    Saved is an EXACT MIRROR of the Simplify tracker: it is rebuilt from the current
    records on every sync. Rows still saved on Simplify are re-emitted (carrying their
    comment, Apply URL and Simplify id); rows no longer saved on Simplify are dropped.
    Rejections aren't tracked here — a rejected entry simply stops being "saved".
    `urls`/`metadata` (optional) only matter for brand-new rows.
    Dedup precedence: URL match (if both sides have one) > company+title."""
    urls = urls or {}
    metadata = metadata or {}
    lines = list(lines)     # never mutate the caller's list: a failed merge must leave the
                            # input untouched so the caller can retry or abort cleanly
    a_t, a_u, a_loc, a_cols = parse_existing(lines, "## Applied")
    s_t, s_u, s_loc, s_cols = parse_existing(lines, "## Saved")

    if a_loc is None or s_loc is None:
        warn.append("Could not locate '## Applied' and/or '## Saved' tables — aborting merge.")
        return lines, {"added_applied": 0, "added_saved": 0, "promoted": 0,
                       "url_matched": 0, "id_linked": 0, "mirror_saved": 0,
                       "mirror_dropped": 0, "mirror_dropped_rows": []}

    existing_url = {**a_u, **s_u}
    new_applied, new_saved, promotions, mirror_saved = [], [], [], []
    url_matched = 0

    def sid_for(key):
        meta = metadata.get(key)
        return meta.get("id", "") if isinstance(meta, dict) else ""

    def old_vals(saved_row):
        """Pull comment/raw/role/URL/Simplify-id from an existing Saved table row."""
        oc = lambda name: (saved_row["cells"][s_cols[name]]
                           if name in s_cols and len(saved_row["cells"]) > s_cols[name] else "")
        sid = ""
        m = SIMPLIFY_LINK_RE.search(oc("simplify") or "")
        if m:
            sid = m.group(1)
        return {"comment": oc("comment"), "raw": oc("raw") or oc("role"),
                "role": oc("role"), "url": clean_url(oc("apply")), "sid": sid}

    for r in recs:
        raw = r["title"]                    # Simplify's verbatim title = the dedup anchor
        key = norm_key(r["company"], raw)
        u = clean_url(urls.get(key, ""))
        if not u:
            meta = metadata.get(key)
            if isinstance(meta, dict):
                u = clean_url(meta.get("url", ""))
        code = ats_code(u) if u else ""
        if not is_strong_code(code):
            code = ""               # weak fallback: fall through to company+title
        has_applied = bool(r["applied"])

        # Resolve which table this record already lives in. Dedup precedence is
        # ATS code (robust to confirm-vs-listing paths + tracking params) over
        # company+RAW title, but the ANSWER matters as much as the match: a hit in
        # Applied means "skip, append-only", while a hit in Saved means "re-emit into
        # the mirror". Treating a Saved hit as a plain skip deleted the row, because
        # the mirror is rebuilt only from what this loop collects.
        applied_hit = (code in a_u if code else False) or key in a_t
        saved_row = None
        if code and code in s_u:
            saved_row = s_u[code]
        elif key in s_t:
            saved_row = s_t[key]
        if code and (code in a_u or code in s_u):
            url_matched += 1

        if applied_hit:
            continue                       # already applied — row left untouched

        if saved_row is not None:
            ov = old_vals(saved_row)
            if has_applied:
                promotions.append((key, r, u, ov, _saved_key_of(saved_row, s_cols)))
            else:
                # still saved → keep in the mirror, carrying comment/URL/id across
                mirror_saved.append({**r, "_url": u or ov["url"], "_raw": ov["raw"] or raw,
                                     "title": ov["role"] or ov["raw"] or raw,
                                     "_sid": ov["sid"], "_comment": ov["comment"],
                                     "_mirror_key": _saved_key_of(saved_row, s_cols)})
            continue

        # New row: Role = slug-inferred title when available, else the raw; Raw = verbatim.
        st = slug_title(u)
        r = {**r, "_url": u, "_raw": raw, "title": st or raw, "_sid": sid_for(key)}
        (new_applied if has_applied else new_saved).append(r)

    # promotions: the old Saved row leaves the mirror and re-enters as Applied,
    # carrying comment + raw + url + Simplify id across
    for key, r, u, ov, _mkey in promotions:
        st = slug_title(u)
        new_applied.append({**r, "_comment": ov["comment"], "_url": u or ov["url"],
                            "_raw": ov["raw"] or r["title"], "title": st or ov["raw"] or r["title"],
                            "_sid": ov["sid"] or sid_for(key)})

    def srt(rs, f):
        return sorted(rs, key=lambda x: parse_date(x[f]) or datetime.min, reverse=True)

    a_ncol = len(split_md_row(lines[a_loc[1]]))
    s_ncol = len(split_md_row(lines[s_loc[1]]))
    applied_md = [row_md(build_cells(a_cols, a_ncol, date=iso(r["applied"]), company=r["company"],
                                     title=r["title"], raw=r.get("_raw", r["title"]), location=r["location"],
                                     apply_url=r.get("_url", ""), comment=r.get("_comment", ""),
                                     simplify_id=r.get("_sid", ""))) for r in srt(new_applied, "applied")]
    saved_md = [row_md(build_cells(s_cols, s_ncol, date=iso(r["saved"]), company=r["company"],
                                   title=r["title"], raw=r.get("_raw", r["title"]), location=r["location"],
                                   apply_url=r.get("_url", ""), comment=r.get("_comment", ""),
                                   simplify_id=r.get("_sid", ""))) for r in srt(mirror_saved + new_saved, "saved")]

    # Name every row the mirror is about to drop, so the dry run shows exactly what would
    # be lost rather than just a count. Keys come from the MATCHED Saved rows (not from
    # the record's re-derived key), because a URL match can pair a record with a Saved row
    # whose title differs — using the record's key there would report a phantom drop.
    kept_keys = {r["_mirror_key"] for r in mirror_saved if r.get("_mirror_key")}
    kept_keys |= {mkey for _k, _r, _u, _ov, mkey in promotions if mkey}
    promoted_keys = {mkey for _k, _r, _u, _ov, mkey in promotions if mkey}
    mirror_dropped_rows = sorted(f"{k[0]} — {k[1]}" for k in s_t
                                 if k not in kept_keys and k not in promoted_keys)

    # The Saved table is an exact mirror: drop the entire old block, then re-emit the
    # current saved set. Rows rejected/deleted in Simplify just disappear here — we
    # don't keep a local rejection ledger (Simplify is the source of truth for status).
    # mirror_guard_errors() must have vetted `recs` before this point: a truncated
    # capture reaching here would delete tracked rows.
    del lines[s_loc[2]:s_loc[3]]
    a2 = find_section(lines, "## Applied")
    for row in reversed(applied_md):
        lines.insert(a2[2], row)
    s2 = find_section(lines, "## Saved")
    for row in reversed(saved_md):
        lines.insert(s2[2], row)

    # near-duplicate heuristic (only when we couldn't code/raw-match)
    seen = {}
    for k in set(a_t) | set(s_t):
        seen.setdefault(k[0], []).append(k[1])
    for r in new_applied + new_saved:
        c, t = norm_key(r["company"], r.get("_raw", r["title"]))
        if any(et != t and (et in t or t in et) for et in seen.get(c, [])):
            warn.append(f"possible near-duplicate at {r['company']}: added '{r.get('_raw', r['title'])}' "
                        f"— existing similar title present (reconcile by hand?)")

    return lines, {"added_applied": len(new_applied), "added_saved": len(new_saved),
                   "promoted": len(promotions), "url_matched": url_matched,
                   "id_linked": sum(1 for r in new_applied + new_saved if r.get("_sid")),
                   "mirror_saved": len(mirror_saved),
                   "mirror_dropped": len(mirror_dropped_rows),
                   "mirror_dropped_rows": mirror_dropped_rows}


def report_new(lines, recs):
    """Return the records that would be added (not matched by company+title in either table).
    Used by the skill to know which rows to click-through for URLs (URL-fetch only for new rows)."""
    a_t, _, a_loc, _ = parse_existing(lines, "## Applied")
    s_t, _, s_loc, _ = parse_existing(lines, "## Saved")
    if a_loc is None or s_loc is None:
        return []
    known = set(a_t) | set(s_t)
    out = []
    for r in recs:
        if norm_key(r["company"], r["title"]) not in known:
            out.append(r)
    return out


def update_sync_header(lines, synced_date):
    """Recompute the sync header from the live table sizes. Returns a new line list.

    Counts actual ROWS, not `parse_existing`'s dedup-key map: that map is keyed by
    (company, raw-title), and a company can legitimately have several distinct
    applications with the same normalized key (e.g. re-applying to the same title months
    apart, or Simplify's own real duplicate rows — kept as-is per playbook §5). Counting
    len(that dict) silently collapsed those down to one, so the header undercounted the
    real total by however many such repeats existed (found live: 129 actual rows reported
    as 111 — 18 rows from 11 repeated company+title pairs went missing from the count).
    """
    lines = list(lines)
    for i, l in enumerate(lines):
        if l.startswith("**Last synced from Simplify:**"):
            a_loc = find_section(lines, "## Applied")
            s_loc = find_section(lines, "## Saved")
            n_applied = (a_loc[3] - a_loc[2]) if a_loc else 0
            n_saved = (s_loc[3] - s_loc[2]) if s_loc else 0
            lines[i] = (f"**Last synced from Simplify:** {synced_date} · {n_applied} applied · "
                        f"{n_saved} saved (not yet applied)")
            break
    return lines


def _add_saved_column(lines, name, position_hint):
    """Insert a column `name` into the Saved table if absent. Idempotent.

    `position_hint(cols, ncol) -> index` chooses where it lands, given the existing
    header map. Every data row gets a blank cell at the same index so the table stays
    rectangular — a ragged row here would be read wrong by every consumer.
    """
    loc = find_section(lines, "## Saved")
    if not loc:
        return lines
    _, sep_idx, first, end = loc
    hdr_idx = sep_idx - 1
    hdr = split_md_row(lines[hdr_idx]) or []
    cols = {h.strip().lower(): i for i, h in enumerate(hdr)}
    if name.lower() in cols:
        return lines

    insert_at = position_hint(cols, len(hdr))
    hdr.insert(insert_at, name)
    lines[hdr_idx] = row_md(hdr)
    sep = split_md_row(lines[sep_idx])
    if sep:
        sep.insert(insert_at, "---")
        lines[sep_idx] = row_md(sep)
    for i in range(first, end):
        cells = split_md_row(lines[i])
        if cells:
            while len(cells) < insert_at:
                cells.append("")
            cells.insert(insert_at, "")
            lines[i] = row_md(cells)
    return lines


def migrate_schema(lines):
    """One-time, idempotent schema upgrades for applied.md's Saved table.

    - `Simplify` — the tracker id. It is the handle for every push-back, so a row without
      one cannot be acted on (empty until a sync captures it).
    - `Status` — the user's intent for the row, set in `tracker.html`: blank/`saved` (leave
      it alone), `applied` (push mark_applied), `rejected` (push a delete). This is
      TRANSIENT: the next sync reads it, acts on it, and the row then either leaves the
      table or reverts to `saved`. Holding intent in the mirror itself is what lets
      `## Saved` remain the single copy of this list instead of needing a second table.

    The Applied table keeps its `Apply` column — the Simplify link folds into that cell.
    """
    lines = list(lines)
    lines = _add_saved_column(
        lines, "Simplify",
        lambda cols, n: (cols.get("comment") if "comment" in cols
                         else cols["apply"] + 1 if "apply" in cols
                         else cols["raw"] + 1 if "raw" in cols else n))
    lines = _add_saved_column(
        lines, "Status",
        # Status sits immediately before Comment: intent and its reason read together.
        lambda cols, n: cols.get("comment", n))
    return lines


def build_url_maps(raw):
    """Split a raw --urls mapping into (metadata, merge_urls).

    `--urls` accepts either {"Company||Title": url} (legacy) or
    {"Company||Title": {"id": ..., "url": ...}} (with the Simplify id). metadata feeds
    the Tier-6 sync; merge_urls feeds merge()'s URL dedup.
    """
    metadata = normalize_metadata(raw)
    merge_urls = {}
    for k, v in raw.items():
        if "||" in k:
            a, b = k.split("||", 1)
            nk = norm_key(a, b)
            merge_urls[nk] = metadata[nk]["url"] if nk in metadata else ""
        elif isinstance(v, str):
            merge_urls[k] = v
        elif isinstance(v, dict):
            merge_urls[k] = v.get("url", "")
        else:
            merge_urls[k] = ""
    return metadata, merge_urls


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Merge a rendered Simplify tracker capture into applied.md. "
                    "Dry run by default; pass --apply to write.")
    ap.add_argument("input", nargs="?", help="text file with tracker innerText")
    ap.add_argument("--json", help="write parsed records as JSON here")
    ap.add_argument("--md", required=True, help="existing applied.md to merge into (Applied "
                    "append-only; the Saved table is an exact mirror of the Simplify tracker)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="sync date for the markdown header (default: today)")
    ap.add_argument("--urls", help="JSON map {\"company||title\": url} OR "
                    "{\"company||title\": {\"id\": ..., \"url\": ...}} of apply URLs / Simplify "
                    "IDs for NEW rows (captured by clicking through only the new tracker rows)")
    ap.add_argument("--report-new", action="store_true",
                    help="don't merge; print JSON of records that are NEW (not yet in the tables) "
                         "so the caller knows which rows to click-through for URLs, then exit")
    ap.add_argument("--migrate-schema", action="store_true",
                    help="don't merge; just add the 'Simplify' and 'Status' columns to "
                         "applied.md's Saved table if missing, then exit")
    ap.add_argument("--apply", action="store_true",
                    help="write the files (default: dry run, prints the plan only)")
    ap.add_argument("--total-jobs", type=int, default=None,
                    help="the 'N TOTAL JOBS' count from the tracker header. The Saved table is "
                         "an exact mirror, so a truncated capture would DELETE rows; when this "
                         "is given and fewer records were parsed, the merge is refused.")
    ap.add_argument("--max-drop-frac", type=float, default=DEFAULT_MAX_DROP_FRAC,
                    help=f"refuse the mirror if it would drop more than this fraction of the "
                         f"existing Saved rows (default {DEFAULT_MAX_DROP_FRAC}); --force overrides")
    ap.add_argument("--force", action="store_true",
                    help="override the truncated-capture / mirror-drop safety guards")
    args = ap.parse_args(argv)
    if args.input is None and not args.migrate_schema:
        ap.error("input file is required unless --migrate-schema is used")

    recs = []
    if args.input:
        tokens = load_tokens(args.input)
        recs = [r for r in (parse_block(b) for b in split_blocks(tokens)) if r]

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(recs, fh, indent=1)

    try:
        with open(args.md, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except FileNotFoundError:
        sys.exit(f"error: {args.md} does not exist. Create the "
                 f"file with '## Applied' and '## Saved (not yet applied)' tables first.")

    lines = migrate_schema(lines)

    # Every write is buffered here and flushed only after ALL work succeeded, so a
    # failure part-way through can never leave applied.md updated and shortlist.md not.
    pending = {}

    if args.migrate_schema:
        backups = _flush({args.md: lines}, apply=True)
        print(f"schema migrated: {args.md}", file=sys.stderr)
        for b in backups:
            print(f"  backup: {b}", file=sys.stderr)
        return 0

    if args.report_new:
        new = report_new(lines, recs)
        json.dump([{"company": r["company"], "title": r["title"],
                    "key": "||".join(norm_key(r["company"], r["title"]))} for r in new],
                  sys.stdout, indent=1)
        print()
        return 0

    metadata, merge_urls = ({}, {})
    if args.urls:
        with open(args.urls, encoding="utf-8") as fh:
            metadata, merge_urls = build_url_maps(json.load(fh))

    # Guard the destructive mirror BEFORE touching anything (playbook §5): the Saved
    # table is rebuilt from `recs`, so an under-scrolled capture silently deletes rows
    # along with their comments, apply URLs and Simplify ids.
    blocked = mirror_guard_errors(lines, recs, total_jobs=args.total_jobs,
                                  max_drop_frac=args.max_drop_frac)
    if blocked and not args.force:
        for b in blocked:
            print(f"REFUSED  {b}", file=sys.stderr)
        print("The Saved table is an exact mirror — merging a partial capture would delete "
              "tracked rows. Re-scroll the tracker to the bottom, or pass --force if you are "
              "certain the capture is complete.", file=sys.stderr)
        return 2
    for b in blocked:
        print(f"WARN (forced past guard): {b}", file=sys.stderr)

    warn = []
    lines, stats = merge(lines, recs, warn, urls=merge_urls, metadata=metadata)
    lines = update_sync_header(lines, args.date)
    pending[args.md] = lines

    backups = _flush(pending, apply=args.apply)

    verb = "merged into" if args.apply else "DRY RUN — would merge into"
    print(f"parsed {len(recs)} Simplify records; {verb} {args.md}: "
          f"+{stats['added_applied']} applied, +{stats['added_saved']} saved "
          f"(mirror keeps {stats['mirror_saved']}, drops {stats['mirror_dropped']}), "
          f"{stats['promoted']} promoted saved→applied, "
          f"{stats['url_matched']} deduped by URL, "
          f"{stats['id_linked']} with Simplify id", file=sys.stderr)
    for d in stats.get("mirror_dropped_rows", []):
        print(f"  - mirror drops {d} (no longer saved on Simplify)", file=sys.stderr)

    for w in warn:
        print("WARN:", w, file=sys.stderr)

    for b in backups:
        print(f"  backup: {b}", file=sys.stderr)

    if not args.apply:
        print("DRY RUN: nothing written. Re-run with --apply to write.", file=sys.stderr)
    return 0


# `backup_file`, `BACKUP_DIR`, `BACKUP_KEEP` come from the md_tables import at the top of
# this file (line ~40). A second, independent copy of `backup_file` used to be defined
# here — dead code by name collision: it silently shadowed the import, so this module was
# actually running its own unsynced backup logic despite importing the canonical one right
# above. Removed; nothing else in this file referenced this definition specifically.


def _flush(pending, apply, backup_dir=BACKUP_DIR):
    """Write every buffered file, or nothing. Called only once all work succeeded.

    Each file is snapshotted before being overwritten. Backups are taken for ALL files
    first, so a failure midway through the writes still leaves a complete set of
    pre-change snapshots rather than a half-backed-up state.
    """
    if not apply:
        return []
    backups = [b for b in (backup_file(p, backup_dir=backup_dir) for p in pending) if b]
    for path, lines in pending.items():
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    return backups


# ---------- URL/metadata map from the skill's click-through phase ----------

def normalize_metadata(raw_urls):
    """Normalize the URL map from the skill's click-through phase.

    Input can be:
      - {"Company||Title": {"id": "...", "url": "..."}}  (new format with Simplify ID)
      - {"Company||Title": "https://..."}  (legacy format, URL only)

    Returns: {(norm_company, norm_title): {"id": str, "url": str}}
    """
    out = {}
    for key, val in (raw_urls or {}).items():
        if "||" not in key:
            continue
        company, title = key.split("||", 1)
        nkey = norm_key(company, title)

        if isinstance(val, dict):
            out[nkey] = {
                "id": val.get("id", ""),
                "url": val.get("url", ""),
            }
        elif isinstance(val, str):
            out[nkey] = {"id": "", "url": val}
        # else: unknown format, skip

    return out


# The entry point MUST stay at the very bottom of this file. Module-level names bind
# sequentially, so a `main()` call placed above the Tier-6 helpers below raised
# NameError the moment --sync-saved was used (after applied.md had already been
# written, leaving a half-applied run).
if __name__ == "__main__":
    sys.exit(main())
