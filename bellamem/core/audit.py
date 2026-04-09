"""bellamem audit — health report for the belief tree.

Read-only. Walks the forest and reports three things that matter
for context quality:

  1. Bandaid piles (R2 entropy signal)
     A belief with 3+ children whose descriptions contain
     recognisable fix / workaround / guard / special-case language.
     A pile of small patches around a single parent means the
     parent is a structural problem, not a series of isolated bugs.

  2. Top ratified decisions (multi-voice, non-reserved)
     What the user has explicitly affirmed across the session.
     Useful as a "what did we actually commit to?" summary.

  3. Disputes (⊥ edges) summary
     Rejected approaches preserved in the tree. These are what
     prevent re-suggestion of things the user corrected.

The audit mutates nothing. It's a report surface for inspecting
how much useful structure the tree has accumulated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .gene import Belief, REL_COUNTER

if TYPE_CHECKING:
    from .bella import Bella


BANDAID_MIN_CHILDREN = 3

_BANDAID_RE = re.compile(
    r"\b(fix|workaround|work around|guard|hack|bandaid|band-aid|"
    r"patch(ing)?|special[- ]case|edge case|kludge|monkey[- ]patch)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass
class BandaidPile:
    parent: Belief
    field_name: str
    children: list[Belief]


@dataclass
class AuditReport:
    fields: int
    beliefs: int
    multi_voice: int
    dispute_count: int
    bandaid_piles: list[BandaidPile] = field(default_factory=list)
    top_ratified: list[tuple[str, Belief]] = field(default_factory=list)
    top_disputes: list[tuple[str, Belief]] = field(default_factory=list)

    def is_clean(self) -> bool:
        # The audit is a report; "clean" means no bandaid piles.
        # Disputes are not errors — they're preserved corrections.
        return not self.bandaid_piles


# ---------------------------------------------------------------------------
# Core audit
# ---------------------------------------------------------------------------

def audit(bella: "Bella", *, top_n: int = 10) -> AuditReport:
    from .bella import is_reserved_field

    total = 0
    multi_voice = 0
    dispute_count = 0
    other_beliefs: list[tuple[str, Belief]] = []
    disputes: list[tuple[str, Belief]] = []

    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            total += 1
            if is_reserved_field(fname):
                continue
            other_beliefs.append((fname, b))
            if b.n_voices >= 2:
                multi_voice += 1
            if b.rel == REL_COUNTER:
                dispute_count += 1
                disputes.append((fname, b))

    report = AuditReport(
        fields=len(bella.fields),
        beliefs=total,
        multi_voice=multi_voice,
        dispute_count=dispute_count,
    )

    # 1) Bandaid piles
    for fname, g in bella.fields.items():
        if is_reserved_field(fname):
            continue
        for parent in g.beliefs.values():
            if len(parent.children) < BANDAID_MIN_CHILDREN:
                continue
            flagged: list[Belief] = []
            for cid in parent.children:
                c = g.beliefs.get(cid)
                if c and _BANDAID_RE.search(c.desc):
                    flagged.append(c)
            if len(flagged) >= BANDAID_MIN_CHILDREN:
                report.bandaid_piles.append(
                    BandaidPile(parent=parent, field_name=fname, children=flagged)
                )
    report.bandaid_piles.sort(key=lambda bp: len(bp.children), reverse=True)

    # 2) Top ratified (multi-voice, non-reserved)
    ratified = [(fname, b) for fname, b in other_beliefs if b.n_voices >= 2]
    ratified.sort(key=lambda t: t[1].mass, reverse=True)
    report.top_ratified = ratified[:top_n]

    # 3) Top disputes by mass
    disputes.sort(key=lambda t: t[1].mass, reverse=True)
    report.top_disputes = disputes[:top_n]

    return report


# ---------------------------------------------------------------------------
# Rendering — plain text, suitable for print
# ---------------------------------------------------------------------------

def render_report(r: AuditReport, *, max_per_section: int = 10) -> str:
    lines: list[str] = []
    lines.append("bellamem audit")
    lines.append("=" * 64)
    lines.append(f"fields:       {r.fields}")
    lines.append(f"beliefs:      {r.beliefs}")
    lines.append(f"multi-voice:  {r.multi_voice}")
    lines.append(f"disputes:     {r.dispute_count}")
    lines.append("")

    if r.is_clean():
        lines.append("clean. no bandaid piles detected.")
    else:
        lines.append(f"found {len(r.bandaid_piles)} bandaid pile(s)")
    lines.append("")

    # Bandaid piles
    lines.append("## bandaid piles (R2 entropy signal)")
    if not r.bandaid_piles:
        lines.append("  (none)")
    else:
        for bp in r.bandaid_piles[:max_per_section]:
            lines.append(f"  {len(bp.children)} patch-shaped children under "
                         f"[{bp.field_name}] {bp.parent.desc[:80]}")
            for c in bp.children[:5]:
                lines.append(f"      - {c.desc[:100]}")
    lines.append("")

    # Top ratified decisions
    lines.append("## top ratified decisions (multi-voice)")
    if not r.top_ratified:
        lines.append("  (none — no user affirmations registered)")
    else:
        for fname, b in r.top_ratified[:max_per_section]:
            lines.append(f"  m={b.mass:.2f} v={b.n_voices}  [{fname[:22]}]  {b.desc[:100]}")
    lines.append("")

    # Top disputes
    lines.append("## top disputes (⊥ edges — rejected approaches)")
    if not r.top_disputes:
        lines.append("  (none)")
    else:
        for fname, b in r.top_disputes[:max_per_section]:
            lines.append(f"  ⊥ m={b.mass:.2f} v={b.n_voices}  [{fname[:22]}]  {b.desc[:100]}")
    lines.append("")

    return "\n".join(lines)
