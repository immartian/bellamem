# Session Handoff

A tiny file you can hand to a fresh Claude Code session. Read this,
then run the three bootstrap commands below, and you have the full
working context — everything we decided, everything we built, and
the arc of how we got here. Nothing else needs to be in your head.

---

## Bootstrap (run these first)

```bash
# 1. See what we just said, in order (working memory)
bellamem replay -t 2500

# 2. See what the accumulated beliefs actually say (long-term memory)
bellamem expand "current state of bellamem and open follow-ups" -t 2000

# 3. See what just changed (signal)
bellamem surprises --top 10
```

**That's the handoff.** The three commands cover working memory, long-
term memory, and the "what mattered" signal. Read them in that order
and you'll know where we are.

If you want the pre-edit pack for any specific work:

```bash
bellamem before-edit "what I'm about to do" --entity <file>
```

---

## What this session produced

Added the three-retrieval-mode architecture (expand / surprises / replay),
R3 auto-consolidation, source-grounded provenance, expanded entropy
audit, and a system-noise filter. Branded the project as **BellaMem —
graph memory for agentic coding**.

### New core modules

- `core/scrub.py` — one-shot migration: remove harness-noise beliefs
  from legacy snapshots (interrupt sentinels, tag leakage, noise-named
  fields). Reparents children cleanly.
- `core/emerge.py` — R3 consolidation: near-duplicate merge + field
  rename. Auto-runs after every ingest. Contrastive-rate baseline +
  optional `--llm` namer for single-topic corpora.
- `core/surprise.py` — top Jaynes step surprises, sign flips, recent
  dispute formations. Walks `Belief.jumps`.
- `core/replay.py` — chronological retrieval from source-grounded
  beliefs. Tail-preserving under budget.

### Core changes (backwards-compatible)

- `gene.py` — `Belief` gains `jumps: list[(ts, delta, voice)]` (cap 32)
  and `sources: list[(key, line)]` (cap 32). `accumulate()` accepts an
  optional `source` kwarg. Serialization handles legacy snapshots.
- `ops.py` — all seven ops thread `source` through. `merge` combines
  survivor + absorbed sources, dedup preserving order.
- `bella.py` — `Claim` gains `source: Optional[tuple[str, int]]`.
  `ingest` routes it through every code path.
- `expand.py` — removed the separate recency layer. Added continuous
  freshness bonus blended into the relevance score. Same function
  handles focused and diffuse queries. `before_edit` unchanged.
- `audit.py` — five new entropy signals: root glut, near-duplicates,
  mass limbo, garbage field names, single-voice rate.

### Adapter hardening

- `adapters/claude_code.py` — `_strip_system_noise()` removes `[Request
  interrupted...]`, `<system-reminder>`, `<local-command-*>`, and bare
  slash commands before the EW sees them. Every Claim now stamped with
  `source=(f"jsonl:{path}", line_number)`. Retroactive ratification
  stamps the *user's* turn line (the actual evidence event).
- `adapters/llm_ew.py` — `LLMExtractor.suggest_field_name()` +
  `make_llm_name_fn()` for LLM-backed field naming when contrastive
  rate fails on single-topic corpora.

### CLI (new commands)

```bash
bellamem scrub [--dry-run]
bellamem emerge [--dry-run] [--min-cosine N] [--llm]
bellamem surprises [--top N] [--since-hours H]
bellamem replay [focus] [-t BUDGET] [--session X] [--since-line N]
bellamem ingest-cc [--no-emerge]   # new flag
```

### Tests

15 → 43 passing. Coverage added for every new module plus the adapter
filter and source threading.

---

## Key principles that emerged this session

Each of these was either stated explicitly by the user or arose from
a back-and-forth correction. They are now saved as feedback memories
in `~/.claude/projects/-media-im3-plus-labX-bellamem/memory/`.

### 1. No ad-hoc stoplists

When a heuristic produces a bad output, do not reflexively add
exclusions to a stoplist. That is the exact bandaid pattern BellaMem
is built to prevent. Look for the structural fix first. This session's
trigger: a TF-IDF-ish field namer producing `memory_session_without`;
my reflex was to add 20 words to a stoplist. The real fix was to
switch to a **contrastive rate metric** (rate in target field − rate
outside), which has no list at all. See
`memory/feedback_no_adhoc_stoplists.md`.

### 2. Dogfood via the state machine

When iterating on BellaMem, the validation loop is running BellaMem
against its own snapshot. Unit tests prove code runs; running it
against the real graph proves the feature is useful. The highest-
surprise jump in this session's graph is the user's "wow, wow, this
is adhoc which we always see" correction — *exactly* the kind of
signal the system exists to detect, caught in its own graph while
building itself. See `memory/feedback_dogfood_via_state_machine.md`.

### 3. Recency is a consolidation state, not a storage layer

The user's framing: *"recency is just unconsolidated belief in queue"*.
Consolidation (R3 emerge) is what transforms raw-young beliefs into
compressed-stable ones. Working memory and long-term memory aren't
different storage — they're two snapshots of the same beliefs at
different consolidation ages. This session's `expand.py` freshness
weight + auto-emerge implement this principle. The
before_edit pack still has *no* recency by design — in a critical-
path query, surfacing the last bandaid instead of the oldest invariant
is actively wrong, and the bench agrees.

### 4. Ground evidence to the log

Every belief carries `sources: list[(session, line_number)]` so we can
always trace a claim back to what was literally said. Timestamps lie
(ingest time ≠ conversation time); line numbers don't. This enabled
`bellamem replay` — line-ordered chronological retrieval, the missing
"narrative view" of the memory.

### 5. The graph watches itself

This session is in the graph. Running `bellamem replay` over the
current snapshot returns the session from ~line 435 onward, in order.
The earlier half is in the graph as ratified beliefs (visible via
`expand`) but invisible to `replay` because those beliefs predate
source tracking — no fabricated provenance. That's the right
behavior.

---

## Known open items (small, clean, not blockers)

All five are bug-fix-sized. None block anything. Each emerged from
running the graph's own queries against the session's narrative and
finding specific misalignments.

1. **Self-marker regex is too narrow.** The LLM EW only fires on
   phrases like "I tend to", "I often", "my default". Today's new
   self-observation — "I fall into ad-hoc bandaid patterns when a
   heuristic produces a bad output" — used phrases like "my reflex",
   "my instinct", "I caught myself" and so was never extracted.
   Fix: broaden `_SELF_MARKERS` in `adapters/llm_ew.py`. ~5 LOC.

2. **Quote detection missing.** When the assistant quotes an existing
   `__self__` belief in its own reply, the LLM EW treats it as a new
   observation and re-ingests it, bumping its mass. The graph then
   rewards me for talking about my bad habits. Fix: check embedding
   similarity against existing `__self__` beliefs before ingesting
   a new self-observation; if cos ≥ 0.9 to an existing belief, treat
   as a quote (skip or confirm-without-accumulating). ~15 LOC.

3. **Directive filter.** Turns like "let's advance", "let's pull up
   the graph", "let's do this" are user instructions, not claims
   about the world. They currently land in the mass layer as high-lr
   user beliefs. Fix: detect imperative-mood sentences in
   `adapters/chat.py` and route them to a no-accumulate path. ~20 LOC.

4. **Entity index has no staleness signal.** The top-30 entities are
   still dominated by old state (`PRINCIPLES.md`, `grow.py`,
   `kernel.py`, `DESIGN.md`, `classic.md`) that was removed in
   v0.0.2 or never part of BellaMem. Today's focus words (`sources`,
   `emerge`, `replay`, `jumps`) don't make the top 30. Fix: add a
   freshness-weighted rank to the entity index, or decay entities
   whose beliefs have no recent sources. Deferred — R2 heal is the
   right place for this. ~30 LOC when we get there.

5. **`surprises` render should show source lines.** The scoring already
   works over `jumps`, and sources are populated. Small render change:
   show `file:line` next to each surprise row so you can click
   (mentally) to the exact turn. ~5 LOC.

### Deferred by design

- **`Belief.consolidation_count`** — an explicit counter bumped each
  time R3 touches a belief without removing it. Would distinguish
  "stable under consolidation" from "just never processed". Not
  needed for current scope; revisit when R2 heal lands or when a
  `bellamem stable` query gets built. See the 2×2 of event_time ×
  consolidation_count in the session transcript around line 482.

- **R2 heal pass** — the MOVE operation exists in `ops.py` but nothing
  calls it. Reparenting beliefs into better fields (entropy-driven
  restructure) is the other half of consolidation. Deferred to a
  future session.

- **MCP server + hooks** — still the v0.1 headline from the CHANGELOG.
  Unchanged by this session's work.

---

## If something feels wrong when you read the graph

Run the three bootstrap commands above and check:

- **`bellamem replay`** should show you the session's narrative in
  line order. If the most recent entry doesn't match what you see
  at the top of the transcript, the session wasn't fully ingested —
  run `bellamem ingest-cc` and try again.

- **`bellamem audit`** should report "clean" or close to it. If you
  see new entropy signals that look wrong (new bandaid piles, new
  root glut, new garbage field names), don't ignore them — they're
  what the system is for.

- **`bellamem surprises`** should still have "wow, wow, this is adhoc
  which we always see" in the top-3 jumps. If it's gone, something
  corrupted the jumps history.

- **If beliefs have no `sources`**: they predate source tracking.
  `expand` and `surprises` still work on them, but `replay` won't.
  That's expected for the first ~400 lines of the session that built
  source tracking itself.

---

## One-line mental model

> BellaMem is the missing long-term layer between an LLM agent and its
> context window. New evidence enters raw, young, and source-grounded.
> Consolidation happens quietly in the background. Retrieval composes
> over the same graph from three angles: `expand` (what we believe),
> `surprises` (what just changed), `replay` (what we said, in order).
> Same store, three questions.

That's enough to continue seamlessly.
