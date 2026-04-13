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

    # Exactly one belief (the last-extracted claim from the assistant
    # turn) earns a second voice. The rest stay single-voice.
    assert multi == 1, (
        f"expected exactly 1 multi-voice belief after 'ya' ratification "
        f"of the last claim; got {multi} (and {single} single-voice)"
    )
    # There should still be some single-voice beliefs — the earlier
    # assistant claims that weren't ratified plus the user's question
    # turn if it extracted anything.
    assert single >= 1, (
        "expected at least some single-voice beliefs in the forest"
    )
