"""Tests for core/prune.py — structural forgetting.

Each test builds a small Bella forest with a known configuration and
checks that `identify_prune_candidates` picks the right beliefs. Uses
synthetic timestamps rather than waiting for real time to pass, by
mutating `event_time` / `last_touched` directly on the Belief objects.
"""

from __future__ import annotations

import time

from bellamem.core import Bella, Claim
from bellamem.core.bella import SELF_MODEL_FIELD
from bellamem.core.embed import HashEmbedder, set_embedder
from bellamem.core.gene import Gene, Belief, REL_COUNTER
from bellamem.core.prune import (
    PruneCriteria,
    apply_prune,
    identify_prune_candidates,
)


DAY = 86400.0


def _fresh() -> Bella:
    set_embedder(HashEmbedder())
    return Bella()


def _age_belief(b: Belief, age_days: float, grace_days: float) -> None:
    """Back-date a belief so it looks older than the real clock."""
    now = time.time()
    b.event_time = now - grace_days * DAY
    b.last_touched = now - age_days * DAY


def _loose_criteria() -> PruneCriteria:
    """Criteria that permits aged beliefs — tests back-date explicitly."""
    return PruneCriteria(age_days=1.0, grace_days=1.0)


# ---------------------------------------------------------------------------
# Positive — these beliefs should be pruned
# ---------------------------------------------------------------------------


def test_aged_single_voice_leaf_is_a_candidate():
    b = _fresh()
    b.ingest(Claim(text="some ephemeral observation", voice="assistant", lr=1.05))
    # Grab the lone belief and age it out.
    g = next(iter(b.fields.values()))
    bel = next(iter(g.beliefs.values()))
    _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    assert report.n_candidates == 1
    assert report.candidates[0][1].id == bel.id


def test_apply_prune_removes_the_belief_and_updates_structures():
    b = _fresh()
    b.ingest(Claim(text="forgettable thought", voice="assistant", lr=1.05))
    g = next(iter(b.fields.values()))
    bel = next(iter(g.beliefs.values()))
    _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    removed = apply_prune(b, report)

    assert removed == 1
    assert bel.id not in g.beliefs
    assert bel.id not in g.roots


# ---------------------------------------------------------------------------
# Structural safety — these must NEVER be pruned
# ---------------------------------------------------------------------------


def test_belief_with_children_is_safe():
    """A belief with any child has grown structure; keep it."""
    b = _fresh()
    b.ingest(Claim(text="parent idea", voice="assistant", lr=1.05))
    g = next(iter(b.fields.values()))
    parent_bid = next(iter(g.beliefs))
    # Add a child under the same field.
    g.add(desc="refinement on parent idea", parent=parent_bid, voice="assistant", lr=1.05)
    # Age both.
    for bel in g.beliefs.values():
        _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    # Only the childless leaf should be a candidate — the parent with a
    # child must be kept no matter how old and unratified it is.
    parent_in_candidates = any(bel.id == parent_bid for _, bel in report.candidates)
    assert not parent_in_candidates
    assert report.skipped_has_children >= 1


def test_dispute_belief_is_safe_even_as_leaf():
    """A belief with rel=⊥ is a rejected-approach marker; never prune."""
    b = _fresh()
    b.ingest(Claim(text="the proposed design", voice="assistant", lr=1.05))
    g = next(iter(b.fields.values()))
    parent_bid = next(iter(g.beliefs))
    dispute = g.deny(parent_bid, desc="this design breaks under rotation", voice="user", lr=1.5)
    assert dispute is not None
    # Age the dispute out. It's a leaf but it's a ⊥ — must be kept.
    _age_belief(dispute, age_days=60, grace_days=30)
    for bel in g.beliefs.values():
        _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    dispute_in_candidates = any(bel.id == dispute.id for _, bel in report.candidates)
    assert not dispute_in_candidates
    assert report.skipped_has_structural_role >= 1


def test_cause_belief_is_safe_even_as_leaf():
    """A belief with rel=⇒ is a cause edge; never prune."""
    b = _fresh()
    b.ingest(Claim(text="the observed effect", voice="assistant", lr=1.05))
    g = next(iter(b.fields.values()))
    effect_bid = next(iter(g.beliefs))
    cause = g.cause(effect_bid, desc="because of the precondition", voice="user", lr=1.5)
    assert cause is not None
    for bel in g.beliefs.values():
        _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    cause_in_candidates = any(bel.id == cause.id for _, bel in report.candidates)
    assert not cause_in_candidates


def test_multi_voice_belief_is_safe():
    """A belief with two distinct voices has been ratified; keep it."""
    b = _fresh()
    b.ingest(Claim(text="a shared decision", voice="assistant", lr=1.05))
    # Re-ingest the same text with a different voice to bump n_voices.
    b.ingest(Claim(text="a shared decision", voice="user", lr=1.8))
    g = next(iter(b.fields.values()))
    bel = next(iter(g.beliefs.values()))
    _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    assert report.n_candidates == 0
    assert report.skipped_multi_voice >= 1


def test_reserved_field_is_skipped():
    """__self__ and friends are system-owned; never touch them."""
    b = _fresh()
    # Put a belief directly into the self-model field.
    b.fields[SELF_MODEL_FIELD] = Gene(name=SELF_MODEL_FIELD)
    g = b.fields[SELF_MODEL_FIELD]
    bel = g.add(desc="a self-observation", voice="assistant", lr=1.05)
    _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    assert report.n_candidates == 0
    assert report.skipped_reserved >= 1


def test_mass_floor_pin_is_safe():
    """A belief with mass_floor > 0 was pinned by the caller; keep it."""
    b = _fresh()
    b.ingest(Claim(text="pinned belief", voice="assistant", lr=1.05))
    g = next(iter(b.fields.values()))
    bel = next(iter(g.beliefs.values()))
    bel.mass_floor = 0.9
    _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    assert report.n_candidates == 0
    assert report.skipped_mass_floor >= 1


# ---------------------------------------------------------------------------
# Temporal safety
# ---------------------------------------------------------------------------


def test_fresh_belief_is_skipped_as_too_recent():
    b = _fresh()
    b.ingest(Claim(text="fresh thought", voice="assistant", lr=1.05))
    # Do NOT age this one. It's <1s old.
    report = identify_prune_candidates(b, _loose_criteria())
    assert report.n_candidates == 0
    assert report.skipped_too_fresh >= 1 or report.skipped_in_grace >= 1


def test_grace_period_protects_new_belief():
    """A belief older than age_days but younger than grace_days is safe."""
    b = _fresh()
    b.ingest(Claim(text="grace period belief", voice="assistant", lr=1.05))
    g = next(iter(b.fields.values()))
    bel = next(iter(g.beliefs.values()))
    # last_touched is old (age cutoff passes) but event_time is new (still in grace).
    now = time.time()
    bel.last_touched = now - 60 * DAY
    bel.event_time = now - 3 * DAY  # created 3 days ago — still in grace

    criteria = PruneCriteria(age_days=30, grace_days=14)
    report = identify_prune_candidates(b, criteria)
    assert report.n_candidates == 0
    assert report.skipped_in_grace >= 1


# ---------------------------------------------------------------------------
# Mass band
# ---------------------------------------------------------------------------


def test_high_mass_belief_is_skipped():
    """A single-voice but high-mass belief (pushed via repeated same-voice
    accumulation) stays because it's outside the base-mass band."""
    b = _fresh()
    b.ingest(Claim(text="strong claim", voice="assistant", lr=5.0))
    g = next(iter(b.fields.values()))
    bel = next(iter(g.beliefs.values()))
    # Re-accumulate with the same voice so n_voices stays at 1. Same-voice
    # evidence is attenuated 10x so we need many repeats to push mass up.
    for _ in range(40):
        bel.accumulate(lr=5.0, voice="assistant")
    assert bel.n_voices == 1
    assert bel.mass > 0.6, f"expected mass above band, got {bel.mass}"
    _age_belief(bel, age_days=60, grace_days=30)

    report = identify_prune_candidates(b, _loose_criteria())
    assert report.n_candidates == 0
    assert report.skipped_mass_out_of_band >= 1
