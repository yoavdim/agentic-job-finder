#!/usr/bin/env python3
"""Push-then-pull sync for the Simplify Saved list (`applied.md`'s `## Saved`).

`## Saved` is the SINGLE copy of this list: an exact mirror of the Simplify tracker, plus a
transient `Status` column carrying the user's intent for each row (set in `tracker.html`).
The sync reads that intent, pushes it to Simplify, records the outcome, and rebuilds the
mirror. There is no second table — that's what makes "which copy is right?" unanswerable
by construction.

WHY THE ORDER IS LOAD-BEARING
-----------------------------
Every decision is made ONCE, from a single pre-mutation snapshot, before anything is
pushed. Interleaving reads and mutations breaks in two specific ways that both lose data:

  - A row that is both user-rejected AND stale gets acted on twice: the first delete
    succeeds, the second fails against a dead id, and the row is then treated as "push
    failed, retry next pass" forever.
  - Anything deleted before it is classified loses the local record of WHY it left. It is
    absent from the next capture, so nothing writes it into `## Rejected`, and
    `dedup_index` will cheerfully re-suggest it.

So: capture (read-only) -> read local statuses -> classify -> report -> execute -> write.
`plan()` below is a pure function of (snapshot, local rows, today) — it performs no I/O and
mutates nothing, so the guarantee is structural rather than a convention someone can
forget. `execute()` takes the plan and a caller-supplied action runner; `mirror_rows()`
builds the new table from the snapshot plus the execution outcomes.

PRECEDENCE, evaluated once per row
----------------------------------
  1. Snapshot says APPLIED (or beyond)  -> the application wins. NEVER delete, whatever the
                                           local status or the age says. Promote to Applied.
  2. Local status `applied`             -> push mark_applied.
  3. Local status `rejected`            -> delete (SAVED-gated) + record the user's reason.
  4. Saved longer than max_saved_age    -> delete (SAVED-gated) + record `too-old` `(auto)`.
  5. otherwise                          -> stays in the mirror.

3 outranks 4 so an explicitly rejected row keeps the user's own reason instead of being
relabelled by the automatic rule. 1 outranks everything: that is the existing
"application wins over rejection" rule from the playbook.
"""
import datetime
import re
import sys
from pathlib import Path

# reasons.split_leading_code is shared with migrate_resolved so "<code> — text" parses
# identically in both places; loaded by path (not a package import) so this file keeps
# working if the skill is copied out of the workspace on its own — see classify_reason.
_REASONS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "lib" / "reasons.py"
if _REASONS_PATH.exists():
    import importlib.util
    _spec = importlib.util.spec_from_file_location("_reasons_shared", _REASONS_PATH)
    _reasons = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_reasons)
    _split_leading_code = _reasons.split_leading_code
else:
    _split_leading_code = None

# Local status vocabulary in the Saved table's Status column. Blank == "saved".
STATUS_SAVED = "saved"
STATUS_APPLIED = "applied"
STATUS_REJECTED = "rejected"
LOCAL_STATUSES = (STATUS_SAVED, STATUS_APPLIED, STATUS_REJECTED)

# Actions the plan can ask for.
ACT_KEEP = "keep"
ACT_MARK_APPLIED = "mark_applied"
ACT_DELETE = "delete"
ACT_PROMOTE = "promote"          # already applied on Simplify; no remote call needed

DEFAULT_MAX_SAVED_AGE_DAYS = 60

# A run that would auto-delete more than this many rows stops and asks for --force. The
# stale rule is meant to trim stragglers; a large batch means either a long-neglected
# backlog or a bug, and both deserve a human look before an irreversible remote delete.
DEFAULT_MAX_AUTO_DELETE = 5

REASON_TOO_OLD = "too-old"
REASON_NOT_INTERESTED = "not-interested"

# tracker.html writes the reject comment as "<reason-code> — <free text>"; reuse that so
# the codes and the judgment-vs-liveness split stay consistent with the shortlist flow.
REASON_CODES = ("link-broken", "listing-removed", "too-old", "not-qualified",
                "not-interested", "sketchy-site", "unknown", "other")


def normalize_status(raw):
    """Map a Status cell to one of LOCAL_STATUSES. Blank/unknown -> 'saved'.

    Unknown text degrades to 'saved' deliberately: the fallback must be the option that
    does nothing, so a typo can never trigger a delete.
    """
    s = (raw or "").strip().lower()
    s = re.sub(r"^\[|\]$", "", s).strip()      # tolerate a "[rejected]"-style cell
    return s if s in LOCAL_STATUSES else STATUS_SAVED


def classify_reason(comment):
    """(reason_code, verbatim_comment) from a user's reject comment.

    Leading-code parsing ("<code> — text") is shared with migrate_resolved.classify_reason
    via reasons.split_leading_code, so the two never disagree on what a code prefix means.
    They deliberately keep DIFFERENT no-match defaults, though: this one falls back to
    'not-interested' rather than 'unknown'/'other', because anything unrecognised here must
    become a judgment rejection — a liveness code would make the role re-suggestable, the
    wrong default for a row the user deliberately rejected on Simplify.
    """
    if _split_leading_code is not None:
        code, c = _split_leading_code(comment)
        return (code, c) if code else (REASON_NOT_INTERESTED, c)
    # reasons.py not found (skill copied out standalone) — inline fallback, same shape.
    c = (comment or "").strip()
    if not c:
        return REASON_NOT_INTERESTED, ""
    head = re.split(r"\s+[—–-]\s+", c, maxsplit=1)[0].strip().lower()
    return (head, c) if head in REASON_CODES else (REASON_NOT_INTERESTED, c)


def age_days(saved_date, today):
    """Days since `saved_date` (ISO), or None when unparseable/absent."""
    if not saved_date:
        return None
    try:
        d = datetime.date.fromisoformat(str(saved_date)[:10])
    except ValueError:
        return None
    return (today - d).days


class Decision:
    """One row's single decision. Carries everything execute()/mirror_rows() need."""

    __slots__ = ("key", "company", "role", "action", "reason", "comment", "app_id",
                 "saved_date", "applied_date", "age", "auto", "why", "snapshot", "local")

    def __init__(self, key, company, role, action, *, reason="", comment="", app_id="",
                 saved_date="", applied_date="", age=None, auto=False, why="",
                 snapshot=None, local=None):
        self.key = key
        self.company = company
        self.role = role
        self.action = action
        self.reason = reason
        self.comment = comment
        self.app_id = app_id
        self.saved_date = saved_date
        self.applied_date = applied_date
        self.age = age
        self.auto = auto
        self.why = why
        self.snapshot = snapshot or {}
        self.local = local or {}

    def as_dict(self):
        return {"company": self.company, "role": self.role, "action": self.action,
                "reason": self.reason, "comment": self.comment, "app_id": self.app_id,
                "saved_date": self.saved_date, "applied_date": self.applied_date,
                "age": self.age, "auto": self.auto, "why": self.why}

    def __repr__(self):
        return f"<Decision {self.company} — {self.role}: {self.action}>"


def plan(snapshot_rows, local_rows, today=None,
         max_saved_age_days=DEFAULT_MAX_SAVED_AGE_DAYS):
    """Decide what happens to every row. PURE: no I/O, no mutation, no remote calls.

    snapshot_rows: what the capture says Simplify currently holds, as
        [{key, company, title, location, saved, applied, url, id}] — `applied` non-empty
        means Simplify has progressed past saved.
    local_rows: what `## Saved` currently holds, as
        {key: {status, comment, app_id, url, saved, company, role, raw, location}}.
    today: date the ages are measured against (explicit so runs are reproducible).

    Returns [Decision], one per snapshot row, in snapshot order. Rows present locally but
    absent from the snapshot are NOT decided here: they are simply gone from Simplify, and
    the mirror rebuild drops them (that's the point of a mirror — the user removed them).
    """
    today = today or datetime.date.today()
    out = []

    for snap in snapshot_rows:
        key = snap["key"]
        local = local_rows.get(key, {})
        status = normalize_status(local.get("status"))
        app_id = local.get("app_id") or snap.get("id") or ""
        saved_date = snap.get("saved") or local.get("saved") or ""
        company = snap.get("company") or local.get("company") or ""
        role = snap.get("title") or local.get("role") or ""
        age = age_days(saved_date, today)

        common = dict(app_id=app_id, saved_date=saved_date, age=age,
                      snapshot=snap, local=local)

        # 1. The website has moved past "saved" — the application is ground truth.
        if snap.get("applied"):
            out.append(Decision(
                key, company, role, ACT_PROMOTE,
                applied_date=snap["applied"],
                comment=local.get("comment", ""),
                why="Simplify shows it applied; application wins over any local status",
                **common))
            continue

        # 2. User marked it applied locally — tell Simplify.
        if status == STATUS_APPLIED:
            out.append(Decision(
                key, company, role, ACT_MARK_APPLIED,
                comment=local.get("comment", ""),
                why="marked applied locally",
                **common))
            continue

        # 3. User rejected it — their reason wins over the automatic rule below.
        if status == STATUS_REJECTED:
            reason, verbatim = classify_reason(local.get("comment"))
            out.append(Decision(
                key, company, role, ACT_DELETE,
                reason=reason, comment=verbatim, auto=False,
                why="rejected locally",
                **common))
            continue

        # 4. Saved and untouched for too long.
        if age is not None and age > max_saved_age_days:
            out.append(Decision(
                key, company, role, ACT_DELETE,
                reason=REASON_TOO_OLD,
                comment=f"saved {saved_date}, untouched {age}d "
                        f"(cutoff {max_saved_age_days}d) (auto)",
                auto=True,
                why=f"stale: saved {age}d ago",
                **common))
            continue

        # 5. Nothing to do; stays in the mirror.
        out.append(Decision(key, company, role, ACT_KEEP,
                            comment=local.get("comment", ""),
                            why="still saved", **common))

    return out


def summarize(decisions):
    counts = {}
    for d in decisions:
        counts[d.action] = counts.get(d.action, 0) + 1
    auto = sum(1 for d in decisions if d.action == ACT_DELETE and d.auto)
    manual = sum(1 for d in decisions if d.action == ACT_DELETE and not d.auto)
    return {"counts": counts, "auto_deletes": auto, "manual_deletes": manual,
            "total": len(decisions)}


def guard_errors(decisions, max_auto_delete=DEFAULT_MAX_AUTO_DELETE):
    """Reasons this plan should not execute without --force."""
    errors = []
    auto = [d for d in decisions if d.action == ACT_DELETE and d.auto]
    if max_auto_delete is not None and len(auto) > max_auto_delete:
        errors.append(
            f"plan would auto-delete {len(auto)} stale row(s) from Simplify, more than the "
            f"{max_auto_delete} allowed per run — the stale rule is for trimming "
            f"stragglers, so a batch this size means a neglected backlog or a bug")
    missing = [d for d in decisions
               if d.action in (ACT_MARK_APPLIED, ACT_DELETE) and not d.app_id]
    if missing:
        names = ", ".join(f"{d.company} — {d.role}" for d in missing[:5])
        errors.append(
            f"{len(missing)} row(s) need a Simplify push but carry no tracker id, so they "
            f"cannot be acted on: {names}"
            + (" …" if len(missing) > 5 else ""))
    return errors


def needs_push(decisions):
    """True when any decision requires a remote call — i.e. there is unpushed intent.

    The mirror must never be rebuilt over intent that hasn't been acted on: doing so would
    silently discard the user's status edits. Callers use this to refuse a pull-only run.
    """
    return any(d.action in (ACT_MARK_APPLIED, ACT_DELETE) for d in decisions)


# ---------- execution ----------
# execute() takes an `actions` object rather than importing simplify_actions directly, so
# it can be driven with a fake in tests and never touches the network there. The real
# caller passes a thin adapter over simplify_actions (see make_actions below).

OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_BLOCKED = "blocked"        # could not verify remote status; left alone
OUTCOME_APPLIED_WINS = "applied_wins"   # remote had progressed; delete refused


class Outcome:
    __slots__ = ("decision", "status", "detail", "applied_date")

    def __init__(self, decision, status, detail="", applied_date=""):
        self.decision = decision
        self.status = status
        self.detail = detail
        self.applied_date = applied_date

    @property
    def ok(self):
        return self.status == OUTCOME_OK

    def as_dict(self):
        return {**self.decision.as_dict(), "outcome": self.status,
                "outcome_detail": self.detail}

    def __repr__(self):
        return f"<Outcome {self.decision.company}: {self.status}>"


def execute(decisions, actions):
    """Run every decision's remote call. Returns [Outcome], one per decision.

    Each delete RE-VERIFIES the remote status immediately before firing, because the
    snapshot was taken earlier and may be stale. If the row has since progressed to applied,
    the delete is refused and the outcome becomes APPLIED_WINS — the same
    "application beats rejection" rule, enforced at the last possible moment.

    A failure is recorded, never raised: the row keeps its local status so the next pass
    retries it. That is why the mirror is built from outcomes rather than from optimism.
    """
    out = []
    for d in decisions:
        if d.action in (ACT_KEEP, ACT_PROMOTE):
            out.append(Outcome(d, OUTCOME_OK))
            continue

        if not d.app_id:
            out.append(Outcome(d, OUTCOME_BLOCKED, "no Simplify tracker id on the row"))
            continue

        if d.action == ACT_MARK_APPLIED:
            res = actions.mark_applied(d.app_id)
            out.append(Outcome(d, OUTCOME_OK if res.get("ok") else OUTCOME_FAILED,
                               res.get("detail", "")))
            continue

        if d.action == ACT_DELETE:
            state = actions.current_state(d.app_id)
            if state == "applied":
                out.append(Outcome(d, OUTCOME_APPLIED_WINS,
                                   "Simplify says applied — delete refused",
                                   applied_date=actions.applied_date(d.app_id)))
                continue
            if state != "saved":
                out.append(Outcome(d, OUTCOME_BLOCKED,
                                   f"could not verify remote status ({state})"))
                continue
            res = actions.delete(d.app_id)
            out.append(Outcome(d, OUTCOME_OK if res.get("ok") else OUTCOME_FAILED,
                               res.get("detail", "")))
            continue

        out.append(Outcome(d, OUTCOME_BLOCKED, f"unknown action {d.action!r}"))
    return out


def mirror_rows(outcomes):
    """Split outcomes into the three lists the writer needs.

    new Saved = snapshot's still-saved rows
                MINUS rows successfully deleted
                MINUS rows promoted/marked applied
    with any row whose push failed or was blocked RETAINED, keeping its local status so the
    next pass retries it. Dropping those would discard the user's intent silently.

    Returns (keep, to_applied, to_rejected):
      keep        — [Outcome] that stay in ## Saved (status preserved where relevant)
      to_applied  — [Outcome] to append to ## Applied
      to_rejected — [Outcome] to append to ## Rejected
    """
    keep, to_applied, to_rejected = [], [], []
    for o in outcomes:
        d = o.decision

        if d.action == ACT_KEEP:
            keep.append(o)
        elif d.action == ACT_PROMOTE:
            to_applied.append(o)
        elif d.action == ACT_MARK_APPLIED:
            (to_applied if o.ok else keep).append(o)
        elif d.action == ACT_DELETE:
            if o.ok:
                to_rejected.append(o)
            elif o.status == OUTCOME_APPLIED_WINS:
                # Remote progressed under us: the application is ground truth, so this
                # becomes an applied row rather than a rejection.
                to_applied.append(o)
            else:
                keep.append(o)          # failed/blocked -> retry next pass
        else:
            keep.append(o)
    return keep, to_applied, to_rejected


def retained_status(outcome):
    """The Status cell a retained row should carry on rewrite.

    A row kept because its push failed must keep the intent that failed, or the next pass
    would see a clean 'saved' row and never retry. A row that was simply still-saved
    reverts to blank.
    """
    d = outcome.decision
    if d.action == ACT_DELETE and not outcome.ok:
        return STATUS_REJECTED
    if d.action == ACT_MARK_APPLIED and not outcome.ok:
        return STATUS_APPLIED
    return ""


class SimplifyActions:
    """Adapter over simplify_actions, so `execute()` has no direct dependency on it.

    Kept deliberately thin: it maps the raw action results onto the {ok, detail} shape
    execute() expects, and nothing else.
    """

    def __init__(self, module, tab_share_url="http://localhost:8766", tab_id=None):
        self._m = module
        self._url = tab_share_url
        self._tab = tab_id
        self._inspected = {}

    def _inspect(self, app_id):
        if app_id not in self._inspected:
            res = self._m.execute_via_tab_share(
                self._m.plan_action("inspect", application_id=app_id),
                tab_share_url=self._url, dry_run=False, tab_id=self._tab)
            body = {}
            if res.get("status") == "success":
                body = res.get("response", {}).get("body") or {}
            self._inspected[app_id] = body
        return self._inspected[app_id]

    def current_state(self, app_id):
        """"saved" | "applied" | "unknown" — the gate on every delete."""
        status = self._m.current_status(self._inspect(app_id))
        return {"delete": "saved", "applied": "applied"}.get(
            self._m.rejection_outcome(status), "unknown")

    def applied_date(self, app_id):
        events = (self._inspect(app_id) or {}).get("status_events") or []
        for e in reversed(events):
            try:
                if int(e.get("status", 0)) == self._m.STATUS_APPLIED and e.get("timestamp"):
                    return str(e["timestamp"])[:10]
            except (TypeError, ValueError):
                continue
        return ""

    def mark_applied(self, app_id):
        body = self._inspect(app_id)
        res = self._m.execute_via_tab_share(
            self._m.plan_action("mark_applied", application_id=app_id,
                                current_events=body.get("status_events")),
            tab_share_url=self._url, dry_run=False, tab_id=self._tab)
        return {"ok": res.get("status") == "success",
                "detail": res.get("error") or res.get("status", "")}

    def delete(self, app_id):
        res = self._m.execute_via_tab_share(
            self._m.plan_action("delete_saved", application_id=app_id),
            tab_share_url=self._url, dry_run=False, tab_id=self._tab)
        self._inspected.pop(app_id, None)
        return {"ok": res.get("status") == "success",
                "detail": res.get("error") or res.get("status", "")}
