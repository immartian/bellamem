import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { userConfigEnvPath } from "./env.js";

export const SLASH_COMMAND_TEMPLATE = `---
description: BellaMem memory — resume | save | recall <topic> | why <topic> | replay | audit
allowed-tools: Bash(bellamem:*)
---

# /bellamem $ARGUMENTS

!\`bellamem $ARGUMENTS\`

<bellamem-instructions>

The output above was produced by the \`$0\` subcommand of BellaMem (or
\`resume\` if no subcommand was given). Respond according to which
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

**If \`$0\` is \`resume\` or empty**: Synthesize in under 300 words.
Lead with what's been touched THIS session or in the last day —
that's the active work the user needs to pick up. Mention long-term
invariants and decisions only when they directly constrain the
active work, or when 3-5 load-bearing ones give essential context.
Do NOT enumerate every section; compress aggressively.

Structure:
(1) **Where we are.** One paragraph: the active thread the user is
    mid-stream on. Use the TOP items from invariants/decisions/
    ephemerals that were touched recently. If the recent work has a
    clear focus (a specific Spiral, feature, bug), lead with that.
(2) **What just mattered.** 2-3 sentences on the freshest disputes,
    retractions, or high-delta mass moves. Skip if nothing recent.
(3) **Still open.** 3-5 items, not 15. Pick what the user likely
    picks up next, not the full ephemeral backlog.
(4) **(optional) Load-bearing background.** 1-2 sentences max, only
    if a stable invariant is directly in play for the active work.

Do not repeat the output verbatim; compose. Resist bullet-point dump
mode — the user wants a NARRATIVE of where they are.

**If \`$0\` is \`save\`**: Report how many new beliefs were added vs merged
by auto-emerge, whether the audit stayed clean (no new entropy
signals), and the top surprises from the new material. Flag anything
worth attention before closing the session.

**If \`$0\` is \`recall\`**: Give a concise answer about \`"$ARGUMENTS"\`
using the expand output above. Include the mass-ranked beliefs, any
⊥ disputes touching the topic, and whether it's a decided matter or
still open. If the graph doesn't have enough on the topic, say so.

**If \`$0\` is \`why\`**: Trace the causal chain from the walker output —
what the focus is caused by (⇒ edges), what it causes, which
invariants a fix must respect (the invariant × metaphysical / normative
sections), and which approaches have already been rejected (⊥ disputes
and retract edges). If the CAUSE edges are fragmented, say so honestly —
don't invent a chain that isn't in the graph.

**If \`$0\` is \`evidence\`**: The output is a provenance chain — every turn
that cited the concept, with the EXACT file path and line number in
the session jsonl. ALWAYS preserve the \`file:line\` references in your
synthesis — they are the point of this command. Format each citation as:
  turn #N [speaker] at file:line — what was said (one sentence summary)
  mass X → Y (+delta)
The user wants to JUMP TO these locations. Do not summarize away the
line numbers.

**If \`$0\` is \`replay\` or \`audit\`**: The raw output above is sufficient.
Comment briefly only if something jumps out (a new entropy signal, an
unexpected line, a belief that contradicts your working understanding).

**If \`$0\` is \`help\` or unknown**: The output above is the usage message
or an error — relay it and wait for a valid subcommand.

</bellamem-instructions>
`;

export const ENV_TEMPLATE = `# bellamem user config — keys resolved in this order (first wins):
#   1. shell env
#   2. project .env (cwd)
#   3. this file
#
# Uncomment and fill to enable ingest + embeddings.

# OPENAI_API_KEY=sk-...

# Optional model overrides — defaults are gpt-4o-mini + text-embedding-3-small.
# OPENAI_MODEL=gpt-4o-mini
# EMBED_MODEL=text-embedding-3-small
`;

export interface InstallResult {
  slashCommandPath: string;
  slashCommandWritten: boolean;
  legacyRemoved: boolean;
  envPath: string;
  envWritten: boolean;
}

export async function runInstall(opts: { force?: boolean } = {}): Promise<InstallResult> {
  const cmdDir = join(homedir(), ".claude", "commands");
  const slashPath = join(cmdDir, "bella.md");
  const legacyPath = join(cmdDir, "bellamem.md");
  const envPath = userConfigEnvPath();

  mkdirSync(cmdDir, { recursive: true });

  const slashExists = existsSync(slashPath);
  let slashWritten = false;
  if (!slashExists || opts.force) {
    writeFileSync(slashPath, SLASH_COMMAND_TEMPLATE, "utf8");
    slashWritten = true;
  }

  // Retire /bellamem — delete the legacy command if present.
  let legacyRemoved = false;
  if (existsSync(legacyPath)) {
    const { unlinkSync } = await import("node:fs");
    unlinkSync(legacyPath);
    legacyRemoved = true;
  }

  const envExists = existsSync(envPath);
  let envWritten = false;
  if (!envExists) {
    mkdirSync(dirname(envPath), { recursive: true });
    writeFileSync(envPath, ENV_TEMPLATE, "utf8");
    envWritten = true;
  }

  return { slashCommandPath: slashPath, slashCommandWritten: slashWritten, legacyRemoved, envPath, envWritten };
}
