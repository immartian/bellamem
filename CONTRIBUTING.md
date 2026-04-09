# Contributing to bellamem

Thanks for looking. A few things to know before you change code.

## The mission is context window management

bellamem retrieves the decisive facts for an LLM coding agent's next
edit under a small token budget. Everything in the repo exists to
serve that goal. If a proposed change doesn't improve retrieval quality,
compression, or the feedback loop that gets facts into the tree,
it's probably off-mission.

Specifically: **this is not a governance tool**. It doesn't enforce
rules, audit against principles, or prevent drift as a primary purpose.
An earlier version of the README described those things; they were
mission creep on an adjacent problem. A focused rescope removed them
in v0.0.2.

## The bench is the CI

```bash
bellamem bench
```

Any change to EW, expand, audit, or the ingest path should be
accompanied by a bench run showing the hit rate hasn't dropped. If
your change moves the numbers in either direction, include the result
in the PR description.

If you find a new failure mode, **add a bench item for it** before
fixing it. The corpus lives in `bellamem/bench_corpus.py` and is the
ground-truth artifact of what we promise the tool can do.

## Architectural constraints (don't break these)

- **`bellamem.core` must never import from `bellamem.adapters`.** The
  core is domain-agnostic. All chat / file / code knowledge lives in
  `adapters/`.
- **The seven operations are the complete mutation API.** Every write
  to the belief tree flows through `CONFIRM | AMEND | ADD | DENY |
  CAUSE | MERGE | MOVE`. No direct writes to `Gene` or `Belief` from
  outside.
- **Every write is a `Claim` carrying `voice` and `lr`.** No silent
  writes, no unattributed beliefs.
- **Reserved fields start with `__`.** Adapters express routing intent
  via `Claim.relation`; core decides where a reserved-field-destined
  claim lands.
- **Zero required runtime dependencies.** Upgrades live behind
  `[project.optional-dependencies]`. A `git clone && pip install -e .`
  should produce a working tool with stdlib alone.

Break any of these and the PR needs a very good reason.

## Running the dogfood loop

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[all]'

# Optional: copy .env.example → .env and fill in your OpenAI key
# to enable the OpenAI embedder and hybrid LLM EW. Default
# (no .env) uses the stdlib HashEmbedder and regex-only EW.

# Try it on your own project
cd /path/to/your/project
~/path/to/bellamem/.venv/bin/bellamem ingest-cc
~/path/to/bellamem/.venv/bin/bellamem audit
~/path/to/bellamem/.venv/bin/bellamem before-edit "describe a proposed edit"
```

If the tool is going to be useful it has to feel useful on your own
code. Dogfood before sending a PR.

## How to extend specific pieces

### Add a new embedder

Implement the `Embedder` protocol in `core/embed.py`:

```python
class MyEmbedder:
    dim: int
    name: str                                        # e.g. "my-encoder-v1"
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
```

Add a lazy import (so users don't need your dependency by default),
a constructor branch in `make_embedder_from_env`, and a `[myext]`
optional dependency in `pyproject.toml`. Snapshots automatically
track your embedder via `name` and refuse to mix with vectors from
another embedder (see `store.py`'s signature check).

### Add a new EW rule (regex path)

Most regex rules live in `adapters/chat.py`. Test your additions
against the bench before merging — the `never`, `not`, and `don't`
tokens are ambiguous enough to create false positives that surface
as phantom disputes in the tree. See `_has_real_denial` for the
existing quoted-and-conditional filter.

### Add a new EW path (LLM-backed)

Structural extraction (cause/effect, self-observation, compound
relations) goes in `adapters/llm_ew.py` behind the `BELLAMEM_EW=hybrid`
flag. Regex path must remain functional with zero LLM calls — the
LLM path is opt-in.

### Add a new EXPAND layer

Edit `core/expand.py:expand_before_edit` and the budget split
constants at the top of that function. Each layer is a simple ranker
that returns `list[(field_name, Belief, score)]` and a quota loop
that respects the token budget. Look at `_causes_for`, `_bridges_for`,
`_self_model_for` for the pattern.

Before merging a new layer, **add at least one bench item that
specifically exercises it**. Otherwise you can't tell if it's actually
doing work.

### Add a bench item

Edit `bellamem/bench_corpus.py`. Each item is a `BenchItem`:

```python
BenchItem(
    id="Q16",
    query="<a question an agent might ask mid-edit>",
    focus_entity="<optional file/function name>",
    expected_any_of=[
        "<a decisive fact, exact-matched>",
        "<an alternative phrasing>",
    ],
    category="<architecture|bandaid|language|epistemics|...>",
)
```

Keep `expected_any_of` specific — one-word matches false-hit on
unrelated pack lines. Three-to-five-word phrases are the sweet spot.
Reference content that was **actually discussed** in the conversation,
not content you wish had been.

## Commit messages

Lead with *what changed*, then a brief *why*. If you touched EW,
expand, audit, or the ingest path, include the bench result delta.

Example:

```
chat: treat "never" as rule, not denial

`never` was in _DENIAL_RE, causing "principles never decay" to be
classified as a deny and land as a ⊥ child of the principle it was
paraphrasing.

Fix: move `never` to _RULE_RE only. Reorder classify() to check
rule before deny.

bench: exact hit rate unchanged, false disputes 63 → 0
```

## Code style

- Python 3.10+, type hints everywhere they clarify
- Dataclasses over dicts for structured records
- No `Any` without a comment explaining why
- Docstrings on public functions; one-line doc or nothing on helpers
- Prefer small files; the whole core fits in your head
- Read [ARCHITECTURE.md](ARCHITECTURE.md) before refactoring across
  modules

## What not to send

- **New runtime dependencies.** Use optional extras.
- **Speculative abstractions.** If the bench doesn't need it, it
  doesn't exist.
- **Backwards-compat shims.** Break forward. Migrate data explicitly.
  The snapshot signature check exists because silent migration is
  the wrong answer.
- **Governance features.** bellamem is not an audit / drift / compliance
  tool. If you're building one, fork.

## Questions

Open an issue with a clear reproduction or a bench item that shows
the problem. "My agent gave a weird answer" is not actionable;
"before-edit missed this decisive fact, here's the bench item that
demonstrates it" is.
