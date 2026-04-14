"""v0.2 walker query API — relevance + 1-hop edge traversal.

The retrieval complement to `resume_text`. Where resume surfaces a
typed structural summary (invariants × mass × state machine), ask
surfaces a focus-scoped slice: concepts whose topics are semantically
near the query, plus the edges that touch them (disputes, retracts,
causes, supports, elaborates).

This module exists because the pre-v0.2 relevance paths
(`bellamem.core.expand.ask`, `expand_before_edit`) read the flat
`Bella` object from `.graph/default.json`, which `save` stopped
updating during the v0.2 migration. Rewiring those paths through a
v0.2-native walker is the prerequisite for GitHub issue #2's
surface reduction — you can't collapse commands around `ask` until
`ask` itself can see the current graph.

Embeddings are not persisted in `v02.json` (see
`schema.Concept.to_json`). We re-hydrate on demand via the shared
`Embedder` cache, which is keyed by sha256(text). Any concept topic
that's been ingested before hits the cache and costs nothing.
Concepts whose topics miss the cache fall back to substring scoring
so an offline walker still returns useful results.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

from bellamem.proto.graph import Graph
from bellamem.proto.schema import Concept, ConceptClass


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _tokenize(q: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", q.lower()) if len(t) >= 2]


def _substring_score(text: str, q_tokens: list[str]) -> float:
    if not q_tokens:
        return 0.0
    low = text.lower()
    hits = sum(1 for t in q_tokens if t in low)
    return hits / len(q_tokens)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _score_concept(
    c: Concept,
    q_tokens: list[str],
    q_emb: Optional[np.ndarray],
) -> float:
    """Mass-weighted relevance: max(substring, cosine) × mass.

    Why max rather than sum: substring match on a literal query like
    "github pages" should survive even when the embedder is offline
    or the concept embedding never hydrated. Pure cosine would drop
    it; pure substring would miss semantic matches like "docs site
    publishing". Max gives both paths a chance to promote a concept.

    Why × mass: a ratified decision at cosine 0.6 should outrank a
    one-off mention at cosine 0.7. This is the same reason the flat
    `ask` uses _relevance_rank_mass_weighted.
    """
    sub = _substring_score(c.topic, q_tokens)
    cos = 0.0
    if q_emb is not None and c.embedding is not None:
        cos = _cosine(q_emb, c.embedding)
    base = max(sub, cos)
    if base <= 0:
        return 0.0
    return base * c.mass


# ---------------------------------------------------------------------------
# Edge neighborhood
# ---------------------------------------------------------------------------

# Edge types surfaced in the pack, in priority order. Disputes and
# retracts come first because "what was rejected" is usually more
# load-bearing for agent decisions than "what supports this".
_EDGE_SECTIONS: list[tuple[str, str]] = [
    ("dispute", "⊥ disputes"),
    ("retract", "retracts"),
    ("cause", "causes"),
    ("support", "supports"),
    ("elaborate", "elaborates"),
]


def _neighbors(
    graph: Graph, concept_ids: set[str]
) -> dict[str, list[tuple[str, str, str]]]:
    """Walk one hop from each seed concept.

    Returns a dict keyed by edge type, each value a list of
    (src_topic, target_topic, direction) tuples. Direction is
    "out" when the seed is the edge source, "in" when target.
    Edges whose "other" endpoint isn't a concept (e.g. turn-hub
    sources for retract/dispute edges voiced by a turn) are
    rendered with the turn id as a placeholder so they still
    surface — dropping them silently was the pre-v0.2 ask's
    quiet failure mode.
    """
    out: dict[str, list[tuple[str, str, str]]] = {}
    for e in graph.edges.values():
        if e.source in concept_ids:
            src_c = graph.concepts.get(e.source)
            tgt_c = graph.concepts.get(e.target)
            if src_c is None:
                continue
            src_label = src_c.topic
            tgt_label = tgt_c.topic if tgt_c else f"(turn {e.target})"
            out.setdefault(e.type, []).append((src_label, tgt_label, "out"))
        elif e.target in concept_ids:
            tgt_c = graph.concepts.get(e.target)
            src_c = graph.concepts.get(e.source)
            if tgt_c is None:
                continue
            tgt_label = tgt_c.topic
            src_label = src_c.topic if src_c else f"(turn {e.source})"
            out.setdefault(e.type, []).append((src_label, tgt_label, "in"))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ask_text(
    graph: Graph,
    focus: str,
    *,
    embedder=None,
    seed_k: int = 12,
    class_filter: Optional[ConceptClass] = None,
    min_score: float = 0.05,
) -> str:
    """Render a relevance-first pack for `focus` over the v0.2 graph.

    Args:
        graph: loaded v0.2 Graph.
        focus: user's query / question / topic.
        embedder: optional `proto.clients.Embedder` for cosine ranking.
            When None, falls back to substring scoring on topics.
            Cache-backed — re-hydrating concept embeddings for ingested
            topics is free.
        seed_k: how many seed concepts to pull before walking.
        class_filter: restrict seeds to one class (invariant / decision
            / observation / ephemeral). Used by routing wrappers that
            know the question's intent.
        min_score: candidates below this score are dropped even if
            they'd otherwise fill the seed slot.

    Returns a multi-line string. First section is the seeds
    (mass-weighted), then one section per edge type that actually
    has neighbors. Empty sections are omitted.
    """
    if not graph.concepts:
        return "# v0.2 ask\n  empty graph — run `bellamem save` first"

    q_tokens = _tokenize(focus)
    q_emb: Optional[np.ndarray] = None

    # Embed the query and hydrate concept embeddings on demand.
    # Hydration is a cache hit for any topic already seen by ingest;
    # misses are silently left as None and substring scoring takes over.
    if embedder is not None and focus.strip():
        try:
            q_emb = embedder.embed(focus)
        except Exception:
            q_emb = None
        if q_emb is not None:
            for c in graph.concepts.values():
                if c.embedding is None:
                    try:
                        c.embedding = embedder.embed(c.topic)
                    except Exception:
                        pass

    # 1. Score and rank candidates
    scored: list[tuple[float, Concept]] = []
    for c in graph.concepts.values():
        if class_filter and c.class_ != class_filter:
            continue
        s = _score_concept(c, q_tokens, q_emb)
        if s >= min_score:
            scored.append((s, c))
    scored.sort(key=lambda t: -t[0])
    seeds = [c for _, c in scored[:seed_k]]
    seed_ids = {c.id for c in seeds}

    # 2. Render
    mode = "cosine+mass" if q_emb is not None else "substring+mass"
    out: list[str] = []
    out.append(f"# v0.2 ask (focus: {focus!r})")
    out.append(
        f"  {len(graph.concepts)} concepts · "
        f"{len(graph.edges)} edges · "
        f"{len(scored)} candidates · mode={mode}"
    )
    if class_filter:
        out.append(f"  class_filter: {class_filter}")
    out.append("")

    if not seeds:
        out.append("## no matches")
        out.append(
            f"  no concepts scored ≥ {min_score} for tokens {q_tokens or '(none)'}"
        )
        out.append(
            "  try broader wording, or run `bellamem resume` for the full pack"
        )
        return "\n".join(out)

    out.append(f"## relevant concepts ({len(seeds)})")
    for score, c in scored[:seed_k]:
        state = f" [{c.state}]" if c.state else ""
        out.append(
            f"  s={score:.2f} m={c.mass:.2f} "
            f"[{c.class_}/{c.nature}]{state} {c.topic}"
        )
    out.append("")

    # 3. One-hop edge neighborhood
    neighbors = _neighbors(graph, seed_ids)
    total_neighbors = 0
    for etype, label in _EDGE_SECTIONS:
        pairs = neighbors.get(etype, [])
        if not pairs:
            continue
        # De-duplicate (same edge can surface twice if both endpoints
        # are seeds; also the edge store itself is already unique so
        # the only dup source is symmetric seeds).
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str, str]] = []
        for src_t, tgt_t, dirn in pairs:
            key = (src_t, tgt_t)
            if key in seen:
                continue
            seen.add(key)
            unique.append((src_t, tgt_t, dirn))
        out.append(f"## {label} ({len(unique)})")
        for src_t, tgt_t, _ in unique:
            arrow = {
                "dispute": "  ⊥  ",
                "retract": "  retract  ",
                "cause": "  ⇒  ",
                "support": "  +  ",
                "elaborate": "  →  ",
            }.get(etype, "  →  ")
            out.append(f"  {src_t}{arrow}{tgt_t}")
        out.append("")
        total_neighbors += len(unique)

    out.append(f"— {len(seeds)} seeds, {total_neighbors} edge neighbors —")
    return "\n".join(out)
