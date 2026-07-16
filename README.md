# Job Search — Kiro-assisted tracker

An agent-driven job-search workflow plus a browser UI for reviewing/triaging roles.
You drive it by talking to **Kiro**; Kiro follows the steering files to search, filter,
verify, and maintain two markdown tables. A single-file HTML app (`tracker.html`) gives
you a nicer view over those tables with a live split-pane job preview.

## What's in here

| File | Purpose |
|---|---|
| `.kiro/steering/search-playbook.md` | The **method** — how Kiro searches, filters, dedups, verifies liveness, migrates resolved rows. Auto-loaded steering. |
| `.kiro/steering/job-search-prefs.md` | The **who/what** — your profile, career goals, hard filters, tier priorities, ATS URL formats. *(Personal — keep local.)* |
| `.kiro/scripts/linkedin_harvest.py` | Harvests a full LinkedIn search / recommended collection (parallel, paginated). |
| `.kiro/scripts/read_jobs.py` | Reads full LinkedIn listings (responsibilities + requirements) for triage. |
| `shortlist.md` | Candidate roles, tiered, with status boxes (`[ ]`/`[x]`/`[nope]`) + Comment. |
| `applied.md` | Applied / saved / rejected tracker; dedup source. |
| `tracker.html` | Browser UI over the two `.md` files. |

> The committed `shortlist.md` / `applied.md` are **samples** — the real ones are
> git-ignored and stay on your machine only. `tracker.html` opens whichever is on disk.

## How to use it (with Kiro)

1. **Give Kiro your info.** Point it at your CV/resume and fill in
   `.kiro/steering/job-search-prefs.md` with your profile, target roles, filters, and
   location. The steering files are auto-loaded, so once they're accurate Kiro applies
   them on every request.
2. **Ask Kiro to run a search pass.** e.g. *"run a job search pass"* — it will search the
   web + BuiltIn + LinkedIn (keyword searches **and** your recommended collection), read
   the full listing of anything promising, filter/tier it, and add keepers to
   `shortlist.md`. See `search-playbook.md` for the exact routine.
3. **Review in the UI.** Open `tracker.html` (below), click rows to preview jobs, tick
   `[x]` when you apply, or reject with a reason.
4. **Sync what you applied to.** Ask Kiro to *"sync from Simplify"* — it pulls your
   Simplify.jobs tracker into `applied.md` and reconciles it against the shortlist.

## The HTML interface (`tracker.html`)

Open `tracker.html` in a **Chromium-based browser** (Chrome/Edge/Brave). It uses the
File System Access API to read/write your `.md` files directly.

- **Load:** click **Open folder…** and grant access to this folder (or **Open file(s)…**
  to pick `shortlist.md` / `applied.md`). It remembers the files, so next time it's a
  one-click reconnect.
- **Edit:** toggle the status checkbox to mark applied; click ✕ to reject (a dialog lets
  you pick a color-coded reason + note). Edits are written back surgically — only the
  cell you touched changes. Rejected rows hide (toggle **show rejected** to see them).
- **Split-pane job preview:** click a row to open its apply link in a **Chrome Split
  View** pane beside the tracker — no iframe, so job boards that block embedding still
  work. To set it up: right-click the `tracker.html` tab → **Split tab** (or drag a tab
  beside it), then click a row. A banner tells you if no split is detected. The badge in
  the header shows the split status.

## Dependencies (from a sibling project)

The Simplify sync and the split-pane preview both rely on tooling that lives outside this
folder, in your global Kiro config (`~/.kiro`, tracked in a separate `kiro-config` repo):

- **Tab Share browser extension** (`~/.kiro/tab_share_extension_chromium/`) — a Chromium
  extension + native-messaging host that exposes a local HTTP API on
  **`http://localhost:8766`** (`/tabs`, `/open`, `/navigate`, `/extract`, `/close`, …).
  The HTML split-preview and the LinkedIn harvest scripts talk to it. Requires the
  extension loaded in Chromium **and** the native host installed.
- **`simplify-tracker-sync` skill** (`~/.kiro/skills/simplify-tracker-sync/`) — drives
  your logged-in Simplify.jobs tab (via Tab Share) to rebuild `applied.md`.

If you don't have these, the tracker still works for viewing/editing the `.md` files and
opening links in new tabs — you just lose the split-pane preview and the Simplify sync.

## Setup (ask Kiro to help)

Assuming you have Kiro available as an agent, the quickest path is to **ask Kiro to set it
up** — it can install/copy the pieces for you. The manual outline:

1. **Get the Tab Share extension + skill** from the `kiro-config` project into `~/.kiro/`
   (extension dir `tab_share_extension_chromium/`, skill dir
   `skills/simplify-tracker-sync/`). Ask Kiro: *"set up the Tab Share extension and the
   simplify-tracker-sync skill from kiro-config."*
2. **Install the native messaging host** (registers the host so Chromium can launch it).
   The extension folder ships an installer (`install.sh` / `install_snap.sh`). Ask Kiro
   to run the right one for your setup.
3. **Load the extension in Chromium:** `chrome://extensions` → enable Developer mode →
   **Load unpacked** → select `~/.kiro/tab_share_extension_chromium/`.
4. **Verify the API is up:** `curl -s http://localhost:8766/tabs` should return JSON. Ask
   Kiro to probe it.
5. **Fill in your profile** in `.kiro/steering/job-search-prefs.md` and point Kiro at your
   CV. Then ask it to run a search pass.

> **Snap Chromium note:** if your Chrome/Chromium is installed via **snap** (common on
> Ubuntu), the native-messaging host needs an **extra install step** — the host manifest
> must go under the snap's confined config path (e.g.
> `~/snap/chromium/common/chromium/NativeMessagingHosts/`) rather than the default
> `~/.config/`. Use the `install_snap.sh` variant, or just ask Kiro — it knows to place
> the manifest in the snap location and to sync the extension files into
> `~/snap/chromium/common/…`.

## Privacy

`shortlist.md`, `applied.md`, `my info/` (CV, interview answers), and
`job-search-prefs.md` hold personal data and are **git-ignored**. Only sample versions of
the trackers and the generic tooling/steering are committed.
