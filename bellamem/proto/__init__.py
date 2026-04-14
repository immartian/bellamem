"""bellamem.proto — v0.2 graph schema prototype.

Tree + cross-edges concept graph with source grounding and per-turn
LLM ingestion. Standalone from bellamem.core / bellamem.adapters.

See memory/project_graph_v02_schema.md for the design.

Public API:
    from bellamem.proto import Source, Concept, Edge, Graph
    from bellamem.proto import ingest_session, load_graph, save_graph
"""
from bellamem.proto.schema import (
    Source, Concept, Edge,
    ConceptClass, ConceptNature, ConceptState, EdgeType,
    slugify_topic,
)
from bellamem.proto.graph import Graph
from bellamem.proto.store import load_graph, save_graph
from bellamem.proto.ingest import ingest_session

__all__ = [
    "Source", "Concept", "Edge", "Graph",
    "ConceptClass", "ConceptNature", "ConceptState", "EdgeType",
    "slugify_topic",
    "load_graph", "save_graph",
    "ingest_session",
]
