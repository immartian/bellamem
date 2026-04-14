"""v0.2 replay — chronological timeline of a session's turns.

Complements `resume_text` (mass-ranked structural summary) and
`ask_text` (relevance + edge walk). Replay is the time-ordered view:
walk `graph.sources` for a session in turn order, show each turn
with the concepts it cited and any edges it established.

This is the v0.2 replacement for `bellamem.core.replay` (flat
snapshot). Same question — "what was said, in what order" — over
the live v0.2 store instead of a frozen default.json.
"""
from __future__ import annotations

from typing import Optional

from bellamem.proto.graph import Graph
from bellamem.proto.schema import Source


def _pick_session(graph: Graph, session: Optional[str]) -> Optional[str]:
    """Resolve the session to replay.

    If `session` is set, use it verbatim. Otherwise pick the session
    with the most recent max(timestamp) — "what am I in right now"
    in wall-clock terms. Sessions without any timestamped sources
    fall back to max(turn_idx) so legacy graphs still resolve, but
    any session that does carry timestamps wins over a timestamp-
    less one regardless of turn count.

    The earlier "longest session wins" heuristic surfaced yesterday's
    800-turn session over today's 60-turn session — bad UX. Sources
    carry Source.timestamp since the R1/timestamp work landed, so
    wall-clock is the honest picker.
    """
    if session:
        return session if any(
            s.session_id == session for s in graph.sources.values()
        ) else None
    by_session_ts: dict[str, float] = {}
    by_session_idx: dict[str, int] = {}
    for s in graph.sources.values():
        if s.timestamp is not None:
            cur_ts = by_session_ts.get(s.session_id, float("-inf"))
            if s.timestamp > cur_ts:
                by_session_ts[s.session_id] = s.timestamp
        cur_idx = by_session_idx.get(s.session_id, -1)
        if s.turn_idx > cur_idx:
            by_session_idx[s.session_id] = s.turn_idx
    if by_session_ts:
        return max(by_session_ts.items(), key=lambda kv: kv[1])[0]
    if by_session_idx:
        return max(by_session_idx.items(), key=lambda kv: kv[1])[0]
    return None


def _concepts_for_turn(
    graph: Graph, source_id: str
) -> list[tuple[str, str]]:
    """Concepts that cite this source, as (class_slug, topic) pairs."""
    out: list[tuple[str, str]] = []
    for c in graph.concepts.values():
        if source_id in c.source_refs:
            tag = f"{c.class_[:3]}/{c.nature[:3]}"
            out.append((tag, c.topic))
    return out


def replay_text(
    graph: Graph,
    *,
    session: Optional[str] = None,
    since_turn: int = 0,
    max_lines: int = 120,
    preview_chars: int = 140,
) -> str:
    """Render a chronological turn-by-turn view of a session.

    Args:
        graph: loaded v0.2 graph
        session: session_id to replay (default: most-recent-activity session)
        since_turn: skip turns with turn_idx < this (default: 0)
        max_lines: tail-preserve at most this many turn lines
        preview_chars: truncate each turn's text to this many chars
    """
    if not graph.sources:
        return "# v0.2 replay\n  empty graph — run `bellamem save` first"

    sid = _pick_session(graph, session)
    if sid is None:
        return (
            f"# v0.2 replay\n"
            f"  session {session!r} not found. "
            f"Known sessions: {sorted({s.session_id for s in graph.sources.values()})}"
        )

    turns: list[Source] = sorted(
        (s for s in graph.sources.values() if s.session_id == sid),
        key=lambda s: s.turn_idx,
    )
    turns = [t for t in turns if t.turn_idx >= since_turn]

    out: list[str] = []
    out.append(f"# v0.2 replay (session: {sid})")
    out.append(
        f"  {len(turns)} turns · "
        f"{len(graph.concepts)} concepts · "
        f"{len(graph.edges)} edges"
    )
    if since_turn > 0:
        out.append(f"  since_turn: {since_turn}")
    out.append("")

    total = len(turns)
    if total > max_lines:
        # Tail-preserve: keep the most recent `max_lines` turns so the
        # replay ends at "now". Drop the head and leave a marker.
        dropped = total - max_lines
        turns = turns[-max_lines:]
        out.append(f"  (dropped {dropped} head turns — showing tail {max_lines})")
        out.append("")

    for s in turns:
        text = (s.text or "").replace("\n", " ").strip()
        if len(text) > preview_chars:
            text = text[: preview_chars - 1] + "…"
        out.append(f"#{s.turn_idx:4d} [{s.speaker:9}] {text}")
        cites = _concepts_for_turn(graph, s.id)
        for tag, topic in cites[:4]:
            out.append(f"          └─ {tag}  {topic}")
        if len(cites) > 4:
            out.append(f"          └─ … +{len(cites) - 4} more")

    out.append("")
    out.append(f"— {len(turns)}/{total} turns shown —")
    return "\n".join(out)
