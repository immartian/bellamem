# bellamem architecture

This document is the map. Read it once and the rest of the repo is legible.

The core claim: **every component here is a direct implementation of one
of BELLA's six rules (R1–R6)**, plus a thin adapter layer that knows about
chat transcripts. Nothing else.

---

## Layers, top to bottom

```
┌─────────────────────────────────────────────────────────────┐
│  CLI                  bellamem ingest-cc / expand / audit   │
│                       / before-edit / bench / entities      │
├─────────────────────────────────────────────────────────────┤
│  adapters/            chat.py        regex EW, reactions    │
│                       claude_code.py .jsonl + turn-pair     │
│                       llm_ew.py      LLM cause + self-obs   │
├─────────────────────────────────────────────────────────────┤
│  core/                bella.py       forest + routing       │
│                       gene.py        Belief + Gene + mass   │
│                       ops.py         seven operations       │
│                       expand.py      expand + before_edit   │
│                       audit.py       drift / bandaid / ⊥    │
│                       principles.py  PRINCIPLES.md loader   │
│                       embed.py       pluggable embedders    │
│                       store.py       atomic JSON snapshot   │
├─────────────────────────────────────────────────────────────┤
│  storage              ~/.bellamem/default.json              │
│                       + embed_cache.json                    │
│                       + llm_ew_cache.json                   │
└─────────────────────────────────────────────────────────────┘
```

**The architectural invariant** (enforced by `core/__init__.py`):
`bellamem.core` never imports from `bellamem.adapters`. Domain knowledge
lives in the adapter layer. The core is domain-agnostic — you can apply
the same `core` to news, personal knowledge, support tickets, anything
that accumulates evidence.

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
    mass_floor: float         # P13 immutable_mass mechanism
```

`mass = sigmoid(log_odds)`. A principle is just a belief with `mass_floor = 0.95`.

### Gene (`core/gene.py`)

One *field* — a forest of beliefs on a single topic.

```python
class Gene:
    name: str                         # e.g. "authentication", "__principles__"
    beliefs: dict[str, Belief]
    roots: list[str]                  # top-level beliefs in this field
```

Gene is the unit that emerges under R3. When belief centroids converge,
multiple genes merge into one. When a new topic appears, a new gene is
born.

### Bella (`core/bella.py`)

The whole forest.

```python
class Bella:
    fields: dict[str, Gene]           # name → gene
    cursor: dict[str, dict]           # jsonl cursor per source (streaming state)
    _entity_index: dict[str, list[...]] # R6 entity → beliefs lookup
```

`Bella.ingest(claim)` is the only way beliefs enter the tree.

---

## The seven operations (`core/ops.py`)

Every mutation of the belief tree is exactly one of these. No other writes.

| op | meaning | R-rule |
|---|---|---|
| **CONFIRM** | `⊨ B` — accumulate evidence for existing belief | R1 |
| **AMEND** | `⊨ B ∧ δ` — confirm + refine description | R1 + R2 |
| **ADD** | `⊢ B' → B` — new supporting child belief | R1 |
| **DENY** | `⊢ B' ⊥ B` — new counter-belief (⊥ edge) | R1 |
| **CAUSE** | `⊢ B' ⇒ B` — new causal predecessor (⇒ edge) | R1 + R6 |
| **MERGE** | fold one belief into another (tree coherence) | R2 + R3 |
| **MOVE** | reparent a belief for better local structure | R2 |

Principle P2 in `PRINCIPLES.md` pins this as the complete mutation API.
Adding a new op is a constitutional change and requires editing the file.

---

## The six rules, operationally

### R1 — accumulate (mass)

**Implementation**: `Belief.accumulate(lr, voice)`

```python
log_odds += log(lr)
mass = sigmoid(log_odds)
```

Independent voices compound. Saying the same thing 5 times with different
sources produces a belief with log(5 × lr) mass, not log(lr). Same-voice
repetition is attenuated by 10× (a single source cannot inflate its own
claim).

**Where it matters**: the principle layer is at `log_odds = log(49)`,
i.e. `mass ≈ 0.98`. A single ratification by the user adds `log(2.2) ≈ 0.79`
— meaningful but not enough to dominate. The `mass_floor` mechanism
prevents any accumulate call from reducing a principle below 0.95.

### R2 — structure (entropy)

**Implementation (partial)**: `core/ops.py:move` and `core/ops.py:merge`

**What's built**: the ops are there, the heal pass that calls them
periodically is not. See CHANGELOG for the port-from-grow.py plan.

**Why it matters for the pitch**: a subtree with many similar bandaid-fix
children is high local entropy. The heal pass surfaces "this isn't a
series of bugs, it's a structural problem" — the bandaid-pile-to-root-cause
promotion. This is what `audit` half-detects today via the `_BANDAID_RE`
pattern in `core/audit.py`.

### R3 — emerge (fields from convergence)

**Implementation**: `core/bella.py:Bella.find_field` + field birth in `ingest`.

Fields are born when a claim doesn't embed close to any existing field's
beliefs (cosine < 0.25). They merge when root centroids converge over
time. This is where `neo4j_missing_coding-agent` etc. come from — cheap
auto-naming from the first claim's salient words.

The field naming is a known quality debt (see CHANGELOG). Functionality
is correct; display is ugly.

### R4 — self-refer (Ψ)

**Implementation**: `__self__` reserved field, populated by
`adapters/llm_ew.py:find_self_observations`.

The LLM identifies first-person habit statements in assistant messages
("I tend to reach for try/except when I hit a KeyError") and routes them
through `Claim(relation="self_observation")`. Core honors this by creating
the `__self__` field if missing and adding the belief there, bypassing
similarity routing. Reserved field rules (P18) allow this because *core*
is making the routing decision, not the adapter directly writing.

`expand_before_edit` loads the top self-observations relevant to the
current focus. When the agent is about to add a try/except, it sees its
own prior "I tend to do this reflexively" as a first-class part of the
context.

### R5 — converge (feedback loops with attenuation)

**Implementation**: turn-pair retroactive ratification in
`adapters/claude_code.py:ingest_session`.

State machine:
1. Assistant turn N: ingest claims at low `lr` (hypothesis)
2. User turn N+1: classify reaction as affirm / correct / neutral via
   `adapters/chat.py:classify_reaction`
3. If affirm: `accumulate(lr=2.2, voice="user")` on all claims from turn N
4. If correct: `accumulate(lr=0.4, voice="user")` — reduces log_odds
5. Reserved: `__principles__` beliefs are mass-floored and unaffected

The user's voice is a *second independent voice* under Jaynes's rule —
exactly what R1 is meant to reward.

### R6 — entangle (entities bridge fields)

**Implementation**: `Bella._entity_index` + `expand_before_edit`'s bridge layer.

Every claim carries `entity_refs: list[str]`. On ingest, each ref is
added to an inverted index `entity → [(field, belief_id), ...]`. When
the before-edit mode runs, it looks up the focus entity and pulls every
belief that mentions it — *regardless of which field* the belief lives in.
That's the graph doing its job: touching `auth.py` surfaces beliefs from
the `session` field, the `database` field, and the `testing` field if
they all co-mentioned `auth.py` at some point.

---

## The five-layer before-edit pack

`core/expand.py:expand_before_edit` is the headline feature. Budget split:

```
40%  invariants  — principles + other mass ≥ 0.80 beliefs
                   tie-broken by focus relevance
20%  disputes    — ⊥ beliefs touching focus (prevents re-suggestion)
20%  causes      — ⇒ chains near focus (root-cause awareness)
10%  bridges     — R6 entity co-mentions
10%  self-model  — R4 __self__ observations close to focus
```

**Recency is absent.** In before-edit mode, recency biases toward the
last bandaid. The layers are ordered so invariants load first and
self-model loads last — the pack reads top-down as "here are the rules,
here's what you already rejected, here are the root causes, here's what's
connected, here's your own anti-pattern." An agent with this context
should refuse the bandaid reflex or at least surface the conflict.

The generic `expand()` uses a different 60/30/10 mass/relevance/recency
split — appropriate for questions like "what did we decide?" where
recency is useful.

---

## The constitution (anti-drift)

`PRINCIPLES.md` is loaded by `core/principles.py` into a reserved
`__principles__` field at `mass = 0.98, mass_floor = 0.95`. Seeding is
**idempotent via stable belief ids**, so re-running `ingest-cc` is a
no-op for unchanged principles. Editing PRINCIPLES.md is the *only* way
to change a principle — there is no API to weaken one at runtime.

The audit command (`core/audit.py`) does three drift checks:

1. **Contradictions**: for each principle, find beliefs with high cosine
   AND `rel=⊥`. These are explicit disputes against a principle.
2. **Bandaid piles** (R2 signal): find parents with ≥3 children whose
   descriptions match fix/workaround/guard/special-case language.
3. **Near-principle claims**: high-mass multi-voice beliefs sitting near
   a principle — not errors, informational. These are usually echoes,
   but a drift case would show up here first.

Exit 0 is reserved for "no contradictions, no bandaid piles." Near-principle
claims are informational and do not fail the exit code.

---

## Voice asymmetry (`adapters/chat.py`)

From principle P9: "User is oracle, assistant is hypothesis."

```
                        user            assistant
decision marker         lr=2.2          lr=1.15
rule marker             lr=2.5          lr=1.3
denial marker           lr=2.5          lr=1.3
observation             lr=1.8          skip (unless entity-dense)
```

Assistant sentences without a rule/decision/denial/content marker are
*dropped*. The regex classifier learned not to ingest bare prose — it
bloated the tree without adding signal.

The `_has_real_denial` filter strips quoted (`parse, don't validate`) and
conditional (`if we don't commit`) denial words before classification.
That filter exists because of an actual false positive caught in audit.

The turn-pair pass then retroactively ratifies user-affirmed assistant
claims. The symmetric view — "all claims equal" — is what earlier
iterations used, and the bench measurably dropped.

---

## Pluggable embedders (`core/embed.py`)

Four backends behind one `Embedder` protocol (just `name`, `dim`,
`embed(text)`, `embed_batch(texts)`):

| backend | dim | cost | use when |
|---|---|---|---|
| **HashEmbedder** | 256 | free | zero-dep default, testing, CI |
| **SentenceTransformerEmbedder** | 384 | free | local, quality, no API |
| **OpenAIEmbedder** | 1536 | ~$0.02/M | production quality |
| **DiskCacheEmbedder** | (wraps) | — | always wrap non-hash |

Factory: `make_embedder_from_env()` reads `BELLAMEM_EMBEDDER` (hash | st
| openai). Snapshot records the embedder's `(name, dim)` signature and
`load()` fails loud if the current embedder differs — you cannot
accidentally mix vectors from two backends.

Disk cache uses **batched saves** (every 50 misses) plus an explicit
`flush()` called at end of ingest. Before that fix, a full ingest took
7+ minutes of disk thrashing; after, it's ~15 seconds.

---

## Claim → belief flow, end to end

```
1. Claude Code .jsonl line         — user or assistant turn
2. adapters/claude_code.py         — iter_new_turns() via cursor
3. adapters/chat.py                — split_sentences, voice-aware classify
4. adapters/llm_ew.py (optional)   — find_cause_pairs + find_self_observations
5. Claim(text, voice, lr, relation, target_hint, target_field)
6. Bella.ingest(claim)             — route, apply op, update entity index
7. Belief in gene with log_odds, voices, embedding, entity_refs
8. (next user turn) turn-pair pass — retroactive affirm/correct
9. store.save() atomic JSON snapshot
```

No step in this chain is more than ~50 lines of code. The whole pipeline
is small enough to hold in your head while editing it — that's the design
goal, not an accident.

---

## What's not here yet

Listed in [CHANGELOG.md](CHANGELOG.md) under "What's not built." The
biggest missing pieces:

- **MCP server** — wrap `before_edit` + `expand` as Claude Code tools
- **R2 heal pass** — port from the herenews-app grow.py
- **Batched embedding ingest** — ~5× speedup on cold caches
- **SQLite backing store** — when JSON starts hurting (~10k beliefs)
- **Held-out bench** — split-half test to eliminate self-reference bias

None are blockers; all are follow-up work.

---

## Reading order for a new contributor

If you want to understand the whole thing, read in this order:

1. `PRINCIPLES.md` — the constitution is the contract
2. `core/gene.py` — Belief + Gene + mass
3. `core/ops.py` — the seven operations
4. `core/bella.py` — routing + ingest
5. `core/expand.py` — the headline feature
6. `adapters/chat.py` — the EW
7. `adapters/claude_code.py` — turn-pair ratification
8. `adapters/llm_ew.py` — structural extraction
9. `core/audit.py` — the anti-drift report
10. `bench.py` — how we measure

About ~2000 lines total across those files. You can read it all in one
sitting.
