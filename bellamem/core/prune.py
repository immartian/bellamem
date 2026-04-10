"""R∅ prune — structural forgetting.

Removes beliefs that never earned a place in the graph: single-voice,
leaf (no children), base-mass (no Jaynes divergence from the prior), old
enough that the conversation has moved on, not ⊥-disputing anything,
and not ⇒-causing anything.

This is structural pruning, not decay. We do not touch `log_odds`; we
only remove beliefs that the rest of the graph has no stake in. By
construction, anything with children, anything that is itself a
dispute or cause, anything with multi-voice ratification, and
anything with a mass_floor is safe.

Complements R3 (emerge/merge) — emerge collapses duplicates, prune
removes orphans. Together they're the full "consolidation state"
story: beliefs enter raw, either earn their keep (by attracting
evidence or growing structure) or eventually age out.

Single-pass by design. Pruning a leaf can turn its parent into a leaf,
which might qualify on a subsequent run, so cascading is left to the
user: run `bellamem prune` again if the dry-run report is non-empty.

Domain-agnostic: this module does not import from adapters. Reserved
fields (`__self__` etc.) are skipped by default.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .gene import Belief, Gene, REL_SUPPORT

if TYPE_CHECKING:
    from .bella import Bella


# ---------------------------------------------------------------------------
# Criteria + report
# ---------------------------------------------------------------------------


@dataclass
class PruneCriteria:
    """All conditions for prune candidacy. A belief must satisfy ALL of these.

    Defaults are conservative — designed so a first-time user running
    `bellamem prune --apply` against their own graph does not delete
    anything meaningful. Tune with CLI flags once you've watched the
    dry-run output on a known-clean snapshot.
    """

    age_days: float = 30.0          # last_touched must be older than this
    grace_days: float = 14.0        # event_time must be older than this
    mass_low: float = 0.48          # inclusive lower bound of the "base mass" band
    mass_high: float = 0.55         # inclusive upper bound
    max_voices: int = 1             # at most this many distinct voices ratified it
    skip_reserved: bool = True      # skip __self__ and friends
    # Absolute floor — any belief with mass_floor > 0 is pinned by the
    # caller (historically PRINCIPLES.md, now just programmatic pins)
    # and must never be pruned regardless of the other checks.
    respect_mass_floor: bool = True


@dataclass
class PruneReport:
    """Result of identify_prune_candidates — what would be removed.

    `candidates` is the list of (field_name, belief) tuples ordered by
    ascending mass so `--top N` shows the weakest first. Counts are
    exact; sampling happens at render time.
    """

    candidates: list[tuple[str, Belief]] = field(default_factory=list)
    total_scanned: int = 0
    skipped_reserved: int = 0
    skipped_has_children: int = 0
    skipped_has_structural_role: int = 0
    skipped_multi_voice: int = 0
    skipped_mass_out_of_band: int = 0
    skipped_too_fresh: int = 0
    skipped_in_grace: int = 0
    skipped_mass_floor: int = 0

    @property
    def n_candidates(self) -> int:
        return len(self.candidates)

    def render(self, top: int = 10) -> str:
        lines: list[str] = []
        lines.append(f"scanned: {self.total_scanned} beliefs")
        lines.append(f"candidates: {self.n_candidates}")
        lines.append("")
        lines.append("filtered out:")
        lines.append(f"  in reserved field:       {self.skipped_reserved}")
        lines.append(f"  has children:            {self.skipped_has_children}")
        lines.append(f"  is dispute or cause:     {self.skipped_has_structural_role}")
        lines.append(f"  multi-voice:             {self.skipped_multi_voice}")
        lines.append(f"  mass outside band:       {self.skipped_mass_out_of_band}")
        lines.append(f"  touched too recently:    {self.skipped_too_fresh}")
        lines.append(f"  still in grace period:   {self.skipped_in_grace}")
        lines.append(f"  has mass_floor pin:      {self.skipped_mass_floor}")
        if not self.candidates:
            return "\n".join(lines)
        lines.append("")
        lines.append(f"top {min(top, self.n_candidates)} candidates (lowest mass first):")
        for fname, b in self.candidates[:top]:
            desc = (b.desc or "").replace("\n", " ")[:72]
            lines.append(
                f"  m={b.mass:.2f} v={b.n_voices}  [{fname[:20]}]  {desc}"
            )
        if self.n_candidates > top:
            lines.append(f"  … {self.n_candidates - top} more")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Identify
# ---------------------------------------------------------------------------


def identify_prune_candidates(
    bella: "Bella", criteria: PruneCriteria | None = None
) -> PruneReport:
    """Walk the forest and return a PruneReport of beliefs eligible for pruning.

    Does not mutate `bella`. The returned report is safe to inspect,
    render, or pass to `apply_prune`.
    """
    from .bella import is_reserved_field

    criteria = criteria or PruneCriteria()
    now = time.time()
    age_cutoff = criteria.age_days * 86400
    grace_cutoff = criteria.grace_days * 86400
    report = PruneReport()

    for field_name, g in bella.fields.items():
        if criteria.skip_reserved and is_reserved_field(field_name):
            report.skipped_reserved += len(g.beliefs)
            continue
        for b in g.beliefs.values():
            report.total_scanned += 1

            # Structural roles are always safe — a belief that denies or
            # causes another is part of the graph's load-bearing memory.
            if b.rel != REL_SUPPORT:
                report.skipped_has_structural_role += 1
                continue
            # A belief with descendants has structure grown below it.
            if b.children:
                report.skipped_has_children += 1
                continue
            # Multi-voice beliefs were ratified by multiple sources.
            if b.n_voices > criteria.max_voices:
                report.skipped_multi_voice += 1
                continue
            # A belief with a mass_floor was explicitly pinned.
            if criteria.respect_mass_floor and b.mass_floor > 0.0:
                report.skipped_mass_floor += 1
                continue
            # Mass must be in the "base" band — anything clearly above
            # or below 0.5 has real evidence on one side.
            m = b.mass
            if m < criteria.mass_low or m > criteria.mass_high:
                report.skipped_mass_out_of_band += 1
                continue
            # last_touched enforces "no recent reinforcement".
            if (now - b.last_touched) < age_cutoff:
                report.skipped_too_fresh += 1
                continue
            # event_time enforces a grace period for freshly-added beliefs
            # so a brand-new ingest cannot immediately be pruned.
            if (now - b.event_time) < grace_cutoff:
                report.skipped_in_grace += 1
                continue

            report.candidates.append((field_name, b))

    report.candidates.sort(key=lambda fb: fb[1].mass)
    return report


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_prune(bella: "Bella", report: PruneReport) -> int:
    """Mutate `bella` by removing every belief in `report.candidates`.

    Returns the number of beliefs actually removed. Silently skips
    anything that has been removed between identify and apply (e.g.
    by another process holding the snapshot).

    Safe against:
      - dangling `parent.children` references (updates parent entry)
      - dangling `Gene.roots` entries (removes from roots if the belief was one)
      - `bella.entity_index` (if present; rebuilt lazily by callers)
    """
    removed = 0
    for field_name, b in report.candidates:
        g = bella.fields.get(field_name)
        if g is None:
            continue
        if b.id not in g.beliefs:
            continue
        if b.parent and b.parent in g.beliefs:
            parent = g.beliefs[b.parent]
            if b.id in parent.children:
                parent.children.remove(b.id)
        if b.id in g.roots:
            g.roots.remove(b.id)
        del g.beliefs[b.id]
        removed += 1
    return removed
