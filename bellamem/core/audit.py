"""bellamem audit — drift, bandaid, and contradiction detection.

The memory cannot prevent violations on its own; it can only make
them visible. The audit walks the forest and surfaces three classes
of concern:

  1. Contradictions against principles
     For each principle in __principles__, find beliefs elsewhere in
     the tree whose embedding is close AND whose relation is DENY.
     A ⊥ belief near a principle is a direct conflict.

  2. Bandaid piles
     A belief with 3+ children whose descriptions contain recognisable
     fix / workaround / guard / special-case language. This is a local
     entropy signal: the presence of many small patches implies the
     parent is a structural problem, not a series of isolated bugs.

  3. Drift candidates
     High-mass (m ≥ 0.7) non-principle beliefs with at least two voices
     that touch a principle's topic (cosine > 0.3) but are not the
     principle itself. Worth eyeballing to see if a well-voiced claim
     is quietly replacing a principle.

The audit is read-only. It mutates nothing. Its job is to make the
drift visible so the user can act on it (edit a principle, DENY a
claim, refactor the affected module).

See PRINCIPLES.md P14 and P21 for the underlying contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .embed import cosine
from .gene import Belief, REL_COUNTER
from .principles import PRINCIPLES_FIELD

if TYPE_CHECKING:
    from .bella import Bella


# Similarity thresholds — tune with dogfooding
CONTRADICTION_SIM = 0.45    # belief→principle similarity to flag as contradiction
DRIFT_SIM = 0.50            # belief→principle similarity for drift check
DRIFT_MASS = 0.70           # minimum mass to qualify as a drift candidate
BANDAID_MIN_CHILDREN = 3    # min children for a bandaid pile

_BANDAID_RE = re.compile(
    r"\b(fix|workaround|work around|guard|hack|bandaid|band-aid|"
    r"patch(ing)?|special[- ]case|edge case|kludge|monkey[- ]patch)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass
class Contradiction:
    principle: Belief          # the principle being contradicted
    belief: Belief             # the contradicting belief
    field_name: str            # where the contradicting belief lives
    similarity: float


@dataclass
class BandaidPile:
    parent: Belief
    field_name: str
    children: list[Belief]     # the flagged children


@dataclass
class DriftCandidate:
    belief: Belief
    field_name: str
    nearest_principle: Belief
    similarity: float


@dataclass
class AuditReport:
    fields: int
    beliefs: int
    principles: int
    multi_voice: int
    contradictions: list[Contradiction] = field(default_factory=list)
    bandaid_piles: list[BandaidPile] = field(default_factory=list)
    drift_candidates: list[DriftCandidate] = field(default_factory=list)
    top_ratified: list[tuple[str, Belief]] = field(default_factory=list)

    def is_clean(self) -> bool:
        # drift_candidates are informational — claims near principles worth
        # an eyeball — not errors. Only contradictions and bandaid piles
        # count as actual audit failures.
        return not self.contradictions and not self.bandaid_piles


# ---------------------------------------------------------------------------
# Core audit
# ---------------------------------------------------------------------------

def audit(bella: "Bella", *, top_n: int = 10) -> AuditReport:
    principles_field = bella.fields.get(PRINCIPLES_FIELD)
    principles: list[Belief] = list(principles_field.beliefs.values()) if principles_field else []

    # Index non-principle beliefs with embeddings
    other_beliefs: list[tuple[str, Belief]] = []
    multi_voice = 0
    total = 0
    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            total += 1
            if fname == PRINCIPLES_FIELD:
                continue
            other_beliefs.append((fname, b))
            if b.n_voices >= 2:
                multi_voice += 1

    report = AuditReport(
        fields=len(bella.fields),
        beliefs=total,
        principles=len(principles),
        multi_voice=multi_voice,
    )

    # --- 1) Contradictions ------------------------------------------------
    for pr in principles:
        if not pr.embedding:
            continue
        for fname, b in other_beliefs:
            if b.rel != REL_COUNTER:
                continue
            if not b.embedding:
                continue
            s = cosine(pr.embedding, b.embedding)
            if s >= CONTRADICTION_SIM:
                report.contradictions.append(
                    Contradiction(principle=pr, belief=b, field_name=fname, similarity=s)
                )
    report.contradictions.sort(key=lambda c: c.similarity, reverse=True)

    # --- 2) Bandaid piles -------------------------------------------------
    for fname, g in bella.fields.items():
        if fname == PRINCIPLES_FIELD:
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

    # --- 3) Drift candidates ---------------------------------------------
    # High-mass, multi-voice conversation beliefs that sit on top of a
    # principle's topic but are worded differently. These are beliefs the
    # user has effectively ratified which the principle might be losing
    # authority to — worth eyeballing.
    for fname, b in other_beliefs:
        if b.mass < DRIFT_MASS or b.n_voices < 2 or not b.embedding:
            continue
        best_pr: Belief | None = None
        best_sim = 0.0
        for pr in principles:
            if not pr.embedding:
                continue
            s = cosine(b.embedding, pr.embedding)
            if s > best_sim:
                best_pr, best_sim = pr, s
        if best_pr and best_sim >= DRIFT_SIM:
            report.drift_candidates.append(
                DriftCandidate(belief=b, field_name=fname,
                               nearest_principle=best_pr, similarity=best_sim)
            )
    report.drift_candidates.sort(key=lambda d: (d.belief.mass, d.similarity), reverse=True)

    # --- 4) Top ratified beliefs (top-N multi-voice non-principles) ------
    ratified = [(fname, b) for fname, b in other_beliefs if b.n_voices >= 2]
    ratified.sort(key=lambda t: t[1].mass, reverse=True)
    report.top_ratified = ratified[:top_n]

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
    lines.append(f"principles:   {r.principles}")
    lines.append(f"multi-voice:  {r.multi_voice}")
    lines.append("")

    if r.is_clean():
        lines.append(f"clean. no contradictions or bandaid piles "
                     f"({len(r.drift_candidates)} claims near principles to eyeball).")
        lines.append("")
    else:
        lines.append(f"issues: {len(r.contradictions)} contradictions, "
                     f"{len(r.bandaid_piles)} bandaid piles "
                     f"({len(r.drift_candidates)} claims near principles to eyeball)")
        lines.append("")

    # Contradictions
    lines.append("## contradictions against principles")
    if not r.contradictions:
        lines.append("  (none)")
    else:
        for c in r.contradictions[:max_per_section]:
            pid = c.principle.entity_refs[0] if c.principle.entity_refs else "?"
            lines.append(f"  ⊥ {pid}  (sim={c.similarity:.2f}, from {c.field_name})")
            lines.append(f"      principle:  {c.principle.desc[:120]}")
            lines.append(f"      claim:      {c.belief.desc[:120]}")
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

    # Claims near principles — informational, not a failure
    lines.append("## claims near principles (eyeball for drift)")
    if not r.drift_candidates:
        lines.append("  (none)")
    else:
        for d in r.drift_candidates[:max_per_section]:
            pid = d.nearest_principle.entity_refs[0] if d.nearest_principle.entity_refs else "?"
            lines.append(f"  m={d.belief.mass:.2f} v={d.belief.n_voices}  "
                         f"near {pid} (sim={d.similarity:.2f})")
            lines.append(f"      claim:      {d.belief.desc[:120]}")
            lines.append(f"      principle:  {d.nearest_principle.desc[:100]}")
    lines.append("")

    # Top ratified
    lines.append("## top ratified decisions (multi-voice, non-principle)")
    if not r.top_ratified:
        lines.append("  (none — no user affirmations registered)")
    else:
        for fname, b in r.top_ratified[:max_per_section]:
            lines.append(f"  m={b.mass:.2f} v={b.n_voices}  [{fname[:22]}]  {b.desc[:100]}")
    lines.append("")

    return "\n".join(lines)
