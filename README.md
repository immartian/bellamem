# bellamem

**A persistent, structured memory for LLM coding agents. It solves the
context window problem.**

LLM coding agents have a finite context window. Over a session, it
fills up. `/compact` summarizes old turns and loses specifics. Rejected
approaches come back. Root causes identified earlier are forgotten.
Across sessions, there's no memory at all.

bellamem replaces that failure mode with a belief tree that grows with
your actual work — ratified decisions, preserved disputes, entity
bridges, causal chains, self-observations — and builds the **decisive
context pack** for each edit under a small token budget.

On a hand-labeled benchmark drawn from a real 98-turn session,
bellamem's structured `before-edit` pack hits 100% retrieval at
**500 tokens**, while flat-recency context plateaus at 93% and
**cannot reach 100% at any budget**. The root-cause beliefs and
user corrections that flat recency loses, bellamem keeps.

---

## What bellamem is (and what it isn't)

**It is:**
- A persistent belief tree keyed to your Claude Code `.jsonl` transcripts
- A voice-aware claim extractor (user is oracle, assistant is hypothesis)
- A retroactive ratification pass (user "yes/agreed" boosts preceding assistant claims)
- A structured context-pack builder (`expand` for generic queries, `expand_before_edit` for pre-edit context)
- An empirical bench comparing structured retrieval against flat recency, LLM compact, and RAG
- ~2500 lines of Python, zero required runtime dependencies

**It is not:**
- A replacement for Claude Code's context window (that's Anthropic's code; bellamem augments, doesn't replace)
- A governance / drift-prevention tool (separate problem; out of scope)
- A graph database, vector store, or LLM framework
- A substitute for docs, tests, or good engineering practice

---

## Status

**v0.0.2 — alpha, rescoped.** Context window management is the mission.
The earlier v0.0.1 shipped a constitution layer (`PRINCIPLES.md`
enforcement, canonical engineering principles) which turned out to be
mission creep on a different problem. Removed.

The data model, retrieval quality, and bench numbers are unchanged by
the rescope — the stripped code was governance, not memory.

---

## Install

```bash
git clone https://github.com/immartian/bellamem
cd bellamem
python3 -m venv .venv
.venv/bin/pip install -e .            # zero-dep default
```

Optional upgrades, each behind an extras flag:

```bash
.venv/bin/pip install -e '.[st]'       # sentence-transformers (local embeddings)
.venv/bin/pip install -e '.[openai]'   # OpenAI embeddings + LLM-backed EW
.venv/bin/pip install -e '.[all]'      # both
```

Copy `.env.example` → `.env` and fill in the backends you enabled.
`.env` is gitignored.

---

## Quickstart

```bash
# Ingest all Claude Code sessions for the current project
bellamem ingest-cc

# Build the before-edit context pack — invariants + disputes + causes
# + entity bridges + self-model, no recency
bellamem before-edit "should I wrap this in try/except" --entity embed.py

# Generic mass-weighted context pack for a question
bellamem expand "what did we decide about persistence"

# Walk the tree and surface bandaid piles + ratified decisions + disputes
bellamem audit

# Empirically compare context strategies (flat, compact, RAG, bellamem)
bellamem bench
```

Every command except `ingest-cc` and `reset` is read-only.

---

## How it builds context

```
Raw Claude Code transcript (.jsonl)
        ↓
    regex EW      ── voice-aware (user oracle, assistant hypothesis)
        ↓
    + LLM EW      ── CAUSE pairs, self-observations (opt-in, gpt-4o-mini)
        ↓
    Claim(text, voice, lr, relation)
        ↓
    Bella.ingest()   routes via embedding, applies Jaynes accumulation
        ↓
    Belief tree (fields → beliefs → typed edges)
        ↓
┌───────┴────────┬──────────┬──────────┐
expand()    before-edit()   audit     bench
  generic    5-layer pack   report    compression
             no recency                vs flat/RAG
```

### The 5-layer before-edit pack

For a proposed edit, `before_edit` assembles context under a token
budget with this split:

```
40%  invariants     — high-mass ratified beliefs anywhere in the tree
20%  disputes       — ⊥ edges touching the focus (prevents re-suggestion)
20%  causes         — ⇒ chains near the focus (root-cause awareness)
10%  entity bridges — R6 co-mention neighborhood
10%  self-model     — __self__ habit observations from LLM EW (R4)
```

**Recency is absent by design.** In before-edit mode, recency biases
toward the last bandaid; mass biases toward the invariant it violates.

---

## Empirical results

From [BENCH.md](BENCH.md), measured on 15 hand-labeled queries drawn
from a real 98-turn dogfood session:

| budget | flat_tail | before_edit |
|---|---|---|
| 200 t  | —    | **80 %** |
| 500 t  | 13 % | **100 %** |
| 1000 t | 13 % | 100 % |
| 2000 t | 13 % | 100 % |
| 4000 t | 93 %*| 100 % |
| 10000 t| 93 %*| 100 % |

_\*self-referential lift — at ≥3000t the tail reaches back into the
turn where the bench corpus was drafted. Below the self-reference band
(≤2000t), flat_tail is pinned at 13%. Clean comparison: **500 tokens
structured beats infinite flat recency**._

All five contenders at 1200 tokens:

```
              flat_tail  compact  rag_topk  expand  before_edit
exact hit        13 %     33 %     93 %     100 %    100 %
embed hit        33 %     73 %    100 %     100 %    100 %
avg tokens     1200      725      1175     1167      853
```

Ordering: `flat_tail << compact << rag_topk < expand ≈ before_edit`.
The before-edit mode is the tightest — it hits 100% using **27% fewer
tokens than generic expand** because the 5-layer budget allocates
precisely.

Full methodology, budget sweeps, and caveats in [BENCH.md](BENCH.md).

---

## The six rules, operationally

bellamem implements BELLA's six-rule calculus for accumulating evidence.
Each rule maps to a concrete component:

| Rule | Name | Component | Concrete behavior |
|---|---|---|---|
| R1 | accumulate | `gene.py:Belief.accumulate` | Jaynes log-odds mass; same-voice attenuation |
| R2 | structure | `audit.py:_BANDAID_RE` | Detects entropy signals (bandaid piles) |
| R3 | emerge | `bella.py:find_field` + field birth | Fields appear via embedding convergence |
| R4 | self-refer | `__self__` field + LLM EW | Agent's habits as part of its own context |
| R5 | converge | `claude_code.py:ingest_session` | Turn-pair retroactive ratification |
| R6 | entangle | `bella.py:entity_index` | Entities bridge fields via co-mention |

The rules are domain-agnostic. bellamem is their application to coding
agent memory. The same calculus underlies a separate news-epistemics
project that originated the theory.

---

## Architecture at a glance

```
bellamem/
  core/
    gene.py           Belief + Gene + Jaynes accumulation
    ops.py            the seven operations: CONFIRM, AMEND, ADD, DENY,
                      CAUSE, MERGE, MOVE (complete mutation API)
    bella.py          forest + routing + entity index
    embed.py          pluggable embedders (Hash/ST/OpenAI) + .env
    store.py          atomic JSON snapshot + signature check
    expand.py         expand() + expand_before_edit() 5-layer pack
    audit.py          bandaid pile detection + ratified + disputes
  adapters/
    chat.py           voice-aware regex EW + turn-pair reaction classifier
    claude_code.py    .jsonl reader + incremental cursor
    llm_ew.py         gpt-4o-mini CAUSE + self-observation extraction
  bench.py            5 contenders, 2 metrics, comparison table
  bench_corpus.py     hand-labeled query/expected-fact pairs
  cli.py              ingest-cc / expand / before-edit / audit / bench ...
```

Full architecture doc: [ARCHITECTURE.md](ARCHITECTURE.md).

**Architectural invariant**: `bellamem.core` never imports from
`bellamem.adapters`. Core is domain-agnostic; adapters are where domain
knowledge lives. This lets the same core run on news, personal knowledge,
support tickets — anything that accumulates evidence.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version:

- The bench is the CI. Run `bellamem bench` after changes to EW, expand,
  or audit and report the delta in the PR.
- Add new embedders by implementing the `Embedder` protocol in `core/embed.py`.
- Add new EW logic in `adapters/`, never in `core/`.
- Every PR that touches retrieval should include a bench item demonstrating
  the failure mode it fixes.

---

## License

MIT. See [LICENSE](LICENSE).
