# Contributing to bellamem

Thanks for looking. A few things to know before you change code.

## The constitution is load-bearing

Read [PRINCIPLES.md](PRINCIPLES.md) first. The tool enforces these
against itself, and the audit command will flag contradictions. If your
change conflicts with a principle, you have two choices:

1. Rethink the change
2. Edit `PRINCIPLES.md` explicitly, with a clear rationale in the
   commit message

There is no silent third option. If a principle is "in the way," that
usually means it's doing its job.

The most load-bearing ones for contributors:

- **P1** — `bellamem.core` must never import from `bellamem.adapters`.
  Core is domain-agnostic. All chat / file / code knowledge lives in
  `adapters/`.
- **P2** — The seven operations (CONFIRM, AMEND, ADD, DENY, CAUSE,
  MERGE, MOVE) are the complete mutation API for the belief tree. No
  direct writes to `Gene` or `Belief` from outside.
- **P3** — Every write is a `Claim` carrying `voice` and `lr`. No
  silent writes, no unattributed beliefs.
- **P6** — Zero required runtime dependencies in v0. Upgrades live
  behind `[project.optional-dependencies]`. A user who `git clone`s
  and runs should get a working tool with stdlib alone.
- **P18** — Reserved field names start with `__`. Adapters express
  routing intent via `Claim.relation`; core decides where it goes.

## Running the dogfood loop

```bash
# setup
python3 -m venv .venv
.venv/bin/pip install -e '.[all]'

# copy and fill in your .env from .env.example if you want
# the openai path — otherwise the default hash embedder works fine

# try it on your own project
cd /path/to/your/project
~/path/to/bellamem/.venv/bin/bellamem ingest-cc
~/path/to/bellamem/.venv/bin/bellamem audit
~/path/to/bellamem/.venv/bin/bellamem before-edit "describe a proposed edit"
```

If the tool is going to be useful it has to feel useful on your own
code. Dogfood before sending a PR.

## The bench is the CI

```bash
bellamem bench
```

Any change to EW, expand, audit, or the ingest path should be
accompanied by a bench run showing the hit rate hasn't dropped. If
your change moves the numbers in a particular direction — up or down —
include the result in the PR description.

If you find a new failure mode, **add a bench item for it** before
fixing it. The corpus lives in `bellamem/bench_corpus.py` and is the
ground-truth artifact of what we promise this tool can do.

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
optional dependency in `pyproject.toml`. Snapshots will automatically
track your embedder via `name` and refuse to mix with vectors from
another embedder (see `store.py`'s signature check).

### Add a new EW rule

Most regex rules live in `adapters/chat.py`. Don't add your marker to
`_DENIAL_RE` without testing it against the audit — the `never`,
`not`, and `don't` tokens are ambiguous enough to create false
positives that surface as phantom contradictions.

If you need structural extraction (cause/effect, self-observation,
compound relations), put it in `adapters/llm_ew.py` behind the
`BELLAMEM_EW=hybrid` flag. The regex path must remain functional with
zero LLM calls.

### Add a new EXPAND layer

Edit `core/expand.py:expand_before_edit` and the budget split
constants at the top of that function. Each layer is a simple ranker
that returns `list[(field_name, Belief, score)]` and a quota loop that
respects the token budget. Look at `_causes_for`, `_bridges_for`,
`_self_model_for` for the pattern.

Before merging a new layer, add at least one bench item that
specifically exercises it, otherwise you can't tell if it's actually
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
        "<principle id if applicable>",
    ],
    category="<constitution|bandaid|language|...>",
)
```

Keep `expected_any_of` specific — one-word matches will false-hit on
unrelated pack lines. Three-to-five-word phrases are the sweet spot.

## Commit messages

Lead with *what changed*, then a brief *why*. If you touched EW,
expand, audit, or the ingest path, include the bench result delta.

Example:

```
chat: treat "never" as rule, not denial

`never` was in _DENIAL_RE, causing "principles never decay" to be
classified as a deny and land as a ⊥ child of the principle it was
paraphrasing. Audit flagged 63 false-positive contradictions on the
dogfood corpus.

Fix: move `never` / `always` / `immutable` to _RULE_RE only. Reorder
classify() to check rule before deny.

bench: 100% before_edit unchanged, audit contradictions 63 → 0
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
- **Speculative abstractions.** YAGNI. The bench will tell you if
  simplicity costs you anything.
- **Backwards-compat shims.** Break forward. Migrate data explicitly.
  The snapshot signature check exists because silent migration is
  the wrong answer.
- **Changes to `PRINCIPLES.md` without a matching rationale.** The
  constitution is stable on purpose.

## Questions

Open an issue with a clear reproduction or a bench item that shows
the problem. "My agent gave a weird answer" is not actionable;
"before-edit missed this decisive fact, here's the bench item that
demonstrates it" is.
