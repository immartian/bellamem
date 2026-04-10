"""Scrub — remove system-noise beliefs from an existing forest.

Historically, harness-injected meta-text like "[Request interrupted by
user for tool use]" and `<system-reminder>` blocks leaked through the
Claude Code adapter and became beliefs. The adapter now filters these
at ingest time, but existing snapshots still carry the rot.

`scrub()` is a one-shot migration that walks the forest and:

  1. Deletes beliefs whose desc matches one of the noise patterns
  2. Reparents orphaned children to their grandparent (or to root)
  3. Drops fields that become empty after the above
  4. Drops fields whose *names* are derived from noise sentinels
     (e.g. `request_interrupted_user`)

Returns a ScrubReport counting what was removed. Purely structural —
no embeddings change, no mass is touched, no ids are rewritten.

Design notes:
- Domain-agnostic: this module does not import from adapters. The
  noise patterns are general enough (bracketed sentinels, harness
  tag names) that they apply to any chat-style transcript source.
- The pattern list is intentionally narrow. A scrub should never
  delete a legitimate claim because someone used the word "reminder"
  in a sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bella import Bella
    from .gene import Gene


# Descriptions of beliefs that should never exist. These are strict:
# the whole desc must match the pattern (allowing for punctuation slop).
_NOISE_DESC_RE = re.compile(
    r"^\s*"
    r"(\[(request interrupted[^\]]*|"
    r"tool (?:execution|use)[^\]]*|"
    r"command output[^\]]*)\]"
    r"|"
    r"</?(system-reminder|local-command-stdout|local-command-stderr|"
    r"local-command-caveat|command-name|command-message|command-args|"
    r"user-prompt-submit-hook|function_calls|function_results|"
    r"bellamem-instructions)\b[^>]*/?>)"
    r"\s*$",
    re.IGNORECASE,
)

# NOTE: An earlier draft added a hand-written list of slash-command
# template signature phrases here so scrub could clean pre-existing
# pollution out of already-ingested graphs. That was an ad-hoc
# stoplist and the user correctly called it out — this project's own
# feedback memory (feedback_no_adhoc_stoplists.md) explicitly says
# stoplists are the exact bandaid pattern bellamem is built to
# detect. The structural fix is:
#   1. Template wraps instructions in <bellamem-instructions> tags
#      (bellamem/templates/bellamem.md)
#   2. Adapter's _NOISE_TAGS strips the tag at ingest time, same
#      mechanism as <system-reminder> and <local-command-stdout>
#   3. Scrub's _NOISE_DESC_RE above recognises the tag for existing
#      graphs that somehow have an orphaned tag belief
# Legacy pollution from pre-wrapper saves is a one-time artifact; it
# will be buried under new turns and fall out of the replay-tail
# budget, and it does not justify a hand-maintained phrase list.

# Field names derived from noise sentinels. If _field_name_from ever
# ran on a noise turn, it produced a name like "request_interrupted_user".
# Drop the entire field — the beliefs inside are by definition derived
# from the same noise turn.
_NOISE_FIELD_NAMES = frozenset([
    "request_interrupted_user",
    "request_interrupted",
    "tool_execution",
    "tool_use",
    "system_reminder",
    "command_name",
    "command_message",
])


@dataclass
class ScrubReport:
    beliefs_removed: int = 0
    fields_removed: list[str] = field(default_factory=list)
    sample_removed: list[tuple[str, str]] = field(default_factory=list)  # (field, desc)

    def render(self) -> str:
        lines = [
            "bellamem scrub",
            "=" * 64,
            f"beliefs removed: {self.beliefs_removed}",
            f"fields removed:  {len(self.fields_removed)}",
        ]
        if self.fields_removed:
            lines.append("")
            lines.append("## dropped fields")
            for fname in self.fields_removed:
                lines.append(f"  - {fname}")
        if self.sample_removed:
            lines.append("")
            lines.append("## sample removed beliefs")
            for fname, desc in self.sample_removed[:20]:
                lines.append(f"  [{fname[:24]}] {desc[:80]}")
        return "\n".join(lines)


def _is_noise_desc(desc: str) -> bool:
    return bool(_NOISE_DESC_RE.match(desc or ""))


def _remove_belief(g: "Gene", bid: str) -> None:
    """Remove a belief, reparenting its children to its own parent.

    Preserves mass-accumulated children even if their parent gets
    dropped — they become roots (or attach to the grandparent). We
    don't discard mass, only noise structure.
    """
    b = g.beliefs.get(bid)
    if b is None:
        return
    grandparent = b.parent
    # Detach from parent's children list / roots list
    if grandparent and grandparent in g.beliefs:
        g.beliefs[grandparent].children = [
            c for c in g.beliefs[grandparent].children if c != bid
        ]
    else:
        g.roots = [r for r in g.roots if r != bid]
    # Reparent children
    for cid in b.children:
        child = g.beliefs.get(cid)
        if child is None:
            continue
        child.parent = grandparent
        if grandparent and grandparent in g.beliefs:
            if cid not in g.beliefs[grandparent].children:
                g.beliefs[grandparent].children.append(cid)
        else:
            if cid not in g.roots:
                g.roots.append(cid)
    del g.beliefs[bid]


def scrub(bella: "Bella") -> ScrubReport:
    """Walk the forest, remove noise beliefs and noise-named fields."""
    report = ScrubReport()

    # 1. Drop entire fields whose names are derived from noise sentinels.
    for fname in list(bella.fields.keys()):
        if fname in _NOISE_FIELD_NAMES:
            g = bella.fields[fname]
            count = len(g.beliefs)
            report.beliefs_removed += count
            for b in list(g.beliefs.values())[:5]:
                report.sample_removed.append((fname, b.desc))
            del bella.fields[fname]
            report.fields_removed.append(fname)

    # 2. Within surviving fields, remove beliefs whose desc is pure noise.
    for fname, g in list(bella.fields.items()):
        to_remove = [bid for bid, b in g.beliefs.items() if _is_noise_desc(b.desc)]
        for bid in to_remove:
            b = g.beliefs.get(bid)
            if b is None:
                continue
            if len(report.sample_removed) < 20:
                report.sample_removed.append((fname, b.desc))
            _remove_belief(g, bid)
            report.beliefs_removed += 1

    # 3. Drop any field that became empty as a result.
    for fname in list(bella.fields.keys()):
        if not bella.fields[fname].beliefs:
            del bella.fields[fname]
            if fname not in report.fields_removed:
                report.fields_removed.append(fname)

    # Entity index is invalid after removing beliefs; force rebuild on next use.
    bella._entity_index = None
    return report
