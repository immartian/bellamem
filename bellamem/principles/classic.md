# Classic engineering canon

Domain-agnostic principles that ship with bellamem as a default corpus.
A project opts in by quoting lines from this file into its own
PRINCIPLES.md. Each line is a belief candidate; the loader keeps the
short id so contradictions can be pinned to a specific principle.

These are not negotiable decoration. They load into the high-mass layer
of every EXPAND pack and an agent that contradicts one generates a
visible ⊥ edge against it.

## Simplicity

C1  YAGNI — do not build for hypothetical future requirements.
    Speculative abstractions are rot waiting to happen.
C2  KISS — prefer the simplest design that could possibly work.
    Clever is expensive; simple is free.
C3  DRY, with caveat — deduplicate only after three real repetitions.
    Three similar lines are cheaper than a premature abstraction.
C4  Make it work, make it right, make it fast — strictly in that order.
    Premature optimization kills clarity; premature generality kills motion.

## Responsibility and shape

C5  Single responsibility — one module has exactly one reason to change.
    If two reasons to change live in one file, split it.
C6  Composition over inheritance — small pieces wired together beat
    tall hierarchies. Inheritance forces coupling you did not ask for.
C7  Small interfaces — public surface area is a liability.
    Fewer exported names means fewer places for drift to hide.
C8  Dependencies point inward — high-level modules never depend on
    low-level modules. Abstract contracts never depend on concrete ones.
C9  Separation of concerns — mixing layers (I/O, logic, storage) in a
    single function makes every layer harder to change.

## Honesty and failure

C10 Fail loud — errors surface immediately. Never swallow an exception
    to make a symptom go away. Silence is the worst bug.
C11 Explicit over implicit — no magic, no hidden side effects, no
    action-at-a-distance. If it matters, name it.
C12 Principle of least astonishment — behavior matches the name.
    If the user must read the source to know what happens, the name is wrong.
C13 Parse, don't validate — transform untrusted input into a trusted
    shape once, at the boundary. Downstream code trusts its inputs.
C14 Boundaries validate, internals trust — only system edges (user
    input, external APIs, storage) check invariants. Internals assume them.

## State and correctness

C15 Single source of truth — one writer per piece of state. If two
    places can update the same thing, they will diverge.
C16 Immutable by default — mutation is opt-in, not the default.
    Immutable data is cheaper to reason about than any clever lock.
C17 Make illegal states unrepresentable — encode invariants in the
    shape of the data, not in runtime checks.
C18 Pure functions at the core, side effects at the edge — a pure
    core can be tested; a side-effecting core cannot.

## Change discipline

C19 Break forward — no feature flags, no backwards-compat shims, no
    "just in case" deprecations. Fix callers, not the callee.
C20 Prefer editing existing files over creating new ones — file
    bloat is cumulative drift.
C21 Small diffs — a change that touches twenty files is harder to
    review than twenty changes that touch one file each.
C22 Leave the campsite cleaner — but only along the path you
    walked. Do not refactor surrounding code you did not need to touch.
