# Changelog

All notable changes will be documented in this file. This project aims
for [Semantic Versioning](https://semver.org). Until v1.0 everything is
subject to change.

## [0.0.1] — 2026-04-09 — first dogfood

Initial alpha. Built and validated end-to-end in a single long session
(see [BENCH.md](BENCH.md) for the numbers). Scope: prove that
BELLA's six-rule calculus works as a memory architecture for LLM
coding agents, with enough substance to dogfood on its own construction.

### Core (`bellamem/core/`)

- **`gene.py`** — `Belief`, `Gene`, Jaynes log-odds accumulation with
  same-voice attenuation (0.1 factor). Stable belief ids via
  `md5(desc + parent)` so repeated claims deduplicate. `mass_floor`
  field for the immutable-mass mechanism (P13).
- **`ops.py`** — the seven operations: CONFIRM, AMEND, ADD, DENY,
  CAUSE, MERGE, MOVE. Every mutation of the tree flows through exactly
  one of these. Principle P2 pins this as the complete mutation API.
- **`bella.py`** — the `Bella` forest, routing via embedding similarity,
  `Claim` dataclass, entity index for R6 bridging, reserved field
  rules (P18) with `is_reserved_field` guard against adapter writes,
  `self_observation` relation routing to `__self__`.
- **`embed.py`** — pluggable embedder protocol. Four backends:
  `HashEmbedder` (zero-dep default), `SentenceTransformerEmbedder`
  (`[st]` extra), `OpenAIEmbedder` (`[openai]` extra), `DiskCacheEmbedder`
  wrapping any of the above. Stdlib-only `.env` loader and
  `make_embedder_from_env` factory. Batched cache saves (every 50
  inserts) plus explicit `flush()` — 8× ingest speedup over naive save.
- **`store.py`** — atomic JSON snapshot (tmp+rename). Embedder
  signature check on load — fails loud with clear reset instructions
  if you switch embedders under an existing tree. Prevents silent
  dimension mismatch.
- **`principles.py`** — parses `PRINCIPLES.md` into a reserved
  `__principles__` field at `mass = 0.98, mass_floor = 0.95`.
  Idempotent via stable belief ids. Constitution changes require
  editing the file.
- **`expand.py`** — two modes:
  - `expand()` — generic mass-weighted pack, 60/30/10 mass/relevance/recency
  - `expand_before_edit()` — 5-layer bandaid-blocker, 40/20/20/10/10
    invariants/disputes/causes/bridges/self-model, **no recency**
- **`audit.py`** — read-only drift detection: contradictions against
  principles (cosine + ⊥ relation), bandaid piles (R2 entropy signal
  via `_BANDAID_RE`), claims-near-principles (informational, not
  errors).

### Adapters (`bellamem/adapters/`)

- **`chat.py`** — voice-aware regex EW. User claims at `lr` 1.8–2.5,
  assistant claims at 1.05–1.3. Reaction classifier for turn-pair
  retroactive ratification (affirm/correct/neutral). Denial filter
  handles quoted (`` `don't` ``) and conditional (`if we don't`)
  denials — both caught by the audit as real upstream bugs.
- **`claude_code.py`** — reads `~/.claude/projects/<cwd>/*.jsonl`
  transcripts. Incremental ingest via per-file line cursor in the
  snapshot. Turn-pair retroactive ratification state machine:
  `affirm → accumulate(lr=2.2, voice="user")`,
  `correct → accumulate(lr=0.4, voice="user")`. Explicit cache flush
  at end of ingest.
- **`llm_ew.py`** — optional LLM-backed EW (`BELLAMEM_EW=hybrid`).
  `LLMExtractor` class with disk-cached gpt-4o-mini calls in JSON
  mode. Two extraction tasks: `find_cause_pairs` (returns cause/effect
  text spans) and `find_self_observations` (first-person habit
  statements for `__self__`). Marker gates (`has_cause_markers`,
  `has_self_markers`) short-circuit when there's nothing to extract.
  Fails loud (C10) on malformed JSON. Cost: ~$0.002 per typical
  98-turn session.

### CLI (`bellamem/cli.py`)

```
bellamem ingest-cc         ingest Claude Code .jsonl transcripts
bellamem expand QUERY      generic mass-weighted context pack
bellamem before-edit QUERY bandaid-blocker pack with 5-layer budget
bellamem audit             drift / contradictions / bandaid report
bellamem entities [NAME]   list / inspect R6 entity bridges
bellamem bench             compare all contenders on the corpus
bellamem embedder          show active embedder config
bellamem show              render the whole forest
bellamem stats             summary
bellamem reset             delete the snapshot
```

### Constitution (`PRINCIPLES.md` + `bellamem/principles/classic.md`)

- 21 bellamem-specific principles (P1–P21) covering architecture,
  dependencies, epistemics, anti-drift, and reading the tree
- 22 canonical engineering principles (C1–C22) covering simplicity
  (YAGNI, KISS, DRY-with-caveat), responsibility, honesty/failure
  (fail-loud, explicit>implicit, parse-don't-validate), state
  correctness, change discipline (break-forward)
- Seeded into a reserved `__principles__` field with `mass_floor = 0.95`
- The `audit` command detects any claim contradicting a principle via
  cosine + ⊥ relation

### Bench (`bellamem/bench.py` + `bench_corpus.py`)

- 15-item hand-labeled corpus drawn from the v0.0.1 dogfood session
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
and cannot reach 100% at any budget. See [BENCH.md](BENCH.md) for the
full methodology and caveats (self-reference bias, corpus size,
retrieval-vs-behavior distinction).

### Known limitations / quality debt

- **Field auto-naming** uses a "first 3 non-stopword words" heuristic
  plus code-identifier / snake_case / camelCase boosts. Produces
  readable-ish names but occasionally gets auto-named fields like
  `neo4j_missing_coding-agent`. Cosmetic; doesn't affect correctness.
- **Turn-pair promotion is uniform** — a user affirm boosts all
  preceding-turn claims equally, not just the directly-affirmed ones.
  Works because lr=2.2 is modest; would need LLM scope detection to
  narrow.
- **Quoted/conditional denial filter is regex-based** and can be
  fooled by unusual formatting. The audit catches most regressions.
- **Ingest is one-embedding-per-claim**, not batched. Could be ~5×
  faster with per-turn batching.

### What's not built yet (tracked for v0.1+)

- **MCP server** — wrap `before_edit` + `expand` as Claude Code tools
  so memory runs during editing, not just after
- **R2 heal pass** — port entropy-driven restructure from the
  herenews-app `grow.py`. The MOVE/MERGE ops exist; nothing calls
  them periodically yet
- **SQLite backing store** — when JSON snapshot starts hurting
  (~10k beliefs)
- **Held-out bench** — split-half test to eliminate the
  self-reference bias in the current numbers
- **Cross-session bench** — run the bench corpus against a different
  project's transcript
- **Ollama backend** — fully offline LLM EW path behind `[ollama]`
  extra
- **`__self__` routing without LLM** — regex path for obvious
  first-person habit statements (currently LLM-only)

### Dependencies

Zero required runtime dependencies. Optional extras:

- `[st]` — `sentence-transformers>=2.2` for local embeddings
- `[openai]` — `openai>=1.0` for OpenAI embeddings + LLM-backed EW
- `[all]` — both

Python 3.10+.
