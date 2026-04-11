# Bella

**Graph memory for agentic coders (Claude Code, Codex soon) — extends
the effective time horizon ~8×.**

<video src="https://github.com/immartian/bellamem/raw/main/docs/bella-viz3d.webm" controls width="720"></video>

*(If the video doesn't render inline where you're reading this, open
[`docs/bella-viz3d.webm`](https://github.com/immartian/bellamem/raw/main/docs/bella-viz3d.webm)
directly — 3D belief graph, drag to rotate, replay bar scrubs history.)*

> Bella is the visual brand; the Python package and CLI remain
> `bellamem`. `pipx install bellamem`, then `bellamem save`.

---

## What changes

A normal Claude Code session is a flat sequence of turns. The context
window holds them in order; when it fills up, the oldest are summarized
or dropped. Bella runs alongside and extracts the *structure* the turns
contain — decisions, rejected approaches, cause chains,
self-observations — into a belief graph that survives session
boundaries. The same eight turns of a debugging session carry different
information depending on which shape you keep:

```
Flat recency (what the context window holds)    │  Bella graph (what survives)
─────────────────────────────────────────────────┼──────────────────────────────────────
user:      test flaked again, third time        │  ratified: retry-jitter fix
assistant: I'll bump the timeout to 5s           │    m=0.74  v=2  (user + assistant)
user:      don't paper over it                   │
assistant: sync retries at 200ms backoff         │     ⇒  CI load → rate-limit → 2s exceeded
assistant: CI load spikes, rate-limiting         │
user:      so the fix is retry jitter            │     ⊥  "bump timeout to 5s"  (rejected)
assistant: patched retry.py, backoff + jitter    │
user:      good                                  │   __self__: "I reach for bandaids when
─────────────────────────────────────────────────┤              retry semantics are the real
8 turns, ~110 tokens, ordered by time            │              problem"
                                                 │  ──────────────────────────────────────
                                                 │  4 beliefs + 2 edge types, ~30 tokens,
                                                 │  ordered by evidence and structure
```

Same information content, different geometry. The left column lets an
agent reconstruct *what was said*. The right column lets it reconstruct
*what was decided, what was rejected, and what caused what* — in a
tenth the tokens, and across session boundaries where the left column
can't go.

**Three retrieval modes answer different questions about the same store:**

| Command | Question |
|---|---|
| [`bellamem expand "X"`](#expand-and-before-edit) | *What do we **believe** about X, ranked by importance?* |
| [`bellamem surprises`](#surprises) | *What just **changed** — what mattered?* |
| [`bellamem replay [X]`](#replay) | *What did we **say** — in what order?* |

A full explanation of *why* this works (Jaynes log-odds accumulation,
Shannon entropy of the mass distribution, the Recursive Emergence
framing, and a worked flaky-test example with before/after diagrams
and numbers) lives in [THEORY.md](https://github.com/immartian/bellamem/blob/main/THEORY.md). The short version: every
belief carries a mass updated by Jaynes's Bayesian rule, the audit's
"entropy signals" are literal Shannon entropy, and the whole thing is
a minimal working instance of
[Recursive Emergence](https://github.com/Recursive-Emergence/RE/blob/main/thesis.md)
for conversational coding memory.

---

## Install

`pipx` is the recommended path — a single global `bellamem` command,
no `.venv` to remember, no PATH surgery:

```bash
pipx install bellamem
# or, from a local clone:
git clone https://github.com/immartian/bellamem
pipx install -e ./bellamem                  # editable install, still global
```

Per-project venv also works:

```bash
cd your-project
python3 -m venv .venv
.venv/bin/pip install bellamem
```

Optional extras:

```bash
pipx inject bellamem 'sentence-transformers>=2.2'   # local embeddings
pipx inject bellamem 'openai>=1.0'                  # OpenAI embeddings + LLM EW
# or with pip:
pip install 'bellamem[st]'      # sentence-transformers
pip install 'bellamem[openai]'  # OpenAI
pip install 'bellamem[all]'     # both
```

Copy `.env.example` → `.env` in your project and fill in the backends
you enabled. `.env` is gitignored.

**Requirements:** Python 3.10+. Git (Bella scopes per-project state via
the git repo root). No other system dependencies.

---

## Quickstart

```bash
# Ingest Claude Code sessions for the current project.
# Auto-runs R3 consolidation (merges near-duplicates) on new claims.
bellamem save

# Three retrieval modes — same memory, different questions:
bellamem expand "what did we decide about persistence"
bellamem surprises                                      # top jumps, sign flips, disputes
bellamem replay                                         # narrative timeline
bellamem replay "ad-hoc bandaid pattern"                # focused narrative

# The pre-edit pack: no recency, surfaces invariants + disputes + causes
bellamem before-edit "should I wrap this in try/except" --entity embed.py

# Health report: bandaid piles, duplicates, garbage field names, mass limbo
bellamem audit

# Render the graph as a picture (needs the [viz] extra or graphviz CLI)
bellamem render --out graph.svg                           # whole forest
bellamem render --out disputes.svg --disputes-only        # just ⊥ edges
bellamem render --out auth.svg --focus "auth tokens"      # subgraph around a focus

# Forget orphan leaves that never earned their place (dry run by default)
bellamem prune                        # preview candidates
bellamem prune --apply                # actually remove them

# Empirically compare context strategies (flat, compact, RAG, Bella)
bellamem bench
```

Every command except `save`, `emerge`, `prune --apply`, and `scrub` is
read-only.

---

## Use with Claude Code

The flow that lets you keep working past the context window without
losing the thread is packaged into four slash commands.

### Install the slash command — once, globally

```bash
bellamem install-commands           # writes ~/.claude/commands/bellamem.md
```

`/bellamem` now works in **every** Claude Code project on your
machine. Per-project install (`--project`) is also supported if you
want to commit the slash command into a specific repo.

### The commands

| Command | What it does |
|---|---|
| `/bellamem` or `/bellamem resume` | Working-memory replay tail + long-term expand pack + top surprises. Run at session start. |
| `/bellamem save` | Ingest the current session (auto-consolidates), run audit, report top new surprises. Run before `/clear` or at end of day. |
| `/bellamem recall <topic>` | Mass-ranked beliefs about a topic, disputes included. Mid-session lookup. |
| `/bellamem why <topic>` | Pre-edit pack: invariants, disputes, causes, entity bridges. Run before a risky change. |
| `/bellamem replay` / `/bellamem audit` | Raw CLI output when you want to look at it directly. |

### The save → clear → resume flow

```
/bellamem save     ← captures this session into the graph
/clear             ← wipe the context window (Claude Code built-in)
/bellamem resume   ← fresh assistant reconstructs where you were
```

On a well-tuned project, `/bellamem resume` comes back in **~30k
tokens** and contains enough to pick up the next decision without
re-asking questions already answered. If it's much larger, run
`bellamem emerge` to consolidate near-duplicates.

### The edit guard (v0.0.4)

Install `bellamem-guard` as a Claude Code **PreToolUse hook** and an
advisory pack (invariants + disputes + causes for the focus) is
injected automatically before every `Edit` / `Write` / `MultiEdit`
call — no manual invocation needed. The guard `exit-2`s when the edit
re-suggests a rejected approach (a `⊥` dispute), refusing the tool
call at the boundary.

Hook registration (once per project) in `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Edit|Write|MultiEdit", "hooks": [{ "type": "command", "command": "bellamem-guard" }] }
    ]
  }
}
```

### Where your data lives

```
~/.claude/commands/
  bellamem.md            installed once (global slash command)

<your-project>/
  .claude/settings.json  PreToolUse hook registration (optional)
  .graph/
    default.json          belief graph (gitignored by default)
    default.emb.bin       belief embeddings, v3 binary sidecar
    embed_cache.json      embedding cache (pruned to live beliefs on save)
    llm_ew_cache.json     LLM EW cache (if BELLAMEM_EW=hybrid)
  .env                    your API keys + embedder choice (never commit)
```

`.graph/` is gitignored by default.

---

## Bella vs `/compact`

Both compress a long session. The difference is load-bearing:

| | `/compact` | Bella |
|---|---|---|
| **Output** | One narrative summary (~2000 tokens) | Queryable belief graph (~3k per retrieval) |
| **Shape** | Prose | Beliefs + typed edges (`→`, `⊥`, `⇒`) + mass + voices + sources |
| **Usage** | Replaces history; summary becomes new context | Load on demand per turn; three retrieval modes |
| **Preserves** | Broad topics, major decisions, flow | Paraphrased decisions, rejected approaches, cause-effect chains, self-observations, line numbers |
| **Loses** | Identifiers, ⊥ corrections, causal structure | Tool outputs, file contents, conversational texture |
| **Cross-session** | None — dies with the session | Full — graph persists, next session inherits it |

On our bench, the compact-style contender (`gpt-4o-mini` summary)
scored **8% LLM-judge rate**; Bella's `expand` scored **92%** at a
comparable budget. The structural weakness of narrative summaries is
that they preserve themes but lose the specific decisions, corrections,
and causes an agent actually needs to act.

The two are complementary, not competing: `/compact` keeps the *feel*
of the conversation going inside one session. Bella keeps the
*decisions* available across sessions.

---

## Empirical results

Latest measurement: [benchmarks/v0.0.4rc1.md](https://github.com/immartian/bellamem/blob/main/benchmarks/v0.0.4rc1.md)
(2026-04-10, budget = 1200 tokens, LLM judge enabled, 13-item
hand-labeled corpus, 1834-belief forest).

```
metric               flat_tail      compact     rag_topk       expand  before_edit
----------------------------------------------------------------------------------
exact hit rate            15 %          0 %         15 %         69 %         46 %
embed hit rate            23 %         31 %         31 %         85 %         77 %
llm judge rate             0 %          8 %         31 %         92 %         69 %
avg tokens used           1200          602         1161         1143          964
```

`flat_tail (0%) < compact (8%) < rag_topk (31%) < before_edit (69%) < expand (92%)`.

**Headline story — compare to [v0.0.2](https://github.com/immartian/bellamem/blob/main/benchmarks/v0.0.2.md):** as
the forest grew from the v0.0.2 dogfood snapshot to 1834 beliefs,
`rag_topk` collapsed from 85% → 31% LLM judge (cosine top-k pulls up
more plausible-looking-but-wrong neighbors in a larger forest), while
`expand` held at 92%. The gap from `expand` to the next-best contender
widened from **15pp to 61pp**. Structured mass-weighted retrieval
scales with forest size; cosine top-k doesn't. The retrieval code
path (`core/expand.py`, `core/bella.py`) is unchanged between v0.0.2
and v0.0.4rc1 — every delta is a property of forest growth, not
algorithm changes.

See [benchmarks/README.md](https://github.com/immartian/bellamem/blob/main/benchmarks/README.md) for the versioning
convention and when to re-run.

---

## Limitations

Bella lives alongside the agent, not inside it. That boundary is
load-bearing and currently unmoved: we can't reach in and rewrite the
context window directly. What we have today is advisory:

- **No direct context-window control.** Bella can't swap active
  tokens, evict irrelevant context, or replace the window wholesale.
  The agent still controls what it attends to; Bella can only offer
  packs the agent can choose to read.
- **/compact stays LLM-driven.** Claude Code's native `/compact`
  writes a narrative summary via an LLM call. A `PreCompact` hook
  that lets Bella substitute a graph-backed compaction would unlock
  most of the remaining wins — and that hook surface does not exist
  in Claude Code today. We can't intercept it from outside.
- **Save/clear/resume is a manual pattern.** You run `/bellamem save`
  → `/clear` → `/bellamem resume` yourself. It works, but it's a
  human-in-the-loop ritual, not an autonomous context manager.
- **The edit guard is a tool-call boundary, not a semantic gate.**
  `bellamem-guard` injects an advisory pack before every edit and
  `exit-2`s on a dispute re-suggestion, but it sees tool-call text,
  not model intent. An agent that ignores the advice can still try
  the edit; the block is at the boundary, not deeper in the model.
- **One adapter at a time.** Claude Code works today. Codex and
  others need their own turn-pair reaction classifier and source
  stamper.

The common thread: every limitation above is about how much of the
agent's context lifecycle we can observe and influence from outside.
With deeper hooks — or a coding agent that exposed its context as a
first-class API — a graph memory like Bella could drive the compaction
cycle itself instead of being handed the leftovers. **We expect the
upside of real context-window control to be substantial.** For now,
the honest frame is: Bella is the memory layer; the agent is still
the window manager.

---

## Status

**v0.1.0 — alpha, dogfooded on its own construction.** Bella was
built in Claude Code sessions that were themselves ingested into the
Bella being built. When the assistant drifted into an ad-hoc bandaid
pattern during development, the user's correction landed in the graph
as the highest-surprise belief of the session. That kind of
self-observation is the point.

Since v0.0.2:

- **v0.0.3** — per-project `.graph/`, automatic R3 consolidation on
  ingest, source grounding + narrative replay, structural pruning,
  `bellamem save` default-to-current-session with incremental ingest,
  and embed-cache prune bounded to live beliefs.
- **v0.0.4rc1** — storage split: belief embeddings moved out of
  `default.json` into a `default.emb.bin` sidecar (v3 format),
  cutting non-vector operations' load time from ~2s to ~500ms.
  `bellamem-guard` PreToolUse hook ships: advisory pack before
  every edit, exit-2 block on dispute re-suggestions. Embedder
  batching reduces save latency.
- **v0.1.0** — log-odds decay gated on `BELLAMEM_DECAY=on`: on every
  save, non-exempt beliefs fade exponentially toward the 0.5 prior at
  a 30-day half-life (reserved fields, `mass_floor` pins, ⊥ disputes,
  and ⇒ causes are exempt). New `bellamem decay` subcommand for
  dry-run preview + `--apply`. v3 → v4 snapshot format adds a
  `decayed_at` header. See the "Decay and reinforcement — the steady
  state" section of THEORY.md for the collision math.
- **v0.1.1 (planned)** — decay on by default after dogfood validates
  the steady state, Three.js 3D viz with temporal replay, and
  graph-backed compaction when the hook surface allows.

See [CHANGELOG.md](https://github.com/immartian/bellamem/blob/main/CHANGELOG.md) for details.

---

## Architecture at a glance

```
bellamem/
  core/
    gene.py           Belief + Gene + Jaynes accumulation + jumps + sources
    ops.py            the seven operations: CONFIRM, AMEND, ADD, DENY,
                      CAUSE, MERGE, MOVE (complete mutation API)
    bella.py          forest + routing + entity index
    embed.py          pluggable embedders (Hash/ST/OpenAI) + .env
    store.py          v3 split snapshot (graph JSON + embeddings.bin) + signature check
    expand.py         expand() + expand_before_edit() with freshness weight
    emerge.py         R3 consolidation — merge + rename
    audit.py          entropy signals: piles, glut, duplicates, limbo, names
    surprise.py       top Jaynes jumps + sign flips + dispute formations
    replay.py         chronological retrieval from source-grounded beliefs
  adapters/
    chat.py           voice-aware regex EW + turn-pair reaction classifier
    claude_code.py    .jsonl reader + system-noise filter + source stamping
    llm_ew.py         gpt-4o-mini CAUSE + self-observation + field naming
  guard.py            bellamem-guard PreToolUse hook entry point
  bench.py            5 contenders, 2 metrics, comparison table
  cli.py              save / expand / before-edit / audit / bench /
                      surprises / emerge / replay / render / prune
```

Full architecture doc: [ARCHITECTURE.md](https://github.com/immartian/bellamem/blob/main/ARCHITECTURE.md).

**Architectural invariant**: `bellamem.core` never imports from
`bellamem.adapters`. Core is domain-agnostic; adapters are where
domain knowledge lives. This lets the same core run on news, personal
knowledge, or support tickets — anything that accumulates evidence.
When a core function needs an LLM-backed refinement (e.g. field naming
when contrastive analysis can't tell two fields apart), the refinement
is passed in as a callback from the CLI — not imported into core.

---

## Theory

The "why" behind Jaynes log-odds, the Shannon-entropy framing of the
audit signals, the Recursive Emergence mapping, and a worked
flaky-test example with before/after diagrams all live in
**[THEORY.md](https://github.com/immartian/bellamem/blob/main/THEORY.md)**. If you want to understand the design
rather than just use the CLI, start there.

---

## Contributing

See [CONTRIBUTING.md](https://github.com/immartian/bellamem/blob/main/CONTRIBUTING.md). Short version:

- The bench is the CI. Run `bellamem bench` after changes to EW,
  expand, or audit and report the delta in the PR.
- Add new embedders by implementing the `Embedder` protocol in
  `core/embed.py`.
- Add new EW logic in `adapters/`, never in `core/`.
- Every PR that touches retrieval should include a bench item
  demonstrating the failure mode it fixes.
- Dogfood changes against Bella's own snapshot before shipping.
  Unit tests prove code runs; running Bella against its own graph
  proves the feature is useful.

---

## License

MIT. See [LICENSE](https://github.com/immartian/bellamem/blob/main/LICENSE).
