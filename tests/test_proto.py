"""Smoke tests for bellamem.proto.

Exercises the v0.2 prototype end-to-end with mock LLM + mock
embedder, no network calls. Covers:
  - schema validation (class / nature / state invariants)
  - graph insert / dedup / edge accumulation
  - store roundtrip
  - ingest_session pipeline with a fake jsonl
  - state transitions on ephemerals via consume / retract edges
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bellamem.proto import (
    Concept, Edge, Graph, Source, load_graph, save_graph, slugify_topic,
)
from bellamem.proto.clients import ClassifyResult, Embedder, TurnClassifier
from bellamem.proto.ingest import (
    apply_classification, ingest_session, read_session_turns,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------

class _DeterministicEmbedder(Embedder):
    """Mock embedder that hashes text to a small deterministic vector.
    Avoids network calls and is cheap to run in tests."""

    def __init__(self) -> None:
        # bypass parent __init__ so we never touch disk
        self.cache_path = Path("/tmp/_unused_test_embed_cache.json")
        self.model = "test"
        self._cache = {}
        self._dirty = False
        self._client = None

    def embed(self, text: str) -> np.ndarray:
        # 8-dim vector from sha256 bytes — same text always yields same vec
        import hashlib
        h = hashlib.sha256(text.encode()).digest()[:32]
        v = np.frombuffer(h, dtype=np.uint8).astype(np.float32) / 255.0
        return v

    def save(self) -> None:
        pass


class _CannedClassifier(TurnClassifier):
    """Returns pre-programmed classification per turn_text."""

    def __init__(self, canned: dict[str, dict]) -> None:
        self.cache_path = Path("/tmp/_unused_test_llm_cache.json")
        self.model = "test"
        self._cache = {}
        self._dirty = False
        self._client = None
        self._canned = canned

    def classify(self, **kwargs) -> ClassifyResult:
        text = kwargs["turn_text"]
        raw = self._canned.get(text, {"act": "none", "cites": [], "creates": [], "concept_edges": []})
        return ClassifyResult.from_raw(raw, was_cached=False)

    def save(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def test_concept_class_validation():
    with pytest.raises(ValueError):
        Concept(id="x", topic="x", class_="nonsense", nature="factual")  # type: ignore

def test_concept_nature_validation():
    with pytest.raises(ValueError):
        Concept(id="x", topic="x", class_="invariant", nature="magic")  # type: ignore

def test_ephemeral_defaults_to_open():
    c = Concept(id="e", topic="t", class_="ephemeral", nature="normative")
    assert c.state == "open"

def test_non_ephemeral_rejects_state():
    with pytest.raises(ValueError):
        Concept(id="x", topic="x", class_="invariant", nature="factual", state="open")

def test_slugify_stable():
    assert slugify_topic("Walker Primitive") == slugify_topic("walker primitive")
    assert slugify_topic("walker primitive") == "walker-primitive"
    assert slugify_topic("   ") == "unnamed"


# ---------------------------------------------------------------------------
# Graph inserts + edge accumulation
# ---------------------------------------------------------------------------

def _mk_concept(topic: str, class_="observation", nature="factual") -> Concept:
    return Concept(
        id=slugify_topic(topic), topic=topic, class_=class_, nature=nature,
        embedding=np.random.rand(8).astype(np.float32),
    )

def test_add_concept_indices_update():
    g = Graph()
    g.add_concept(_mk_concept("alpha", "invariant", "metaphysical"))
    assert "alpha" in g.by_class["invariant"]
    assert "alpha" in g.by_nature["metaphysical"]

def test_add_edge_accumulates_voices():
    g = Graph()
    g.add_concept(_mk_concept("target"))
    e1 = Edge(type="support", source="s#0", target="target",
              established_at="s#0", voices=["user"])
    e2 = Edge(type="support", source="s#0", target="target",
              established_at="s#0", voices=["assistant"])
    g.add_edge(e1)
    g.add_edge(e2)
    # Same (type, source, target) → same edge id → merged
    assert len(g.edges) == 1
    merged = list(g.edges.values())[0]
    assert set(merged.voices) == {"user", "assistant"}


# ---------------------------------------------------------------------------
# Store roundtrip
# ---------------------------------------------------------------------------

def test_store_roundtrip(tmp_path: Path):
    g = Graph()
    g.add_source(Source(session_id="s", file_path="/tmp/fake", speaker="user",
                        turn_idx=0, text="hello"))
    g.add_concept(_mk_concept("shipped feature", "decision", "normative"))
    g.add_edge(Edge(type="voice-cross", source="s#0", target="shipped-feature",
                    established_at="s#0", voices=["user"]))

    path = tmp_path / "g.json"
    save_graph(g, path)
    assert path.exists()

    g2 = load_graph(path)
    assert set(g2.concepts) == set(g.concepts)
    assert len(g2.edges) == 1
    assert g2.sources["s#0"].text == "hello"
    # Indices rebuilt
    assert "shipped-feature" in g2.by_class["decision"]


# ---------------------------------------------------------------------------
# apply_classification state transitions
# ---------------------------------------------------------------------------

def test_apply_cites_existing_concept():
    g = Graph()
    g.add_concept(_mk_concept("walker primitive", "invariant", "metaphysical"))
    src = Source(session_id="s", file_path="x", speaker="user",
                 turn_idx=1, text="ya, the walker is right")
    result = ClassifyResult.from_raw({
        "act": "walk",
        "cites": [{"concept_id": "walker-primitive", "edge": "voice-cross"}],
        "creates": [],
        "concept_edges": [],
    })
    apply_classification(g, src, result, _DeterministicEmbedder())
    assert "s#1" in g.concepts["walker-primitive"].source_refs
    assert len(g.edges) == 1
    e = list(g.edges.values())[0]
    assert e.type == "voice-cross"

def test_apply_creates_new_concept():
    g = Graph()
    src = Source(session_id="s", file_path="x", speaker="assistant",
                 turn_idx=0, text="let's switch to mass-weighted cosine")
    result = ClassifyResult.from_raw({
        "act": "add",
        "cites": [],
        "creates": [{
            "topic": "mass-weighted cosine scoring",
            "class": "decision",
            "nature": "normative",
            "parent_hint": None,
        }],
        "concept_edges": [],
    })
    apply_classification(g, src, result, _DeterministicEmbedder())
    assert "mass-weighted-cosine-scoring" in g.concepts
    c = g.concepts["mass-weighted-cosine-scoring"]
    assert c.class_ == "decision"
    assert c.nature == "normative"

def test_apply_retract_transitions_ephemeral():
    g = Graph()
    g.add_concept(Concept(
        id="committing-now", topic="committing all three fixes now",
        class_="ephemeral", nature="normative",
        embedding=np.random.rand(8).astype(np.float32),
    ))
    assert "committing-now" in g.open_ephemerals

    retract_src = Source(session_id="s", file_path="x", speaker="assistant",
                         turn_idx=2, text="Wait — the regen had side-effects")
    result = ClassifyResult.from_raw({
        "act": "walk",
        "cites": [{"concept_id": "committing-now", "edge": "retract"}],
        "creates": [],
        "concept_edges": [],
    })
    apply_classification(g, retract_src, result, _DeterministicEmbedder())
    assert g.concepts["committing-now"].state == "retracted"
    assert "committing-now" not in g.open_ephemerals

def test_apply_none_turn_is_no_op_except_source():
    g = Graph()
    src = Source(session_id="s", file_path="x", speaker="user",
                 turn_idx=0, text="do whatever you feel necessary")
    result = ClassifyResult.from_raw({"act": "none", "cites": [], "creates": [], "concept_edges": []})
    apply_classification(g, src, result, _DeterministicEmbedder())
    assert "s#0" in g.sources
    assert len(g.concepts) == 0
    assert len(g.edges) == 0


# ---------------------------------------------------------------------------
# ingest_session end-to-end
# ---------------------------------------------------------------------------

def _write_fake_jsonl(path: Path, turns: list[tuple[str, str]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for speaker, text in turns:
            rec = {
                "type": speaker,
                "message": {"role": speaker,
                            "content": [{"type": "text", "text": text}]},
            }
            f.write(json.dumps(rec) + "\n")


def test_ingest_session_end_to_end(tmp_path: Path):
    jsonl = tmp_path / "abcd1234-fake.jsonl"
    _write_fake_jsonl(jsonl, [
        ("assistant", "I'll ship the mass-weighted cosine scoring."),
        ("user", "ya"),
        ("assistant", "Wait — actually we should dispute the anchor approach first."),
    ])

    # Verify read_session_turns parses the fake jsonl
    turns = read_session_turns(jsonl)
    assert len(turns) == 3
    assert turns[0].speaker == "assistant"
    assert turns[1].text == "ya"

    # Canned classifier: turn 0 creates a concept, turn 1 cites it with
    # voice-cross, turn 2 creates a dispute
    canned = {
        "I'll ship the mass-weighted cosine scoring.": {
            "act": "add",
            "cites": [],
            "creates": [{
                "topic": "mass-weighted cosine scoring",
                "class": "decision",
                "nature": "normative",
                "parent_hint": None,
            }],
            "concept_edges": [],
        },
        "ya": {
            "act": "walk",
            "cites": [{"concept_id": "mass-weighted-cosine-scoring", "edge": "voice-cross"}],
            "creates": [],
            "concept_edges": [],
        },
        "Wait — actually we should dispute the anchor approach first.": {
            "act": "add",
            "cites": [],
            "creates": [{
                "topic": "anchor approach",
                "class": "decision",
                "nature": "normative",
                "parent_hint": None,
            }],
            "concept_edges": [],
        },
    }

    graph = Graph()
    stats = ingest_session(
        graph, jsonl,
        embedder=_DeterministicEmbedder(),
        classifier=_CannedClassifier(canned),
        save_every=100,  # don't write intermediate
        save_to=None,
    )
    assert stats["total_turns"] == 3
    assert "mass-weighted-cosine-scoring" in graph.concepts
    assert "anchor-approach" in graph.concepts
    # The "ya" turn should have voice-crossed the mass-weighted concept
    c = graph.concepts["mass-weighted-cosine-scoring"]
    assert len(c.source_refs) == 2  # created in turn 0, cited in turn 1
    # And there should be a voice-cross edge from abcd1234#1 → the concept
    edge_types = {e.type for e in graph.edges.values()}
    assert "voice-cross" in edge_types
