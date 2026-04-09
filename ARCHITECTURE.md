# bellamem architecture

This document is the map. Read it once and the rest of the repo is legible.

The core claim: **every component implements one of BELLA's six rules
(R1–R6), plus a thin adapter layer that knows about chat transcripts.**
Nothing else. The tool's job is context window management — retrieve
the decisive facts for the agent's next step under a small token
budget — and every piece of the code exists to serve that goal.

---

## Layers, top to bottom

```
┌─────────────────────────────────────────────────────────────┐
│  CLI                  bellamem ingest-cc / expand / audit   │
│                       / before-edit / bench / entities      │
├─────────────────────────────────────────────────────────────┤
│  adapters/            chat.py        voice-aware regex EW   │
│                       claude_code.py .jsonl + turn-pair     │
│                       llm_ew.py      LLM cause + self-obs   │
├─────────────────────────────────────────────────────────────┤
│  core/                bella.py       forest + routing       │
│                       gene.py        Belief + Gene + mass   │
│                       ops.py         seven operations       │
│                       expand.py      expand + before_edit   │
│                       audit.py       bandaid + ratified     │
│                       embed.py       pluggable embedders    │
│                       store.py       atomic JSON snapshot   │
├─────────────────────────────────────────────────────────────┤
│  storage              ~/.bellamem/default.json              │
│                       + embed_cache.json                    │
│                       + llm_ew_cache.json                   │
└─────────────────────────────────────────────────────────────┘
```

**Architectural invariant** (enforced by `core/__init__.py`):
`bellamem.core` never imports from `bellamem.adapters`. Core is
domain-agnostic; domain knowledge lives in adapters. The same core
could run on news, knowledge bases, support tickets — any stream of
evidence.

---

## The data model

### Belief (`core/gene.py`)

One node in the tree.

```python
@dataclass
class Belief:
    id: str                   # stable hash of (desc, parent)
    desc: str                 # short fact, paraphrased
    parent: Optional[str]     # parent belief id
    rel: str                  # "→" (support), "⊥" (deny), "⇒" (cause)
    children: list[str]
    voices: set[str]          # independent source names
    log_odds: float           # R1 Jaynes accumulator
    n_voices: int             # count of distinct voices
    embedding: list[float]    # from the active embedder
    entity_refs: list[str]    # opaque entity strings for R6 bridging
    mass_floor: float         # optional minimum mass (e.g., for pinned beliefs)
```

`mass = sigmoid(log_odds)`. The `mass_floor` field lets a caller pin a
belief's mass above a threshold — useful for hand-seeded beliefs or
anchored decisions. It is *not* a governance mechanism; it's just a
parameter.

### Gene (`core/gene.py`)

One *field* — a forest of beliefs on a single topic.

```python
class Gene:
    name: str                       # e.g. "authentication", "__self__"
    beliefs: dict[str, Belief]
    roots: list[str]                # top-level beliefs in this field
```

Fields are the unit that emerges under R3. When a new claim doesn't
embed close to any existing field, a new gene is born. When centroids
converge (future R3 heal pass), fields can merge.

### Bella (`core/bella.py`)

The whole forest.

```python
class Bella:
    fields: dict[str, Gene]                       # name → gene
    cursor: dict[str, dict]                       # jsonl cursor per source
    _entity_index: dict[str, list[(field, bid)]]  # R6 entity → beliefs
```

`Bella.ingest(claim)` is the only way beliefs enter the tree.

### Reserved field namespace

Fields whose name starts with `__` are **reserved**. The only reserved
field currently in use is `__self__`, which holds R4 self-observations
extracted by the LLM EW. Adapters cannot write to reserved fields
directly via routing — they must express intent through `Claim.relation`
and core decides where it goes. This keeps domain knowledge out of
core-owned fields.

---

## The seven operations (`core/ops.py`)

Every mutation of the belief tree is exactly one of these. No other
writes.

| op | meaning | R-rule |
|---|---|---|
| **CONFIRM** | `⊨ B` — accumulate evidence for existing belief | R1 |
| **AMEND** | `⊨ B ∧ δ` — confirm + refine description | R1 + R2 |
| **ADD** | `⊢ B' → B` — new supporting child belief | R1 |
| **DENY** | `⊢ B' ⊥ B` — new counter-belief (⊥ edge) | R1 |
| **CAUSE** | `⊢ B' ⇒ B` — new causal predecessor (⇒ edge) | R1 + R6 |
| **MERGE** | fold one belief into another (tree coherence) | R2 + R3 |
| **MOVE** | reparent a belief for better local structure | R2 |

Adding a new op is a schema change. Don't.

---

## The six rules, operationally

### R1 — accumulate (mass)

**Implementation**: `Belief.accumulate(lr, voice)`

```python
log_odds += log(lr)
mass = sigmoid(log_odds)
```

Independent voices compound. Saying the same thing 5 times with 5
different sources produces mass ≈ sigmoid(5 × log(lr)). Same-voice
repetition is attenuated by a 0.1 factor — a single source cannot
inflate its own claim.

### R2 — structure (entropy)

**Implementation (partial)**: `core/ops.py:move`, `core/ops.py:merge`,
`core/audit.py:_BANDAID_RE`

The MOVE and MERGE ops exist; an automatic heal pass that calls them
is not yet wired. The audit detects R2 entropy signals via the bandaid
regex — a subtree with 3+ children matching `fix|workaround|guard|
special-case|patch` patterns is a signal that the parent is a
structural problem, not a series of isolated bugs.

### R3 — emerge (fields from convergence)

**Implementation**: `core/bella.py:Bella.find_field` + field birth in `ingest`.

Fields are born when a claim doesn't embed close to any existing
field's beliefs (cosine < `FIELD_MATCH`). They can merge when root
centroids converge — the emergence pass from the news-epistemics
reference implementation is not yet ported.

### R4 — self-refer (Ψ)

**Implementation**: `__self__` reserved field, populated by
`adapters/llm_ew.py:find_self_observations`.

The LLM EW identifies first-person habit statements in assistant
messages ("I tend to reach for try/except when I hit a KeyError") and
emits them as `Claim(relation="self_observation")`. Core routes these
to `__self__` regardless of embedding similarity (this is the one place
similarity-based routing is bypassed).

`expand_before_edit` loads the top self-observations relevant to the
current focus. When the agent is about to do something, its own prior
pattern statements surface as part of the context.

### R5 — converge (feedback with attenuation)

**Implementation**: turn-pair retroactive ratification in
`adapters/claude_code.py:ingest_session`.

State machine:
1. Assistant turn N → ingest claims at low `lr` (hypothesis)
2. User turn N+1 → classify reaction via
   `adapters/chat.py:classify_reaction`
3. affirm → `accumulate(lr=2.2, voice="user")` on all pending claims
4. correct → `accumulate(lr=0.4, voice="user")` (dampens log_odds)
5. neutral → leave pending claims alone

The user is a second independent voice under Jaynes's rule. User
affirmation is exactly the independent evidence R1 is meant to reward.

### R6 — entangle (entities bridge fields)

**Implementation**: `Bella._entity_index` + `expand_before_edit`'s
bridge layer.

Every claim carries `entity_refs: list[str]` — opaque strings extracted
from the claim text (file paths, code identifiers, library names).
On ingest, each ref is added to `entity_index: dict[entity, [(field, bid), ...]]`.
The before-edit mode looks up the focus entity and pulls every belief
that mentions it, regardless of which field the belief lives in.

This is the graph doing its job: touching `auth.py` surfaces beliefs
from the `session` field, the `database` field, and the `testing`
field if they all co-mentioned `auth.py` at some point.

---

## The 5-layer before-edit pack

`core/expand.py:expand_before_edit` is the headline feature. Budget:

```
40%  invariants     — high-mass ratified beliefs (mass ≥ 0.80)
                      tie-broken by focus relevance
20%  disputes       — ⊥ beliefs touching focus
                      (prevents re-suggestion of rejected approaches)
20%  causes         — ⇒ chains near focus
                      (root-cause awareness)
10%  bridges        — R6 entity co-mentions
                      (whole-picture neighborhood)
10%  self-model     — R4 __self__ observations close to focus
                      (agent's own anti-patterns)
```

**Recency is absent.** Layers are ordered so that principled invariants
load first, disputes come next (blocking rejected approaches), causes
third (root-cause awareness), bridges fourth (whole-picture), and
self-model last (how the agent tends to fail). The pack reads top-down
as a chain of custody for the decision.

The generic `expand()` uses a different 60/30/10 mass / relevance /
recency split — appropriate for "what did we decide about X?" style
questions where recency is useful.

---

## Claim → belief flow, end to end

```
1. Claude Code .jsonl line          — user or assistant turn
2. adapters/claude_code.py          — iter_new_turns() via cursor
3. adapters/chat.py                 — split_sentences + voice-aware classify
4. adapters/llm_ew.py (optional)    — find_cause_pairs + find_self_observations
5. Claim(text, voice, lr, relation, target_hint, target_field)
6. Bella.ingest(claim)              — route, apply op, update entity index
7. Belief in gene with log_odds, voices, embedding, entity_refs
8. (next user turn) turn-pair pass  — retroactive affirm/correct
9. store.save()                     — atomic JSON snapshot
```

No step in this chain is more than ~50 lines of code. The whole
pipeline fits in your head while editing it — that's the design goal,
not an accident.

---

## Pluggable embedders (`core/embed.py`)

Four backends behind one `Embedder` protocol:

| backend | dim | cost | use when |
|---|---|---|---|
| **HashEmbedder** | 256 | free | zero-dep default, testing, CI |
| **SentenceTransformerEmbedder** | 384 | free | local, quality, no API |
| **OpenAIEmbedder** | 1536 | ~$0.02/M | production quality |
| **DiskCacheEmbedder** | (wraps) | — | always wrap non-hash |

Factory: `make_embedder_from_env()` reads `BELLAMEM_EMBEDDER`. The
snapshot records the embedder's `(name, dim)` signature and `load()`
fails loud if the current embedder differs — you cannot accidentally
mix vectors from two backends.

Disk cache uses batched saves (every 50 misses) plus explicit `flush()`
at end of ingest. Before that fix, a full ingest took 7+ minutes of
disk thrashing; after, ~15 seconds.

---

## Reading order for a new contributor

Read in this order; ~2000 lines total.

1. `bellamem/core/gene.py` — Belief + Gene + mass
2. `bellamem/core/ops.py` — seven operations
3. `bellamem/core/bella.py` — routing + ingest
4. `bellamem/core/expand.py` — the headline feature
5. `bellamem/adapters/chat.py` — the EW
6. `bellamem/adapters/claude_code.py` — turn-pair ratification
7. `bellamem/adapters/llm_ew.py` — structural extraction
8. `bellamem/core/audit.py` — the report surface
9. `bellamem/bench.py` — how we measure

---

## What's not built yet

Listed in [CHANGELOG.md](CHANGELOG.md) under v0.0.2 "not built."
Largest missing pieces:

- **MCP server + hooks** — integration with Claude Code as an automatic guardrail
- **R2 heal pass** — periodic local restructure (MOVE/MERGE calls)
- **Batched embedding ingest** — ~5× speedup on cold caches
- **SQLite backing store** — when JSON snapshot starts hurting (~10k beliefs)
- **Held-out bench** — split-half test to eliminate self-reference bias

None are blockers; all are follow-up work on the core mission (better
context under a smaller budget).
