# Changelog

All notable changes will be documented in this file. This project aims
for [Semantic Versioning](https://semver.org). Until v1.0, everything
is subject to change.

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
(see [BENCH.md](BENCH.md) for the numbers). Scope: prove that BELLA's
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
and cannot reach 100% at any budget. See [BENCH.md](BENCH.md) for
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
