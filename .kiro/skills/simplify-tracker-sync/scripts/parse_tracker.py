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


def load_tokens(path):
    lines = [l.rstrip() for l in open(path, encoding="utf-8")]
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


def esc(s):
    return (s or "").replace("|", "\\|").strip()


def norm_key(company, title):
    """Dedup key: normalized company + title. Case-insensitive, whitespace-collapsed."""
    def n(s):
        return re.sub(r"\s+", " ", (s or "").strip().lower())
    return (n(company), n(title))


# ---------- append-only merge into an existing applied.md ----------
# We NEVER rebuild the file. We parse the existing Applied / Saved tables, then only:
#   - add Simplify rows whose (company,title) key isn't present anywhere,
#   - promote an existing Saved row to Applied if Simplify now shows an applied date
#     (carrying its comment across).
# Every existing row (and its comment) and every other section is left untouched.

ROW_RE = re.compile(r"^\|(.+)\|\s*$")

def split_md_row(line):
    m = ROW_RE.match(line.rstrip())
    if not m:
        return None
    return [c.strip() for c in m.group(1).split("|")]


def find_section(lines, heading_prefix):
    """Return (header_idx, sep_idx, first_data_idx, end_idx) for a '## heading' table,
    or None. end_idx is the exclusive index where the table's data rows stop."""
    for i, l in enumerate(lines):
        if l.strip().startswith(heading_prefix):
            # find the separator row (|---|) after the heading
            j = i + 1
            while j < len(lines) and not re.match(r"^\|\s*:?-+", lines[j].strip()):
                if lines[j].strip().startswith("##"):
                    return None  # hit next section w/o a table
                j += 1
            if j >= len(lines):
                return None
            sep = j
            k = sep + 1
            while k < len(lines) and lines[k].strip().startswith("|"):
                k += 1
            return (i, sep, sep + 1, k)
    return None


URL_MD_RE = re.compile(r"\[[^\]]*\]\((https?://[^)\s]+)\)")

def clean_url(u):
    """Strip Simplify tracking query params so URLs key consistently."""
    if not u:
        return ""
    u = u.strip()
    m = URL_MD_RE.search(u)
    if m:
        u = m.group(1)
    u = re.split(r"[?#]", u, maxsplit=1)[0]   # drop query/fragment
    return u.rstrip("/")


# Extract a stable ATS identifier from an apply URL. The code survives the
# confirm-vs-listing path difference and tracking params, so it's a better dedup
# key than the full URL. Handles the common ATS shapes; falls back to the cleaned URL.
ATS_CODE_RES = [
    r"applytojob\.com/apply/(?:confirm/)?([A-Za-z0-9]+)",   # <code> or confirm/<code>
    r"greenhouse\.io/[^/]+/jobs/(\d+)",
    r"boards\.greenhouse\.io/[^/]+/jobs/(\d+)",
    r"ashbyhq\.com/[^/]+/([0-9a-f-]{16,})",
    r"recruitee\.com/o/([a-z0-9-]+)",
    r"myworkdayjobs\.com/.*/(\w+)$",
    r"[?&]gh_jid=(\d+)",
]
def ats_code(u):
    u = clean_url(u) if "](" in (u or "") else (u or "").strip()
    # re-clean in case a bare URL with params was passed
    bare = re.split(r"[?#]", u, maxsplit=1)[0]
    for pat in ATS_CODE_RES:
        m = re.search(pat, u) or re.search(pat, bare)
        if m:
            return m.group(1)
    return bare.rstrip("/")


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
            if code:
                umap[code] = row
    return tmap, umap, loc, cols


def fmt_apply(url):
    return f"[Apply]({url})" if url else ""


def build_cells(cols, ncol, *, date="", company="", title="", raw="", location="", apply_url="", comment="", reason=""):
    """Build a row's cell list positioned per the table's header columns.
    `title` = display/inferred Role; `raw` = verbatim Simplify title (dedup anchor)."""
    cells = [""] * ncol
    cells[0] = date  # date/status column
    if "company" in cols: cells[cols["company"]] = esc(company)
    if "role" in cols: cells[cols["role"]] = esc(title)
    if "raw" in cols: cells[cols["raw"]] = esc(raw or title)
    if "location" in cols: cells[cols["location"]] = esc(location)
    if "apply" in cols: cells[cols["apply"]] = fmt_apply(apply_url)
    if "reason" in cols: cells[cols["reason"]] = reason
    if "comment" in cols: cells[cols["comment"]] = comment
    return cells

def row_md(cells):
    return "| " + " | ".join(cells) + " |"


def merge(lines, recs, warn, urls=None):
    """Append-only merge. `urls` (optional) = {norm_key: url} for brand-new rows.
    Dedup precedence: URL match (if both sides have one) > company+title."""
    urls = urls or {}
    a_t, a_u, a_loc, a_cols = parse_existing(lines, "## Applied")
    s_t, s_u, s_loc, s_cols = parse_existing(lines, "## Saved")

    if a_loc is None or s_loc is None:
        warn.append("Could not locate '## Applied' and/or '## Saved' tables — aborting merge.")
        return lines, {"added_applied": 0, "added_saved": 0, "promoted": 0, "url_matched": 0}

    existing_url = {**a_u, **s_u}
    new_applied, new_saved, promotions = [], [], []
    url_matched = 0

    for r in recs:
        raw = r["title"]                    # Simplify's verbatim title = the dedup anchor
        key = norm_key(r["company"], raw)
        u = clean_url(urls.get(key, ""))
        code = ats_code(u) if u else ""
        has_applied = bool(r["applied"])
        # ATS-code dedup first (robust to confirm-vs-listing paths + tracking params).
        if code and code in existing_url:
            url_matched += 1
            continue
        # then company + RAW title (stable: matches whatever Simplify re-emits next sync)
        if key in a_t:
            continue                       # already applied — untouched
        if key in s_t:
            if has_applied:
                promotions.append((key, r, u))
            continue
        # New row: Role = slug-inferred title when available, else the raw; Raw = verbatim.
        st = slug_title(u)
        r = {**r, "_url": u, "_raw": raw, "title": st or raw}
        (new_applied if has_applied else new_saved).append(r)

    # promotions: drop from Saved, add to Applied carrying comment + raw + url
    remove_idxs = []
    for key, r, u in promotions:
        old = s_t[key]
        oc = lambda name: (old["cells"][s_cols[name]] if name in s_cols and len(old["cells"]) > s_cols[name] else "")
        cmt = oc("comment")
        old_raw = oc("raw") or r["title"]
        old_url = clean_url(oc("apply"))
        remove_idxs.append(old["idx"])
        st = slug_title(u)
        new_applied.append({**r, "_comment": cmt, "_url": u or old_url,
                            "_raw": old_raw, "title": st or old_raw})

    def srt(rs, f):
        return sorted(rs, key=lambda x: parse_date(x[f]) or datetime.min, reverse=True)

    a_ncol = len(split_md_row(lines[a_loc[1]]))
    s_ncol = len(split_md_row(lines[s_loc[1]]))
    applied_md = [row_md(build_cells(a_cols, a_ncol, date=iso(r["applied"]), company=r["company"],
                                     title=r["title"], raw=r.get("_raw", r["title"]), location=r["location"],
                                     apply_url=r.get("_url", ""), comment=r.get("_comment", ""))) for r in srt(new_applied, "applied")]
    saved_md = [row_md(build_cells(s_cols, s_ncol, date=iso(r["saved"]), company=r["company"],
                                   title=r["title"], raw=r.get("_raw", r["title"]), location=r["location"],
                                   apply_url=r.get("_url", ""))) for r in srt(new_saved, "saved")]

    for idx in sorted(remove_idxs, reverse=True):
        del lines[idx]
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
                   "promoted": len(promotions), "url_matched": url_matched}


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
    for i, l in enumerate(lines):
        if l.startswith("**Last synced from Simplify:**"):
            am, _, _, _ = parse_existing(lines, "## Applied")
            sm, _, _, _ = parse_existing(lines, "## Saved")
            lines[i] = (f"**Last synced from Simplify:** {synced_date} · {len(am)} applied · "
                        f"{len(sm)} saved (not yet applied)")
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="text file with tracker innerText")
    ap.add_argument("--json", help="write parsed records as JSON here")
    ap.add_argument("--md", required=True, help="existing applied.md to merge into (append-only)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="sync date for the markdown header (default: today)")
    ap.add_argument("--urls", help="JSON map {\"company||title\": url} of apply URLs for NEW rows "
                                    "(captured by clicking through only the new tracker rows)")
    ap.add_argument("--report-new", action="store_true",
                    help="don't merge; print JSON of records that are NEW (not yet in the tables) "
                         "so the caller knows which rows to click-through for URLs, then exit")
    args = ap.parse_args()

    tokens = load_tokens(args.input)
    recs = [r for r in (parse_block(b) for b in split_blocks(tokens)) if r]

    if args.json:
        json.dump(recs, open(args.json, "w"), indent=1)

    try:
        lines = open(args.md, encoding="utf-8").read().split("\n")
    except FileNotFoundError:
        sys.exit(f"error: {args.md} does not exist. This is an append-only merge; create the "
                 f"file with '## Applied' and '## Saved (not yet applied)' tables first.")

    if args.report_new:
        new = report_new(lines, recs)
        json.dump([{"company": r["company"], "title": r["title"],
                    "key": "||".join(norm_key(r["company"], r["title"]))} for r in new],
                  sys.stdout, indent=1)
        return

    urls = {}
    if args.urls:
        raw = json.load(open(args.urls))
        for k, v in raw.items():
            if "||" in k:
                a, b = k.split("||", 1)
                urls[norm_key(a, b)] = v
            else:
                urls[k] = v

    warn = []
    lines, stats = merge(lines, recs, warn, urls=urls)
    update_sync_header(lines, args.date)
    open(args.md, "w").write("\n".join(lines))

    print(f"parsed {len(recs)} Simplify records; merged into {args.md}: "
          f"+{stats['added_applied']} applied, +{stats['added_saved']} saved, "
          f"{stats['promoted']} promoted saved→applied, "
          f"{stats['url_matched']} deduped by URL", file=sys.stderr)
    for w in warn:
        print("WARN:", w, file=sys.stderr)


if __name__ == "__main__":
    main()
