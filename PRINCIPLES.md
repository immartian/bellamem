# bellamem — the constitution

Stable rules. Break one only by editing this file explicitly.
The memory loads these into the high-mass layer of every context
pack. A claim that contradicts a principle generates a visible ⊥
edge against it. Drift is therefore never silent.

Structure:
- Section A merges the domain-agnostic engineering canon from
  `bellamem/principles/classic.md`. Each item is copied in by id
  so it is explicitly part of *this* project's constitution, not
  inherited invisibly.
- Section B is bellamem's own project-specific constitution.

If you edit a line here, you are making a deliberate change to
the rules the tool enforces against itself. Commit the diff with
a reason.


# A. Engineering canon (inherited from classic.md)

## Simplicity
- **C1 YAGNI** — do not build for hypothetical future requirements.
  Speculative abstractions are rot waiting to happen.
- **C2 KISS** — prefer the simplest design that could possibly work.
- **C3 DRY with caveat** — deduplicate only after three real repetitions.
  Three similar lines are cheaper than a premature abstraction.
- **C4 Work, right, fast — in that order.** Premature optimization
  kills clarity; premature generality kills motion.

## Responsibility and shape
- **C5 Single responsibility** — one module has exactly one reason to change.
- **C6 Composition over inheritance.**
- **C7 Small interfaces** — public surface area is a liability.
- **C8 Dependencies point inward** — high-level never depends on low-level.
- **C9 Separation of concerns** — no mixing of I/O, logic, and storage.

## Honesty and failure
- **C10 Fail loud** — errors surface immediately. Never swallow to
  make a symptom go away. Silence is the worst bug.
- **C11 Explicit over implicit** — no magic, no hidden side effects.
- **C12 Least astonishment** — behavior matches the name.
- **C13 Parse, don't validate** — transform at the boundary into a trusted shape.
- **C14 Boundaries validate, internals trust.**

## State and correctness
- **C15 Single source of truth** — one writer per piece of state.
- **C16 Immutable by default.**
- **C17 Make illegal states unrepresentable.**
- **C18 Pure core, side effects at the edge.**

## Change discipline
- **C19 Break forward** — no feature flags, no backwards-compat shims.
- **C20 Prefer editing existing files over creating new ones.**
- **C21 Small diffs.**
- **C22 Leave the campsite cleaner — but only along the path you walked.**


# B. bellamem's own constitution

## Architecture
- **P1** `bellamem.core` must never import from `bellamem.adapters`.
  The core is domain-agnostic; adapters are where domain knowledge lives.
- **P2** The **seven operations** — CONFIRM, AMEND, ADD, DENY, CAUSE,
  MERGE, MOVE — are the complete mutation API for the belief tree.
  No direct writes to `Gene` or `Belief`. Ever.
- **P3** Every write is a `Claim` carrying `voice` and `lr`.
  No silent writes, no unattributed beliefs.
- **P4** Domain-agnostic contract: `Claim`, `ingest`, `expand` are
  language-independent. No domain-specific types in core.
- **P5** CLI is a thin layer. All logic lives in `core/` or `adapters/`.
  The CLI parses arguments and prints results; nothing else.

## Dependencies and distribution
- **P6** Zero runtime dependencies in v0. Everything stdlib-only.
  Upgrades (sentence-transformers, sqlite, mcp) live behind
  `[project.optional-dependencies]` and must not be required.
- **P7** Python for v0, Rust for v1+. No speculative Rust in v0.
  No placeholder Rust files, no dual-stack hedging.
- **P8** Persistence must be atomic. Write via tmp+rename. A crash
  during save never corrupts a snapshot.

## Epistemics
- **P9** User is oracle; assistant is hypothesis. The `lr` of a claim
  reflects this asymmetry. User claims start at 1.8–2.5; assistant
  claims start at 1.05–1.3.
- **P10** Retroactive ratification: the user's reaction to the
  preceding assistant turn adjusts the lr of claims extracted from
  that turn. Affirmation promotes; correction denies.
- **P11** Same-voice repetition is attenuated (R1 voice attenuation,
  factor 0.1). Saying the same thing to yourself is not independent
  evidence.
- **P12** Entropy must not locally increase: every operation reduces H
  in its subtree (R2). The heal pass is the anti-drift pass.

## Anti-drift
- **P13** Principles are **immutable_mass**: they never decay below
  m=0.95 and are never pruned. The only way to change a principle is
  to edit this file.
- **P14** A claim that semantically contradicts a principle auto-generates
  a ⊥ edge against that principle. Contradictions are visible, not silent.
- **P15** No feature flags, no backwards-compat shims, no speculative
  abstractions. Break forward. (Extends C19, specialized to bellamem.)
- **P16** Prefer editing existing files over creating new ones.
  (Extends C20.)
- **P17** If a claim cannot be mapped to one of the seven ops, drop
  it. Silence is better than schema drift.
- **P18** Reserved field names start with `__` and have enforced rules:
  `__principles__` is immutable_mass; `__self__` is R4 self-observation
  only; domain adapters must not write to reserved fields directly.

## Reading the tree
- **P19** Context loads by mass, not by recency. Recency is a tiebreaker.
- **P20** DISPUTES are never discarded. Rejected approaches must stay
  visible so the agent never re-suggests them.
- **P21** Before any edit, load invariants + disputes + causes +
  entity bridges for the target entity. In `expand_before_edit` mode
  recency is actively harmful and is dropped from the budget.


# How this file gets enforced

1. `core/principles.py` parses this file on every `ingest-cc`.
   Each `- **P<n>** ...` or `- **C<n>** ...` line becomes a belief
   in the reserved `__principles__` field with m=0.98 and
   `immutable_mass=True`.
2. Belief ids are stable hashes, so re-seeding is idempotent.
3. The CLI command `bellamem audit` walks the tree and the source
   tree together, reporting: (a) claims contradicting a principle,
   (b) bandaid piles (R2 entropy signals), (c) drift candidates —
   high-mass beliefs added after a principle that partially contradict it.
4. Changing a principle means editing this file *and* running
   `bellamem audit` to see what breaks.

If you are an LLM reading this: you are not allowed to weaken a
principle to make a local task easier. If a principle is in your way,
surface the conflict and ask. The principles exist because their
absence was paid for.
