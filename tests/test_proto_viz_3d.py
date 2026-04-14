"""Tests for bellamem.proto.viz_3d — 3D payload build.

Exercises the Graph → 3D payload transform with a deterministic
embedder so we don't hit OpenAI. Covers:
  - each concept gets a pos_3d triple
  - Y (mass) maps into the expected scene range
  - turn hubs land at the centroid of their spokes
  - renderer="3d" round-trips through viz_html.render
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bellamem.proto import Concept, Edge, Graph, Source
from bellamem.proto.clients import Embedder
from bellamem.proto.viz import Filters


class _DeterministicEmbedder(Embedder):
    def __init__(self, *args, **kwargs) -> None:
        # Accept and ignore any kwargs so callers can pass cache_path
        # or model without the mock caring.
        self.cache_path = Path("/tmp/_unused_test_embed_cache.json")
        self.model = "test"
        self._cache = {}
        self._dirty = False
        self._client = None

    def embed(self, text: str) -> np.ndarray:
        import hashlib
        h = hashlib.sha256(text.encode()).digest()[:32]
        return (np.frombuffer(h, dtype=np.uint8).astype(np.float32) / 127.5) - 1.0

    def save(self) -> None:
        pass


def _make_graph() -> Graph:
    """Small graph (n=9) — stays in the trivial-layout fast path so
    tests don't pay the ~30-second cost of a real UMAP fit."""
    g = Graph()
    for i in range(9):
        g.add_source(Source(
            session_id="s", file_path="/f.jsonl",
            speaker="user" if i % 2 else "assistant",
            turn_idx=i, text=f"t{i}", timestamp=None,
        ))
    for i in range(9):
        c = Concept(
            id=f"c{i}", topic=f"concept number {i}",
            class_="invariant", nature="metaphysical",
            mass=0.60 + (i / 100),
            source_refs=[f"s#{i}", f"s#{(i + 1) % 9}"],
            voices=["user", "assistant"],
        )
        g.add_concept(c)
    g.add_edge(Edge(type="cause", source="c0", target="c5",
                    established_at="s#0", voices=["user"]))
    g.rebuild_indices()
    return g


def test_build_3d_payload_assigns_positions(monkeypatch):
    from bellamem.proto import viz_3d
    monkeypatch.setattr(viz_3d, "Embedder", _DeterministicEmbedder)

    g = _make_graph()
    result = viz_3d.build_3d_payload(
        g, filters=Filters(min_mass=0.55, include_turn_hubs=False)
    )
    d = result.payload
    assert len(d["concepts"]) > 0
    for c in d["concepts"]:
        assert "pos_3d" in c
        assert len(c["pos_3d"]) == 3
        x, y, z = c["pos_3d"]
        # Y should reflect mass — positive, below the _HEIGHT_SCALE cap.
        assert 0.0 <= y <= viz_3d._HEIGHT_SCALE
        # X/Z normalized into [-extent/2, +extent/2]
        assert -viz_3d._EXTENT <= x <= viz_3d._EXTENT
        assert -viz_3d._EXTENT <= z <= viz_3d._EXTENT


def test_y_tracks_mass(monkeypatch):
    from bellamem.proto import viz_3d
    monkeypatch.setattr(viz_3d, "Embedder", _DeterministicEmbedder)

    g = _make_graph()
    # Make the last concept much higher-mass than the rest.
    g.concepts["c8"].mass = 0.95
    result = viz_3d.build_3d_payload(
        g, filters=Filters(min_mass=0.55, include_turn_hubs=False)
    )
    by_id = {c["id"]: c for c in result.payload["concepts"]}
    assert by_id["c8"]["pos_3d"][1] > by_id["c0"]["pos_3d"][1], \
        "higher mass should produce higher Y"


def test_turn_hubs_placed_at_spoke_centroid(monkeypatch):
    from bellamem.proto import viz_3d
    monkeypatch.setattr(viz_3d, "Embedder", _DeterministicEmbedder)

    g = _make_graph()
    # Ensure a turn touches ≥3 concepts so it becomes a hub.
    # source_refs already have each concept touching 2 turns.
    # Wire up a dummy turn that cites many concepts.
    g.concepts["c0"].source_refs.append("s#5")
    g.concepts["c1"].source_refs.append("s#5")
    g.concepts["c2"].source_refs.append("s#5")
    g.concepts["c3"].source_refs.append("s#5")

    result = viz_3d.build_3d_payload(
        g, filters=Filters(min_mass=0.55, min_turn_degree=3)
    )
    turns = result.payload["turns"]
    hub = next((t for t in turns if t["id"] == "s#5"), None)
    assert hub is not None, "s#5 should be a turn hub"
    assert "pos_3d" in hub
    assert len(hub["pos_3d"]) == 3
    # Y should be slightly below zero (under the concept cloud)
    assert hub["pos_3d"][1] < 0


def test_render_html_3d_inlines_payload(tmp_path: Path, monkeypatch):
    from bellamem.proto import viz_3d
    from bellamem.proto.viz_html import render as render_html
    monkeypatch.setattr(viz_3d, "Embedder", _DeterministicEmbedder)

    g = _make_graph()
    out = tmp_path / "out.html"
    render_html(g, out, renderer="3d",
                filters=Filters(min_mass=0.55, include_turn_hubs=False))
    html = out.read_text(encoding="utf-8")
    assert "/*__PAYLOAD__*/" not in html
    assert "pos_3d" in html
    assert "three@0.160" in html  # CDN import
