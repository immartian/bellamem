"""Tests for core/decay.py — log-odds decay math and policy.

Builds small Bella forests, applies decay, checks the math, the policy
gate, and the end-to-end effect. No disk I/O — snapshot round-trips are
exercised in test_decay_store.py.
"""

from __future__ import annotations

import hashlib

from bellamem.core import Bella
from bellamem.core.bella import SELF_MODEL_FIELD
from bellamem.core.gene import Gene, Belief, REL_COUNTER, REL_CAUSE
from bellamem.core.decay import (
    SECONDS_PER_DAY,
    apply_decay,
    decay_factor,
    is_decay_exempt,
)


def _mk_belief(desc: str, *, log_odds: float = 2.0,
               mass_floor: float = 0.0, rel: str = "→") -> Belief:
    bid = hashlib.md5(desc.encode()).hexdigest()[:12]
    b = Belief(id=bid, desc=desc, rel=rel)
    b.log_odds = log_odds
    b.mass_floor = mass_floor
    return b


# ---------------------------------------------------------------------------
# decay_factor — the math
# ---------------------------------------------------------------------------

def test_decay_factor_one_half_life_halves():
    f = decay_factor(30 * SECONDS_PER_DAY, 30.0)
    assert abs(f - 0.5) < 1e-9


def test_decay_factor_two_half_lives_quarters():
    f = decay_factor(60 * SECONDS_PER_DAY, 30.0)
    assert abs(f - 0.25) < 1e-9


def test_decay_factor_zero_dt_is_identity():
    assert decay_factor(0.0, 30.0) == 1.0


def test_decay_factor_negative_dt_is_identity():
    # Clock skew: caller's `now() - decayed_at` can go negative if the
    # system clock jumped backwards. Return no-op, don't raise.
    assert decay_factor(-1.0, 30.0) == 1.0


def test_decay_factor_zero_half_life_is_identity():
    # Kill-switch for the pipeline — callers pass 0 to bypass decay.
    assert decay_factor(30 * SECONDS_PER_DAY, 0.0) == 1.0


def test_decay_factor_monotone_in_dt():
    f1 = decay_factor(1 * SECONDS_PER_DAY, 30.0)
    f2 = decay_factor(7 * SECONDS_PER_DAY, 30.0)
    f3 = decay_factor(30 * SECONDS_PER_DAY, 30.0)
    assert 1.0 > f1 > f2 > f3 > 0.0


# ---------------------------------------------------------------------------
# is_decay_exempt — policy gate
# ---------------------------------------------------------------------------

def test_reserved_field_is_exempt():
    b = _mk_belief("self-obs")
    assert is_decay_exempt(SELF_MODEL_FIELD, b) is True


def test_pinned_belief_is_exempt():
    b = _mk_belief("pinned fact", mass_floor=0.6)
    assert is_decay_exempt("normal_field", b) is True


def test_plain_belief_is_not_exempt():
    b = _mk_belief("plain fact")
    assert is_decay_exempt("normal_field", b) is False


def test_dispute_mass_is_not_exempt():
    # Locked decision: ⊥ edges are structural, but the mass fades.
    b = _mk_belief("rejected approach", rel=REL_COUNTER)
    assert is_decay_exempt("normal_field", b) is False


def test_cause_mass_is_not_exempt():
    # Same rule for ⇒ — the edge stays, the confidence fades.
    b = _mk_belief("root cause", rel=REL_CAUSE)
    assert is_decay_exempt("normal_field", b) is False


# ---------------------------------------------------------------------------
# apply_decay — end-to-end on a small Bella
# ---------------------------------------------------------------------------

def _fresh_forest() -> Bella:
    """Forest with one belief per category, log_odds=2.0 on each."""
    b = Bella()

    g = Gene(name="normal")
    plain = _mk_belief("plain fact")
    pinned = _mk_belief("pinned fact", mass_floor=0.6)
    dispute = _mk_belief("rejected approach", rel=REL_COUNTER)
    g.beliefs[plain.id] = plain
    g.beliefs[pinned.id] = pinned
    g.beliefs[dispute.id] = dispute
    b.fields["normal"] = g

    gself = Gene(name=SELF_MODEL_FIELD)
    self_obs = _mk_belief("self obs")
    gself.beliefs[self_obs.id] = self_obs
    b.fields[SELF_MODEL_FIELD] = gself

    return b


def _plain_id() -> str:
    return _mk_belief("plain fact").id


def _dispute_id() -> str:
    return _mk_belief("rejected approach", rel=REL_COUNTER).id


def _pinned_id() -> str:
    return _mk_belief("pinned fact", mass_floor=0.6).id


def _self_obs_id() -> str:
    return _mk_belief("self obs").id


def test_apply_decay_halves_plain_beliefs_at_one_half_life():
    b = _fresh_forest()
    orig = b.fields["normal"].beliefs[_plain_id()].log_odds

    apply_decay(b, 30 * SECONDS_PER_DAY, 30.0)

    new = b.fields["normal"].beliefs[_plain_id()].log_odds
    assert abs(new - orig * 0.5) < 1e-9


def test_apply_decay_also_fades_disputes():
    # Locked: ⊥ mass fades even though the edge is preserved.
    b = _fresh_forest()
    orig = b.fields["normal"].beliefs[_dispute_id()].log_odds

    apply_decay(b, 30 * SECONDS_PER_DAY, 30.0)

    new = b.fields["normal"].beliefs[_dispute_id()].log_odds
    assert abs(new - orig * 0.5) < 1e-9


def test_apply_decay_leaves_pinned_alone():
    b = _fresh_forest()
    orig = b.fields["normal"].beliefs[_pinned_id()].log_odds

    apply_decay(b, 30 * SECONDS_PER_DAY, 30.0)

    new = b.fields["normal"].beliefs[_pinned_id()].log_odds
    assert new == orig


def test_apply_decay_leaves_reserved_field_alone():
    b = _fresh_forest()
    orig = b.fields[SELF_MODEL_FIELD].beliefs[_self_obs_id()].log_odds

    apply_decay(b, 30 * SECONDS_PER_DAY, 30.0)

    new = b.fields[SELF_MODEL_FIELD].beliefs[_self_obs_id()].log_odds
    assert new == orig


def test_apply_decay_report_counts():
    b = _fresh_forest()
    report = apply_decay(b, 30 * SECONDS_PER_DAY, 30.0)
    # 2 decayed (plain + dispute), 2 exempt (pinned + self_obs)
    assert report.decayed == 2
    assert report.exempt == 2
    assert report.dt_seconds == 30 * SECONDS_PER_DAY
    assert report.half_life_days == 30.0
    assert abs(report.factor - 0.5) < 1e-9


def test_apply_decay_zero_dt_is_noop():
    b = _fresh_forest()
    orig = b.fields["normal"].beliefs[_plain_id()].log_odds
    report = apply_decay(b, 0.0, 30.0)
    assert b.fields["normal"].beliefs[_plain_id()].log_odds == orig
    assert report.factor == 1.0
    assert report.quiet_fades == 0


def test_apply_decay_does_not_touch_jumps():
    """Locked invariant: jumps is for Jaynes dynamics, not silent slides."""
    b = _fresh_forest()
    plain = b.fields["normal"].beliefs[_plain_id()]
    jumps_before = list(plain.jumps)

    apply_decay(b, 30 * SECONDS_PER_DAY, 30.0)

    assert plain.jumps == jumps_before


def test_apply_decay_detects_quiet_fade():
    """Belief that decays from ratified (>=0.70) into limbo [0.48, 0.55]
    counts as one quiet_fade."""
    b = Bella()
    g = Gene(name="normal")
    # log_odds=1.0 → mass ≈ 0.731 (ratified)
    # After 3 half-lives → log_odds=0.125 → mass ≈ 0.531 (limbo)
    fading = _mk_belief("fading fact", log_odds=1.0)
    g.beliefs[fading.id] = fading
    b.fields["normal"] = g

    report = apply_decay(b, 90 * SECONDS_PER_DAY, 30.0)
    assert report.quiet_fades == 1
    assert report.decayed == 1


def test_apply_decay_does_not_flag_stable_ratified_as_fade():
    """A belief that stays ratified after decay isn't a quiet_fade."""
    b = Bella()
    g = Gene(name="normal")
    # log_odds=5.0 → mass ≈ 0.993. After 1 half-life → 2.5 → mass ≈ 0.924.
    stable = _mk_belief("still ratified", log_odds=5.0)
    g.beliefs[stable.id] = stable
    b.fields["normal"] = g

    report = apply_decay(b, 30 * SECONDS_PER_DAY, 30.0)
    assert report.quiet_fades == 0
    assert report.decayed == 1
