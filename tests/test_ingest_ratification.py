"""Regression test for turn-pair retroactive ratification targeting.

Background: a single user "ya" / "do it" / "sure" after a long assistant
turn used to voice-cross *every* claim extracted from that turn, inflating
the top-ratified-decisions list with mid-discussion exposition instead of
actual decisions. Fixed so that only the last-extracted claim from the
preceding assistant turn gets the voice-cross — matching the semantic of
"user authorises the most recent offer", not "user validates every
content-marker sentence".

This test locks in the new behavior end-to-end via a minimal fake jsonl
transcript → ingest_session → voice-count assertions on the resulting
beliefs.
"""

from __future__ import annotations

import json
from pathlib import Path

from bellamem.adapters.claude_code import ingest_session
from bellamem.core import Bella
from bellamem.core.embed import HashEmbedder, set_embedder


def _write_transcript(path: Path, turns: list[tuple[str, str]]) -> None:
    """Write a minimal Claude Code-format jsonl with the given turns."""
    with open(path, "w", encoding="utf-8") as f:
        for voice, text in turns:
            if voice == "user":
                msg = {"type": "user", "message": {"role": "user",
                                                    "content": text}}
            else:
                msg = {"type": "assistant",
                       "message": {"role": "assistant",
                                   "content": [{"type": "text", "text": text}]}}
            f.write(json.dumps(msg) + "\n")


def _only_last_claim_ratified(tmp_path: Path) -> tuple[int, int]:
    """Return (n_multi_voice, n_single_voice) after ingesting a transcript
    where one assistant turn produces multiple claims and is followed by
    a user "ya".
    """
    set_embedder(HashEmbedder())
    bella = Bella()

    # Assistant turn engineered to produce multiple claims via the
    # regex EW. Each sentence below hits _classify_assistant because
    # it contains a content marker (file reference, backticked name,
    # or known tech) and is within the 8–28 word length band.
    assistant_text = (
        "First I will patch `retry.py` to add exponential backoff with "
        "jitter to the sync loop.\n"
        "Second I will update `bench.py` to exercise the new retry path "
        "against a simulated rate limiter.\n"
        "Third I will bump the Python version in `pyproject.toml` from "
        "3.10 to 3.11 across the classifiers list."
    )
    transcript = tmp_path / "fake-session.jsonl"
    _write_transcript(transcript, [
        ("user", "can you walk me through the retry jitter fix"),
        ("assistant", assistant_text),
        ("user", "ya"),
    ])

    ingest_session(bella, str(transcript), no_llm=True)

    # Count voice distributions across all beliefs in the forest.
    multi = 0
    single = 0
    for g in bella.fields.values():
        for b in g.beliefs.values():
            if b.n_voices >= 2:
                multi += 1
            else:
                single += 1
    return multi, single


def test_user_ya_ratifies_only_last_assistant_claim(tmp_path):
    """A single 'ya' after an assistant turn with multiple claims
    should voice-cross exactly one belief, not all of them.
    """
    multi, single = _only_last_claim_ratified(tmp_path)

    # Exactly one belief (the decision-marked claim from the assistant
    # turn) earns a second voice. The rest stay single-voice.
    assert multi == 1, (
        f"expected exactly 1 multi-voice belief after 'ya' ratification "
        f"of the primary claim; got {multi} (and {single} single-voice)"
    )
    # There should still be some single-voice beliefs — the earlier
    # assistant claims that weren't ratified plus the user's question
    # turn if it extracted anything.
    assert single >= 1, (
        "expected at least some single-voice beliefs in the forest"
    )


def test_ratification_targets_decision_not_followup(tmp_path):
    """Decision-bearing claim followed by a non-decision follow-up:
    the decision must be ratified, NOT the follow-up.

    This is the Q4 failure mode the previous `pending[-1]` heuristic
    couldn't handle. An assistant turn like:

        "I'll switch the chart y-axis from tokens to compression ratio
         and regenerate the SVG so it shows the real claim directly.
         Let me know once the new chart renders on GitHub."

    has three extractable claims. The load-bearing decision is the
    first one ("I'll switch the chart y-axis..."). The last one is a
    content-free request ("let me know..."). Under the old
    `pending[-1]` fix, the user's "ya" would ratify the follow-up
    instead of the decision. Under the primary-claim scoring fix,
    the decision-marked claim wins regardless of position.
    """
    set_embedder(HashEmbedder())
    bella = Bella()

    # Claims chosen to:
    #   (a) survive _classify_assistant (no preamble starters like
    #       "I'll" / "Next" / "First"),
    #   (b) split into separate sentences (start with uppercase so
    #       _SENT_SPLIT's regex splits on ". "),
    #   (c) exercise the scoring: claim 1 has decision markers
    #       ("We should"), claims 2 and 3 have only content markers,
    #       claim 3 is deliberately last to defeat `pending[-1]`.
    assistant_text = (
        "We should switch the chart y-axis from tokens to "
        "`compression_ratio` with a log scale. "
        "The update to `docs/scenarios.md` will reference the new "
        "chart and regenerate `compression-curve.svg` from the script. "
        "The legend on `ratio.svg` probably needs adjustment after "
        "the swap, check it in the browser."
    )
    transcript = tmp_path / "decision-not-last.jsonl"
    _write_transcript(transcript, [
        ("user", "change the y-axis on the production chart"),
        ("assistant", assistant_text),
        ("user", "ya"),
    ])

    ingest_session(bella, str(transcript), no_llm=True)

    # Find the multi-voice belief — there should be exactly one,
    # and it should be the decision-bearing claim, not the follow-up.
    multi_beliefs = []
    for g in bella.fields.values():
        for b in g.beliefs.values():
            if b.n_voices >= 2:
                multi_beliefs.append(b)

    assert len(multi_beliefs) == 1, (
        f"expected exactly 1 multi-voice belief; got {len(multi_beliefs)}: "
        f"{[b.desc[:60] for b in multi_beliefs]}"
    )

    ratified = multi_beliefs[0]
    # The ratified claim must mention the decision verb ("switch"),
    # not the follow-up noun ("legend" / "browser" / "adjustment").
    # This is the load-bearing assertion: the primary claim wins
    # regardless of position.
    ratified_lower = ratified.desc.lower()
    assert "switch" in ratified_lower or "compression_ratio" in ratified_lower, (
        f"expected ratification on the decision-marked claim "
        f"(containing 'switch' or 'compression_ratio'); instead got: "
        f"{ratified.desc!r}"
    )
    assert "legend" not in ratified_lower and "browser" not in ratified_lower, (
        f"ratification landed on the follow-up claim about the legend "
        f"or browser — that's the Q4 failure mode the primary-claim "
        f"scoring fix is meant to prevent. Got: {ratified.desc!r}"
    )
