# Run configuration

Check the stages to run on the next pass.

## Stage 0 — Maintenance

- [X] `0a` [Fold thoughts.md into prefs](search-playbook.md#stage-0a) — 🧠
- [X] `0b` [Sync Simplify tracker](search-playbook.md#stage-0b) — 🔧
- [X] `0c` [Process manual.md URLs](search-playbook.md#stage-0c) — 🧠
- [X] `0d` [Migrate resolved shortlist rows](search-playbook.md#stage-0d) — 🔧
- [X] `0e` [Liveness sweep](search-playbook.md#stage-0e) — 🔧

## Stage 1 — Searches

- [X] `1` [Search — sources](search-playbook.md#stage-1) (master)
  - [X] `1a` [Web search](search-playbook.md#stage-1a) — 🧠
  - [X] `1b` [Regional boards](search-playbook.md#stage-1b) — 🧠
  - [X] `1c` [BuiltIn](search-playbook.md#stage-1c) — 🧠
  - [X] `1d` [LinkedIn keyword searches](search-playbook.md#stage-1d) — 🔧
  - [X] `1e` [LinkedIn recommended](search-playbook.md#stage-1e) — 🔧
  - [X] `1f` [Triage + ATS verify](search-playbook.md#stage-1f) — 🧠
  - [X] `1g` [Filter &amp; tier](search-playbook.md#stage-1g) — 🧠

## Stage 2 — Wrap-up

- [X] `2a` [Open keepers, close Scratch](search-playbook.md#stage-2a)
- [X] `2b` [Bump headers](search-playbook.md#stage-2b)

## Profiles

```yaml
profiles:
  no-llm-sweep: ["0b", "0d", "0e"]
```

## Dependencies

```yaml
requires:
  0a: []
  0b: [0a]
  0c: [0a]
  0d: [0a]
  0e: [0a]
  1: []
  1a: [1]
  1b: [1]
  1c: [1]
  1d: [1]
  1e: [1]
  1f: [1]
  1g: [1f]
  2a: [1g]
  2b: [1, 0b]
```
