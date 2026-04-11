"""Log-odds decay — v0.1.0 forgetting via exponential regress to prior.

Bella's forgetting mechanism beyond structural `prune`: a belief's
`log_odds` exponentially regresses toward 0 (mass → 0.5) when it isn't
reinforced. "Use it or lose it" in Bayesian form.

Design decisions (locked 2026-04-10):

- **Formula.** `log_odds *= exp(-Δt / τ)` where `τ = half_life_days/ln(2)`.
  Max-entropy prior decay: zero is the prior log_odds, so mass regresses
  to 0.5 without picking an arbitrary target.

- **Application timing.** Batched on `bellamem save`, not on-read or
  continuous. Lazy decay would break `surprises` (which reads raw
  `jumps`) — its integrals wouldn't match the observed mass.

- **Wall-clock Δt.** Computed from `bella.decayed_at` to `now()`. A
  project paused for a month loses more mass than an active one — this
  is intentional: bellamem is a memory of what's still being exercised.

- **Exemptions.** Reserved fields (`__self__`) and pinned beliefs
  (`mass_floor > 0`). ⊥ disputes and ⇒ causes are NOT exempt at the
  mass level: the edges are structural history (queryable via replay),
  but their confidence fades if nobody is re-exercising the reasoning.

- **jumps untouched.** Decay does not append to `belief.jumps`. The
  jumps log is a record of evidence; decay is a silent drift, not
  evidence. `surprises` continues to score real Jaynes steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .bella import is_reserved_field
from .gene import mass_of

if TYPE_CHECKING:
    from .bella import Bella
    from .gene import Belief


DEFAULT_HALF_LIFE_DAYS = 30.0
SECONDS_PER_DAY = 86400.0
_LN2 = math.log(2.0)

# Quiet-fade thresholds: a belief that crossed from ratified into limbo
# via decay alone this pass is a signal the audit can surface so the user
# notices things fading before prune removes them.
QUIET_FADE_RATIFIED_MIN = 0.70
QUIET_FADE_LIMBO_LO = 0.48
QUIET_FADE_LIMBO_HI = 0.55


@dataclass
class DecayReport:
    """Summary of one decay pass."""
    decayed: int
    exempt: int
    quiet_fades: int
    factor: float
    dt_seconds: float
    half_life_days: float

    def brief(self) -> str:
        days = self.dt_seconds / SECONDS_PER_DAY
        return (
            f"decay: dt={days:.2f}d half_life={self.half_life_days:.1f}d "
            f"factor={self.factor:.4f} "
            f"decayed={self.decayed} exempt={self.exempt} "
            f"quiet_fades={self.quiet_fades}"
        )


def decay_factor(dt_seconds: float, half_life_days: float) -> float:
    """Return the multiplicative factor log_odds is scaled by.

    Edge cases — both resolve to 1.0 (no-op):
      - dt_seconds <= 0 (clock skew or first pass)
      - half_life_days <= 0 (kill switch for the pipeline)
    """
    if dt_seconds <= 0 or half_life_days <= 0:
        return 1.0
    tau_seconds = (half_life_days / _LN2) * SECONDS_PER_DAY
    return math.exp(-dt_seconds / tau_seconds)


def is_decay_exempt(field_name: str, belief: "Belief") -> bool:
    """Policy gate — True means the belief's log_odds must NOT be touched.

    Exempt:
      - reserved fields (`__self__` etc) — self-model stays stable
      - `mass_floor > 0` (pinned) — explicit elevation by the caller

    Not exempt (intentional):
      - ⊥ disputes and ⇒ causes — edges remain, mass fades
      - high-mass beliefs — mass alone isn't a shield; only pins are
    """
    if is_reserved_field(field_name):
        return True
    if belief.mass_floor > 0.0:
        return True
    return False


def apply_decay(bella: "Bella",
                dt_seconds: float,
                half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> DecayReport:
    """Scale log_odds on all non-exempt beliefs in place.

    Does NOT persist the snapshot and does NOT update `bella.decayed_at`
    — the caller is responsible for both (so unit tests can run the
    math without touching disk, and the save path can sequence decay
    + timestamp update + write as one transaction).
    """
    factor = decay_factor(dt_seconds, half_life_days)

    decayed = 0
    exempt = 0
    quiet_fades = 0

    for field_name, g in bella.fields.items():
        for belief in g.beliefs.values():
            if is_decay_exempt(field_name, belief):
                exempt += 1
                continue
            old_log_odds = belief.log_odds
            new_log_odds = old_log_odds * factor
            if new_log_odds != old_log_odds:
                old_mass = mass_of(old_log_odds)
                new_mass = mass_of(new_log_odds)
                belief.log_odds = new_log_odds
                if (old_mass >= QUIET_FADE_RATIFIED_MIN and
                        QUIET_FADE_LIMBO_LO <= new_mass <= QUIET_FADE_LIMBO_HI):
                    quiet_fades += 1
            decayed += 1

    return DecayReport(
        decayed=decayed,
        exempt=exempt,
        quiet_fades=quiet_fades,
        factor=factor,
        dt_seconds=dt_seconds,
        half_life_days=half_life_days,
    )
