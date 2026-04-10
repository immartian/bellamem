"""Smoke test for docs/example_session.py.

The README's worked example cites specific numbers (belief counts,
disputes, causes, limbo, Shannon entropy) that come from running
example_session.py against the real BellaMem core. This test runs the
same script and asserts the numbers match so the example can't silently
drift when core behavior changes.

If this test fails, either (a) the example script needs updating to
match the new reality, or (b) the README needs updating to cite the new
numbers — depending on which moved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


_DOCS = Path(__file__).parent.parent / "docs"


@pytest.fixture(autouse=True)
def _docs_on_path():
    sys.path.insert(0, str(_DOCS))
    yield
    sys.path.remove(str(_DOCS))


def _fresh_example() -> tuple:
    """Run the dialogue once, return (bella, before_stats)."""
    from example_session import run_dialogue, measure
    from bellamem.core import Bella
    from bellamem.core.embed import HashEmbedder, set_embedder
    set_embedder(HashEmbedder())
    bella = Bella()
    run_dialogue(bella)
    return bella, measure(bella)


def _compressed_example(bella):
    from example_session import age_beliefs, compress, measure
    age_beliefs(bella, days=60)
    merged, pruned = compress(bella)
    return measure(bella), merged, pruned


# ---------------------------------------------------------------------------
# Before ingest: the expected shape of a freshly-ingested session
# ---------------------------------------------------------------------------


def test_before_state_has_expected_structure():
    """The dialogue should produce the full six-rule structure after ingest."""
    _bella, before = _fresh_example()
    # One ⊥ dispute — the 'bandaid, not a fix' denial of timeout bump
    assert before.disputes == 1
    # Two ⇒ causes — CI load → rate-limit, and rate-limit → 2s exceeded
    assert before.causes == 2
    # One __self__ observation — "I reach for timeout bumps when..."
    assert before.self_observations == 1
    # At least one ratified (multi-voice) belief — the retry-jitter fix
    assert before.multi_voice >= 1
    # Real residue is present pre-compression
    assert before.single_voice_leaves >= 3


def test_before_entropy_is_sane():
    """Entropy must be positive and reasonable for a ~10-belief graph."""
    _bella, before = _fresh_example()
    assert 2.0 < before.entropy_bits < 5.0


# ---------------------------------------------------------------------------
# After compression: what survives and what gets cut
# ---------------------------------------------------------------------------


def test_compression_preserves_all_structural_ties():
    """Disputes, causes, ratified decisions, and self-obs must all survive."""
    bella, before = _fresh_example()
    after, _merged, _pruned = _compressed_example(bella)
    assert after.disputes == before.disputes
    assert after.causes == before.causes
    assert after.self_observations == before.self_observations
    assert after.multi_voice == before.multi_voice


def test_compression_reduces_belief_count():
    """Prune must actually remove beliefs."""
    bella, before = _fresh_example()
    after, _merged, pruned = _compressed_example(bella)
    assert pruned > 0
    assert after.beliefs < before.beliefs
    # At least some of the residue must go — the dialogue has ~4 prunable
    # single-voice leaves by construction (retry observation, patch message,
    # quota observation, ticket note). If fewer than 3 are caught, something
    # is off.
    assert (before.beliefs - after.beliefs) >= 3


def test_compression_reduces_entropy():
    """The compressed graph should have strictly lower mass entropy."""
    bella, before = _fresh_example()
    after, _merged, _pruned = _compressed_example(bella)
    assert after.entropy_bits < before.entropy_bits


def test_compression_reduces_single_voice_leaves():
    bella, before = _fresh_example()
    after, _merged, _pruned = _compressed_example(bella)
    assert after.single_voice_leaves < before.single_voice_leaves
