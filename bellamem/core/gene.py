"""Belief tree — the substrate.

A Gene is one field: a forest of Beliefs within a single topic.
A Belief carries Jaynes mass (log_odds + n_voices), an embedding,
entity references, and typed edges to other beliefs.

Forked and generalized from bella/kernel.py. Domain-agnostic:
nothing here knows about news, code, or chat.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Mass — R1 accumulate
# ---------------------------------------------------------------------------

def mass_of(log_odds: float) -> float:
    """sigmoid(log_odds) → mass ∈ [0, 1]. Prior Λ₀ = 0 → m₀ = 0.5."""
    if log_odds > 40:
        return 1.0
    if log_odds < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-log_odds))


def log_lr(lr: float) -> float:
    """log likelihood ratio, clamped for numerical sanity."""
    if lr <= 0:
        return 0.0
    return math.log(max(1e-6, min(1e6, lr)))


# ---------------------------------------------------------------------------
# Belief — one node
# ---------------------------------------------------------------------------

REL_SUPPORT = "→"   # supports parent
REL_COUNTER = "⊥"   # denies / disputes parent
REL_CAUSE = "⇒"     # causes parent


@dataclass
class Belief:
    id: str                                 # stable hash of (desc, parent_id)
    desc: str                               # short fact, 3-8 words
    parent: Optional[str] = None            # parent belief id
    rel: str = REL_SUPPORT                  # relation to parent
    children: list[str] = field(default_factory=list)
    voices: set[str] = field(default_factory=set)  # distinct source names
    log_odds: float = 0.0                   # Jaynes accumulator (R1)
    n_voices: int = 0                       # independent evidence count
    embedding: Optional[list[float]] = None
    entity_refs: list[str] = field(default_factory=list)  # opaque entity ids
    event_time: float = field(default_factory=time.time)
    last_touched: float = field(default_factory=time.time)
    mass_floor: float = 0.0                 # mass never drops below this (P13)

    @property
    def mass(self) -> float:
        return mass_of(self.log_odds)

    def _floor_log_odds(self) -> float:
        """Inverse sigmoid of mass_floor — the minimum log_odds value."""
        if self.mass_floor <= 0.0:
            return float("-inf")
        if self.mass_floor >= 1.0:
            return float("inf")
        return -math.log(1.0 / self.mass_floor - 1.0)

    def _enforce_floor(self) -> None:
        floor = self._floor_log_odds()
        if self.log_odds < floor:
            self.log_odds = floor

    def accumulate(self, lr: float, voice: str = "", *, attenuate_same_voice: float = 0.1) -> None:
        """R1: add log(lr) to the accumulator. Same-voice evidence is attenuated."""
        effective_lr = lr
        if voice and voice in self.voices:
            # Same source saying it again — attenuated (SPEC §R1 voice attenuation)
            effective_lr = 1.0 + (lr - 1.0) * attenuate_same_voice
        self.log_odds += log_lr(effective_lr)
        self._enforce_floor()
        if voice:
            if voice not in self.voices:
                self.n_voices += 1
            self.voices.add(voice)
        self.last_touched = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "desc": self.desc,
            "parent": self.parent,
            "rel": self.rel,
            "children": list(self.children),
            "voices": sorted(self.voices),
            "log_odds": self.log_odds,
            "n_voices": self.n_voices,
            "embedding": self.embedding,
            "entity_refs": list(self.entity_refs),
            "event_time": self.event_time,
            "last_touched": self.last_touched,
            "mass_floor": self.mass_floor,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Belief":
        b = cls(
            id=d["id"],
            desc=d["desc"],
            parent=d.get("parent"),
            rel=d.get("rel", REL_SUPPORT),
            children=list(d.get("children", [])),
            voices=set(d.get("voices", [])),
            log_odds=d.get("log_odds", 0.0),
            n_voices=d.get("n_voices", 0),
            embedding=d.get("embedding"),
            entity_refs=list(d.get("entity_refs", [])),
            event_time=d.get("event_time", time.time()),
            last_touched=d.get("last_touched", time.time()),
            mass_floor=d.get("mass_floor", 0.0),
        )
        return b


def belief_id(desc: str, parent: Optional[str]) -> str:
    """Stable id: hash of normalized desc + parent. Same fact under same
    parent collapses to the same belief (implicit dedupe on insert)."""
    key = f"{(desc or '').strip().lower()}|{parent or ''}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Gene — one field (forest of beliefs on one topic)
# ---------------------------------------------------------------------------

@dataclass
class Gene:
    name: str
    beliefs: dict[str, Belief] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def add(self, desc: str, *, parent: Optional[str] = None,
            rel: str = REL_SUPPORT, voice: str = "", lr: float = 1.5,
            embedding: Optional[list[float]] = None,
            entity_refs: Optional[list[str]] = None,
            mass_floor: float = 0.0) -> Belief:
        """Add a belief (or accumulate if it already exists by id).

        Returns the resulting Belief. The caller gets a stable reference.
        mass_floor is only applied on first insert; existing beliefs keep theirs.
        """
        if parent and parent not in self.beliefs:
            parent = None  # dangling parent → promote to root
        bid = belief_id(desc, parent)
        if bid in self.beliefs:
            b = self.beliefs[bid]
            b.accumulate(lr, voice)
            if entity_refs:
                for e in entity_refs:
                    if e not in b.entity_refs:
                        b.entity_refs.append(e)
            return b
        b = Belief(
            id=bid, desc=desc.strip(), parent=parent, rel=rel,
            embedding=embedding, entity_refs=list(entity_refs or []),
            mass_floor=mass_floor,
        )
        b.accumulate(lr, voice)
        self.beliefs[bid] = b
        if parent:
            self.beliefs[parent].children.append(bid)
        else:
            self.roots.append(bid)
        return b

    def confirm(self, bid: str, voice: str = "", lr: float = 1.5) -> None:
        if bid in self.beliefs:
            self.beliefs[bid].accumulate(lr, voice)

    def amend(self, bid: str, detail: str, voice: str = "", lr: float = 1.5) -> None:
        if bid in self.beliefs:
            b = self.beliefs[bid]
            if detail and detail not in b.desc:
                b.desc = f"{b.desc}; {detail}"[:200]
            b.accumulate(lr, voice)

    def deny(self, target_bid: str, desc: str, *, voice: str = "", lr: float = 1.5,
             embedding: Optional[list[float]] = None) -> Optional[Belief]:
        """Explicit counter-belief — a ⊥ child of the target."""
        if target_bid not in self.beliefs:
            return None
        return self.add(desc, parent=target_bid, rel=REL_COUNTER,
                        voice=voice, lr=lr, embedding=embedding)

    def cause(self, effect_bid: str, desc: str, *, voice: str = "", lr: float = 1.5,
              embedding: Optional[list[float]] = None) -> Optional[Belief]:
        """Causal predecessor — ⇒ child of the effect."""
        if effect_bid not in self.beliefs:
            return None
        return self.add(desc, parent=effect_bid, rel=REL_CAUSE,
                        voice=voice, lr=lr, embedding=embedding)

    def render(self, *, max_mass_only: float = 0.0, indent: str = "  ") -> str:
        lines: list[str] = []
        def show(bid: str, depth: int) -> None:
            b = self.beliefs[bid]
            if b.mass < max_mass_only:
                return
            marker = {REL_SUPPORT: " ", REL_COUNTER: "⊥", REL_CAUSE: "⇒"}.get(b.rel, " ")
            pad = indent * depth
            m = f"m={b.mass:.2f}"
            v = f"v={b.n_voices}"
            lines.append(f"{pad}{marker} [{m} {v}] {b.desc}")
            for c in b.children:
                show(c, depth + 1)
        for r in self.roots:
            if r in self.beliefs:
                show(r, 0)
        return "\n".join(lines)

    def root_centroid(self) -> Optional[list[float]]:
        embs = [self.beliefs[r].embedding for r in self.roots
                if r in self.beliefs and self.beliefs[r].embedding]
        if not embs:
            return None
        dim = len(embs[0])
        return [sum(e[i] for e in embs) / len(embs) for i in range(dim)]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "created_at": self.created_at,
            "roots": list(self.roots),
            "beliefs": {bid: b.to_dict() for bid, b in self.beliefs.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Gene":
        g = cls(name=d["name"], created_at=d.get("created_at", time.time()))
        g.roots = list(d.get("roots", []))
        g.beliefs = {bid: Belief.from_dict(bd) for bid, bd in d.get("beliefs", {}).items()}
        return g
