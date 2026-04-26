# Changelog

All notable changes will be documented in this file. This project aims
for [Semantic Versioning](https://semver.org). Until v1.0, everything
is subject to change.

## Unreleased — 2026-04-26 — Theory split out

**Removed**
- `bella/` subfolder — the BELLA formal theory (SPEC, VISION, MEMORY,
  EXAMPLES) has moved to its own repository under the Recursive
  Emergence org: https://github.com/Recursive-Emergence/bella. That
  repo is now the canonical home for the theory framework and its
  validation harness (`replay/`).

**Changed**
- README §Theory and ARCHITECTURE.md updated to point at the external
  theory repo. Bellamem remains the empirical tool — local
  accumulating memory for LLM coding agents — and references the
  theory rather than embedding it.

## [0.3.1.2] — 2026-04-15 — Daemon replaces cron

**Added**
- `bellamem daemon start|stop|status|logs` — long-running background
  process that serves the localhost web UI on port 7878 AND runs a
  per-project save loop in-process. One process manages everything.
  PID file at `~/.config/bellamem/daemon.pid`, log at
  `~/.config/bellamem/daemon.log`.
- `bellamem daemon logs --follow` tails the log like `tail -f`.
- Save loop stagger (500ms per project) so OpenAI calls don't dogpile
  on daemon start. Shared Embedder + TurnClassifier across projects
  so sha256 caches stay warm. Default save interval: 5 minutes per
  project. `--save-interval-minutes` override on start.
- Tail window (default 200 turns) per save tick — critical for large
  sessions like a 242 MB Claude Code jsonl where a full scan would
  block the tick for minutes.

**Removed**
- `scripts/bellamem-dogfood-cron.sh` — retired. The daemon subsumes
  the cron-based save loop. Users migrating from the Python era
  should run `bellamem daemon start` and remove the cron line from
  their crontab (`crontab -e` and delete the line referencing
  `bellamem-dogfood-cron.sh`).

**Fixed**
- Web UI topbar nav links (`/overview`, `/graph`, `/trace`) 404'd
  when clicked from inside a per-project view because the hardcoded
  hrefs didn't carry the `/p/<id>/` prefix. Added `web/static/nav.js`
  which rewrites the nav at page load and prepends a "← projects"
  breadcrumb.
- `bellamem daemon start` reports the correct URL even when the port
  walker bumps off the default (previous behavior reported the
  configured port, not the actually-bound one).
- `resolveSessionJsonl` (used by `bellamem save`) now skips
  metadata-only jsonls (file-history-snapshot) that would otherwise
  win the mtime race against an active session.
- `resolveSessionJsonl` previously used `require("node:fs")` inside
  an ESM module, which silently threw and returned false for every
  file, making `bellamem save` unreachable via the cron bridge.

## [0.3.0-alpha] — 2026-04-15 — Node/TypeScript rewrite

Full port to TypeScript. Python v0.2 is frozen at tag `v0.2.0-ref`
and removed from master. `.graph/v02.json` is unchanged — the Node
port reads and writes byte-identical graphs, validated by a diff
harness against the reference implementation on the live 567-concept
session graph.

**Added**
- `packages/bellamem/` — Node port of the v0.2 core. Same seven ops
  (resume/save/recall/why/ask/audit/replay), same R1 mass formula,
  same frozen `PROMPT_VERSION = "v2"` classifier prompt.
- `bellamem serve` — localhost web UI on port 7878 (Hono). Three
  views: **overview** (audit + mass histogram + class × nature grid),
  **graph** (D3 force-directed with session coloring and concept
  drawer), **trace** (turn-by-turn session replay with live R1 mass
  deltas — the provenance-visible differentiator vs claude-mem).
- `bellamem install` — writes `~/.claude/commands/bellamem.md` and
  a user-config `.env` template so `/bellamem` works in Claude Code
  out of the box.
- `docs/rewrite/v0.2-spec.md` — language-independent spec (~1200
  lines) that any future port must implement. Freezes the schema,
  R1 formula, classifier prompt (verbatim), cache keys, audit
  thresholds.
- `docs/rewrite/v0.3-node-plan.md`, `v0.3.1-web-ui-plan.md` — why
  we ported, phased rollout, web UI scope.
- Vitest suite (51 tests) covering schema, graph, audit, walker,
  resume, replay, and live-graph round-trip fidelity.

**Removed**
- Python package (`bellamem/`, `tests/`, `benchmarks/`,
  `experiments/`, `pyproject.toml`). Recoverable via
  `git checkout v0.2.0-ref`.
- Flat v0.1 commands dropped for v0.3.0: `expand`, `emerge`,
  `before-edit`, `prune`, `decay`, `scrub`, `surprises`, `entities`,
  `render`, `bench`, `ingest-cc`. The walker (`ask`/`recall`/`why`)
  subsumes retrieval; the typed graph obviates the rest.
- `THEORY.md`, `CONTRIBUTING.md` — both Python-specific; the v0.2
  spec doc is the new implementation reference.

**Migration**
- `.graph/v02.json` is unchanged — no data migration required.
- If you were on Python: `pipx uninstall bellamem`, install the Node
  package, then `bellamem install` to refresh the slash command.

## [0.2.3] — 2026-04-14 — 3D viz port to v0.2

Closes Phase A of the VIZ_DESIGN spec — the Three.js 3D visualization
now reads the v0.2 graph natively instead of the flat v0.1 schema.
The legacy `bellamem/viz/render3d.py` stays in-tree as-is; the v0.2
port lives in `bellamem/proto/viz_3d.py` alongside the 2D renderers.

### New

- **`python -m bellamem.proto viz --renderer 3d`** — third renderer
  option alongside `d3` and `cytoscape`. Outputs a self-contained
  Three.js HTML file with:
  - UMAP × topic-embedding for X/Z clustering (semantically near
    concepts land near each other in the scene)
  - Mass for Y (ratified content rises above the floor plane)
  - Class-per-shape: invariant=icosahedron, decision=tetrahedron,
    observation=sphere, ephemeral=cube
  - Nature-per-color: metaphysical=amber, normative=blue, factual=green
  - Turn hubs placed at the centroid of their spoke concepts (hub
    sits ~1.5 units below the concept cloud)
  - Hover tooltips, click-to-sidebar, orbit camera, reset view,
    toggle hubs/edges

- **`bellamem/proto/viz_3d.py`** — new data-layer module that
  reuses `viz.build_payload` for filter+hub work and extends the
  payload dict with per-concept `pos_3d` triples. Re-embeds topic
  text at render time using the existing `Embedder` cache; first
  render against a fresh graph costs ~$0.01 of OpenAI calls for
  400+ concepts, subsequent renders are instant.

- **`bellamem/proto/viz_template_3d.html`** — new Three.js scene
  template. Self-contained, imports Three.js r160 from CDN.
  4 new tests in `tests/test_proto_viz_3d.py` covering position
  assignment, Y-mass correspondence, turn-hub centroid placement,
  and end-to-end HTML payload inlining.

### Notes

- Requires the `[viz3d]` extra (`umap-learn>=0.5`, already declared
  in pyproject). Clean error message if missing.
- Temporal replay (Phase B) and session filtering are still
  deferred. The current 3D view is a static typed-structural
  snapshot, same as the 2D renderers.

## [0.2.2] — 2026-04-14 — graph-health pass: rebuild-mass, prompt v2, audit layer

A graph-quality session after 0.2.1 — three structural fixes landed
together. Motivation: a diagnostic scan showed 85% of concepts stuck
at the m=0.5 mass floor, 97% of edges turn-to-concept instead of
concept-to-concept, and an LLM extractor that split single ideas
into five sub-attribute concepts. The graph read as a transcript
with tags, not a concept map.

### New

- **`python -m bellamem.proto rebuild-mass`** — one-shot R1 repair
  pass. Replays each concept's stored `source_refs` through `cite()`
  to recompute `mass` and `voices`. Used to fix graphs built by the
  legacy `experiments/proto_tree.py` path which bypassed `cite()`
  entirely (its own ProtoGraph dataclass had no voices/mass at all).
  On the live graph: 531/627 frozen concepts → 0, top mass 0.769 → 0.881.
  `--dry-run` flag for preview-without-save.

- **`python -m bellamem.proto audit`** — entropy + health signals
  over the current graph. Five metrics with ok/soft/hard verdicts:
  `concept_density`, `structural_edge_ratio`, `mass_floor_fraction`,
  `mass_spread` (bucket-entropy of mass distribution), and
  `orphan_refs`. Exits 1 on any hard flag. The `bellamem resume`
  footer now surfaces red flags inline so structural problems are
  visible in every resume, not just on explicit audit.

- **Prompt v2** for `TurnClassifier` (cache-busted from v1). Three
  targeted changes:
  - **ONE CONCEPT PER IDEA** rule with an explicit bad/good example
    taken from live data — the classifier was splitting "concept
    map visual vocabulary" into five parallel sub-attribute concepts.
  - `concept_edges` promoted to a first-class output with examples
    of when to use `cause` / `elaborate` / `dispute`. Previously it
    was a one-liner at the bottom.
  - "Don't default to support" rule. Support was 436/691 edges in
    the live graph; it's the weakest cc-edge signal and should be
    the fallback, not the default.

### Changed

- `DEDUP_COSINE` lowered from 0.85 to 0.78 for tighter near-variant
  merging at ingest time in `find_similar_concept()`.

### Fixed

- Mock embedder in `tests/test_proto.py` now produces zero-centered
  32-dim vectors (was uint8-based 8-dim, all-positive). The lower
  `DEDUP_COSINE` hit the old embedder's ~0.75 cosine noise floor
  and false-merged unrelated topics in one test.

### Added tests

- `tests/test_proto_audit.py` — 14 tests covering each audit signal
  and the aggregate AuditReport flag behavior
- 2 tests in `tests/test_proto.py` for `rebuild_mass_from_source_refs`

48 proto tests total, all passing.

### Deliberately NOT included

- Retroactive near-duplicate merge pass via hand-maintained stopword
  jaccard — violated the no-ad-hoc-stoplists rule. The real fix for
  concept explosion lives in the extractor prompt (above).
- Baseline-diff surprise signals ("what moved since last session").
  Needs session-boundary tracking and a persisted snapshot. Deferred.

## [0.2.1] — 2026-04-14 — docs: absolute image URLs for PyPI rendering

PyPI renders the project description from the uploaded wheel and
cannot resolve relative image paths. 0.2.0's README referenced five
assets as `docs/brand/...` and `docs/compression-curve-production.svg`,
which displayed as broken-image placeholders on the PyPI project
page even though they render fine on GitHub.

Fix: rewrite all five image sources to absolute
`https://raw.githubusercontent.com/immartian/bellamem/master/...`
URLs. Pinned to `master` (not a version tag) so future releases
don't have to update the URLs with each bump.

No code changes.

## [0.2.0] — 2026-04-14 — v0.2 stable: hot path + dynamics + interactive viz

Promotes the v0.2 schema from alpha to stable after landing concept
mass dynamics (R1), pattern emergence (R3), stale-state transition
(R5), source-grounded timestamps, and an interactive hypergraph
concept-map visualization in the browser.

### What's new since a1

- **BELLA R1 — concept mass via Jaynes accumulate.** New voice on a
  concept bumps log-odds by a large delta; repeat voice by a small
  one. Mass saturates at 1.0 via sigmoid, giving ratified content
  a 4× visible lead over single-voice floor concepts.
- **BELLA R3 — pattern emergence.** Recurring multi-source patterns
  rise without hand-labeling.
- **BELLA R5 — stale-state sweep.** `sweep_stale_ephemerals` flips
  open ephemerals to `stale` after max-age with no re-voice,
  unrooting abandoned plans from the open-work list.
- **Source.timestamp** from jsonl contents — wall-clock is now
  available for replay and the R5 age check.
- **Interactive hypergraph concept-map viz** (`bellamem.proto viz`).
  Two renderers — D3 v7 force-directed (draggable nodes) and
  Cytoscape.js + fcose (auto-settling) — plus a static graphviz
  SVG path for docs-embedding. Visual vocabulary: shape encodes
  class (hexagon / diamond / ellipse / round-rect), fill color
  encodes nature (amber / blue / green), node radius encodes mass,
  border style encodes ephemeral state. Turn hubs materialize the
  hypergraph: any turn that cites ≥N concepts becomes a visible
  rosette linking its cited concepts. Turn-hub ground truth comes
  from `concept.source_refs` (1280+ citations) not `graph.edges`
  (670 typed Edges), closing a 33% coverage gap that would have
  left high-mass concepts looking orphaned.
- **Release hygiene**: `openai` + `numpy` moved from optional
  extras to core deps for clean install.

## [0.2.0a1] — 2026-04-14 — v0.2 hot path: tree+cross-edges, class×nature, dogfooded

First alpha of the v0.2 graph schema. The flat belief field from v0.1
is replaced with a tree of typed concepts over first-class edges,
source-grounded citations back to session jsonls, and per-turn LLM
ingestion via a cheap cached classifier. Clean break — no migration
from the v0.1 flat graph. Legacy `.graph/default.json` is untouched
but unused by the new hot path.

### What's new

- **`bellamem.proto` module** — five files (schema, graph, store,
  clients, ingest) + resume + dispatcher. Concepts are classified on
  two orthogonal axes: **class** (invariant / decision / observation /
  ephemeral) and **nature** (factual / normative / metaphysical).
  Ephemerals have a state machine (open / consumed / retracted /
  stale). Edges are first-class with their own voices, confidence,
  and established_at provenance.
- **`bellamem resume`** now prints a v0.2-native typed structural
  summary (invariant×metaphysical, invariant×normative, open
  ephemerals, retracted approaches, dispute edges) instead of the
  flat replay/expand/surprises composite.
- **`bellamem save`** now incrementally ingests the latest Claude
  Code session jsonl into `.graph/v02.json` via
  `bellamem.proto.ingest_session`. Already-ingested turns skip via
  `turn.id in graph.sources`, so re-runs are cheap.
- **`bellamem-guard` hook** rewritten v0.2-native. Reads
  `.graph/v02.json` directly, advisory sections match the resume
  layout, blocking check fires on retracted ephemeral topics or
  dispute-edge target concepts (2-source-ref minimum).
- **`bellamem-dogfood-cron.sh`** rewired to call `python -m
  bellamem.proto ingest LATEST_JSONL`. Incremental by design.
- **Retraction detection groundwork** (`classify_retraction` +
  `LLMExtractor.pick_retraction`) — LLM-delegated, no lexical
  fallback, 13 passing spec tests. Not wired into ingest yet.
- **`bellamem/proto/RULES.md`** — BELLA R1–R6 mapping to v0.2.

### Fixed

- **Cache-poisoning bug in `proto.TurnClassifier`.** Error-fallback
  responses were being cached and silently masqueraded as real
  classifications on re-run. Fix: don't cache error responses.
- **Bench corpus cleanup.** Q01/Q10/Q13/Q14 removed — scope
  mismatch, answers live in docs/source not conversations.

### What's NOT migrated yet

Most non-hot-path CLI commands still read the flat graph: expand,
ask, before-edit, entities, bench, audit, emerge, replay,
surprises, scrub, why, prune, decay, render. Each migrates as
touched.

### Dogfood

Ran against the full 637-turn session where v0.2 was designed and
built. Produced 437 concepts, 524 edges, 62 invariant×metaphysical
concepts capturing the session's architectural arc by name.
Runtime ~30 min cold cache (~$0.20), free on re-run.

### Breaking

- `bellamem resume` no longer prints replay/expand/surprises — use
  `bellamem.proto.load_graph()` for parseable output.
- `bellamem save` no longer prints audit/surprises sections.
- `bellamem show` groups by class × nature instead of flat tree.
- `bellamem stats` reports v0.2 counts (sources/concepts/edges).

## [0.1.2] — 2026-04-13 — second user-reported fix, classifier rot fixes, scenarios harness

The second patch cycle. Ships another user-reported fix from
@Salgado-Andres, two structural fixes to the ingest classifier
that close known feedback loops, an audit-exit-semantics cleanup,
the scenarios harness with empirical compression measurements,
and a bunch of brand asset additions.

### Fixed

- **`.graph` saved to shell cwd instead of project directory when
  cwd is not a git repo** (#4, reported by @Salgado-Andres). Same
  reporter as #3, same precise diagnosis. On Windows, launching
  Claude Code from `C:\Program Files\Claude Code` (not a git repo)
  caused `paths.project_root()` to silently fall back to cwd —
  bellamem then created `.graph/` inside the Claude Code install
  directory, which also hit Windows permission issues. Fix adds a
  new `BELLAMEM_PROJECT` env var as an explicit project anchor (the
  reporter's exact suggestion), and makes the cwd fallback emit a
  one-time stderr warning so silent fallback never happens again.
- **Turn-pair ratification rot.** A user "ya" / "do it" / "sure"
  was voice-crossing *every* claim extracted from the preceding
  assistant turn, inflating the top-ratified-decisions list with
  mid-discussion exposition instead of actual decisions. The
  classifier now ratifies only the last-extracted claim — matching
  the semantic of "user authorises the most recent offer", not
  "user validates every content-marker sentence". Found via
  dogfooding: the audit's own top decisions had become things like
  *"Pros: ..."* and *"Cons: ..."* lists.
- **Chat EW skips quoted graph output.** When the assistant quoted
  audit/expand/surprises lines (`m=0.74 v=2 [field] ...`) into
  conversation, those quoted lines were being re-extracted as new
  claims and showing up in the next audit as fresh bandaid piles.
  The graph was eating its own inspection reports — an ouroboros
  feedback loop the graph had already ratified as a known rot. Fix
  adds a `_GRAPH_OUTPUT_RE` filter that recognizes the unambiguous
  `m=0.XX v=N` and `score=X.XX Δ=` fingerprints and short-circuits
  classification on sentences containing them.

### Changed

- **`bellamem audit` exits 0 by default**, with a new `--strict`
  flag for CI linter pipelines that want to fail the build on
  entropy signals. Previously audit returned exit code 4 whenever
  it found anything, which made `/bellamem audit` look like a
  shell failure in Claude Code for any non-pristine graph. Audit
  is a diagnostic, not a linter — finding signals is normal, not
  an error. `--no-exit-code` is kept as a backwards-compat no-op.
- **Finished the `BellaMem → Bella` rebrand** across the slash-command
  template, source comments, and the three test files that still
  asserted against the old name. Full test suite is now 121/121
  green for the first time since the rebrand.

### Added

- **`docs/scenarios.py` — scenarios harness**, a multi-scenario
  measurement framework that demonstrates Bella's compression with
  reproducible numbers. Covers entropy reduction, structural
  preservation, surfacing correctness, and token compression. Four
  synthetic scenarios (rejected-refactor, flaky-test, long-debug,
  sprint) at sizes from 80 to 1065 raw tokens, with structural
  preservation and surfacing checks per scenario.
- **`docs/scenarios.md` — generated report** that pins the headline
  metrics: synthetic linear-fit break-even at **~214 raw tokens**,
  production median compression ratio of **17.6×**, range
  3.6×–90× across 15 sessions sampled from 15 different Claude
  Code projects on a developer machine.
- **`docs/compression-curve.svg`** — small-scale linear-regime chart
  showing the synthetic scenarios + linear fit + break-even point.
  Self-contained SVG, system-ui font (no Camo-proxy font loading
  issues), 720×480 viewBox.
- **`docs/compression-curve-production.svg`** — log-x production
  chart showing 15 real-session data points, the budget ceiling at
  1500, and the synthetic scenarios overlaid as gray markers in
  the lower-left for context. The "expand is bounded, raw is not"
  story made visual.
- **README "Compression at scale" subsection** under Empirical
  Results, surfacing the production curve and the median 17.6×
  number that's now the strongest empirical claim in the repo.
- **`tests/test_scenarios.py` — 7 regression assertions**: structure
  preservation per scenario, surfacing correctness per scenario,
  flaky-test entropy drop, long-debug positive token compression,
  rejected-refactor dispute survival, sprint vs. long-debug
  monotonic ratio growth, break-even point pinned under 300 tokens.
- **`docs/brand/context-collapse.{svg,png}`** — the README hero
  image refresh from earlier in the cycle: bigger LOSS/ENTROPY
  typography, expanded viewBox, raster regenerated to match.
- **`docs/brand/flat-vs-graph.svg` and `docs/brand/zimbo-to-agent.svg`**
  finally tracked after drifting untracked across several sessions.

### Housekeeping

- Tests: 121 → 122 → 122 (added break-even pin, then merged into
  the existing scenarios suite).
- Master is 16+ commits ahead of v0.1.1 at release time.

### Not in this release

- Three.js 3D viz (deferred to v0.1.3+)
- `bellamem ask` unified retrieval, autonomic lifecycle timer
  (tracked in #2 — command surface reduction)
- Graph-backed `/compact`, native edit-guard primitive (tracked in
  #1 — blocked on upstream Claude Code hooks)

---

## [0.1.1] — 2026-04-13 — Windows fix, AGPL transition, theory split

First patch release. Ships the fix for the first community-reported
bug (Windows `UnicodeDecodeError` on `bellamem save`), the license
transition from MIT to AGPL-3.0-or-later, and the split between the
formal BELLA theory and the bellamem implementation.

### Fixed

- **Windows `UnicodeDecodeError` on `.jsonl` ingest** (#3, reported
  by @Salgado-Andres). `adapters/claude_code.py:iter_turns` opened
  `.jsonl` transcripts without an explicit `encoding`, so on Windows
  Python's default code page (`cp1252`) couldn't decode UTF-8 bytes
  like `0x90`. Auditing for the same pattern surfaced **11 more**
  text-mode `open()` calls with the same latent bug — every read/write
  of user content in `adapters/llm_ew.py`, `core/store.py`,
  `core/embed.py`, `cli.py` DOT output, and `bench.py`. All 12 sites
  now open with explicit `encoding="utf-8"`. The two `.jsonl` readers
  additionally use `errors="replace"` so undecodable prose bytes
  become `?` rather than crashing the ingest — JSON structural
  characters are always ASCII-safe so parsing continues. The other
  10 sites are strict: bellamem owns those files and corruption
  should fail loud.

### Changed

- **License: MIT → AGPL-3.0-or-later.** Community use stays free;
  cloud vendors who modify Bella and serve it over a network must
  open-source their modifications. Updated `LICENSE`, `pyproject.toml`
  (license field + classifier), and README.
- **`bella/` — formal theory lives alongside the implementation.**
  The domain-agnostic BELLA calculus (SPEC, VISION, EXAMPLES, MEMORY)
  is now a sibling to the `bellamem/` Python package. `THEORY.md`
  restructured to cover only implementation choices (thresholds, data
  structures, worked example, decay math); formal definitions live in
  `bella/SPEC.md`. This positions bellamem as one application of the
  BELLA calculus — the abstract `Bella` base class is deferred until
  a second application forces the interface to be discovered.
- **Canonical-workspace policy.** `bellamem save` and the slash-command
  session discovery now use `project_root()` (the git repo root) by
  default instead of raw `cwd`. One project, one memory, regardless of
  which subfolder you launched Claude Code from. A stderr caveat fires
  when `cwd ≠ project_root` so the behavior is visible.
- **`.env` loads from `project_root()`**, not cwd. One project, one
  config, regardless of subfolder.
- **README tightening.** Pain section compressed from 37 → 24 lines
  using the "intern with amnesia" framing from the content arsenal.
  Architecture file tree replaced with a 6-line summary + link to
  `ARCHITECTURE.md`. Added the "RAG retrieves documents. Agents need
  to retrieve beliefs." positioning line. Net: README shrunk 534 → 495
  lines.
- **Finish `BellaMem → Bella` rebrand** across slash-command template,
  source comments, and the three tests that still asserted against the
  old name (`test_visualize.py`, `test_composites.py`,
  `test_example_session.py`).

### Housekeeping

- `.claude/` fully gitignored and removed from history via
  `git filter-repo`. The `bellamem-guard` hook config is no longer
  tracked; new clones can write their own `settings.json` locally.
- `.codex/` gitignored for Codex agent state.
- Full test suite now 109/109 green for the first time since the
  rebrand.

### Not in this release

- Three.js 3D viz (deferred to v0.1.2+)
- `bellamem ask` unified retrieval, autonomic lifecycle timer
  (tracked in #2 — command surface reduction)
- Graph-backed `/compact`, native edit-guard primitive, belief-
  addressable context window (tracked in #1 — blocked on upstream
  Claude Code hooks)

---

## [0.1.0] — 2026-04-11 — log-odds decay and the steady state

First version where beliefs fade when nobody talks about them anymore.
Decay is off-by-default (`BELLAMEM_DECAY=on` to enable); v0.1.0 proves
the machinery is safe, the on-by-default flip comes in a follow-up
after a week of dogfood on a 5-minute cron.

### Added

- **`core/decay.py`** — exponential log-odds decay with a single knob
  (`half_life_days`, default 30). `decay(bella, dt_days)` multiplies
  every non-exempt belief's `log_odds` by `exp(-dt * ln(2) / τ)`, which
  pulls mass toward 0.5 (the prior) at a rate set by `τ`. Exempt:
  reserved-field beliefs (`__self__`, `__user__` etc.), `mass_floor`
  pins, ⊥ disputes, ⇒ causes. Returns a report with per-belief
  `QuietFade` entries for any belief that crossed from ratified (≥ 0.55)
  into limbo (0.45–0.55) on this pass.
- **`bellamem decay` subcommand** — standalone dry-run preview + `--apply`,
  with `--dt-override N` for "what if N days of decay right now?"
  sanity checks. Prints the top movers (by |Δmass|) and any quiet
  fades.
- **`BELLAMEM_DECAY=on` gating in `bellamem save`** — when set, each
  save auto-applies decay for `(now − decayed_at)` before ingest. The
  snapshot header carries `decayed_at`; pre-v4 snapshots backfill it
  from `saved_at` on load. Zero-touch migration, no change to
  `embeddings.bin`.
- **v3 → v4 snapshot format** — adds `decayed_at` to the header.
  `load()` handles v3 transparently by treating `decayed_at ==
  saved_at`. Round-trip tested.
- **THEORY.md "Decay and reinforcement — the steady state" section.**
  Works out the collision-math break-even: how many collisions per
  half-life a belief needs to survive, why reinforcement and decay
  compose multiplicatively (so 288 saves/day with tiny Δt give the
  same factor as 1 save/day with big Δt), and the brain analogue.
  The TL;DR: with `BELLAMEM_DECAY=on` + a 5-minute cron, active topics
  get topped up continuously while dormant topics bleed off — not as
  phased passes but as simultaneous dynamics.
- **`benchmarks/v0.1.0a.md`** — worst-case stress test (one simulated
  half-life, no reinforcement). Decay did not break retrieval:
  `expand` held 85% LLM judge (−7pp, within 1-item noise on a 13-item
  corpus) while cutting `avg_tokens_used` by 31% (1243 t/hit → 927
  t/hit). No ratified belief crossed into limbo at Δt=30d. Structural
  ordering preserved: `flat_tail < compact < rag_topk < before_edit
  ≤ expand`.

### Changed

- **`Bella` forest gains `decayed_at: float`** (default = save time).
  Round-trip via `store.py` preserves it across save/load.

### Why this matters

v0.0.4rc1 shipped the storage split and the edit guard — things that
make bellamem feel like a well-behaved Python package. v0.1.0 ships
the first piece of *dynamics*: the graph now has both reinforcement
(via new ingest collisions) and forgetting (via decay), and the
steady state is a real computable point, not a vibe. Everything that
gets mentioned repeatedly stays high; everything that stops coming
up fades gently toward 0.5 without ever being "deleted." Prune remains
the reaper; decay is the upstream slide that makes prune more
effective without forcing it.

The stress-test bench ran against a throwaway copy of the live
1834-belief forest with one full half-life applied in a single pass
— strictly worse than reality, since real operation composes
reinforcement against decay. v0.1.0 post-dogfood numbers will land
in `benchmarks/v0.1.0.md`.

### Deliberately not in v0.1.0

- **Decay on-by-default.** Stays gated on `BELLAMEM_DECAY=on` until
  a week of dogfood validates the steady-state hypothesis on real
  traffic.
- **A `bellamem pin` CLI.** Manual pinning was considered and
  rejected in favor of natural reinforcement via collisions — a
  belief that actually matters will get re-mentioned and topped up
  by the next ingest; a belief nobody brings up shouldn't be kept
  alive by a manual flag.
- **Recording decay in `jumps`.** Decay is not a Jaynes step — it's
  a bleed. Putting it in `jumps` would flood the surprise signal
  with thousands of small fade entries and drown out real updates.

---

## [0.0.4rc1] — 2026-04-10 — storage split + edit guard

Non-vector operations got ~4× faster and the edit-time advisory pack
became automatic.

### Added

- **v3 split snapshot format.** Belief embeddings moved out of
  `default.json` into a `default.emb.bin` sidecar. Non-vector
  operations (`expand`, `audit`, `replay`, `surprises`) stopped
  having to parse and deserialize the embedding arrays just to read
  graph structure. Load time on the dogfood forest dropped from
  ~2s to ~500ms. `load()` and `save()` handle both v2 and v3
  formats transparently.
- **`bellamem-guard` PreToolUse hook** (`bellamem/guard.py`,
  entry point `bellamem-guard` in `pyproject.toml`). Installed as
  a Claude Code `PreToolUse` hook on `Edit|Write|MultiEdit`, it
  injects a 5-layer advisory pack (invariants / disputes / causes /
  bridges / self-model) before every edit and `exit-2`s when the
  edit re-suggests an approach already rejected by a ⊥ dispute.
  Boundary-level, not semantic — it sees tool-call text, not model
  intent — but that's enough to catch re-suggestion of rejected
  approaches in practice.
- **Embedder pre-warm in `ingest_session`.** One batched embedder
  call per save before the turn-loop starts, instead of lazy
  per-claim dispatch. Cuts save latency for the steady-state case
  where ingest produces many new claims.
- **`benchmarks/v0.0.4rc1.md`** — re-run of the bench against the
  grown 1834-belief forest. `rag_topk` collapsed from 85% → 31%
  LLM judge as the forest grew (cosine top-k pulls up more
  plausible-looking-but-wrong neighbors in a larger forest), while
  `expand` held at 92%. Gap from `expand` to next-best contender
  widened from 15pp to 61pp. Retrieval code path is unchanged since
  v0.0.2 — every delta is a property of forest growth, not
  algorithm changes.

### Why this matters

The storage split is the kind of boring-but-load-bearing fix that
makes bellamem feel acceptable to run on every `save`. The guard
closes the remaining gap in the save → clear → resume loop: now
the agent gets the right context *before* it edits, not just when
it pauses to recall. And the bench re-run is what proves the
graph-memory model scales with forest size while cosine top-k
doesn't — the headline story since v0.0.2.

---

## [0.0.3rc1] — 2026-04-09 — graph memory, per-project state, slash commands

First **release candidate**. v0.0.3 is the first version where bellamem
is comfortable being installed into other projects — previous versions
assumed a global `~/.bellamem/` snapshot and the bellamem repo's own
`.venv`. This RC cleans up both.

### Added

- **Per-project graph at `<project>/.graph/default.json`.** Each git
  repo gets its own belief graph — no more cross-project contamination.
  The embed cache and LLM EW cache move alongside the snapshot. All
  three paths still honor `BELLAMEM_SNAPSHOT`, `BELLAMEM_EMBEDDER_CACHE_PATH`,
  and `BELLAMEM_EW_LLM_CACHE_PATH` overrides.
- **`bellamem migrate`** — one-shot command that copies legacy
  `~/.bellamem/` state into the current project's `.graph/`. Copies
  rather than moves, so the legacy files stay in place until you've
  verified the migration. Safe to re-run.
- **`bellamem/paths.py`** — leaf utility that centralizes runtime-state
  path resolution. Used by both core and adapters; imports only stdlib.
- **Slash command layer** (`.claude/commands/bellamem.md` +
  `bellamem-cmd.sh`) — `/bellamem`, `/bellamem save`, `/bellamem recall`,
  `/bellamem why`, `/bellamem replay`, `/bellamem audit`, `/bellamem help`.
  The dispatcher auto-detects `.venv/bin/bellamem` or falls back to
  whatever `bellamem` is on `$PATH` (pipx, user install, system).
- **`bellamem replay`** — narrative timeline retrieval. Returns beliefs
  from the latest session in source-line order, tail-preserving under
  tight budgets. Complements `expand` (mass-weighted) and `surprises`
  (signal).
- **`bellamem surprises`** — top Jaynes step surprises weighted by prior
  uncertainty, sign flips (beliefs that crossed 0.5), and recent ⊥ edge
  formations. The "what just mattered?" signal.
- **`bellamem emerge`** — R3 consolidation: near-duplicate merge (cosine
  ≥ 0.92 in the same field) + field rename from content. Auto-runs at
  the end of each `ingest-cc` unless `--no-emerge` is passed. `--llm`
  flag optionally uses gpt-4o-mini to name fields the contrastive-rate
  baseline can't disambiguate.
- **`bellamem scrub`** — one-time migration that removes system-noise
  beliefs (interrupt sentinels, command echoes) from pre-filter
  snapshots.
- **Provenance (`sources`) on every belief** — `(session_key, line_number)`
  list populated by the Claude Code adapter at ingest time. Retroactive
  ratification stamps the user's line, not the assistant's. Preserves
  full evidence trail across merges (source lists are unioned).
- **Audit entropy signals** — `bellamem audit` now reports bandaid
  piles (R2), root glut (fields where most beliefs are unconnected),
  near-duplicate pairs (R3 merge candidates), mass limbo (decisions
  stuck at 0.45–0.55), garbage auto-generated field names, and the
  multi-voice ratified / top-disputes summaries.
- **`bellamem render`** — graphviz-backed visualization of the belief
  forest. Filters: `--focus` + `--depth` for subgraph BFS, `--field`,
  `--disputes-only`, `--min-mass`, `--max-nodes`. Output format is
  inferred from the `--out` extension (.svg/.png/.pdf/.dot). Visual
  encoding: node border width + label size ~ mass, node fill ~ field,
  edge styles encode → support, ⊥ dispute (red dashed), ⇒ cause
  (blue). Requires the `[viz]` extra (`pip install bellamem[viz]`)
  for direct rasterization; without it, `bellamem render` writes a
  `.dot` source file and tells you how to render it with the system
  `dot` command.
- **`bellamem resume`** and **`bellamem save`** — composite CLI
  commands that replace the old shell dispatcher. `resume` prints
  working memory (replay tail) + long-term memory (expand pack) +
  signal (top surprises) in one call. `save` runs ingest-cc +
  auto-emerge + audit + surprises. Both write the same section
  headers the slash command post-processing expects, so the
  synthesize-according-to-subcommand flow is unchanged — only the
  dispatch mechanism has moved from bash into Python.
- **`bellamem install-commands`** — install the `/bellamem` Claude
  Code slash command. Default destination is `~/.claude/commands/`
  (global, works in every project). `--project` switches to
  `./.claude/commands/` for per-project install. `--dry-run` and
  `--force` available. Reads the template from packaged data via
  `importlib.resources` so `pipx install bellamem` carries it along.
- **`bellamem prune`** — structural forgetting: remove leaf beliefs
  that never earned their place. A belief is a prune candidate iff
  it's a leaf (no children), single-voice (`n_voices == 1`), in the
  base-mass band (`0.48 ≤ mass ≤ 0.55`, i.e. Jaynes never moved it
  off the prior), not itself a ⊥ dispute or ⇒ cause, in a non-reserved
  field, has no `mass_floor` pin, and has been both (a) untouched for
  `--age-days` (default 30) and (b) created more than `--grace-days`
  ago (default 14). Dry-run is the default: users must pass `--apply`
  to actually mutate the snapshot. Complements R3 emerge — emerge
  collapses duplicates, prune removes orphans. Together they form the
  full "consolidation state" story: beliefs enter raw, earn their
  keep through evidence or structure, or eventually age out. Log-odds
  decay (the Bayesian-principled alternative) is deferred to v0.1.
- **README "Use with Claude Code" section** — walks through install
  (pipx preferred), dropping the slash commands into a target project,
  first-run verification, and the save → clear → resume flow with a
  30k-token reference point.
- **Embedded graph rendering in the README** — `docs/bellamem-disputes.svg`
  is BellaMem's own ⊥ dispute structure, rendered directly from the
  live snapshot. Shows the rejected-approaches graph that `/compact`
  fundamentally cannot preserve.
- **Worked example in the README** — a full flaky-test debugging session
  compressed into a belief graph, with before/after SVGs generated by
  [`docs/example_session.py`](docs/example_session.py) and checked into
  the repo. Covers the six structural primitives (ratified decision,
  ⊥ dispute, ⇒ cause chain, `__self__` observation, residue, lattice
  compression) in one 13-turn dialogue. Numbers in the README come
  straight from the script; a pytest smoke test keeps them synced.
- **Upfront flat-vs-graph visual** — a monospace side-by-side at the
  top of the README contrasting 8 raw conversation turns with the
  4 beliefs + 2 edges BellaMem extracts from them. The reader gets
  the pitch in ~10 seconds before any prose.
- **Theory section** — a short "Why it works" explaining how Jaynes
  log-odds accumulation (with voice attenuation), Shannon entropy
  (literal, not metaphorical — prune reduces measurable entropy),
  and the [Recursive Emergence thesis](https://github.com/Recursive-Emergence/RE/blob/main/thesis.md)
  compose. The Ψ/Φ/Ω symbols from RE map directly onto the belief
  graph / ratified beliefs / dispute structure; the RE memory-update
  integral is the continuous form of Jaynes log-odds. BellaMem is a
  minimal working instance of RE for one substrate.
- **`.graph/` entry in `.gitignore`** — the per-project snapshot is
  gitignored by default. Remove the line if you want to commit your
  graph.

### Changed

- **Install guidance.** README now leads with `pipx install bellamem`
  as the recommended path. Per-project venv and editable installs
  still documented as alternatives.
- **`--snapshot` help text** and `.env.example` updated to reflect
  `<project>/.graph/default.json` as the default.
- **`bellamem-cmd.sh` dispatcher** auto-detects install style — tries
  `.venv/bin/bellamem` first, then `command -v bellamem`, fails loud
  with install instructions if neither is available.

### Changed

- **`bellamem save` and `bellamem ingest-cc` now default to "current
  session only"** — the mtime-latest transcript, i.e. the session
  Claude Code is actively writing to. The old "sweep every transcript
  in the project" behaviour is still available via the new
  `--all-sessions` flag, and is the right thing for first-run backfill
  or recovery after `bellamem reset`. The default flipped because
  the common case for `/bellamem save` is end-of-session save of the
  current day's work, not batch ingest of months of history. The
  old `--latest-only` flag is kept as a compat alias; it's now
  redundant.

### Fixed

- **`bellamem save --latest-only` picked the wrong file.** The
  adapter's `list_sessions` sorted transcripts alphabetically by
  their random UUID filename, so `sessions[-1]` (what `latest_only`
  returned) was whatever UUID sorted last, not the mtime-latest
  file. Fixed by sorting on `os.path.getmtime()` instead. Caught
  during dogfood when `bellamem save --latest-only` in a real
  project ingested a March 14 session instead of today's active
  one, and a subsequent `/bellamem resume` showed confidently stale
  content.
- **`replay._latest_session_key` picked by belief event_time
  (ingest time, not turn time).** A batch ingest leaves every new
  belief with nearly identical `event_time`, so the tiebreak was
  effectively random and often landed on a months-old session.
  Fixed by picking the session whose underlying `.jsonl` file has
  the newest mtime on disk. Same root cause as the `list_sessions`
  bug above — "use filesystem mtime, not the in-memory stamp."
- **`bellamem save` streams per-session progress during ingest**,
  instead of printing the whole summary after the full ingest
  completes. `adapters/claude_code.py:ingest_project` is now a
  generator that yields one result per session; CLI callers
  iterate and print as each session finishes. Previously the save
  could spend 50 minutes at 98% CPU with zero visible output.
- **`bellamem save` and long ingests now show progress in real time.**
  Python's default stdout buffering is line-buffered for a TTY but
  fully block-buffered (4-8 KB) for a pipe, which is how Claude Code
  captures a background task's stdout. The result: a long first-run
  ingest could write **zero bytes** of visible output for many minutes
  while the process was grinding at 96% CPU, making it look like the
  command was hung when it was actually making progress. Fixed by
  calling `sys.stdout.reconfigure(line_buffering=True)` at the top of
  `main()`, so every `print()` flushes on a newline regardless of
  whether stdout is a TTY or a pipe. Caught during a real-user dogfood:
  a first `/bellamem save` on a project with ~632 MB of transcripts
  ran silently for 50 minutes before we diagnosed it.

### Removed

- **`.claude/commands/bellamem-cmd.sh`** — the bash dispatcher that
  the first `/bellamem` slash command relied on is gone. Composition
  logic moved into `bellamem resume` and `bellamem save`, so the
  slash command is now a single pure-markdown file (shipped as
  package data and installed via `bellamem install-commands`). No
  more two-file per-project install, no more `chmod +x`, no more
  shell-script PATH-resolution edge cases.

### Deprecated

- **`~/.bellamem/` runtime state.** Legacy paths still work as a
  read-fallback when no `.graph/` exists, with a one-time deprecation
  warning per file. Run `bellamem migrate` to transition.

### Why this matters

v0.0.2 proved the graph-memory-for-agentic-coding idea could carry its
own weight on one project. v0.0.3 is the work that lets it travel:
per-project state so two repos don't contaminate each other, slash
commands so users don't need to remember CLI invocations during a
session, and an install story that doesn't assume you're developing
bellamem itself. The data model, bench numbers, and six-rule calculus
are unchanged — this is a packaging and UX release built on top of the
stabilized core.

The cross-session hand-off (`/bellamem save` → `/clear` →
`/bellamem resume`) was validated end-to-end on the day this RC was
cut, with a fresh resume reconstructing full session state in
~30k tokens of context.

---

## [0.0.2] — 2026-04-09 — rescope to context window management

**Removed the constitution layer** as mission creep. bellamem's job is
context window management for LLM coding agents — retrieving the
decisive facts for the next edit under a small token budget — and the
earlier PRINCIPLES.md / governance layer was a different problem
pretending to be the same one.

### Removed

- `core/principles.py` — the PRINCIPLES.md loader
- `bellamem/principles/classic.md` — the canonical engineering canon
- `PRINCIPLES.md` at the repo root — the project constitution
- Contradiction-against-principle detection in `core/audit.py`
- Drift-candidates-near-principle detection in `core/audit.py`
- `--principles` flag on `ingest-cc`
- `seed_principles` export from `core/__init__.py`
- Bench items that specifically tested constitution features (Q03, Q11)
- Constitution references throughout README, ARCHITECTURE, CHANGELOG

### Retained

- **All core data model** — `gene.py`, `ops.py`, `bella.py`, `embed.py`,
  `store.py` unchanged
- **`mass_floor` field on Belief** — kept as generic plumbing for
  pinning a belief's mass above a threshold. Not a governance concept.
- **`__self__` reserved field** — R4 self-observation, part of
  the memory, not the constitution
- **Reserved field prefix rules** — `__` prefix still protected,
  `is_reserved_field` still exported
- **Bandaid pile detection in audit** — R2 entropy signal, part of
  context quality (identifies structural problems disguised as bugs)
- **Top ratified decisions report in audit** — useful summary of
  "what did we commit to"
- **Top disputes (⊥ edges) in audit** — rejected approaches preserved

### Added

- **Disputes summary in `bellamem audit`** — replaces the removed
  contradiction section with a simpler "top disputes by mass" listing

### Why this matters

The bench numbers from v0.0.1 **do not depend on the constitution
layer**. The before-edit pack's wins come from disputes, entity
bridges, and multi-voice ratified beliefs — all of which are populated
from the session transcript, not from a hand-written PRINCIPLES.md.
The rescope removes ~300 LOC and ~60% of the documentation without
touching the load-bearing retrieval machinery.

### Migration

If you were using a PRINCIPLES.md with v0.0.1: the file still has
whatever you wrote into it, but bellamem no longer loads it. High-mass
pinned beliefs can still be created by calling `ops.add(..., mass_floor=0.95)`
directly; we just no longer ship a loader that reads PRINCIPLES.md and
calls that.

---

## [0.0.1] — 2026-04-09 — first dogfood

Initial alpha. Built and validated end-to-end in a single long session
(see [benchmarks/v0.0.2.md](benchmarks/v0.0.2.md) for the numbers). Scope: prove that BELLA's
six-rule calculus works as a memory architecture for LLM coding agents,
with enough substance to dogfood on its own construction.

### Core (`bellamem/core/`)

- **`gene.py`** — `Belief`, `Gene`, Jaynes log-odds accumulation with
  same-voice attenuation (0.1 factor). Stable belief ids via
  `md5(desc + parent)` so repeated claims deduplicate. `mass_floor`
  field for pinned beliefs.
- **`ops.py`** — the seven operations: CONFIRM, AMEND, ADD, DENY,
  CAUSE, MERGE, MOVE. Every mutation flows through exactly one.
- **`bella.py`** — the `Bella` forest, routing via embedding similarity,
  `Claim` dataclass, entity index for R6 bridging, reserved field
  rules with `is_reserved_field` guard, `self_observation` relation
  routing to `__self__`.
- **`embed.py`** — pluggable embedder protocol. Four backends:
  `HashEmbedder` (zero-dep default), `SentenceTransformerEmbedder`
  (`[st]` extra), `OpenAIEmbedder` (`[openai]` extra), `DiskCacheEmbedder`
  wrapping any of the above. Stdlib-only `.env` loader and
  `make_embedder_from_env` factory. Batched cache saves (every 50
  inserts) plus explicit `flush()` — 8× ingest speedup over naive save.
- **`store.py`** — atomic JSON snapshot (tmp+rename). Embedder
  signature check on load — fails loud if you switch embedders under
  an existing tree.
- **`expand.py`** — two modes:
  - `expand()` — generic mass-weighted pack, 60/30/10 mass/relevance/recency
  - `expand_before_edit()` — 5-layer pack, 40/20/20/10/10
    invariants/disputes/causes/bridges/self-model, **no recency**
- **`audit.py`** — read-only report: bandaid piles (R2 entropy signal),
  top ratified decisions, top disputes.

### Adapters (`bellamem/adapters/`)

- **`chat.py`** — voice-aware regex EW. User claims at `lr` 1.8–2.5,
  assistant claims at 1.05–1.3. Reaction classifier for turn-pair
  retroactive ratification (affirm/correct/neutral). Denial filter
  handles quoted (`` `don't` ``) and conditional (`if we don't`)
  denials.
- **`claude_code.py`** — reads `~/.claude/projects/<cwd>/*.jsonl`
  transcripts. Incremental ingest via per-file line cursor. Turn-pair
  retroactive ratification state machine.
- **`llm_ew.py`** — optional LLM-backed EW (`BELLAMEM_EW=hybrid`).
  `LLMExtractor` class with disk-cached gpt-4o-mini calls in JSON
  mode. Two extraction tasks: `find_cause_pairs` (structured cause→effect
  extraction) and `find_self_observations` (first-person habit
  statements for `__self__`). Marker gates short-circuit when there's
  nothing to extract. Fails loud on malformed JSON. Cost: ~$0.002
  per typical 98-turn session.

### CLI (`bellamem/cli.py`)

```
bellamem ingest-cc         ingest Claude Code .jsonl transcripts
bellamem expand QUERY      generic mass-weighted context pack
bellamem before-edit QUERY 5-layer pre-edit context pack
bellamem audit             bandaid piles / ratified / disputes
bellamem entities [NAME]   list / inspect R6 entity bridges
bellamem bench             compare all contenders on the corpus
bellamem embedder          show active embedder config
bellamem show              render the whole forest
bellamem stats             summary
bellamem reset             delete the snapshot
```

### Bench (`bellamem/bench.py` + `bench_corpus.py`)

- 15-item hand-labeled corpus drawn from the dogfood session
- 5 contenders: `flat_tail`, `compact` (gpt-4o-mini summary),
  `rag_topk`, `expand`, `before_edit`
- 2 metrics: `exact` (substring) and `embed` (cosine ≥ 0.40, union
  with exact)
- Budget sweep + full comparison table rendering

### Headline result

On the 15-item corpus at 1200 tokens:

```
flat_tail  13 %   compact  33 %   rag_topk  93 %   expand  100 %   before_edit  100 %
```

`before_edit` at 500 tokens reaches 100%; `flat_tail` plateaus at 93%
and cannot reach 100% at any budget. See [benchmarks/v0.0.2.md](benchmarks/v0.0.2.md) for
methodology and caveats (self-reference bias, corpus size,
retrieval-vs-behavior distinction).

### What's not built yet (tracked for v0.1+)

- **MCP server + hooks** — wrap `before_edit` + `expand` as Claude
  Code tools and integrate via `PreToolUse` / `SessionEnd` hooks
- **R2 heal pass** — port entropy-driven restructure from the
  herenews-app `grow.py`. The MOVE/MERGE ops exist; nothing calls
  them periodically yet
- **SQLite backing store** — when JSON snapshot starts hurting
  (~10k beliefs)
- **Held-out bench** — split-half test to eliminate the self-reference
  bias in the current numbers
- **Cross-session bench** — run the bench corpus against a different
  project's transcript
- **Ollama backend** — fully offline LLM EW path behind `[ollama]`
  extra

### Dependencies

Zero required runtime dependencies. Optional extras:

- `[st]` — `sentence-transformers>=2.2` for local embeddings
- `[openai]` — `openai>=1.0` for OpenAI embeddings + LLM-backed EW
- `[all]` — both
- `[test]` — `pytest>=7.0`

Python 3.10+.
