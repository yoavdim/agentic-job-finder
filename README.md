# Job Search — Kiro-assisted tracker

Hunt for jobs by chatting with an AI agent. You talk to **Kiro** (or any other LLM),
and it searches, filters, and keeps two tidy markdown tables up to date (guided by the included skills and steering files).
There's also a little browser app for reviewing roles with a side-by-side job preview.

![The tracker.html UI showing a tiered, color-coded job shortlist](<./Example%20Screen.png>)

> Sample data (fictional job seeker) shown above.

## How to use it

1. **Tell Kiro about you.** Point it at your resume and fill in
   `.kiro/steering/job-search-prefs.md` (profile, target roles, filters, location). It's
   auto-loaded, so Kiro uses it on every request.
2. **Ask for a search pass.** Just say *"run a job search pass."* Kiro syncs what you've
   applied to from Simplify, searches the web, BuiltIn, and LinkedIn, reads the promising
   listings, tiers them, and adds keepers to `shortlist.md`.
3. **Review in the UI.** Open `tracker.html`, click a row to preview the job, tick `[x]`
   when you apply, or reject it with a reason.

## The browser app (`tracker.html`)

Open it in a **Chromium browser** (Chrome/Edge/Brave) — it reads and writes your `.md`
files directly.

- **Load:** click **Open folder…** (or **Open file(s)…** to pick the files). It
  remembers them for next time.
- **Split preview:** click a row to open its link in a **Chrome Split View** beside the
  tracker — no iframe, so job boards that block embedding still work. Right-click the tab
  → **Split tab** to set it up.
- **Edit:** tick the box to mark applied, or click ✕ to reject with a color-coded reason.
  Only the cell you touch gets rewritten. Rejected rows hide until you show them.

## Extras you'll want

The Simplify sync and split preview rely on the **Tab Share extension**
([`yoavdim/tab-share`](https://github.com/yoavdim/tab-share)) — a Chromium extension +
native host exposing a local API at **`http://localhost:8766`**. It powers the split
preview and the LinkedIn scripts. It's a standalone open-source project — clone it anywhere
and follow its own install guide.

Without it, the tracker still views/edits your `.md` files and opens links in new tabs —
you just lose the split preview and Simplify sync.

## Setup

Easiest path: **ask Kiro to set it up.** The short version:

1. Clone the [Tab Share repo](https://github.com/yoavdim/tab-share) somewhere convenient:
   `git clone https://github.com/yoavdim/tab-share.git`
2. Load it in Chromium first to get its extension ID: `chrome://extensions` → Developer
   mode → **Load unpacked** → pick the repo's `chromium/` folder. Copy the 32-char ID.
3. Register the native host with that ID: `chromium/install.sh <EXTENSION_ID>` (or
   `install_snap.sh <EXTENSION_ID>` for snap Chromium). Then reload the extension.
4. Check it's alive: `curl -s http://localhost:8766/tabs` should return JSON.
5. Fill in your profile, point Kiro at your resume, and ask for a search pass.

> Full instructions (Firefox build, snap paths, troubleshooting) live in the repo's
> `INSTALL.md`.

## The magic behind the scenes

Three browser tricks make `tracker.html` feel like a native app despite being a single
static HTML file:

- **File System Access API** — instead of uploading copies, the page gets a live handle to
  your actual `shortlist.md` / `applied.md` (via **Open folder…**/**Open file(s)…**). It
  writes back *surgically* — only the cell you touched changes — and caches the handles in
  IndexedDB to reconnect in one click.
- **Split View instead of an iframe** — most job boards block being embedded in an
  `<iframe>` (via `X-Frame-Options` / CSP `frame-ancestors`), so an in-page preview would
  just show a blank box. Instead the preview uses Chrome's native **Split View**: the job
  page loads in a real, full-privilege tab beside the tracker — no embedding, so nothing is
  blocked.
- **Tab Share drives the split tab** — a static web page normally can't touch another tab.
  The Tab Share extension exposes a tiny local API at `localhost:8766`; `tracker.html` calls
  it to find its split partner (matching on the browser's `splitViewId`) and, on each row
  click, `POST`s a `/navigate` to point that partner pane at the role's link. That's how
  clicking a row swaps the preview without any iframe. Its CORS policy is what makes this
  safe: the local `file://` page is allowed only the low-risk `/tabs` and `/navigate`
  endpoints, and arbitrary websites are blocked outright.

## What's in here

| File                                   | Purpose                                                                                          |
| -------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `.kiro/steering/search-playbook.md`  | The **method** — how Kiro searches, filters, dedups, and verifies. Auto-loaded.            |
| `.kiro/steering/job-search-prefs.md` | The **who/what** — your profile, goals, filters, priorities. *(Personal — keep local.)* |
| `.kiro/scripts/linkedin_harvest.py`  | Harvests a full LinkedIn search / recommended collection.                                        |
| `.kiro/scripts/read_jobs.py`         | Reads full LinkedIn listings for triage.                                                         |
| `shortlist.md`                       | Candidate roles, tiered, with status boxes + comments. *(Yours — local only.)*                   |
| `applied.md`                         | Applied / saved / rejected tracker; dedup source. *(Yours — local only.)*                        |
| `shortlist.sample.md` / `applied.sample.md` | **Fictional demo data** so the repo runs out of the box.                                  |
| `tracker.html`                       | Browser UI over the two `.md` files.                                                           |

> Your real `shortlist.md` / `applied.md` are **git-ignored** so personal data never gets
> committed. Only the `*.sample.md` files ship with the repo. To start: copy a sample to the
> real name (`cp shortlist.sample.md shortlist.md`), or open a sample straight from
> `tracker.html` via **Open file(s)…**.

## License

Licensed under the [GNU GPL v3](LICENSE) © 2026 Yoav Dim. If you use, modify, or
redistribute this software, please retain the notice and credit the original author
([@yoavdim](https://github.com/yoavdim)).
