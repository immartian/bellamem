"""Surprise — per-belief event scoring from the R1 jump log.

Each call to `Belief.accumulate()` appends a (timestamp, delta_log_odds,
voice) tuple to `Belief.jumps`. This module reads that log and derives
three kinds of signal:

  1. Jaynes step surprises
     For each jump, `surprise = |delta| * 4 * p * (1 - p)` where `p`
     is the belief's mass *immediately before* the jump. This is the
     Jaynes/Shannon intuition: strong evidence against a 50/50 prior
     is maximally surprising; piling confirmation on mass=0.95 is
     almost free. Quadratic penalty weights uncertainty correctly.

  2. Sign flips
     Jumps whose cumulative log_odds crosses zero. "We used to
     believe the opposite" is the canonical surprise — the belief
     passed through the indeterminate middle and landed on the
     other side.

  3. Dispute formations
     ⊥-edged beliefs created recently. These are the events where
     bellamem recorded "we just rejected an approach."

The functions here are pure reads over the forest. They do not
mutate anything. The CLI `bellamem surprises` renders the results.

Domain-agnostic: does not import from adapters. Expects Belief.jumps
to have been populated at accumulate time (gene.py:accumulate).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .gene import Belief, REL_COUNTER, mass_of

if TYPE_CHECKING:
    from .bella import Bella


@dataclass
class JumpSurprise:
    field_name: str
    belief: Belief
    timestamp: float
    delta: float
    prior_mass: float
    posterior_mass: float
    score: float
    voice: str


@dataclass
class SignFlip:
    field_name: str
    belief: Belief
    timestamp: float
    prior_mass: float     # before the flipping jump
    posterior_mass: float # after
    voice: str


@dataclass
class SurpriseReport:
    jump_surprises: list[JumpSurprise] = field(default_factory=list)
    sign_flips: list[SignFlip] = field(default_factory=list)
    recent_disputes: list[tuple[str, Belief]] = field(default_factory=list)
    total_jumps_scanned: int = 0


def _replay_jumps(b: Belief) -> list[tuple[float, float, float, float, str]]:
    """Replay stored jumps and yield (ts, prior_mass, posterior_mass, delta, voice).

    We reconstruct the pre-state by starting from `log_odds` and
    subtracting jumps in reverse. This avoids having to store pre-state
    on every jump (which would double the snapshot size).

    Returns the replay in chronological order — oldest jump first.
    """
    if not b.jumps:
        return []
    # Current log_odds is the end state. Walk backwards to reconstruct
    # log_odds just before the first jump, then walk forward producing
    # (prior, posterior) pairs.
    total_delta = sum(j[1] for j in b.jumps)
    lo_start = b.log_odds - total_delta
    result: list[tuple[float, float, float, float, str]] = []
    lo = lo_start
    for ts, delta, voice in b.jumps:
        prior = mass_of(lo)
        lo += delta
        posterior = mass_of(lo)
        result.append((ts, prior, posterior, delta, voice))
    return result


def score_surprise(delta: float, prior_mass: float) -> float:
    """Uncertainty-weighted surprise: |delta| * 4 * p * (1 - p).

    Peak contribution when prior_mass == 0.5. A strong piece of
    evidence against a belief we held at 0.5 is maximally surprising;
    the same evidence for a belief at 0.95 is almost discarded. The
    factor of 4 normalizes peak weight to 1.0 at p=0.5.
    """
    uncertainty = 4.0 * prior_mass * (1.0 - prior_mass)
    return abs(delta) * uncertainty


def compute_surprises(bella: "Bella", *, top_n: int = 10,
                      recent_window_seconds: float | None = None
                      ) -> SurpriseReport:
    """Walk the forest and compute all three surprise signals.

    If recent_window_seconds is given, only consider jumps more recent
    than (now - window). Defaults to None (all jumps in the log).
    """
    import time as _time

    report = SurpriseReport()
    now = _time.time()
    cutoff = (now - recent_window_seconds) if recent_window_seconds else None

    all_jumps: list[JumpSurprise] = []
    all_flips: list[SignFlip] = []

    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            if not b.jumps:
                continue
            replay = _replay_jumps(b)
            for ts, prior, posterior, delta, voice in replay:
                if cutoff is not None and ts < cutoff:
                    continue
                report.total_jumps_scanned += 1

                score = score_surprise(delta, prior)
                # Ignore trivially small contributions — the log is noisy
                # with small same-voice confirmations that score ~0.
                if score > 0.01:
                    all_jumps.append(JumpSurprise(
                        field_name=fname, belief=b, timestamp=ts,
                        delta=delta, prior_mass=prior, posterior_mass=posterior,
                        score=score, voice=voice,
                    ))

                # Sign flip: prior and posterior straddle 0.5
                if (prior < 0.5 <= posterior) or (posterior < 0.5 <= prior):
                    all_flips.append(SignFlip(
                        field_name=fname, belief=b, timestamp=ts,
                        prior_mass=prior, posterior_mass=posterior,
                        voice=voice,
                    ))

    all_jumps.sort(key=lambda j: j.score, reverse=True)
    report.jump_surprises = all_jumps[:top_n]

    all_flips.sort(key=lambda f: f.timestamp, reverse=True)
    report.sign_flips = all_flips[:top_n]

    # Recent disputes — ⊥-edged beliefs, ordered by last_touched desc
    disputes: list[tuple[str, Belief]] = []
    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            if b.rel == REL_COUNTER:
                if cutoff is not None and b.last_touched < cutoff:
                    continue
                disputes.append((fname, b))
    disputes.sort(key=lambda t: t[1].last_touched, reverse=True)
    report.recent_disputes = disputes[:top_n]

    return report


def render_surprise_report(r: SurpriseReport, *, max_per_section: int = 10) -> str:
    lines: list[str] = []
    lines.append("bellamem surprises")
    lines.append("=" * 64)
    lines.append(f"jumps scanned: {r.total_jumps_scanned}")
    lines.append("")

    lines.append("## top Jaynes step surprises "
                 "(|Δ| weighted by prior uncertainty)")
    if not r.jump_surprises:
        lines.append("  (none — no jumps above the noise floor)")
    else:
        for s in r.jump_surprises[:max_per_section]:
            lines.append(
                f"  score={s.score:.2f}  "
                f"Δ={s.delta:+.2f}  "
                f"{s.prior_mass:.2f}→{s.posterior_mass:.2f}  "
                f"[{s.field_name[:20]}]  {s.belief.desc[:70]}"
            )
    lines.append("")

    lines.append("## sign flips (belief crossed 0.5 — polarity change)")
    if not r.sign_flips:
        lines.append("  (none)")
    else:
        for f in r.sign_flips[:max_per_section]:
            direction = "↑" if f.posterior_mass > f.prior_mass else "↓"
            lines.append(
                f"  {direction}  {f.prior_mass:.2f}→{f.posterior_mass:.2f}  "
                f"[{f.field_name[:20]}]  {f.belief.desc[:80]}"
            )
    lines.append("")

    lines.append("## recent dispute formations (⊥ edges)")
    if not r.recent_disputes:
        lines.append("  (none)")
    else:
        for fname, b in r.recent_disputes[:max_per_section]:
            lines.append(
                f"  ⊥ m={b.mass:.2f}  [{fname[:20]}]  {b.desc[:80]}"
            )
    lines.append("")

    return "\n".join(lines)
