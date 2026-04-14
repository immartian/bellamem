"""Spec for phase 1 of session narrative parsing: within-speaker
retraction detection.

`classify_retraction(prev, curr, llm_ew)` is a thin wrapper that
delegates the relational judgment to the LLM EW. Retraction is
relational pragmatics — whether curr supersedes prev depends on both
turns together, not on any token in curr alone — so the wrapper
holds no English tokens or language-specific seeds. By design there
is no lexical fallback; if no extractor is provided the function
returns ``None``.

These tests use a mock extractor that returns canned answers for
specific (prev, curr) pairs. The pairs document what real retractions
(and real non-retractions) look like; the mock's answer is what a
correct LLM SHOULD return. The tests verify:

  1. The wrapper delegates to the extractor.
  2. The wrapper honors the extractor's judgment (both positive and
     negative).
  3. The wrapper's empty/None input handling.

Integration with a real LLM is exercised in live dogfood runs, not in
this unit-level spec.
"""
from __future__ import annotations

from bellamem.adapters.chat import classify_retraction


# ---------------------------------------------------------------------------
# Mock LLM extractor
# ---------------------------------------------------------------------------

class MockRetractionLLM:
    """Canned-answer stand-in for LLMExtractor.pick_retraction.

    Construct with a dict mapping (prev, curr) → response, where the
    response is either a retraction dict (to simulate a positive
    detection) or ``None`` (to simulate the LLM saying "not a
    retraction"). Also records all calls so tests can assert the
    wrapper actually delegated.
    """

    def __init__(self, canned: dict[tuple[str, str], dict | None]):
        self.canned = canned
        self.calls: list[tuple[str, str]] = []

    def pick_retraction(self, prev: str, curr: str) -> dict | None:
        self.calls.append((prev, curr))
        if (prev, curr) in self.canned:
            return self.canned[(prev, curr)]
        raise AssertionError(
            f"MockRetractionLLM was called with an un-canned pair:\n"
            f"  prev={prev!r}\n  curr={curr!r}"
        )


def _retract(confidence: str = "high") -> dict:
    return {"type": "retract", "target": "prior", "confidence": confidence}


# ---------------------------------------------------------------------------
# Positive cases — documented real retraction patterns
# ---------------------------------------------------------------------------

def test_wait_marker_real_session_sample():
    """Sample 5 from the 2026-04-11 session. Canonical motivating
    case: assistant plans to commit, next turn pauses to investigate
    side effects instead."""
    prev = "All three levels of the SVG now say Bella. Committing all three fixes together."
    curr = (
        "Wait — the regen had side-effects I need to understand before committing. "
        "`example.graph.json` lost 1842 lines and two new files appeared."
    )
    mock = MockRetractionLLM({(prev, curr): _retract("high")})
    result = classify_retraction(prev, curr, mock)
    assert result == _retract("high")
    assert mock.calls == [(prev, curr)]


def test_actually_reversal_with_justification():
    prev = "Going with the anchor-based cascade — it's the cleanest structural fit."
    curr = (
        "Actually, on reflection, anchors are still a hand-curated list. "
        "Let's do the LLM cascade instead."
    )
    mock = MockRetractionLLM({(prev, curr): _retract("high")})
    result = classify_retraction(prev, curr, mock)
    assert result == _retract("high")


def test_hmm_that_is_wrong_self_correction():
    prev = "The primary-claim scorer picks the last decision-marked sentence in the turn."
    curr = "Hmm, that's not right — it actually picks the HIGHEST-scoring one regardless of position."
    mock = MockRetractionLLM({(prev, curr): _retract("high")})
    assert classify_retraction(prev, curr, mock) == _retract("high")


def test_non_english_retraction():
    """Language-agnostic: French retraction. A regex-based
    implementation would miss this entirely; the LLM wrapper
    handles it by virtue of the underlying LLM's multilingual
    semantics."""
    prev = "Je vais livrer l'option A demain matin."
    curr = (
        "Attends — en y réfléchissant, l'option A laisse le problème "
        "de décroissance non résolu. Faisons plutôt l'option B."
    )
    mock = MockRetractionLLM({(prev, curr): _retract("high")})
    assert classify_retraction(prev, curr, mock) == _retract("high")


def test_medium_confidence_retraction_is_honored():
    """When the LLM flags an ambiguous reversal as medium confidence,
    the wrapper must preserve that signal rather than upgrade or
    drop it."""
    prev = "The filter is fine — shipping as-is."
    curr = "Wait, one more case I want to verify first."
    mock = MockRetractionLLM({(prev, curr): _retract("medium")})
    assert classify_retraction(prev, curr, mock) == _retract("medium")


# ---------------------------------------------------------------------------
# Negative cases — the LLM judges these as non-retractions and the
# wrapper must return None accordingly
# ---------------------------------------------------------------------------

def test_plain_continuation_is_not_retraction():
    prev = "I'll patch retry.py to add exponential backoff."
    curr = "Then I'll update bench.py to exercise the new retry path."
    mock = MockRetractionLLM({(prev, curr): None})
    assert classify_retraction(prev, curr, mock) is None


def test_actually_as_emphasis_is_not_retraction():
    """'Actually' used as an intensifier, not a reversal. A regex
    would false-positive; the LLM reads it in context and says no."""
    prev = "The centroid measures topic similarity."
    curr = "Actually, it's a really useful finding — now we know why C failed."
    mock = MockRetractionLLM({(prev, curr): None})
    assert classify_retraction(prev, curr, mock) is None


def test_wait_as_imperative_is_not_retraction():
    """'Wait' as a literal imperative pause ('wait for the build'),
    not a reversal marker."""
    prev = "Running the centroid check now."
    curr = "Wait for the OpenAI embedder to finish, then I'll report the numbers."
    mock = MockRetractionLLM({(prev, curr): None})
    assert classify_retraction(prev, curr, mock) is None


def test_topic_switch_is_not_retraction():
    """A topic switch is not a retraction of the prior topic."""
    prev = "The filter cascade is done and tested."
    curr = "Now, about the bench corpus — we should prune the four universal misses."
    mock = MockRetractionLLM({(prev, curr): None})
    assert classify_retraction(prev, curr, mock) is None


# ---------------------------------------------------------------------------
# Wrapper contract
# ---------------------------------------------------------------------------

def test_empty_prior_returns_none_without_calling_llm():
    mock = MockRetractionLLM(canned={})
    assert classify_retraction("", "Wait — that's wrong.", mock) is None
    assert mock.calls == []  # no LLM call spent on an empty pair


def test_empty_curr_returns_none_without_calling_llm():
    mock = MockRetractionLLM(canned={})
    assert classify_retraction("Committing now.", "", mock) is None
    assert mock.calls == []


def test_whitespace_only_is_treated_as_empty():
    mock = MockRetractionLLM(canned={})
    assert classify_retraction("   ", "Wait — that's wrong.", mock) is None
    assert classify_retraction("Committing now.", "\n\t ", mock) is None
    assert mock.calls == []


def test_no_llm_returns_none_by_design():
    """No lexical fallback. If no extractor is provided, the function
    returns None — the graph simply does not record retraction edges
    for that ingest. This is the structural guarantee against ad-hoc
    English regexes creeping back in under the name 'offline mode'."""
    prev = "Committing all three fixes together."
    curr = "Wait — the regen had side-effects."
    assert classify_retraction(prev, curr, None) is None
    assert classify_retraction(prev, curr) is None  # default llm_ew=None
