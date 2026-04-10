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


# Bounded history length for Belief.jumps. We don't need the full
# accumulate log; just enough to detect sign flips and rank recent
# surprises. 32 covers typical multi-session accumulation without
# ballooning the snapshot.
JUMPS_MAX = 32

# Bounded provenance length for Belief.sources. Each source is a
# (session_key, line_number) tuple recording where an accumulate came
# from. Same bound as jumps — hot beliefs drop oldest first while
# always keeping the most recent sources for "where did this come
# from?" queries.
SOURCES_MAX = 32


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
    # R1 accumulate history — append-only list of (timestamp, delta_log_odds, voice)
    # for each accumulate() call. Bounded to JUMPS_MAX (oldest dropped).
    # Used by core/surprise.py for surprise scoring and sign-flip detection;
    # the main tree does not depend on it.
    jumps: list[tuple[float, float, str]] = field(default_factory=list)
    # Evidence provenance — list of (session_key, line_number) tuples,
    # one per accumulate that came from a transcript. Programmatic
    # accumulates (tests, CLI actions) don't add to sources. Bounded
    # to SOURCES_MAX. Parallel to jumps conceptually, separate in
    # storage because they answer different questions: jumps = Jaynes
    # dynamics, sources = "which line of which session did this evidence
    # come from?"
    sources: list[tuple[str, int]] = field(default_factory=list)

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

    def accumulate(self, lr: float, voice: str = "", *,
                   attenuate_same_voice: float = 0.1,
                   source: Optional[tuple[str, int]] = None) -> None:
        """R1: add log(lr) to the accumulator. Same-voice evidence is attenuated.

        Records each effective step into self.jumps so downstream analysis
        (core/surprise.py) can weight jumps by prior uncertainty and detect
        sign flips. The floor clamp is applied AFTER the raw delta is logged
        so we still see an attempted downward move even if mass_floor caught it.

        If `source` is provided, it is appended to self.sources with
        SOURCES_MAX bounding. Programmatic accumulates (tests, CLI actions
        without transcript provenance) should pass source=None.
        """
        effective_lr = lr
        if voice and voice in self.voices:
            # Same source saying it again — attenuated (SPEC §R1 voice attenuation)
            effective_lr = 1.0 + (lr - 1.0) * attenuate_same_voice
        delta = log_lr(effective_lr)
        self.log_odds += delta
        self._enforce_floor()
        now = time.time()
        self.jumps.append((now, delta, voice or ""))
        if len(self.jumps) > JUMPS_MAX:
            # Drop oldest — we care about recent surprise dynamics
            self.jumps = self.jumps[-JUMPS_MAX:]
        if source is not None:
            self.sources.append(source)
            if len(self.sources) > SOURCES_MAX:
                self.sources = self.sources[-SOURCES_MAX:]
        if voice:
            if voice not in self.voices:
                self.n_voices += 1
            self.voices.add(voice)
        self.last_touched = now

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
            "jumps": [list(j) for j in self.jumps],
            "sources": [list(s) for s in self.sources],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Belief":
        raw_jumps = d.get("jumps") or []
        jumps: list[tuple[float, float, str]] = []
        for j in raw_jumps:
            if isinstance(j, (list, tuple)) and len(j) >= 2:
                ts = float(j[0])
                delta = float(j[1])
                voice = str(j[2]) if len(j) >= 3 else ""
                jumps.append((ts, delta, voice))
        raw_sources = d.get("sources") or []
        sources: list[tuple[str, int]] = []
        for s in raw_sources:
            if isinstance(s, (list, tuple)) and len(s) >= 2:
                try:
                    sources.append((str(s[0]), int(s[1])))
                except (ValueError, TypeError):
                    continue
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
            jumps=jumps,
            sources=sources,
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
            mass_floor: float = 0.0,
            source: Optional[tuple[str, int]] = None) -> Belief:
        """Add a belief (or accumulate if it already exists by id).

        Returns the resulting Belief. The caller gets a stable reference.
        mass_floor is only applied on first insert; existing beliefs keep theirs.
        """
        if parent and parent not in self.beliefs:
            parent = None  # dangling parent → promote to root
        bid = belief_id(desc, parent)
        if bid in self.beliefs:
            b = self.beliefs[bid]
            b.accumulate(lr, voice, source=source)
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
        b.accumulate(lr, voice, source=source)
        self.beliefs[bid] = b
        if parent:
            self.beliefs[parent].children.append(bid)
        else:
            self.roots.append(bid)
        return b

    def confirm(self, bid: str, voice: str = "", lr: float = 1.5,
                *, source: Optional[tuple[str, int]] = None) -> None:
        if bid in self.beliefs:
            self.beliefs[bid].accumulate(lr, voice, source=source)

    def amend(self, bid: str, detail: str, voice: str = "", lr: float = 1.5,
              *, source: Optional[tuple[str, int]] = None) -> None:
        if bid in self.beliefs:
            b = self.beliefs[bid]
            if detail and detail not in b.desc:
                b.desc = f"{b.desc}; {detail}"[:200]
            b.accumulate(lr, voice, source=source)

    def deny(self, target_bid: str, desc: str, *, voice: str = "", lr: float = 1.5,
             embedding: Optional[list[float]] = None,
             source: Optional[tuple[str, int]] = None) -> Optional[Belief]:
        """Explicit counter-belief — a ⊥ child of the target."""
        if target_bid not in self.beliefs:
            return None
        return self.add(desc, parent=target_bid, rel=REL_COUNTER,
                        voice=voice, lr=lr, embedding=embedding, source=source)

    def cause(self, effect_bid: str, desc: str, *, voice: str = "", lr: float = 1.5,
              embedding: Optional[list[float]] = None,
              source: Optional[tuple[str, int]] = None) -> Optional[Belief]:
        """Causal predecessor — ⇒ child of the effect."""
        if effect_bid not in self.beliefs:
            return None
        return self.add(desc, parent=effect_bid, rel=REL_CAUSE,
                        voice=voice, lr=lr, embedding=embedding, source=source)

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
