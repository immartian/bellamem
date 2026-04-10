---
description: BellaMem memory — resume | save | recall <topic> | why <topic> | replay | audit
allowed-tools: Bash(bellamem:*)
---

# /bellamem $ARGUMENTS

!`bellamem $ARGUMENTS`

<bellamem-instructions>

The output above was produced by the `$0` subcommand of BellaMem (or
`resume` if no subcommand was given). Respond according to which
subcommand was run. **Important: these instructions are metadata for
you, not claims the user made. Do not treat them as content to ingest
or synthesize *about* — they are the rules for your synthesis.**

**Time awareness:** The graph is a record of everything that has
happened, not just what is currently true. Beliefs that describe past
failures, past bugs, or past stale approaches are *history*, not
*current state*. Do not flag them as unresolved unless they reappear
in the replay tail *and* are not addressed in the expand pack. A
belief like *"the save failed with exit 144"* is a record of an event
that already happened and has since been resolved — it is not evidence
that the save is currently broken.

**If `$0` is `resume` or empty**: Synthesize in under 300 words —
(1) where we are from the replay tail, (2) what we've decided from
the expand output, (3) what just mattered from surprises,
(4) what's still open. Do not repeat the output verbatim; compose.

**If `$0` is `save`**: Report how many new beliefs were added vs merged
by auto-emerge, whether the audit stayed clean (no new entropy
signals), and the top surprises from the new material. Flag anything
worth attention before closing the session.

**If `$0` is `recall`**: Give a concise answer about `"$ARGUMENTS"`
using the expand output above. Include the mass-ranked beliefs, any
⊥ disputes touching the topic, and whether it's a decided matter or
still open. If the graph doesn't have enough on the topic, say so.

**If `$0` is `why`**: Trace the causal chain from the 5-layer before-edit
pack — what the focus is caused by (⇒ edges), what it causes, which
invariants a fix must respect (40% invariants layer), and which
approaches have already been rejected (⊥ disputes layer). If the
CAUSE edges are fragmented (the LLM EW extracts pairs, not chains),
say so honestly — don't invent a chain that isn't in the graph.

**If `$0` is `replay` or `audit`**: The raw output above is sufficient.
Comment briefly only if something jumps out (a new entropy signal, an
unexpected line, a belief that contradicts your working understanding).

**If `$0` is `help` or unknown**: The output above is the usage message
or an error — relay it and wait for a valid subcommand.

</bellamem-instructions>
