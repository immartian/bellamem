# Benchmarks

Versioned empirical results for Bella's retrieval quality. Each file
is a snapshot of `bellamem bench` output + its methodology notes at
the named release.

## Index

| Version | File | Retrieval code at | Notes |
|---|---|---|---|
| v0.1.0a  | [v0.1.0a.md](v0.1.0a.md)   | unchanged from v0.0.2 | Decay stress test: worst-case `--dt-override 30d` against a scratch copy of the forest. `expand` tokens −31% for −7pp judge (1 item, noise floor); `before_edit` +8pp (1 item). Ordering preserved. Realistic steady-state comes in v0.1.0.md post-dogfood. |
| v0.0.4rc1 | [v0.0.4rc1.md](v0.0.4rc1.md) | unchanged from v0.0.2 | Re-run against the grown 1834-belief forest. `rag_topk` collapsed (85→31 judge) as the forest grew; `expand` held (100→92). Gap from `expand` to next-best contender widened from 15pp to 61pp. |
| v0.0.2 | [v0.0.2.md](v0.0.2.md) | `expand` / `expand_before_edit` as shipped in v0.0.2 | Initial dogfood benchmark: 13 hand-labeled queries. 5 contenders (flat_tail / compact / rag_topk / expand / before_edit). |

## When to add a new entry

Re-run the bench and commit a new `vX.Y.Z.md` whenever any of the
following change:

- `bellamem/core/expand.py` (retrieval scoring, freshness weight, layer allocation)
- `bellamem/core/bella.py` entity index / routing (feeds R6)
- `bellamem/adapters/llm_ew.py` or `chat.py` (EW extraction quality)
- `bellamem/bench_corpus.py` (the ground-truth queries themselves)

Releases that touch **only** storage, hook wiring, CLI ergonomics,
latency, or unrelated components (v0.0.3 cache prune, v0.0.4rc1
split/guard/batching) do not invalidate the prior bench — cite the
most recent applicable version in the README.

## How to run

```bash
bellamem bench                                    # all contenders, all budgets
bellamem bench --contenders expand,before_edit    # subset
bellamem bench --budgets 500,1000,2000            # subset
bellamem bench --out benchmarks/vX.Y.Z.md         # redirect output
```

The bench uses an LLM judge for the "embed hit" metric (configurable;
defaults to `gpt-4o-mini`). Cost is ~$0.01 per full run. See
[v0.0.2.md](v0.0.2.md) for methodology and judge details.

## File convention

Each versioned file should contain:

1. **Run date** and release tag at the top.
2. **Corpus description** — size of the belief forest, session count, query list.
3. **Configuration** — embedder, judge model, any non-default flags.
4. **Results tables** — exact hit / embed hit / avg tokens, per contender, per budget.
5. **What changed since the previous version** — one-line delta so
   readers can tell whether a number drift is noise or a real regression.
6. **Caveats** — anything the methodology can't control for
   (self-reference lift, corpus drift, etc.).

Prior versions are immutable history. Fix a bug in the current version
only; leave old files alone so the historical record stays honest.
