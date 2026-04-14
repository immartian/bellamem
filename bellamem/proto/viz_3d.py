"""3D viz payload for the v0.2 graph — Three.js + UMAP.

Phase A of the VIZ_DESIGN.md port. Builds on the shared
`viz.build_payload` (filters, turn hubs, depths) and adds:
  - topic embeddings, re-embedded fresh and cached on disk
  - UMAP 2D reduction for X/Z positions
  - mass for Y (height in the scene)
  - class × nature palette
  - turn-hub 3D positions (placed at the centroid of their spokes)

Keeps the concern split clean: this module is the data layer
(transform graph → payload dict), `viz_template_3d.html` is the
Three.js scene. `viz_html.render(renderer="3d")` inlines the
payload into the template.

Embeddings are re-computed at render time against the existing
Embedder cache (`.graph/embed_cache.json` by default). First run
against a fresh graph costs one OpenAI call per topic (~$0.01
for 400 concepts); subsequent runs are instant via cache hits.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from bellamem.proto.clients import Embedder
from bellamem.proto.graph import Graph
from bellamem.proto.schema import Concept
from bellamem.proto.viz import Filters, VizPayload, build_payload, payload_to_dict


# X/Z spatial extent in scene units. 30 matches the legacy viz so
# camera controls feel familiar.
_EXTENT = 30.0
# Y-axis height scale: mass ∈ [0.5, ~0.95] maps to scene y-units.
# Ratified content sits a clean few units above the m=0.5 floor.
_HEIGHT_SCALE = 14.0
# UMAP random state — deterministic layout across renders.
_UMAP_SEED = 42


@dataclass
class Viz3DPayload:
    payload: dict
    n_embedded: int
    n_cached: int


def _compute_umap(embeddings: np.ndarray) -> np.ndarray:
    """Reduce embeddings to 2D via UMAP. Falls back to trivial
    first-two-dims for tiny graphs (<10) where UMAP is unstable."""
    n = embeddings.shape[0]
    if n == 0:
        return np.zeros((0, 2), dtype=np.float32)
    if n < 10:
        padded = np.zeros((n, 2), dtype=np.float32)
        if embeddings.shape[1] >= 1:
            padded[:, 0] = embeddings[:, 0]
        if embeddings.shape[1] >= 2:
            padded[:, 1] = embeddings[:, 1]
        return padded
    try:
        import umap  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "3D viz requires the [viz3d] extra. Install with:\n"
            "  pip install 'bellamem[viz3d]'\n"
            "or switch to --renderer d3 / --renderer cytoscape for 2D."
        ) from exc
    reducer = umap.UMAP(
        n_components=2,
        random_state=_UMAP_SEED,
        n_neighbors=min(15, n - 1),
        min_dist=0.12,
        metric="cosine",
    )
    return reducer.fit_transform(embeddings).astype(np.float32)


def _normalize_2d(coords: np.ndarray, extent: float = _EXTENT) -> np.ndarray:
    """Center at origin, scale to fit in `extent`."""
    if coords.shape[0] == 0:
        return coords
    mins = coords.min(axis=0)
    maxs = coords.max(axis=0)
    center = (mins + maxs) / 2
    span = float(max((maxs - mins).max(), 1e-6))
    k = extent / span
    return (coords - center) * k


def build_3d_payload(
    graph: Graph,
    *,
    filters: Optional[Filters] = None,
    embed_cache_path: Optional[Path] = None,
) -> Viz3DPayload:
    """Build the filtered payload + 3D positions.

    Reuses `viz.build_payload` for concept selection, edge filtering,
    and turn-hub construction. Adds per-concept embedding lookup and
    UMAP reduction. Turn hubs are placed at the centroid of their
    cited concepts, projected into the scene.
    """
    payload = build_payload(graph, filters)
    data = payload_to_dict(payload)

    cache_path = embed_cache_path or (Path.cwd() / ".graph" / "embed_cache.json")
    embedder = Embedder(cache_path=cache_path)

    # Re-embed every kept concept's topic. Hits the cache for anything
    # already embedded during ingest.
    before_hits = len(embedder._cache)  # noqa: SLF001 — cheap introspection
    topic_vectors: list[np.ndarray] = []
    for c in payload.concepts:
        topic_vectors.append(embedder.embed(c.topic))
    embedder.save()
    after_hits = len(embedder._cache)  # noqa: SLF001

    if not topic_vectors:
        # Empty graph — nothing to place.
        for c_dict in data["concepts"]:
            c_dict["pos_3d"] = [0.0, 0.0, 0.0]
        data["turns"] = [
            {**t, "pos_3d": [0.0, 0.0, 0.0]} for t in data["turns"]
        ]
        return Viz3DPayload(payload=data, n_embedded=0, n_cached=0)

    # Stack, run UMAP, normalize the 2D result into scene coordinates.
    dims = max(v.shape[0] for v in topic_vectors)
    arr = np.zeros((len(topic_vectors), dims), dtype=np.float32)
    for i, v in enumerate(topic_vectors):
        arr[i, : v.shape[0]] = v
    coords_2d = _compute_umap(arr)
    coords_2d = _normalize_2d(coords_2d)

    # Attach (x, y, z) to each concept in the dict payload. X/Z come
    # from UMAP, Y from mass.
    id_to_pos: dict[str, tuple[float, float, float]] = {}
    for c, (x, z) in zip(payload.concepts, coords_2d):
        y = float(max(0.0, (c.mass - 0.5) * 2 * _HEIGHT_SCALE))
        id_to_pos[c.id] = (float(x), y, float(z))

    for c_dict in data["concepts"]:
        x, y, z = id_to_pos.get(c_dict["id"], (0.0, 0.0, 0.0))
        c_dict["pos_3d"] = [x, y, z]

    # Turn hubs: centroid of their spoke targets, slightly below the
    # cluster (Y = -1) so they read as "underneath" the concepts in
    # the scene.
    hub_positions: dict[str, tuple[float, float, float]] = {}
    targets_by_hub: dict[str, list[str]] = {}
    for e in payload.turn_edges:
        targets_by_hub.setdefault(e.source, []).append(e.target)
    for turn in payload.turns:
        targets = targets_by_hub.get(turn.id, [])
        pts = [id_to_pos[t] for t in targets if t in id_to_pos]
        if not pts:
            hub_positions[turn.id] = (0.0, -2.0, 0.0)
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        hub_positions[turn.id] = (cx, -1.5, cz)

    data["turns"] = [
        {**t, "pos_3d": list(hub_positions.get(t["id"], (0.0, -2.0, 0.0)))}
        for t in data["turns"]
    ]

    return Viz3DPayload(
        payload=data,
        n_embedded=after_hits - before_hits,
        n_cached=len(topic_vectors) - (after_hits - before_hits),
    )
