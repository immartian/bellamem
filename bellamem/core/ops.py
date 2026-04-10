"""The seven operations.

Every write to the belief tree is exactly one of these. They are the
entire mutation API. Domain adapters translate incoming signals (chat
messages, code events, ...) into Op calls.

    CONFIRM   accumulate evidence for an existing belief
    AMEND     confirm + refine description with a detail
    ADD       new supporting child belief
    DENY      new counter-belief (⊥) under a target
    CAUSE     new causal predecessor (⇒) under an effect
    MERGE     two beliefs are one (R3 emergence — v0.5)
    MOVE      reparent for coherence (R2 entropy heal — v0.5)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .gene import Gene, Belief, REL_SUPPORT, REL_COUNTER, REL_CAUSE


OP_CONFIRM = "CONFIRM"
OP_AMEND = "AMEND"
OP_ADD = "ADD"
OP_DENY = "DENY"
OP_CAUSE = "CAUSE"
OP_MERGE = "MERGE"
OP_MOVE = "MOVE"


@dataclass
class OpResult:
    op: str
    belief: Optional[Belief]
    note: str = ""
    field: Optional[str] = None  # set by Bella.ingest after routing


def confirm(g: Gene, bid: str, voice: str = "", lr: float = 1.5,
            *, source: Optional[tuple[str, int]] = None) -> OpResult:
    if bid not in g.beliefs:
        return OpResult(OP_CONFIRM, None, f"missing target {bid}")
    g.confirm(bid, voice=voice, lr=lr, source=source)
    return OpResult(OP_CONFIRM, g.beliefs[bid])


def amend(g: Gene, bid: str, detail: str, voice: str = "", lr: float = 1.5,
          *, source: Optional[tuple[str, int]] = None) -> OpResult:
    if bid not in g.beliefs:
        return OpResult(OP_AMEND, None, f"missing target {bid}")
    g.amend(bid, detail, voice=voice, lr=lr, source=source)
    return OpResult(OP_AMEND, g.beliefs[bid])


def add(g: Gene, desc: str, *, parent: Optional[str] = None, voice: str = "",
        lr: float = 1.5, embedding=None, entity_refs=None,
        mass_floor: float = 0.0,
        source: Optional[tuple[str, int]] = None) -> OpResult:
    b = g.add(desc, parent=parent, rel=REL_SUPPORT, voice=voice, lr=lr,
              embedding=embedding, entity_refs=entity_refs,
              mass_floor=mass_floor, source=source)
    return OpResult(OP_ADD, b)


def deny(g: Gene, target_bid: str, desc: str, *, voice: str = "", lr: float = 1.5,
         embedding=None,
         source: Optional[tuple[str, int]] = None) -> OpResult:
    b = g.deny(target_bid, desc, voice=voice, lr=lr, embedding=embedding,
               source=source)
    if b is None:
        return OpResult(OP_DENY, None, f"missing target {target_bid}")
    return OpResult(OP_DENY, b)


def cause(g: Gene, effect_bid: str, desc: str, *, voice: str = "", lr: float = 1.5,
          embedding=None,
          source: Optional[tuple[str, int]] = None) -> OpResult:
    b = g.cause(effect_bid, desc, voice=voice, lr=lr, embedding=embedding,
                source=source)
    if b is None:
        return OpResult(OP_CAUSE, None, f"missing effect {effect_bid}")
    return OpResult(OP_CAUSE, b)


def merge(g: Gene, survivor_bid: str, absorbed_bid: str) -> OpResult:
    """Fold absorbed into survivor. Voices, log_odds, entities, children,
    and sources all move over. The absorbed belief is removed.
    """
    if survivor_bid not in g.beliefs or absorbed_bid not in g.beliefs:
        return OpResult(OP_MERGE, None, "missing side")
    if survivor_bid == absorbed_bid:
        return OpResult(OP_MERGE, g.beliefs[survivor_bid], "noop")
    s = g.beliefs[survivor_bid]
    a = g.beliefs[absorbed_bid]
    s.log_odds += a.log_odds
    s.voices.update(a.voices)
    s.n_voices = len(s.voices)
    for e in a.entity_refs:
        if e not in s.entity_refs:
            s.entity_refs.append(e)
    # Combine sources: survivor's existing sources + absorbed's, dedup
    # preserving order, cap to SOURCES_MAX. Two beliefs saying the same
    # thing from the same turn should count once, not twice.
    from .gene import SOURCES_MAX
    seen_sources = set(s.sources)
    for src in a.sources:
        if src not in seen_sources:
            s.sources.append(src)
            seen_sources.add(src)
    if len(s.sources) > SOURCES_MAX:
        s.sources = s.sources[-SOURCES_MAX:]
    # Reparent absorbed's children to survivor
    for cbid in a.children:
        if cbid in g.beliefs:
            g.beliefs[cbid].parent = survivor_bid
            if cbid not in s.children:
                s.children.append(cbid)
    # Detach absorbed from its parent
    if a.parent and a.parent in g.beliefs:
        pc = g.beliefs[a.parent].children
        if absorbed_bid in pc:
            pc.remove(absorbed_bid)
    if absorbed_bid in g.roots:
        g.roots.remove(absorbed_bid)
    del g.beliefs[absorbed_bid]
    return OpResult(OP_MERGE, s)


def move(g: Gene, bid: str, new_parent: Optional[str]) -> OpResult:
    """Reparent a belief (for entropy-driven restructure)."""
    if bid not in g.beliefs:
        return OpResult(OP_MOVE, None, "missing target")
    if new_parent and new_parent not in g.beliefs:
        return OpResult(OP_MOVE, None, "missing new parent")
    # Cycle guard
    cur = new_parent
    while cur:
        if cur == bid:
            return OpResult(OP_MOVE, None, "would cycle")
        cur = g.beliefs[cur].parent if cur in g.beliefs else None
    b = g.beliefs[bid]
    old_parent = b.parent
    if old_parent and old_parent in g.beliefs:
        pc = g.beliefs[old_parent].children
        if bid in pc:
            pc.remove(bid)
    elif bid in g.roots:
        g.roots.remove(bid)
    b.parent = new_parent
    if new_parent:
        g.beliefs[new_parent].children.append(bid)
    else:
        g.roots.append(bid)
    return OpResult(OP_MOVE, b)
