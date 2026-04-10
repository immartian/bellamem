"""A worked example — one coding session compressed into a graph.

This script is the source of truth for the README's worked example and
flat-vs-graph snippet. It:

  1. Builds a fresh Bella with the zero-dep hash embedder.
  2. Ingests the claims from a ~15-turn flaky-test debugging dialogue,
     using the public Python API (Bella.ingest with Claim) — the same
     path the Claude Code adapter uses for real transcripts.
  3. Renders docs/example-before.svg — the graph right after ingest.
  4. Ages every belief by 60 days (touching event_time / last_touched)
     so they're outside the default prune grace period.
  5. Runs emerge (merge duplicates) then prune --apply (remove residue).
  6. Renders docs/example-after.svg — the compressed graph.
  7. Prints a numbers table: belief counts, limbo size, dispute count,
     cause count, Shannon entropy of the normalized mass distribution.

Run as: python docs/example_session.py

The script is also imported by tests/test_example_session.py so the
example can't silently drift from code behavior. When LR values, prune
criteria, or emerge logic change, the test fails loudly.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bellamem.core import Bella, Claim, save as save_snapshot, load as load_snapshot
from bellamem.core.embed import HashEmbedder, set_embedder
from bellamem.core.emerge import emerge
from bellamem.core.prune import (
    PruneCriteria,
    apply_prune,
    identify_prune_candidates,
)
from bellamem.core.visualize import RenderOptions, to_dot


DAY = 86400.0


# ---------------------------------------------------------------------------
# The dialogue — one flaky-test debugging session
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    """One line of dialogue, translated into a Claim-producing action.

    kind:
      "add"     — plain assertion, new belief in its own field
      "deny"    — creates a ⊥ edge on the belief tagged `target`
      "cause"   — creates a ⇒ edge on the belief tagged `target`
      "confirm" — re-accumulates an existing belief (tagged by `target`)
                  from a different voice, so n_voices rises
      "self"    — lands in __self__ via relation="self_observation"

    tag: symbolic name so later turns can reference the same belief.
    """

    voice: str
    text: str
    kind: str = "add"
    tag: Optional[str] = None        # symbolic id for back-reference
    target: Optional[str] = None     # tag of the belief being denied/caused/confirmed
    lr: float = 1.5


DIALOGUE: list[Turn] = [
    Turn(voice="user",
         text="the integration test flaked again, third time this week",
         tag="symptom",
         lr=2.0),

    Turn(voice="assistant",
         text="i'll bump the sync timeout from 2s to 5s",
         tag="bandaid_proposal",
         lr=1.1),

    Turn(voice="user",
         text="that's a bandaid, not a fix — don't paper over it",
         kind="deny",
         target="bandaid_proposal",
         lr=2.0),

    Turn(voice="assistant",
         text="the sync retries an external api with 200ms backoff",
         tag="retry_observation",
         lr=1.1),

    Turn(voice="assistant",
         text="ci load spikes rate-limit us and the first retry also fails",
         tag="rate_limit_cause",
         kind="cause",
         target="symptom",
         lr=1.1),

    Turn(voice="assistant",
         text="we exceed the 2s window before the second retry completes",
         tag="window_exceeded",
         kind="cause",
         target="rate_limit_cause",
         lr=1.1),

    Turn(voice="user",
         text="so the real fix is retry jitter, not timeout bumping",
         tag="fix",
         lr=2.0),

    Turn(voice="assistant",
         text="so the real fix is retry jitter, not timeout bumping",
         kind="confirm",
         target="fix",
         lr=1.5),

    Turn(voice="assistant",
         text="i reach for timeout bumps when retry semantics are the real problem",
         kind="self",
         lr=1.3),

    Turn(voice="assistant",
         text="patched retry.py — exponential backoff, jitter plus or minus 50ms",
         tag="patch_applied",
         lr=1.1),

    Turn(voice="user",
         text="good",
         kind="confirm",
         target="fix",
         lr=2.0),

    Turn(voice="assistant",
         text="this is the third rate-limit incident on this path this quarter",
         tag="quota_observation",
         lr=1.05),

    Turn(voice="user",
         text="add it to the ticket pile",
         tag="ticket_noted",
         lr=1.2),
]


# ---------------------------------------------------------------------------
# Run the dialogue through Bella
# ---------------------------------------------------------------------------


def run_dialogue(bella: Bella) -> dict[str, tuple[str, str]]:
    """Ingest DIALOGUE into `bella`. Returns {tag: (field_name, belief_id)}.

    The symbolic `tag` registry lets later turns reference earlier beliefs
    by name instead of by guessing ids — which matters for deny/cause/confirm
    where the claim needs `target_hint`.
    """
    tags: dict[str, tuple[str, str]] = {}

    for turn in DIALOGUE:
        if turn.kind == "self":
            claim = Claim(
                text=turn.text,
                voice=turn.voice,
                lr=turn.lr,
                relation="self_observation",
            )
            result = bella.ingest(claim)
        elif turn.kind in ("deny", "cause"):
            if turn.target is None or turn.target not in tags:
                raise RuntimeError(
                    f"turn references unknown target {turn.target!r}: {turn.text!r}"
                )
            target_field, target_bid = tags[turn.target]
            claim = Claim(
                text=turn.text,
                voice=turn.voice,
                lr=turn.lr,
                relation=turn.kind,
                target_hint=target_bid,
                target_field=target_field,
            )
            result = bella.ingest(claim)
        elif turn.kind == "confirm":
            if turn.target is None or turn.target not in tags:
                raise RuntimeError(
                    f"turn references unknown target {turn.target!r}: {turn.text!r}"
                )
            target_field, target_bid = tags[turn.target]
            # Confirm by calling ops.confirm directly on the target belief.
            # The `turn.text` is the acknowledgment ("good") — that text
            # itself shouldn't become a belief; it should bump the target's
            # n_voices and log_odds. This mirrors the retroactive
            # ratification the real adapter does when a user turn says
            # "yes" / "good" / "right" about the preceding assistant turn.
            from bellamem.core import ops
            g = bella.fields[target_field]
            result = ops.confirm(g, target_bid, voice=turn.voice, lr=turn.lr)
            result.field = target_field
        else:  # "add"
            claim = Claim(text=turn.text, voice=turn.voice, lr=turn.lr)
            result = bella.ingest(claim)

        if turn.tag and result.belief is not None:
            tags[turn.tag] = (result.field, result.belief.id)

    return tags


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def mass_entropy_bits(bella: Bella) -> float:
    """Shannon entropy of the normalized mass distribution, in bits.

    Treat each belief's mass as an unnormalized probability, normalize to
    a distribution over beliefs, compute H = -sum(p log2 p). This is a
    loose metric — masses aren't formally probabilities — but it gives a
    defensible number for the 'how concentrated is the graph' claim.
    """
    masses = [
        b.mass
        for g in bella.fields.values()
        for b in g.beliefs.values()
    ]
    total = sum(masses)
    if total <= 0:
        return 0.0
    entropy = 0.0
    for m in masses:
        p = m / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


@dataclass
class Stats:
    beliefs: int
    fields: int
    single_voice_leaves: int
    multi_voice: int
    disputes: int
    causes: int
    self_observations: int
    limbo_count: int
    entropy_bits: float

    def render(self, label: str) -> str:
        return (
            f"{label:<10} "
            f"beliefs={self.beliefs}  "
            f"fields={self.fields}  "
            f"single-voice-leaves={self.single_voice_leaves}  "
            f"ratified={self.multi_voice}  "
            f"disputes={self.disputes}  "
            f"causes={self.causes}  "
            f"self-obs={self.self_observations}  "
            f"limbo={self.limbo_count}  "
            f"entropy={self.entropy_bits:.2f} bits"
        )


def measure(bella: Bella) -> Stats:
    from bellamem.core.gene import REL_CAUSE, REL_COUNTER
    from bellamem.core.bella import SELF_MODEL_FIELD

    beliefs = 0
    single_voice_leaves = 0
    multi_voice = 0
    disputes = 0
    causes = 0
    self_observations = 0
    limbo = 0

    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            beliefs += 1
            if b.rel == REL_COUNTER:
                disputes += 1
            if b.rel == REL_CAUSE:
                causes += 1
            if fname == SELF_MODEL_FIELD:
                self_observations += 1
            if b.n_voices == 1 and not b.children:
                single_voice_leaves += 1
            if b.n_voices >= 2:
                multi_voice += 1
            if 0.48 <= b.mass <= 0.55:
                limbo += 1

    return Stats(
        beliefs=beliefs,
        fields=len(bella.fields),
        single_voice_leaves=single_voice_leaves,
        multi_voice=multi_voice,
        disputes=disputes,
        causes=causes,
        self_observations=self_observations,
        limbo_count=limbo,
        entropy_bits=mass_entropy_bits(bella),
    )


# ---------------------------------------------------------------------------
# Aging + compression
# ---------------------------------------------------------------------------


def age_beliefs(bella: Bella, days: float) -> None:
    """Back-date every belief so prune's age checks pass."""
    delta = days * DAY
    now = time.time()
    for g in bella.fields.values():
        for b in g.beliefs.values():
            b.event_time = now - delta
            b.last_touched = now - delta


def compress(bella: Bella) -> tuple[int, int]:
    """Run emerge + prune --apply. Returns (merged_count, pruned_count)."""
    report = emerge(bella)
    merged = len(report.merges)
    prune_report = identify_prune_candidates(bella, PruneCriteria())
    pruned = apply_prune(bella, prune_report)
    return merged, pruned


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_svg(bella: Bella, out_path: Path, title: str) -> None:
    try:
        import graphviz  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "example_session needs the graphviz python package "
            "(pip install bellamem[viz]) to render SVGs"
        ) from e

    opts = RenderOptions(title=title)
    dot = to_dot(bella.fields, opts)
    src = graphviz.Source(dot, engine="neato")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src.render(
        filename=out_path.stem,
        directory=str(out_path.parent),
        format=out_path.suffix.lstrip("."),
        cleanup=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(out_dir: Path | None = None) -> None:
    out_dir = out_dir or Path(__file__).parent
    set_embedder(HashEmbedder())

    # --- phase 1: ingest the dialogue ----------------------------------------
    bella = Bella()
    run_dialogue(bella)
    before = measure(bella)
    print("=" * 70)
    print("phase 1 — after ingest")
    print("=" * 70)
    print(before.render("before"))
    print()

    render_svg(bella, out_dir / "example-before.svg",
               title="BellaMem — one session, ingested")

    # --- phase 2: age + compress ---------------------------------------------
    # Save, load fresh, age, compress, save again.  Using the full round-trip
    # so the compressed state is a real snapshot the README can point at.
    snap_path = out_dir / "example.graph.json"
    save_snapshot(bella, str(snap_path))

    bella2 = load_snapshot(str(snap_path))
    age_beliefs(bella2, days=60)
    merged, pruned = compress(bella2)
    save_snapshot(bella2, str(snap_path))

    after = measure(bella2)
    print("=" * 70)
    print("phase 2 — after aging + emerge + prune")
    print("=" * 70)
    print(f"merged: {merged}  pruned: {pruned}")
    print(after.render("after "))
    print()

    render_svg(bella2, out_dir / "example-after.svg",
               title="BellaMem — same session, thirty days on")

    # --- phase 3: the compression summary -----------------------------------
    print("=" * 70)
    print("compression")
    print("=" * 70)
    print(f"beliefs:              {before.beliefs} → {after.beliefs} "
          f"({100 * (before.beliefs - after.beliefs) / before.beliefs:.0f}% fewer)")
    print(f"single-voice leaves:  {before.single_voice_leaves} → {after.single_voice_leaves}")
    print(f"ratified decisions:   {before.multi_voice} → {after.multi_voice}  (preserved)")
    print(f"⊥ disputes:           {before.disputes} → {after.disputes}  (preserved)")
    print(f"⇒ cause edges:        {before.causes} → {after.causes}  (preserved)")
    print(f"__self__ observations:{before.self_observations} → {after.self_observations}  (preserved)")
    print(f"limbo (0.48–0.55):    {before.limbo_count} → {after.limbo_count}")
    print(f"mass entropy:         {before.entropy_bits:.2f} bits → {after.entropy_bits:.2f} bits")


if __name__ == "__main__":
    main()
