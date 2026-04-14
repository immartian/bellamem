"""Graph container: mutable concepts + first-class edges + append-only sources.

Derivable indices (by_class, by_nature, by_session, children_of,
open_ephemerals) are rebuilt on demand or after batch updates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from bellamem.proto.schema import (
    Concept, ConceptClass, ConceptNature, Edge, EdgeType, Source,
    slugify_topic,
)


DEDUP_COSINE = 0.85  # concept-merge threshold on topic embedding cosine


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@dataclass
class Graph:
    """Container for the three primary stores.

    Call `rebuild_indices()` after bulk changes if you want the
    `by_*` lookup tables to reflect the latest state. Per-turn
    ingest keeps them in sync automatically.
    """
    sources: dict[str, Source] = field(default_factory=dict)
    concepts: dict[str, Concept] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)

    # Derivable indices — rebuildable from the primary stores.
    by_class: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    by_nature: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    by_session: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    children_of: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    open_ephemerals: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # Inserts / lookups
    # ------------------------------------------------------------------

    def add_source(self, source: Source) -> None:
        self.sources[source.id] = source

    def add_concept(self, concept: Concept) -> None:
        self.concepts[concept.id] = concept
        self._index_concept(concept)

    def add_edge(self, edge: Edge) -> Edge:
        """Insert or accumulate an edge.

        If an edge with the same (type, source, target) already
        exists, add any new voice instead of creating a duplicate.
        This is BELLA R1 applied at the edge level.
        """
        existing = self.edges.get(edge.id)
        if existing is None:
            self.edges[edge.id] = edge
            return edge
        # Merge: accumulate voices, raise confidence on re-ratification
        for v in edge.voices:
            if v not in existing.voices:
                existing.voices.append(v)
        # Simple confidence bump: low→medium→high
        if len(existing.voices) >= 2 and existing.confidence == "low":
            existing.confidence = "medium"
        if len(existing.voices) >= 3 and existing.confidence != "high":
            existing.confidence = "high"
        return existing

    def find_similar_concept(
        self,
        topic: str,
        embedding: np.ndarray,
    ) -> Optional[Concept]:
        """Find an existing concept that should absorb a new topic.

        First checks canonical slug match. Then falls back to cosine
        similarity above DEDUP_COSINE against concept topic
        embeddings. Returns None if no match — caller should create
        a new concept.
        """
        slug = slugify_topic(topic)
        if slug in self.concepts:
            return self.concepts[slug]
        best: Optional[Concept] = None
        best_sim = 0.0
        for c in self.concepts.values():
            if c.embedding is None:
                continue
            s = _cosine(embedding, c.embedding)
            if s > best_sim:
                best_sim = s
                best = c
        if best is not None and best_sim >= DEDUP_COSINE:
            return best
        return None

    def nearest_concepts(
        self,
        query_embedding: np.ndarray,
        k: int = 8,
        min_sim: float = 0.30,
    ) -> list[Concept]:
        """Top-k concepts by cosine similarity against a query."""
        scored: list[tuple[float, Concept]] = []
        for c in self.concepts.values():
            if c.embedding is None:
                continue
            s = _cosine(query_embedding, c.embedding)
            if s >= min_sim:
                scored.append((s, c))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [c for _, c in scored[:k]]

    def open_ephemerals_in_session(self, session_id: str) -> list[Concept]:
        """Return open ephemeral concepts with at least one source
        ref in the given session. Used by the ingest loop's context
        assembly for consume/retract detection."""
        out: list[Concept] = []
        for c in self.concepts.values():
            if c.class_ != "ephemeral" or c.state != "open":
                continue
            for sr in c.source_refs:
                src = self.sources.get(sr)
                if src is not None and src.session_id == session_id:
                    out.append(c)
                    break
        return out

    # ------------------------------------------------------------------
    # Indices
    # ------------------------------------------------------------------

    def _index_concept(self, c: Concept) -> None:
        self.by_class[c.class_].add(c.id)
        self.by_nature[c.nature].add(c.id)
        if c.parent is not None:
            self.children_of[c.parent].add(c.id)
        if c.class_ == "ephemeral" and c.state == "open":
            self.open_ephemerals.add(c.id)
        # by_session: every session touching this concept
        for sr in c.source_refs:
            sid = sr.split("#", 1)[0]
            self.by_session[sid].add(c.id)

    def rebuild_indices(self) -> None:
        """Rebuild all derivable indices from the primary stores."""
        self.by_class = defaultdict(set)
        self.by_nature = defaultdict(set)
        self.by_session = defaultdict(set)
        self.children_of = defaultdict(set)
        self.open_ephemerals = set()
        for c in self.concepts.values():
            self._index_concept(c)

    # ------------------------------------------------------------------
    # JSON serialization
    # ------------------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "sources": {sid: s.to_json() for sid, s in self.sources.items()},
            "concepts": {cid: c.to_json() for cid, c in self.concepts.items()},
            "edges": [e.to_json() for e in self.edges.values()],
            "stats": {
                "n_sources": len(self.sources),
                "n_concepts": len(self.concepts),
                "n_edges": len(self.edges),
                "by_class": {k: len(v) for k, v in self.by_class.items()},
                "by_nature": {k: len(v) for k, v in self.by_nature.items()},
                "by_session": {k: len(v) for k, v in self.by_session.items()},
                "open_ephemerals": len(self.open_ephemerals),
            },
        }

    @classmethod
    def from_json(cls, data: dict) -> "Graph":
        g = cls()
        for sid, sdata in data.get("sources", {}).items():
            g.sources[sid] = Source.from_json(sdata)
        for cid, cdata in data.get("concepts", {}).items():
            g.concepts[cid] = Concept.from_json(cdata)
        for edata in data.get("edges", []):
            e = Edge.from_json(edata)
            g.edges[e.id] = e
        g.rebuild_indices()
        return g
