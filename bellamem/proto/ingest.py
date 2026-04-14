"""Session ingest: read jsonl → per-turn classify → apply to graph.

Public entry point is `ingest_session(graph, jsonl_path, ...)`. The
per-turn application logic lives in `apply_classification` and is
pure over (Graph, Source, ClassifyResult) so it's trivially testable.

Runnable as `python -m bellamem.proto.ingest [snapshot_path]` for
dogfooding against a session snapshot.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from bellamem.proto.clients import (
    ClassifyResult, Embedder, TurnClassifier,
)
from bellamem.proto.graph import Graph
from bellamem.proto.schema import (
    Concept, ConceptClass, ConceptNature, Edge, Source, slugify_topic,
)
from bellamem.proto.store import load_graph, save_graph


CONTEXT_K = 8
RECENT_TURN_N = 3
MAX_TURN_CHARS = 1500


# ---------------------------------------------------------------------------
# jsonl reading
# ---------------------------------------------------------------------------

def _extract_turn_text(msg: dict) -> str:
    c = msg.get("message", {}).get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for item in c:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return ""


def _parse_timestamp(raw: Optional[str]) -> Optional[float]:
    """Parse a claude-code ISO-8601 timestamp string to a float
    (POSIX seconds). Returns None on any failure — Source.timestamp
    is optional so downstream code tolerates missing values.

    Claude-code format: '2026-04-11T17:03:33.105Z' — trailing 'Z'
    means UTC but Python's fromisoformat rejects the Z prior to 3.11
    in some cases. Normalize to '+00:00' for broad compatibility.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        norm = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        return datetime.fromisoformat(norm).timestamp()
    except Exception:
        return None


def _derive_session_id(jsonl_path: Path, records: list[dict]) -> str:
    """Stable session_id for cross-run idempotency.

    Prefers the `sessionId` field from the jsonl records themselves
    (first 8 chars of the UUID) — this way a snapshot copy of the
    same conversation produces the same session_id as the original,
    which is what makes incremental ingest skip work across runs.

    Falls back to the filename stem (first 8 chars) for non-
    claude-code sources that don't carry a sessionId.
    """
    for rec in records:
        sid = rec.get("sessionId")
        if isinstance(sid, str) and sid:
            return sid[:8]
    return jsonl_path.stem[:8]


def read_session_turns(jsonl_path: Path) -> list[Source]:
    """Read a claude-code jsonl session and return Sources in order.

    Skips tool-use notifications and bracketed system messages.
    Each turn gets a 0-based turn_idx counted only among accepted
    speaker turns (not raw file line numbers). session_id is
    derived from jsonl contents (stable across filename changes).
    """
    lines = jsonl_path.read_text().splitlines()
    records: list[dict] = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except Exception:
            continue

    session_id = _derive_session_id(jsonl_path, records)

    turns: list[Source] = []
    idx = 0
    for rec in records:
        t = rec.get("type")
        if t not in ("user", "assistant"):
            continue
        text = _extract_turn_text(rec).strip()
        if not text:
            continue
        # Skip bracketed system messages (tool-use notifications etc.)
        if text.startswith("<") and ">" in text[:120]:
            continue
        turns.append(Source(
            session_id=session_id,
            file_path=str(jsonl_path),
            speaker=t,
            turn_idx=idx,
            text=text[:MAX_TURN_CHARS],
            timestamp=_parse_timestamp(rec.get("timestamp")),
        ))
        idx += 1
    return turns


# ---------------------------------------------------------------------------
# Context assembly + formatting
# ---------------------------------------------------------------------------

def _format_concepts(concepts: list[Concept]) -> str:
    if not concepts:
        return "(none)"
    return "\n".join(
        f'- id="{c.id}" topic="{c.topic}" class={c.class_} nature={c.nature}'
        + (f" state={c.state}" if c.state else "")
        for c in concepts
    )


def _format_turns(turns: list[Source]) -> str:
    if not turns:
        return "(none)"
    lines = []
    for t in turns:
        snippet = t.text[:300].replace("\n", " ")
        if len(t.text) > 300:
            snippet += " …"
        lines.append(f"T{t.turn_idx} [{t.speaker}]: {snippet}")
    return "\n".join(lines)


def assemble_context(
    graph: Graph,
    turn: Source,
    recent: list[Source],
    embedder: Embedder,
) -> tuple[list[Concept], list[Concept], list[Source]]:
    """Return (nearest, open_ephemerals_in_session, recent_turns)
    for the classifier prompt. All three lists are bounded and cheap."""
    if graph.concepts:
        turn_emb = embedder.embed(turn.text[:600])
        nearest = graph.nearest_concepts(turn_emb, k=CONTEXT_K)
    else:
        nearest = []
    ephemerals = graph.open_ephemerals_in_session(turn.session_id)
    return nearest, ephemerals, recent[-RECENT_TURN_N:]


# ---------------------------------------------------------------------------
# Apply classification result to graph
# ---------------------------------------------------------------------------

def apply_classification(
    graph: Graph,
    turn: Source,
    result: ClassifyResult,
    embedder: Embedder,
) -> None:
    """Apply the LLM's per-turn output to the graph.

    Side effects:
      - turn source is added (unconditional)
      - cited concepts gain a source_ref and an edge from the turn
      - state transitions fire on ephemerals via consume/retract edges
      - new concepts are created (with cosine dedup against existing)
      - concept→concept edges are added between existing concepts
    """
    graph.add_source(turn)

    if result.act == "none":
        return

    # 1) Cites: turn→concept edges + source_ref updates
    for cite in result.cites:
        if not isinstance(cite, dict):
            continue
        cid = cite.get("concept_id") or cite.get("id")
        if not cid or cid not in graph.concepts:
            continue
        c = graph.concepts[cid]
        c.cite(turn.id, turn.speaker)
        edge_type = cite.get("edge") or cite.get("edge_type", "support")
        try:
            e = Edge(
                type=edge_type,
                source=turn.id,
                target=cid,
                established_at=turn.id,
                voices=[turn.speaker],
                confidence=cite.get("confidence", "medium"),
            )
        except ValueError:
            continue  # bad edge type — skip rather than crash
        graph.add_edge(e)
        # State transitions on ephemerals
        if c.class_ == "ephemeral":
            if edge_type in ("consume-success", "consume-failure"):
                c.state = "consumed"
                graph.open_ephemerals.discard(c.id)
            elif edge_type == "retract":
                c.state = "retracted"
                graph.open_ephemerals.discard(c.id)

    # 2) Creates: new concepts with cosine dedup
    for create in result.creates:
        if not isinstance(create, dict):
            continue
        topic = (create.get("topic") or "").strip()
        if not topic:
            continue
        class_: ConceptClass = create.get("class", "observation")  # type: ignore
        nature: ConceptNature = create.get("nature", "factual")  # type: ignore
        if class_ not in {"invariant", "decision", "observation", "ephemeral"}:
            class_ = "observation"
        if nature not in {"factual", "normative", "metaphysical"}:
            nature = "factual"
        parent = create.get("parent_hint")
        if parent and parent not in graph.concepts:
            parent = None

        topic_emb = embedder.embed(topic)
        existing = graph.find_similar_concept(topic, topic_emb)
        if existing is not None:
            existing.cite(turn.id, turn.speaker)
            # Optional: could upgrade class/nature if new classification is
            # stronger, but that's a judgment call — defer for now.
            continue

        cid = slugify_topic(topic)
        if cid in graph.concepts:
            cid = f"{cid}-{len(graph.concepts)}"
        try:
            # Create with empty source_refs + voices, then call cite()
            # so the R1 mass-accumulate path runs for the first citation
            # too. Otherwise new concepts would start at mass=0.5 with
            # empty voices, and the first citation's mass bump would
            # only apply to the second speaker.
            new_concept = Concept(
                id=cid,
                topic=topic,
                class_=class_,
                nature=nature,
                parent=parent,
                embedding=topic_emb,
            )
            new_concept.cite(turn.id, turn.speaker)
        except ValueError:
            continue
        graph.add_concept(new_concept)

    # 3) Concept→concept edges
    for edge in result.concept_edges:
        if not isinstance(edge, dict):
            continue
        src = edge.get("source")
        tgt = edge.get("target")
        etype = edge.get("type")
        if not src or not tgt or not etype:
            continue
        if src not in graph.concepts or tgt not in graph.concepts:
            continue
        try:
            e = Edge(
                type=etype,
                source=src,
                target=tgt,
                established_at=turn.id,
                voices=[turn.speaker],
                confidence=edge.get("confidence", "medium"),
            )
        except ValueError:
            continue
        graph.add_edge(e)


# ---------------------------------------------------------------------------
# Session ingest
# ---------------------------------------------------------------------------

def ingest_session(
    graph: Graph,
    jsonl_path: Path,
    *,
    embedder: Embedder,
    classifier: TurnClassifier,
    on_progress: Optional[Callable[[int, int, int, int], None]] = None,
    save_every: int = 25,
    save_to: Optional[Path] = None,
) -> dict:
    """Ingest every turn of a session jsonl into the graph.

    Periodic cache flushes + optional intermediate graph saves so
    long runs can be interrupted and resumed (next run hits cache).

    Returns a stats dict.
    """
    turns = read_session_turns(jsonl_path)
    processed: list[Source] = []
    stats = {
        "total_turns": len(turns),
        "llm_calls": 0,
        "cache_hits": 0,
        "skipped_already_ingested": 0,
        "act_counts": {"walk": 0, "add": 0, "none": 0},
        "started_at": time.time(),
    }

    for i, turn in enumerate(turns):
        # Incremental ingest: skip turns already processed in a prior
        # run. Safe because sources are append-only — once a turn is
        # ingested its effect on the graph is fixed. This makes the
        # cron cheap: re-running on the same session only processes
        # newly-appended turns.
        if turn.id in graph.sources:
            stats["skipped_already_ingested"] += 1
            processed.append(turn)
            continue
        nearest, ephemerals, recent = assemble_context(
            graph, turn, processed, embedder
        )
        context_ids = [c.id for c in nearest] + [c.id for c in ephemerals]
        recent_ids = [s.id for s in recent]
        result = classifier.classify(
            turn_text=turn.text,
            speaker=turn.speaker,
            nearest_fmt=_format_concepts(nearest),
            ephemerals_fmt=_format_concepts(ephemerals),
            recent_fmt=_format_turns(recent),
            context_ids=context_ids,
            recent_ids=recent_ids,
        )
        if result.was_cached:
            stats["cache_hits"] += 1
        else:
            stats["llm_calls"] += 1
        stats["act_counts"][result.act] = stats["act_counts"].get(result.act, 0) + 1

        apply_classification(graph, turn, result, embedder)
        processed.append(turn)

        if (i + 1) % save_every == 0:
            embedder.save()
            classifier.save()
            if save_to is not None:
                save_graph(graph, save_to)
            if on_progress is not None:
                on_progress(i + 1, len(graph.concepts), len(graph.edges), stats["llm_calls"])

    embedder.save()
    classifier.save()

    # R5 completion: age out ephemerals that never got consumed/
    # retracted/re-voiced. Runs at the end of each ingest session
    # so the "open work" list self-maintains without a separate
    # maintenance command.
    stale_count = graph.sweep_stale_ephemerals()
    stats["stale_transitions"] = stale_count

    stats["finished_at"] = time.time()
    stats["elapsed_s"] = stats["finished_at"] - stats["started_at"]
    return stats


# ---------------------------------------------------------------------------
# CLI entry point (for `python -m bellamem.proto.ingest SNAPSHOT_PATH`)
# ---------------------------------------------------------------------------

def _load_env_file(env_path: Path) -> None:
    """Minimal .env loader for `python -m` invocation where the shell
    env may not carry OPENAI_API_KEY etc. Does not overwrite existing
    env vars — shell env takes precedence."""
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    import tempfile
    _load_env_file(Path.cwd() / ".env")
    SCRATCH = Path(tempfile.gettempdir()) / "bellamem-proto-tree"
    SCRATCH.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) >= 2:
        snapshot = Path(sys.argv[1])
    else:
        snapshot = SCRATCH / "session-snapshot.jsonl"
    if not snapshot.exists():
        print(f"snapshot not found: {snapshot}", file=sys.stderr)
        return 1

    out_path = Path(".graph") / "v02.json"
    embed_cache = SCRATCH / "proto-embed-cache.json"
    llm_cache = SCRATCH / "proto-llm-cache.json"

    embedder = Embedder(embed_cache)
    classifier = TurnClassifier(llm_cache)
    graph = load_graph(out_path)

    print(f"input:  {snapshot}")
    print(f"output: {out_path}")
    print(f"initial graph: {len(graph.concepts)} concepts, {len(graph.edges)} edges")
    print()

    def progress(n_turns: int, n_concepts: int, n_edges: int, n_llm: int) -> None:
        print(f"  [{n_turns}] concepts={n_concepts} edges={n_edges} llm={n_llm}", flush=True)

    stats = ingest_session(
        graph, snapshot,
        embedder=embedder, classifier=classifier,
        on_progress=progress, save_every=25, save_to=out_path,
    )
    save_graph(graph, out_path)

    print()
    print("=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"turns:      {stats['total_turns']}")
    print(f"llm calls:  {stats['llm_calls']}")
    print(f"cache hits: {stats['cache_hits']}")
    print(f"acts:       {stats['act_counts']}")
    print(f"concepts:   {len(graph.concepts)}")
    print(f"edges:      {len(graph.edges)}")
    print(f"by_class:   { {k: len(v) for k, v in graph.by_class.items()} }")
    print(f"by_nature:  { {k: len(v) for k, v in graph.by_nature.items()} }")
    print(f"elapsed:    {stats['elapsed_s']:.1f}s")
    print(f"output:     {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
