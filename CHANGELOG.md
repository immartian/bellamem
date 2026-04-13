# Changelog

All notable changes will be documented in this file. This project aims
for [Semantic Versioning](https://semver.org). Until v1.0, everything
is subject to change.

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
