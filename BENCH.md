# bellamem bench — empirical results

> **The load-bearing claim**: a structured, mass-weighted context pack
> contains the decisive facts for an agent's next edit at a *fraction*
> of the token budget needed by flat recency.
>
> **The result**: validated on a 15-item hand-labeled corpus drawn from
> a real 98-turn dogfood session. `before_edit` at 500 tokens reaches
> 100% hit rate; `flat_tail` plateaus at 93% and cannot reach 100% at
> any budget, because one decision made deep in the session is
> unreachable from recency alone.

This document records the methodology, the numbers, and the caveats.
It exists so future changes to EW, expand, and audit can be evaluated
against a written record rather than a vibe.

---

## Methodology

### The corpus (`bench_corpus.py`)

15 hand-written items. Each is a question an agent might ask mid-edit,
paired with the decisive fact(s) that must be in the context pack for
the agent to answer correctly. Example:

```python
BenchItem(
    id="Q01",
    query="Should I add a fallback default when the snapshot is missing "
          "the embedder signature so old files still load?",
    focus_entity="store.py",
    expected_any_of=[
        "C19", "P15", "break forward",
        "no backwards-compat shims", "no feature flags",
    ],
    category="bandaid",
)
```

Categories covered: constitutional, architectural, bandaid-prone,
language/dependency, voice-asymmetry, audit semantics, extraction design.
See `bench_corpus.py` for all 15.

### The contenders (`bench.py`)

Five context-builder strategies. Each is a function producing a `BenchPack`
(list of strings) under a shared token budget.

| contender | strategy | cost |
|---|---|---|
| **flat_tail** | last N characters of transcript | free |
| **compact** | LLM summary of transcript (gpt-4o-mini) | ~$0.0002/query |
| **rag_topk** | top-k turns by cosine sim to query | free (pre-embedded) |
| **expand** | bellamem generic mass-weighted expand | free |
| **before_edit** | bellamem 5-layer structured expand_before_edit | free |

### The metrics

**exact** — substring match (case-insensitive) of any expected fact in
any pack line. Conservative.

**embed** — union of exact and semantic match. For each pack line and
each expected fact, cosine ≥ 0.40 counts as a hit. Lenient but still
meaningful. `embed` is definitionally a superset of `exact`.

The bench reports both. `exact` is the number to quote; `embed` tells
you how close the pack got semantically even when the phrasing differs.

---

## The main result (budget = 1200 tokens)

```
metric               flat_tail      compact     rag_topk       expand  before_edit
----------------------------------------------------------------------------------
exact hit rate            13 %         33 %         93 %        100 %    100 %
embed hit rate            33 %         73 %        100 %        100 %    100 %
avg tokens used           1200          725         1175         1167     853
```

### Per-item breakdown

```
id     category         flat_tail      compact     rag_topk       expand  before_edit
-------------------------------------------------------------------------------------
Q01    bandaid                  ✗            ✗            ✓            ✓            ✓
Q02    language                 ~            ✓            ✓            ✓            ✓
Q03    constitution             ✗            ✗            ✓            ✓            ✓
Q04    bandaid                  ✗            ✓            ✓            ✓            ✓
Q05    dependency               ✗            ✗            ✓            ✓            ✓
Q06    audit                    ✗            ~            ✓            ✓            ✓
Q07    extraction               ✓            ✓            ✓            ✓            ✓
Q08    constitution             ✗            ~            ✓            ✓            ✓
Q09    persistence              ✗            ✓            ✓            ✓            ✓
Q10    epistemics               ~            ~            ✓            ✓            ✓
Q11    constitution             ✗            ~            ✓            ✓            ✓
Q12    extraction               ✓            ✓            ✓            ✓            ✓
Q13    expand                   ✗            ~            ~            ✓            ✓
Q14    constitution             ✗            ✗            ✓            ✓            ✓
Q15    epistemics               ~            ~            ✓            ✓            ✓

legend: ✓ exact hit   ~ embed-only hit   ✗ miss
```

### Ordering

```
flat_tail << compact << rag_topk < expand ≈ before_edit
```

- **flat_tail is barely functional at 1200 tokens.** This is `/compact`
  dementia measured: 13/15 questions unanswerable from recency alone.
- **compact (LLM summary) preserves topics but loses specifics.** The
  33% → 73% gap between exact and embed reveals the failure mode: the
  summary is *semantically near* the right answer but has shed the
  specific identifiers (principle numbers, exact phrasing).
- **RAG is a surprisingly strong baseline.** 93% exact on a topical
  corpus. This is the alternative bellamem must beat, and it does —
  but only on one item (Q13) at equal budget.
- **expand and before_edit both hit 100%.** before_edit uses **27% fewer
  tokens** (853 vs 1167) because the 5-layer budget is more efficient.

---

## The budget sweep — where is recency actually useful?

### flat_tail across budgets

```
budget=500    exact hit rate            13 %
budget=1000   exact hit rate            13 %
budget=2000   exact hit rate            13 %
budget=3000   exact hit rate            73 %   ← self-reference lift begins
budget=4000   exact hit rate            93 %
budget=6000   exact hit rate            93 %
budget=10000  exact hit rate            93 %   ← ceiling — one item unreachable
```

**Below 2000 tokens, flat_tail is pinned at 13%** — the last ~8000 chars
of the transcript contain zero decisive facts. At 3000+ tokens, the tail
reaches back into the turn where the bench corpus was written, and the
score jumps via self-reference lift (see *Caveats*). Even at 10,000
tokens, flat_tail plateaus at 93% — Q06 (audit drift semantics) was
settled in the middle of the session and cannot be retrieved by
recency at any budget.

### before_edit across budgets

```
budget=200    exact hit rate            80 %
budget=300    exact hit rate            93 %
budget=500    exact hit rate           100 %
budget=800    exact hit rate           100 %
budget=1200   exact hit rate           100 %
budget=2000   exact hit rate           100 %
```

**before_edit is already at 100% by 500 tokens.** Even at 200 tokens
(12/15 items), it dominates flat_tail at any budget under 3000 tokens.

### The headline comparison

```
               before_edit     flat_tail
   200 t           80 %           —
   500 t          100 %          13 %
 1,000 t          100 %          13 %
 2,000 t          100 %          13 %
 4,000 t          100 %          93 %*
10,000 t          100 %          93 %*   (ceiling)
```

**before_edit at 500 tokens hits what flat_tail cannot reach at any
budget.** On the clean comparison (≤2000t, outside the self-reference
band), the ratio is:

- **500 tokens structured → 100% hit rate**
- **2000 tokens flat recency → 13% hit rate**

A 4× budget difference in flat recency's favor, and it still scores
~8× worse. The structural context is **at least an order of magnitude
more informative per token** on this corpus.

---

## What the CAUSE + self-observation layers add

After enabling `BELLAMEM_EW=hybrid` (LLM-backed extraction for cause
pairs and self-observations), the ingest stats from a 98-turn session:

```
claims:      348 written
affirmed:     75 (user ratifications of preceding assistant turns)
causes:       68 structured cause→effect pairs extracted
self_obs:     22 first-person habit statements in __self__
```

Those 68 cause pairs and 22 self-observations aren't reachable from
regex alone — they require LLM parsing of cause/effect spans that sit
on different sides of "because" / "root cause" / "caused by".

The before-edit pack at 1200 tokens then surfaces them in their own
layers:

```
Layer 1 (invariants)  — 13 principles, ranked by focus relevance
Layer 2 (causes, ⇒)   — 11 cause beliefs near the focus
Layer 3 (disputes, ⊥) — prior rejections
Layer 4 (bridges)     — R6 entity co-mentions
Layer 5 (__self__)    — 6 habit observations near the focus
```

For the query "should I wrap embed() in try/except to swallow errors,"
the self-model layer pulls the LLM's paraphrased observations — e.g.
"I tend to reach for try/except when I hit a KeyError" — as a first-class
part of the context. An agent with this pack sees not only the `C10 Fail
loud` invariant but also *its own prior statement that this is a reflex
it tends to have*.

This is R4 (self-refer) doing its job: the agent's anti-pattern is
visible in the context at the exact moment the anti-pattern would fire.

---

## Cost and speed

Per full bench run (1200t budget, all 5 contenders, 15 items):

- **embedding**: free on warm cache, ~$0.0001 on cold cache (OpenAI
  text-embedding-3-small)
- **compact contender**: ~15 gpt-4o-mini calls, ~$0.002 total
- **total runtime**: ~25 seconds on warm cache

Per ingest session (98 turns, hybrid LLM EW enabled):

- **embeddings**: ~350 claims × ~20 tokens = ~7k input tokens → $0.00014
- **LLM EW**: ~30 cause/self-obs calls × ~700 tokens each → ~$0.002
- **total per session**: **~$0.002**

Ingest wall-clock on a warm cache: ~15 seconds. On a cold cache before
the batched-save fix: 7+ minutes (thrashing on `_save()`). After fix:
~23 seconds.

---

## Caveats

### 1. Self-referential corpus

**The bench corpus was written from the same conversation the tree was
built on.** This shows up clearly in the flat_tail budget sweep: at
~3000 tokens the tail reaches back into the turn where the corpus was
drafted, and scores jump from 13% → 73% → 93%. That ~80% jump is
leakage.

The clean comparison is flat_tail at ≤2000 tokens (13%) vs before_edit
at any budget (100%+). The ~3000-10000t flat_tail numbers have a
self-reference bias and should be read accordingly.

**Fix**: split the transcript in half, build the tree on the first half,
ask questions whose answers live in the first half. Or run the bench
on a different conversation entirely. Tracked as follow-up work.

### 2. Retrieval, not agent behavior

This bench measures whether the decisive fact *appears in the pack*,
not whether an agent *uses* the fact to produce the correct action.
The step from "context contains X" to "agent does Y because of X" is
not measured here. That would require a ground-truth task outcome, an
actual agent, and a task harness — research-grade work.

The retrieval result is necessary but not sufficient. If retrieval
failed, agent behavior couldn't possibly be right. The fact that
retrieval succeeds is evidence the *architecture* isn't the bottleneck.

### 3. Small corpus (n=15)

15 items is enough to validate direction, not enough for statistical
significance. A corpus of 50–100 items across multiple conversations
would be more defensible. Scaling the corpus is straightforward and
planned.

### 4. One embedder, one compact model

Results depend on OpenAI's text-embedding-3-small (for embeddings) and
gpt-4o-mini (for compact). Different backends would shift the numbers.
The HashEmbedder path would likely score lower on `rag_topk` and
`bellamem_expand` because trigram hashing is coarser than a real
encoder.

### 5. Corpus skews toward constitutional questions

Most items ask about decisions, principles, or architecture — areas
where bellamem's constitution layer dominates. A corpus of "is there a
bug in this function body" questions would favor RAG more and the
principle layer less. A fair reading is: **bellamem is dramatically
better for the constitutional/architectural case; for pure code-local
questions the ordering might be different**.

---

## What this does prove

The load-bearing hypothesis of bellamem — *that accumulating belief
structure preserves decisive context at dramatically lower token cost
than flat recency* — is **measurably true on the data we have**. Not
a feeling, not a story. The numbers:

- **500 tokens structured ≥ any budget flat recency** on this corpus
- **Structured context is ~8× more informative per token** at the clean
  comparison point
- **Before-edit pack (5-layer, no recency) uses 27% fewer tokens than
  generic expand** for the same hit rate
- **The LLM EW layer (68 causes + 22 self-observations) costs ~$0.002
  per session** and populates layers that regex alone cannot touch

If bellamem had failed this test — if before_edit had scored below
RAG, or if flat_tail had dominated at reachable budgets — the whole
project would need rethinking. It didn't.

---

## How to run the bench yourself

```bash
# Full bench, default 1200 token budget
bellamem bench

# Subset of contenders (skip compact to avoid LLM cost)
bellamem bench --contenders flat_tail,rag_topk,expand,before_edit

# Smaller budget to see degradation
bellamem bench -t 500

# Budget sweep for one contender
for b in 500 1000 2000 4000; do
    bellamem bench -t $b --contenders before_edit \
        | grep "exact hit rate"
done
```

The corpus lives in `bellamem/bench_corpus.py`. Add items by editing
the list — they're plain Python dataclasses. Run the bench after
adding; if your item makes before_edit drop, you've found a real gap.
