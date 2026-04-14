"""LLM + embedding clients with disk-backed caches.

Two abstractions:
    Embedder        — text → np.ndarray, cached by sha256(text)
    TurnClassifier  — (source, context) → LLM JSON output, cached by
                      sha256(turn_text || context_ids || prompt_version)

Both caches are idempotency-critical — re-running against unchanged
sources and unchanged graph state must produce identical output.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np


PROMPT_VERSION = "v2"
LLM_MODEL_DEFAULT = "gpt-4o-mini"
EMBED_MODEL_DEFAULT = "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You watch a developer/AI conversation and maintain a project concept graph for the bellamem project.

For each new turn, decide what the turn does to the graph.

A concept is a distinct IDEA identified by a short topic phrase (3-10 words)
and classified on two axes:

class (temporal profile):
  - invariant: time-stable principles or structural facts; never expire
  - decision: revisable commitments ("we'll ship X before Y")
  - observation: factual claims about current state ("the bench scored N")
  - ephemeral: time-bound plans with a state machine (open/consumed/retracted/stale)

nature (epistemic type):
  - factual: measurable or checkable against reality
  - normative: commitments about how we SHOULD act or build
  - metaphysical: claims about what the system or its concepts ARE

The context you receive:
  - nearest_concepts: existing concepts in the graph relevant to this turn
  - open_ephemerals: ephemeral plans in this session still in "open" state
  - recent_turns: the last few turns for anaphora
  - current_turn: the turn to classify

Output strict JSON with:
  - act: "walk" | "add" | "none"
    "walk" = turn reacts to existing concepts (ratification, dispute, consume, retract)
    "add"  = turn introduces a genuinely new IDEA
    "none" = procedural (question, meta-authorization, acknowledgment, tool notification)

  - cites: list of {"concept_id": "<id from nearest/ephemerals>", "edge": "<edge_type>"}
    These are TURN → concept edges (how this turn touched an existing concept).
    edge types: voice-cross | support | dispute | retract | consume-success | consume-failure

  - creates: list of {"topic": "<3-8 word phrase>", "class": "<class>", "nature": "<nature>", "parent_hint": "<concept_id|null>"}
    Only create concepts for genuinely new IDEAS. Prefer citing existing concepts.

  - concept_edges: list of {"source": "<concept_id>", "target": "<concept_id>", "type": "<edge_type>", "confidence": "low|medium|high"}
    CONCEPT → concept structural relationships. First-class citizens — always
    look for these when a turn relates two ideas.
    edge types: cause | elaborate | dispute | support
    Use these whenever the turn establishes a structural link between two concepts:
      - cause:     "X because Y"       / "Y led to X"         / "the reason for X is Y"
      - elaborate: "X is a case of Y"  / "X specializes Y"    / "X refines Y"
      - dispute:   "X contradicts Y"   / "X is wrong, Y is right"
      - support:   "X confirms Y"      / "X and Y agree"  (use sparingly — weakest signal)
    Source and target must both be concept_ids, either from nearest_concepts
    or from concepts you're creating in this same turn.

ONE CONCEPT PER IDEA — do NOT split into attributes:
  Bad:  creates=[{topic: "shape encoding for concept map"},
                 {topic: "fill color encoding for concept map"},
                 {topic: "label positioning for concept map"},
                 {topic: "turn hubs representation"},
                 {topic: "edges representation in concept map"}]
  Good: creates=[{topic: "concept map visual vocabulary", class: "invariant", nature: "metaphysical"}]
        (one node capturing the whole discussion; sub-attributes are prose in the turn text,
         not separate concepts. Only split if each attribute will plausibly be cited again
         independently later.)

EXTRACTION RULES:
- Questions → act=none
- Meta-authorization ("do whatever", "sure go", "I trust your call") → act=none
- Short acknowledgments ("thanks", "ok", "ya") → act=walk with voice-cross IF the prior
  turn had a concrete proposal; otherwise act=none
- Retraction markers ("wait — hold on", "scratch that", "actually on reflection")
  → act=walk with retract cite
- Tool notifications, shell output, task-notification blocks → act=none

CONCEPT HYGIENE:
- Topic phrases: noun-phrase form, concise, canonical, re-usable across sessions
- Prefer merging near-variant topics — if "walker primitive" exists, don't create
  "walker abstraction", "walker protocol", "walker interface". Use the existing one.
- Prefer zero creates over one create. Prefer one create over many.
- When in doubt between walk and none, prefer none

EDGE HYGIENE:
- Don't default to "support" for cites. Support is the weakest edge and should be
  rare — use voice-cross for neutral mention, or prefer a more informative edge type.
- Always scan for cause / elaborate / dispute between the concepts you touch. Those
  are the structural edges the graph actually needs.
- concept_edges is first-class — populate it aggressively whenever two concepts
  relate, not just as an afterthought.

Return ONLY valid JSON.
"""

USER_TEMPLATE = """### nearest_concepts
{nearest}

### open_ephemerals
{ephemerals}

### recent_turns
{recent}

### current_turn
speaker: {speaker}
text: \"\"\"
{text}
\"\"\"

Output JSON only."""


# ---------------------------------------------------------------------------
# Embedder
# ---------------------------------------------------------------------------

class Embedder:
    """OpenAI text-embedding-3-small with disk cache.

    Cache key = sha256(text). Cache lives at `cache_path` — pass a
    /tmp path for scratch use, a persistent path for durable cache.
    """

    def __init__(
        self,
        cache_path: Path,
        *,
        model: str = EMBED_MODEL_DEFAULT,
        client=None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.model = model
        self._cache: dict[str, list[float]] = {}
        self._dirty = False
        self._client = client  # injected for tests; default OpenAI
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text())
            except Exception:
                self._cache = {}

    def save(self) -> None:
        if not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache))
        self._dirty = False

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI()
        return self._client

    def embed(self, text: str) -> np.ndarray:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in self._cache:
            return np.array(self._cache[key], dtype=np.float32)
        client = self._ensure_client()
        resp = client.embeddings.create(model=self.model, input=text[:8000])
        v = list(resp.data[0].embedding)
        self._cache[key] = v
        self._dirty = True
        return np.array(v, dtype=np.float32)


# ---------------------------------------------------------------------------
# TurnClassifier
# ---------------------------------------------------------------------------

@dataclass
class ClassifyResult:
    """Typed wrapper around the LLM's per-turn JSON output."""
    act: str  # "walk" | "add" | "none"
    cites: list[dict]
    creates: list[dict]
    concept_edges: list[dict]
    was_cached: bool = False

    @classmethod
    def from_raw(cls, data: dict, was_cached: bool = False) -> "ClassifyResult":
        return cls(
            act=data.get("act", "none"),
            cites=data.get("cites") or [],
            creates=data.get("creates") or [],
            concept_edges=data.get("concept_edges") or [],
            was_cached=was_cached,
        )


class TurnClassifier:
    """Per-turn LLM classifier with disk cache.

    Cache key = sha256(prompt_version || turn_text || sorted context_ids ||
    recent_ids). This IS the idempotency contract: same inputs produce
    the same cache hit, same graph state on re-run.
    """

    def __init__(
        self,
        cache_path: Path,
        *,
        model: str = LLM_MODEL_DEFAULT,
        client=None,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.model = model
        self._cache: dict[str, dict] = {}
        self._dirty = False
        self._client = client
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text())
            except Exception:
                self._cache = {}

    def save(self) -> None:
        if not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache))
        self._dirty = False

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI()
        return self._client

    @staticmethod
    def _cache_key(turn_text: str, context_ids: list[str], recent_ids: list[str]) -> str:
        h = hashlib.sha256()
        h.update(PROMPT_VERSION.encode())
        h.update(b"\x00")
        h.update(turn_text.encode())
        h.update(b"\x00")
        h.update(",".join(sorted(context_ids)).encode())
        h.update(b"\x00")
        h.update(",".join(recent_ids).encode())
        return h.hexdigest()

    def classify(
        self,
        *,
        turn_text: str,
        speaker: str,
        nearest_fmt: str,
        ephemerals_fmt: str,
        recent_fmt: str,
        context_ids: list[str],
        recent_ids: list[str],
    ) -> ClassifyResult:
        key = self._cache_key(turn_text, context_ids, recent_ids)
        if key in self._cache:
            return ClassifyResult.from_raw(self._cache[key], was_cached=True)

        user = USER_TEMPLATE.format(
            nearest=nearest_fmt,
            ephemerals=ephemerals_fmt,
            recent=recent_fmt,
            speaker=speaker,
            text=turn_text,
        )
        try:
            client = self._ensure_client()
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            raw = resp.choices[0].message.content or "{}"
            parsed = json.loads(raw)
        except Exception as e:
            # Fail closed: return a none fallback to keep the ingest
            # loop running, but do NOT cache it. Error responses must
            # never masquerade as real classifications on re-run —
            # we learned this the hard way on 2026-04-14 when missing
            # OPENAI_API_KEY caused 637 turns to cache as act=none,
            # and the next run silently hit the cache instead of
            # retrying. If the same turn happens to fail repeatedly,
            # it'll retry each time — acceptable cost for correctness.
            print(f"  [classify error] {e}")
            return ClassifyResult.from_raw(
                {"act": "none", "cites": [], "creates": [], "concept_edges": []},
                was_cached=False,
            )

        self._cache[key] = parsed
        self._dirty = True
        return ClassifyResult.from_raw(parsed, was_cached=False)
