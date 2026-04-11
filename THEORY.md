# Theory — Why Bella Works

Bella is a concrete instance of three ideas. None are original; the
work is assembling them into a queryable substrate for one specific
kind of memory.

- [How do we think?](#how-do-we-think) — the missing layer
- [Why it works: Jaynes × Shannon × Recursive Emergence](#why-it-works-jaynes--shannon--recursive-emergence)
- [A worked example: one session, compressed](#a-worked-example-one-session-compressed)
- [The six rules, operationally](#the-six-rules-operationally)
- [Consolidation as state, not layer](#consolidation-as-state-not-layer)
- [Forgetting — structural vs Bayesian decay](#forgetting--structural-vs-bayesian-decay)
- [Provenance (source grounding)](#provenance-source-grounding)

---

## How do we think?

We remember something from long before — a rule we learned years ago, a
correction from last quarter, a bug we fixed in a codebase we haven't
touched in months — and yet in the very same moment we hold the exact
sentence we just heard, the line of code we're about to edit, the
question still hanging in the air. Kahneman called it fast and slow.
The quieter way to say it: we have working memory and long-term memory,
and the remarkable thing isn't that we have both — it's that we compose
them seamlessly. The invariant you learned a decade ago and the word
someone said two seconds ago both land in your attention together,
weighted by relevance to what you're doing right now. No special layer
for "recent," no separate storage for "important." One mind, two
consolidation states, one composed answer.

LLM coding agents don't think this way. They have one layer: the context
window. Everything in it is "now"; everything outside it is gone. When
the window fills, `/compact` summarizes the tail and the specifics
disappear. When a session ends, the decisions evaporate. The next
session asks the same questions that were answered yesterday, makes the
same mistakes that were corrected last week, re-suggests approaches the
user already rejected — because nothing persisted between the flat tail
and the empty start. The agent has a kind of working memory. It has no
long-term memory at all.

**Bella is an attempt to give agents the missing layer — and the
bridge between the two.**

It's a local, file-backed belief graph that accumulates what matters
across sessions and topics: the decisions you ratified, the approaches
you rejected, the causal chains you traced, the patterns you've observed
in your own behavior. Each belief carries a *mass* — confidence
accumulated via Jaynes' log-odds from every time the belief was re-said,
re-confirmed, or contradicted — and typed edges: `→` support,
`⊥` counter-evidence, `⇒` cause. Beliefs also carry their provenance:
the session file and line number of every turn that contributed to
them, so you can always trace a claim back to what was actually said.

When an agent is about to act, it doesn't reload the whole tree. It
calls `expand(focus, budget)` and gets back a tight context pack —
mass-weighted, dispute-aware, with a continuous freshness bonus so
recent turns surface naturally without a dedicated "recent layer." The
same retrieval function answers both *"what did we decide about auth?"*
(mass dominates) and *"what am I in the middle of?"* (freshness
dominates). The graph doesn't fight the context window; it compresses
into it, on demand, at query time.

The consolidation story matters as much as the retrieval story. New
beliefs enter raw, young, and source-grounded. An automatic
consolidation pass runs after each ingest: near-duplicates collapse,
fields rename themselves from their own content, the graph's shape
quietly improves. The distinction between working memory and long-term
memory isn't a layer — it's an emergent property of where a belief sits
in the consolidation pipeline. Recent beliefs are raw because they
haven't been processed yet; old beliefs are compressed because they
have. Same model, different states.

Thinking fast and slow isn't a trick. It's composition. An agent with
working memory and no long-term memory is half-blind. An agent with
long-term memory and no fidelity on recent turns is half-deaf. Bella is
trying to give agents both — and to make the boundary between them
soft enough that neither the agent nor the user has to reason about
which layer a given fact came from. You just ask, and the right thing
comes back.

---

## Why it works: Jaynes × Shannon × Recursive Emergence

Three ideas do the heavy lifting.

**Jaynes.** A belief's mass is not a truth value; it's a sigmoid over
accumulated log-odds. Every piece of evidence adds `log(lr)` to the
accumulator, and the sigmoid keeps the result in `[0, 1]`. This is
E.T. Jaynes's Bayesian update rule, which is the only coherent way to
combine independent evidence without throwing information away. The
voice-attenuation factor (same-voice evidence is attenuated 10×)
enforces the "independent" part: an assistant repeating itself cannot
fake ratification, because its log-odds contribution decays. That's
what makes the `expand` pack rank-orderable by mass in a principled
way instead of by arbitrary weights.

**Shannon.** Entropy is literal, not metaphorical. The "mass limbo
band" is a high-entropy region *by definition*: a belief at mass 0.52
carries ≈1 bit of pure uncertainty because it has no evidence to
distinguish true from false. When `prune` removes residue leaves, it
isn't cleanup — it's a measurable drop in the Shannon entropy of the
mass distribution (3.45 → 2.79 bits in the worked example below). The
audit's other entropy signals (bandaid piles, root glut, garbage
field names) are structural entropy measures too, in the same sense:
each one quantifies specific forms of disorder the graph has failed
to resolve.

**Recursive Emergence.** The [RE thesis](https://github.com/Recursive-Emergence/RE/blob/main/thesis.md)
proposes a unified principle: "every meaningful structure arises from
interactions that reduce entropy while generating reusable patterns,"
and those patterns accumulate into memory that biases future
interactions toward more structure. The thesis's three symbols map
onto Bella directly — **Ψ** (recursive memory state) is the belief
graph, **Φ** (emergent coherence) is a ratified belief, **Ω**
(contradiction-resolving lattice) is the ⊥ dispute structure. The
memory update law `Ψ(t+1) = Ψ(t) + ∫ w(φ)·φ dφ` is the integral form
of Jaynes's log-odds accumulation. The emergence potential
`P = R · ΔH · S` (reusability × entropy reduction × structural
compatibility) is what `expand(focus, budget)` computes when it ranks
beliefs for retrieval. Bella isn't *inspired by* RE — it's a minimal
working instance of RE for conversational coding memory.

The work is the assembly: making these three ideas commit to specific
decisions about what to store, what to merge, what to forget, and what
to retrieve, so that an agent's next edit can actually use them.

---

## A worked example: one session, compressed

Every number and every rendered diagram below is produced by running
[`docs/example_session.py`](docs/example_session.py) against the real
Bella core — no synthetic diagrams, no hand-waved numbers. A pytest
smoke test keeps the example from drifting when the code changes.

### The session

```text
user:      the integration test flaked again, third time this week
assistant: I'll bump the sync timeout from 2s to 5s
user:      that's a bandaid, not a fix — don't paper over it
assistant: the sync retries an external api with 200ms backoff
assistant: CI load spikes rate-limit us and the first retry also fails
assistant: we exceed the 2s window before the second retry completes
user:      so the real fix is retry jitter, not timeout bumping
assistant: so the real fix is retry jitter, not timeout bumping
assistant: I reach for timeout bumps when retry semantics are the real problem
assistant: patched retry.py — exponential backoff, jitter ±50ms
user:      good
assistant: this is the third rate-limit incident on this path this quarter
user:      add it to the ticket pile
```

Thirteen turns. A correction. A cause chain. A ratified fix. A
self-observation. And a few observations nobody followed up on —
things that were said once and never referenced again.

### The graph right after ingest

![Bella example: after ingest](docs/example-before.svg)

Eleven beliefs, two fields. Every structural primitive Bella cares
about is present: a ⊥ dispute (the user denying the timeout bump), a
⇒ cause chain running from "CI load" through "rate-limit" to "2s
window exceeded", a two-voice ratified decision ("retry jitter is the
fix"), a `__self__` observation ("I reach for timeout bumps when
retry semantics are the real problem"), and four single-voice leaves
that landed in the graph and then the conversation moved on.

### The same graph thirty days later

```bash
bellamem emerge         # R3 consolidation — no-op here, nothing to merge
bellamem prune --apply  # structural forgetting — removes unratified leaves
```

![Bella example: after compression](docs/example-after.svg)

Seven beliefs. The four single-voice leaves that never earned
structural ties are gone. Everything that *did* earn structural
ties — the dispute, both cause edges, the ratified decision, the
self-observation, and the load-bearing ancestors of every kept
edge — survived untouched. Nothing with children, multi-voice
evidence, or a structural role was even considered for removal.

### The numbers

| | before | after |
|---|---:|---:|
| total beliefs | 11 | 7 |
| single-voice leaves | 7 | 3 |
| ratified decisions (multi-voice) | 1 | 1 |
| ⊥ disputes | 1 | 1 |
| ⇒ cause edges | 2 | 2 |
| `__self__` observations | 1 | 1 |
| mass in limbo band (0.48–0.55) | 7 | 3 |
| Shannon entropy of mass distribution | 3.45 bits | 2.79 bits |

A 36% reduction in belief count, a 19% reduction in mass-distribution
entropy, and every load-bearing piece of memory preserved exactly.
The "compression" is literal: fewer symbols, same decisions, same
disputes, same causal story — measured in bits.

---

## The six rules, operationally

Bella implements a six-rule calculus for accumulating evidence. Each
rule maps to a concrete component:

| Rule | Name | Component | Concrete behavior |
|---|---|---|---|
| R1 | accumulate | `gene.py:Belief.accumulate` | Jaynes log-odds mass + jumps history + sources |
| R2 | structure | `audit.py` entropy signals | Bandaid piles, root glut, mass limbo, single-voice rate |
| R3 | emerge | `emerge.py` | Near-duplicate merge + field rename (auto-runs after ingest) |
| R4 | self-refer | `__self__` field + LLM EW | Agent's habits as part of its own context |
| R5 | converge | `claude_code.py:ingest_session` | Turn-pair retroactive ratification with sourced evidence |
| R6 | entangle | `bella.py:entity_index` | Entities bridge fields via co-mention |

The rules are domain-agnostic. Bella is their application to agentic
coding memory.

---

## Consolidation as state, not layer

Bella's core insight, beyond retrieval, is that **recency is a
consolidation state, not a storage layer**. Every belief enters the
graph raw and young. An R3 consolidation pass runs automatically at
the end of each `ingest-cc`:

- **Near-duplicate merge** — pairs of beliefs in the same field with
  embedding cosine ≥ 0.92 are folded together. Voices, log_odds,
  entities, children, and sources all move to the survivor. No mass
  is discarded.
- **Field rename** — fields whose auto-generated names look like
  regex accidents (e.g. `log_odds_accumulate_log`) get renamed from
  their own content. Two namers: a zero-dep contrastive-rate baseline,
  and (optionally) a cheap LLM refiner for cases where the corpus is
  too coherent for contrastive analysis to work.

Consolidation is idempotent — running it twice on an already-healed
tree is a no-op. `bellamem emerge --dry-run` previews what would
change without mutating the snapshot.

Over time, the distinction between working memory and long-term memory
stops being a layer and becomes an emergent property of where a belief
sits in this pipeline. Recent beliefs are raw because they haven't
been processed yet; old beliefs are compressed because they have.

---

## Forgetting — structural vs Bayesian decay

Accumulation without forgetting is a bug. Most of what gets said in a
long coding session is assistant-voice exposition — real sentences
about the work, but never revisited, never ratified, never disputed,
never used as the parent of another claim. They enter the graph at
base mass (0.53) and sit there forever unless something explicitly
pulls them into structure. After enough sessions, a meaningful
fraction of the graph is this kind of residue.

`bellamem prune` removes it — but only under strict structural
safety rails. A belief is a prune candidate **iff all of** the
following hold:

- It's a **leaf** (`children == []`) — nothing grew below it.
- It's single-voice — only one source has ever ratified it.
- It's in the **base-mass band** (0.48 ≤ mass ≤ 0.55) — Jaynes has
  never moved it off the prior in either direction.
- It's **not itself a ⊥ dispute or ⇒ cause** — rejected approaches
  and causal predecessors are load-bearing memory, never pruned.
- It has **no `mass_floor` pin** — pinned beliefs were deliberately
  elevated by the caller.
- It's in a **non-reserved field** — `__self__` and other system
  fields are never touched.
- It's been **untouched for at least `--age-days`** (default 30).
- It was **created more than `--grace-days`** ago (default 14) —
  brand-new beliefs always get a grace period.

By construction, anything with structural ties — children, disputes,
causes, multi-voice ratification, high mass, recent evidence — is
safe. What's left is the residue: one-off assistant observations that
nothing ever grabbed onto.

Pruning is **structural**, not **Bayesian**. Structural pruning
solves the visible problem (residue accumulation) at the node level,
cleanly, without touching the Jaynes accumulator. v0.1.0a adds a
second, complementary forgetting mechanism — exponential log-odds
decay — which handles the orthogonal problem of **mass that was
earned but is no longer exercised**. The next section covers the
steady-state dynamics.

`bellamem prune` complements `bellamem emerge`: **emerge merges
duplicates**, **prune removes orphans**. With v0.1.0a decay added,
the full consolidation pipeline is: beliefs enter raw, either earn
their keep or age out (prune), and beliefs that earned their keep
but stopped being exercised regress toward the prior (decay). The
long-term graph stays a signal, not a transcript.

---

## Decay and reinforcement — the steady state

Exponential log-odds decay (v0.1.0a, shipped off-by-default under
`BELLAMEM_DECAY=on`) pulls each non-exempt belief's `log_odds` toward
zero on every save:

```
log_odds ← log_odds · exp(-Δt / τ),   τ = half_life_days / ln 2
```

With `half_life_days = 30`, a belief left alone for 30 days has its
log_odds halved (mass drifts toward 0.5). A belief left alone for
60 days has a quarter of its original log_odds. Exponential decay
is **composable**: 288 tiny passes per day yield the same
multiplicative factor as one big pass per day, so the frequency of
saves doesn't change the math — only the Δt.

Decay by itself is only half the story. The other half is
**reinforcement via ingest collision**: when a new claim embeds
close enough to an existing belief to route into it, Bella calls
`accumulate(lr)`, which adds `log(lr)` to the belief's accumulator.
For the default `lr = 1.5`, that's `≈ 0.405` per collision.

At equilibrium, decay and reinforcement balance out. For a belief
that collides once every `T` days, the steady-state log_odds solves

```
x · exp(-T / τ) + log(lr) = x
```

Plugging in `τ ≈ 43.28 d`, `log(lr) ≈ 0.405`:

| collision interval | steady log_odds | steady mass | verdict |
|---|---:|---:|---|
| 7 days  | 3.24 | 0.962 | stays ratified |
| 30 days | 0.81 | 0.692 | stays above limbo |
| 60 days | 0.47 | 0.615 | barely above limbo |
| 90 days | 0.23 | 0.557 | in limbo → prune eligible |
| 180 days | 0.00 | 0.500 | archaeology |

**A belief that comes up even once every two months naturally
self-sustains.** A belief that never comes up for six months decays
to the prior and becomes prune-eligible — at which point `bellamem
prune` can clean it structurally. This is the "use it or lose it"
policy in exact, measurable form.

### Why this is more brain-like than pinning

The design question we explicitly rejected was whether to add a
manual `bellamem pin` command so users could elevate "important"
decisions into a decay-exempt state. The rejected argument was that
architectural decisions would silently erode. The accepted argument
is that **if a decision truly matters, its topic will collide again**
— and every collision reinforces the belief automatically. If the
topic never comes up, the decision isn't load-bearing for current
work, and letting it regress to the prior is honest, not a bug.

This mirrors hippocampal consolidation in the computational
neuroscience literature: reinforcement and forgetting are **concurrent
competitive dynamics**, not separate phases. Frequently re-encoded
memories are protected by their re-encoding rate; rarely re-encoded
ones are protected by nothing and fade. There is no "important"
flag, and no curator. The dynamics are the policy.

### Why exponents are the right shape

Linear decay (`log_odds -= k·Δt`) has two failures: it crosses zero
and overshoots into negative, and its half-life depends on initial
mass. Exponential decay toward zero is the unique shape that:

1. **Never flips the sign.** Mass asymptotically approaches 0.5
   from whichever side it started, without crossing.
2. **Is composable.** Two passes with Δt₁ and Δt₂ equal one pass
   with Δt₁+Δt₂. Save frequency doesn't matter.
3. **Is max-entropy.** The belief regresses to its prior (log_odds=0
   ↔ mass=0.5) which is the only non-informative state consistent
   with R1 accumulate's prior.

### Interaction with `surprises`

`surprises` reads `belief.jumps` — the log of Jaynes accumulate
events. Decay does **not** append to jumps. This is an invariant
from earlier design work: jumps is a record of evidence, decay is a
silent drift of confidence in the absence of evidence. If decay
appended to jumps, `surprises` would score drift events the same as
evidence events and the top-surprises list would flood with fades
instead of genuine user corrections and sign flips.

A future audit signal ("quiet fades" — beliefs that crossed from
ratified into limbo via decay alone on the most recent pass) already
lives in the `DecayReport` returned by `apply_decay` and is
surfaced at the moment of the save or the manual `bellamem decay`
run. It is deliberately **not** persisted across sessions: if you
need to act on a quiet fade, act when you see it. If you don't see
it, it wasn't load-bearing.

### Continuous vs batched saves

Because exponential decay is composable, Bella gets the same
long-run state whether it saves once a day or every five minutes —
so running `bellamem save` on a short cron/systemd timer is
mathematically equivalent to a resident process, at a fraction of
the complexity. The actual benefit of frequent saves isn't
decay-related: it's **reinforcement latency**. A new collision that
lands in the graph at 12:03 doesn't help until the belief it
reinforces is persisted, and every query between collisions and
save is working from stale state.

For v0.1.0a the recommended pattern is a 5–10 minute cron timer
running `BELLAMEM_DECAY=on bellamem save`. Resident mode (socket,
daemon, live state) is a v0.2+ design question — worth it only if
measurement shows the cron approach genuinely bottlenecks the
reinforcement loop.

---

## Provenance (source grounding)

Every belief carries a `sources: list[(session_key, line_number)]`
field. When the adapter ingests a transcript turn, the belief it
creates is stamped with the exact line the claim came from. When
retroactive ratification fires (the user saying *"yes, exactly"*
confirms the preceding assistant turn), the ratification is stamped
with the *user's* line, not the assistant's — so you can trace which
line of evidence bumped which belief's mass.

Sources enable:

- **Narrative replay** — line-ordered retrieval via `bellamem replay`
- **"Beliefs from the last N lines"** — direct index, no heuristic
- **Multi-source auditing** — a belief with three sources shows the
  mention → re-mention → ratification pattern as a concrete chain
- **Provenance under merge** — when two beliefs merge, their source
  lists are unioned, preserving the full evidence trail

`event_time` is still used for the freshness weight in `expand`, but
for authoritative "what was recent?" queries, sources are the truth.
Timestamps can lie (ingest time ≠ conversation time); line numbers
don't.
