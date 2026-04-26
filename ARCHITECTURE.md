# bellamem architecture

This document is the map. Read it once and the rest of the repo is legible.

Bellamem is **local accumulating memory for LLM coding agents**. The
terminal CLI (`bellamem resume`, `save`, `ask`, `audit`, `replay`)
and the localhost web UI (`bellamem serve`) are both read/write
surfaces over one store: `.graph/v02.json`, a typed belief graph
with per-turn provenance.

The core claim: **every concept in the graph carries its sources,
and every mass value is the sigmoid of a sum of log-odds bumps, one
per citation.** Retrieval is therefore grounded — we can always point
at the exact turns that ratified a belief.

Theory lives in a separate repository:
[Recursive-Emergence/bella](https://github.com/Recursive-Emergence/bella).
Implementation spec for the current schema is
`docs/rewrite/v0.2-spec.md` — language-independent, ~1200 lines, the
contract every port must honor.

---

## Layout

```
packages/bellamem/            Node/TypeScript implementation (v0.3.0+)
  src/
    schema.ts                 Source · Concept · Edge · slugify · R1 mass
    graph.ts                  Graph container + indices + dedup + R5 sweep
    store.ts                  Atomic JSON load/save of .graph/v02.json
    walker.ts                 askText — relevance + 1-hop edge walk
    audit.ts                  5 health signals (density / structural /
                              floor / spread / orphans)
    resume.ts                 Typed structural summary (10 sections)
    replay.ts                 Chronological per-session view
    ingest.ts                 Streaming jsonl reader + per-turn classify
                              + applyClassification + ingestSession loop
    clients.ts                Embedder + TurnClassifier (OpenAI SDK)
                              with sha256-keyed disk caches
    trace.ts                  Session unroll + mass history replay
                              (powers the web trace view)
    server.ts                 Hono app + /api/* + SSE file-watcher
    cli.ts                    Commander dispatch for 7 CLI ops + serve
    env.ts / paths.ts         Env resolution, project root, Claude Code
                              project-dir encoding
    guard.ts                  PreToolUse hook body
  bin/
    bellamem.ts               CLI entry
    bellamem-guard.ts         Guard hook entry
  test/                       Vitest suites (51 tests)
  web/
    overview.html / graph.html / trace.html   UI shells
    static/{app.css, overview.js, graph.js, trace.js}   client code

docs/                         Plans, rewrite history, brand assets
  rewrite/
    v0.2-spec.md              Language-independent schema + ops contract
    v0.3-node-plan.md         Why we ported; phased rollout
    v0.3.1-web-ui-plan.md     Web UI scope + differentiator
```

---

## Invariants (don't break these)

1. **Byte-compatible `.graph/v02.json`.** Any reader/writer must
   produce a file byte-equivalent to the spec's schema. The
   `v0.2.0-ref` tag holds the Python reference implementation that
   the current Node port was validated against.

2. **Deterministic IDs.** `slugifyTopic` produces stable concept
   ids across runs, processes, languages. `Edge.id` is
   `sha256(type|source|target)[:16]`. `Source.id` is
   `{session_id}#{turn_idx}`.

3. **R1 mass is frozen.** `_logit` / `_sigmoid` with
   `MASS_DELTA_NEW_VOICE = 0.5`, `MASS_DELTA_REPEAT_VOICE = 0.1`,
   starting mass 0.5. Changing any of these breaks graph continuity
   across versions.

4. **Cache keys are frozen.** `PROMPT_VERSION = "v2"`. The classifier
   cache key is `sha256(PROMPT_VERSION || turn_text || sorted(context_ids) || recent_ids)`
   with `\x00` separators. Any prompt wording change requires bumping
   the version and invalidating the cache.

5. **Seven ops.** `resume`, `save`, `recall`, `why`, `ask`, `audit`,
   `replay` + `serve` (web UI). Nothing else is in the v0.2 contract.
   `save` ingests a session jsonl; the rest are read-only.

6. **Fail loud, don't cache errors.** Classifier errors return a
   synthetic `act=none` that is NEVER written to the cache. Learned
   the hard way when a missing `OPENAI_API_KEY` poisoned 637 turns
   as cached `none` responses.

---

## Data model, briefly

### Source — immutable turn pointer

```ts
{
  session_id: string           // first 8 chars of jsonl sessionId
  file_path: string
  speaker: "user" | "assistant"
  turn_idx: number             // 0-based among kept turns
  text_preview: string         // 200 chars persisted
  timestamp: number | null     // POSIX seconds
}
```
`id = "{session_id}#{turn_idx}"`.

### Concept — topic-keyed, dual-axis classified

```ts
{
  id: string                   // slugifyTopic(topic)
  topic: string
  class: "invariant" | "decision" | "observation" | "ephemeral"
  nature: "factual" | "normative" | "metaphysical"
  state: null | "open" | "consumed" | "retracted" | "stale"
  mass: number                 // starts 0.5, R1 accumulates
  voices: string[]             // ordered, deduped speakers
  source_refs: string[]        // ordered, deduped source ids
  first_voiced_at / last_touched_at: string | null
  parent: string | null        // tree spine (optional)
}
```

Class is the temporal profile, nature is the epistemic type. Only
ephemerals carry state — all other classes have `state=null`.

Mass is R1 applied at the concept: each `cite(source_id, speaker)`
call that introduces a new speaker bumps log-odds by +0.5; a repeat
speaker bumps by +0.1. Idempotent on duplicate source_id.

### Edge — first-class typed relationship

```ts
{
  id: string                   // sha256(type|source|target)[:16]
  type: "support" | "dispute" | "cause" | "elaborate" |
        "voice-cross" | "retract" |
        "consume-success" | "consume-failure"
  source: string               // source_id (turn) OR concept_id
  target: string               // always a concept_id
  established_at: string
  voices: string[]
  confidence: "low" | "medium" | "high"
}
```

Edge identity is `(type, source, target)`. Same semantic edge voiced
twice accumulates voices and bumps confidence (low→medium at 2
voices, →high at 3).

---

## Ingest flow

```
session jsonl                             (Claude Code)
   │  read_session_turns  (streaming line-by-line; honors --tail)
   ▼
turn stream   { sessionId, speaker, turn_idx, text, timestamp }
   │
   │  for each turn:
   │    1. if already ingested → skip (idempotent)
   │    2. assembleContext(graph, turn) →
   │         { nearest_k=8, open_ephemerals_in_session, recent_3 }
   │    3. classifier.classify(...)  // OpenAI, cached by sha256
   │    4. applyClassification(graph, turn, result)
   │         · addSource
   │         · for each cite: addEdge(turn → concept) + R1 cite +
   │           state transitions on ephemerals
   │         · for each create: embed topic → findSimilarConcept
   │           (DEDUP_COSINE=0.78) → addConcept + R1 cite
   │         · for each concept_edge: addEdge
   │
   ▼
.graph/v02.json   (atomic temp-then-rename write)
```

R5 (stale sweep) runs at the end of every ingest: open ephemerals
whose last_touched_at source is older than 7 days flip to stale.

---

## Read surfaces

- **`resume`** — 10-section typed layout, ranked by mass within
  invariants and by last_touched_at within ephemerals/decisions.
  This is the primary context-reconstitution entry point.
- **`ask` / `recall` / `why`** — walker.ts. Scoring is
  `max(substring, cosine) × mass`, followed by a 1-hop edge walk
  with dispute/retract/cause/support/elaborate sections.
- **`audit`** — 5 signals with frozen thresholds. Surfaces in
  resume as red flags (soft/hard only).
- **`replay`** — chronological per-session view. Session picker
  prefers max-timestamp over max-length.
- **`serve`** — Hono localhost web UI. Three views backed by
  `/api/*` JSON endpoints: **overview** (audit + histogram +
  class×nature grid + session panorama), **graph** (D3 force-
  directed with session coloring and concept drawer), **trace**
  (session picker + turn-by-turn scrubber with live R1 mass
  deltas — the provenance-visible differentiator).

---

## Running the dogfood loop

```bash
cd packages/bellamem
npm install
npm run build                              # tsc → dist/
node dist/bin/bellamem.js resume --graph ../../.graph/v02.json
node dist/bin/bellamem.js audit
node dist/bin/bellamem.js serve --graph ../../.graph/v02.json
npm test                                   # 51 vitest suites
```

Set `OPENAI_API_KEY` in your shell or the project `.env` to enable
ingest. Without it, read-side ops still work against an existing
graph; `save` will fail closed on the first LLM call.

## Python reference

The Python v0.2 implementation was frozen at tag `v0.2.0-ref` and
deleted from master after the Node port's diff harness confirmed
byte-for-byte equivalence on resume/audit/replay against the live
graph. To recover the reference: `git checkout v0.2.0-ref`.
