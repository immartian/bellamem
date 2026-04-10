"""Replay — chronological belief retrieval from source-grounded beliefs.

The standard `expand()` returns beliefs ordered by mass and relevance.
`replay()` returns beliefs ordered by their source line numbers,
reconstructing a conversation's narrative flow from the graph.

This only works for beliefs that carry `sources` — i.e. beliefs
ingested after source grounding was added. Legacy beliefs (pre-source)
are invisible to replay. That's the right behavior: they have no
temporal position in any specific session.

Design:
- Default to the "latest session" — picked by the newest event_time
  across beliefs whose sources include a jsonl session key.
- If a focus is given, filter to beliefs with cosine ≥ min_cosine.
- Sort the remaining candidates by `min(line for line in sources if
  source points to this session)` — the earliest turn in which the
  belief was first heard.
- Render under a token budget, dropping oldest entries if the budget
  would otherwise be exceeded. (Tail-preserving: we keep recent turns
  because the recent tail is usually where "what was I doing?" lives.)

This command answers a different question than `expand`:
- `expand "X"` → what do we believe about X, ranked by importance?
- `replay "X"` → in what order did we talk about X this session?

Together they form a belief-and-narrative view of the memory.

Domain-agnostic: does not import from adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .embed import embed, cosine
from .gene import Belief, REL_SUPPORT, REL_COUNTER, REL_CAUSE
from .tokens import count_tokens

if TYPE_CHECKING:
    from .bella import Bella


# Default minimum cosine threshold when a focus is provided. Below this,
# the belief is considered unrelated to the focus and dropped. Picked
# to match the FIELD_MATCH threshold used in routing (0.25) — beliefs
# not related enough to route into the same field are probably also
# not related enough to show in a focused replay.
REPLAY_MIN_COSINE = 0.20


@dataclass
class ReplayEntry:
    field_name: str
    belief: Belief
    session_key: str
    line: int          # earliest line of this belief in this session
    cosine: float      # focus relevance, 0 if no focus


@dataclass
class ReplayResult:
    focus: Optional[str]
    session_key: Optional[str]
    budget_tokens: int
    entries: list[ReplayEntry] = field(default_factory=list)
    total_candidates: int = 0   # pre-budget count, for reporting

    def _render_entry(self, e: ReplayEntry) -> str:
        marker = {REL_SUPPORT: " ", REL_COUNTER: "⊥", REL_CAUSE: "⇒"}.get(
            e.belief.rel, " "
        )
        return (
            f"L{e.line:5d} {marker} "
            f"[{e.field_name[:22]} m={e.belief.mass:.2f} v={e.belief.n_voices}] "
            f"{e.belief.desc}"
        )

    def text(self) -> str:
        session_short = (
            self.session_key.split("/")[-1][:28] if self.session_key else "none"
        )
        focus_suffix = f" focus={self.focus!r}" if self.focus else ""
        header = (
            f"# bellamem replay (session={session_short}{focus_suffix}, "
            f"budget={self.budget_tokens}t)"
        )
        body_lines = [self._render_entry(e) for e in self.entries]
        return "\n".join([header, *body_lines])

    def used_tokens(self) -> int:
        return sum(count_tokens(self._render_entry(e)) for e in self.entries)


def _latest_session_key(bella: "Bella") -> Optional[str]:
    """Return the jsonl session key with the most recently created belief.

    Scans beliefs for the newest event_time and returns whichever jsonl
    session its sources point at. Ties broken by any valid match. Returns
    None if no beliefs have jsonl sources.
    """
    best_key: Optional[str] = None
    best_time = -1.0
    for g in bella.fields.values():
        for b in g.beliefs.values():
            if not b.sources:
                continue
            if b.event_time <= best_time:
                continue
            for src_key, _line in b.sources:
                if src_key.startswith("jsonl:"):
                    best_time = b.event_time
                    best_key = src_key
                    break
    return best_key


def replay(bella: "Bella", focus: Optional[str] = None,
           *, session: Optional[str] = None,
           since_line: Optional[int] = None,
           budget_tokens: int = 1500,
           min_cosine: float = REPLAY_MIN_COSINE) -> ReplayResult:
    """Return beliefs from a session in chronological (line-number) order.

    Args:
        bella: the forest
        focus: optional relevance filter; empty/None returns all beliefs
            from the session regardless of topic
        session: override the target session key; default picks the most
            recently-active jsonl session from bella's cursor state
        since_line: only include beliefs whose earliest line in this
            session is ≥ this value. Useful for "what happened after
            turn N?" queries.
        budget_tokens: soft cap on output size; tail-preserving — we
            drop the oldest entries if the budget would be exceeded
        min_cosine: minimum focus-relevance when focus is provided
    """
    session_key = session or _latest_session_key(bella)
    result = ReplayResult(focus=focus, session_key=session_key,
                           budget_tokens=budget_tokens)
    if session_key is None:
        return result

    q_emb = embed(focus) if focus else None

    # 1. Collect candidates
    candidates: list[ReplayEntry] = []
    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            if not b.sources:
                continue
            # Earliest line of this belief in the target session
            session_lines = [ln for (key, ln) in b.sources if key == session_key]
            if not session_lines:
                continue
            earliest = min(session_lines)
            if since_line is not None and earliest < since_line:
                continue
            sim = 0.0
            if q_emb is not None:
                if not b.embedding:
                    continue  # no way to measure relevance
                sim = cosine(q_emb, b.embedding)
                if sim < min_cosine:
                    continue
            candidates.append(ReplayEntry(
                field_name=fname, belief=b, session_key=session_key,
                line=earliest, cosine=sim,
            ))

    result.total_candidates = len(candidates)

    # 2. Sort chronologically (by line asc)
    candidates.sort(key=lambda e: e.line)

    # 3. Tail-preserving budget: iterate from most recent backward, keep
    #    what fits, then re-sort into chronological order for output.
    kept: list[ReplayEntry] = []
    used = 0
    for entry in reversed(candidates):
        cost = count_tokens(result._render_entry(entry)) + 1
        if used + cost > budget_tokens:
            # Tail-preserving: stop as soon as we can't fit more recent entries
            break
        kept.append(entry)
        used += cost
    kept.reverse()  # back to chronological
    result.entries = kept

    return result
