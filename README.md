# bellamem

**Local, accumulating memory for LLM coding agents.**

Not RAG. Not `/compact`. A belief tree that grows with mass, enforces its
own constitution, and loads decisive context by importance not recency.

bellamem is an application of [BELLA](#theory)'s six-rule calculus to the
specific problem coding agents keep failing at: remembering what you already
decided, why you rejected something, and which of your own habits keep
leading to bandaid fixes.

---

## Why

Every heavy Claude Code / Cursor user has felt it:

- `/compact` turns a session summary into a shallow paraphrase and loses the
  "why". Rejected approaches come back next session.
- Flat recency context biases toward the last bandaid instead of the
  invariant the bandaid violates.
- RAG retrieves topical documents but has no memory of accumulated
  decisions, disputes, or the agent's own anti-patterns.
- Manual `MEMORY.md` files rot because nothing enforces them.

bellamem does one thing: it builds a persistent, structured belief tree
from your actual conversations and loads the *decisive facts* for each
edit under a small token budget. The numbers (see [BENCH.md](BENCH.md))
show structured context retrieving decisive facts at roughly **1/8 the
token budget of flat recency** on a hand-labeled dogfood corpus.

It is, deliberately, not a graph database, not a vector store, and not an
LLM framework. It is ~3000 lines of Python, stdlib-only by default, with
opt-in upgrades for real embeddings and LLM-backed extraction.

---

## Status

**v0.0.1 — alpha.** Dogfooded on a single long session (this one) and
validated against a hand-written bench. Production-ready for "try it on
your own project," not yet for "bet your release on it." See
[CHANGELOG.md](CHANGELOG.md) for what's built and what isn't.

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

Copy `.env.example` → `.env` and fill in whatever backends you enabled.
`.env` is gitignored.

---

## Quickstart — ingest a Claude Code session and query it

bellamem reads Claude Code transcripts from
`~/.claude/projects/<escaped-cwd>/*.jsonl` directly. Run it from the
directory of the project you want memory for:

```bash
# ingest all sessions for the current project
bellamem ingest-cc

# ask what you'd need to know before a proposed edit
bellamem before-edit "should I wrap this in try/except to swallow errors" \
    --entity embed.py

# generic mass-weighted context pack
bellamem expand "should I use Rust for the prototype"

# walk the belief tree and report drift / contradictions / bandaid piles
bellamem audit

# benchmark against flat-tail, compact, RAG, generic expand
bellamem bench
```

Every command is read-only except `ingest-cc` and `reset`.

---

## The core idea in one picture

```
Raw Claude Code transcript (.jsonl)
        ↓
    regex EW  ──────── voice-aware (user oracle, assistant hypothesis)
        ↓
    + LLM EW ──────── CAUSE pairs, self-observations (opt-in, gpt-4o-mini)
        ↓
    Claim(text, voice, lr, relation)
        ↓
    Bella.ingest()  ── routes via embedding, applies Jaynes accumulation
        ↓
    Belief tree (fields → beliefs → typed edges)
        ↓
┌───────┴────────┬──────────┬──────────┐
expand()    before-edit()   audit     bench
  generic    5-layer pack   drift      compression
             no recency     report     vs flat/RAG
```

Five layers in the before-edit pack, in order of priority:

```
40%  invariants     — principles + high-mass rules  (always on)
20%  disputes       — ⊥ edges touching the focus    (blocks re-suggestion)
20%  causes         — ⇒ chains near the focus       (root-cause awareness)
10%  entity bridges — R6 co-mention neighborhood     (whole-picture)
10%  self-model     — __self__ habit observations    (agent's own anti-patterns)
```

Recency is *absent* by design. In before-edit mode, recency biases toward
the last bandaid; mass bias toward the invariant it violates.

For the full architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## The constitution

bellamem enforces a hand-written `PRINCIPLES.md` as an **always-loaded,
mass-floored layer of the context pack**. The principles can only change
by editing that file; they never decay; any claim that contradicts one
auto-generates a visible ⊥ edge in the tree.

The shipped `PRINCIPLES.md` contains 21 bellamem-specific principles plus
22 canonical engineering principles (YAGNI, KISS, fail-loud, boundaries
validate internals trust, break forward, etc.) — see
[PRINCIPLES.md](PRINCIPLES.md) for the full text.

The constitution idea is the anti-drift mechanism. Your project's rules
become a load-bearing artifact the memory checks at every edit, not
decoration in a README nobody reads.

---

## Measured against flat recency

From [BENCH.md](BENCH.md), run on 15 hand-labeled queries drawn from a
real 98-turn dogfood session:

| budget | flat_tail | before_edit |
|---|---|---|
| 200 t | — | **80 %** |
| 500 t | 13 % | **100 %** |
| 1000 t | 13 % | 100 % |
| 2000 t | 13 % | 100 % |
| 4000 t | 93 %* | 100 % |
| 10000 t | 93 %* | 100 % |

_\*self-referential lift — the tail reaches back into the turn where we
wrote the bench corpus. The clean 13% plateau at ≤2000t is the fair
comparison._

**before_edit at 500 tokens reaches 100% exact match on the corpus.
flat_tail plateaus at 93% — it cannot reach 100% at any budget, because
one decision was made deep in the session and recency can never surface
it.**

One full comparison at 1200 tokens:

```
              flat_tail  compact  rag_topk  expand  before_edit
exact hit        13 %     33 %     93 %     100 %    100 %
embed hit        33 %     73 %    100 %     100 %    100 %
avg tokens     1200      725      1175     1167      853
```

Caveats: corpus is small (n=15) and self-referential (written from the
same conversation the tree was built on). A held-out split test and a
cross-session test are planned. Full methodology and caveats in
[BENCH.md](BENCH.md).

---

## What it's not

- **Not a graph database.** The belief tree is a dict of dicts. Our ops
  are one-hop. We don't need Cypher.
- **Not a vector store.** Embeddings are pluggable, not the architecture.
- **Not an LLM framework.** bellamem doesn't call models for you; it
  builds the context pack you hand to your own agent.
- **Not a replacement for good docs.** PRINCIPLES.md is the anti-drift
  layer; it's your architecture in commit-able form, not a substitute
  for human writing.
- **Not a finished product.** v0.0.1. See
  [CHANGELOG.md](CHANGELOG.md) for what's done and
  [CONTRIBUTING.md](CONTRIBUTING.md) for what's not.

---

## Theory

bellamem is a domain application of **BELLA**, a six-rule calculus for
systems that accumulate evidence over time. The same machinery underlies
a news-epistemics application in a separate project; the theory
document (`SPEC.md`) lives there.

The six rules, in operational form:

| Rule | Name | What it gives you |
|---|---|---|
| R1 | accumulate | Jaynes log-odds mass; repetition compounds |
| R2 | structure | entropy-driven local restructure |
| R3 | emerge | fields appear from centroid convergence |
| R4 | self-refer | agent models its own patterns (`__self__`) |
| R5 | converge | feedback attenuates cycles (turn-pair ratification) |
| R6 | entangle | entities bridge fields via co-mention |

The claim worth taking seriously: these rules are **domain-agnostic**.
They describe any system that receives evidence over time, needs to
structure it without losing contested claims, and must retrieve by
importance not recency. News, coding agent memory, personal knowledge
management — same calculus, different domain.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version:

- Extend the bench corpus whenever you spot a new failure mode
- Add new embedders by implementing the `Embedder` protocol in `core/embed.py`
- Add new EW adapters in `adapters/` — never in `core/`
- Respect `PRINCIPLES.md` — the tool enforces it against itself

---

## License

MIT. See [LICENSE](LICENSE).
