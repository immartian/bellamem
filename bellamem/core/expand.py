"""EXPAND(focus, budget) — the thing that replaces /compact.

Instead of loading the last N messages, load the highest-mass
beliefs relevant to the current focus, plus any DISPUTES touching
it (so rejected approaches are never re-suggested), plus a small
recency tail.

v0 budget split (tunable):
  60%  high-mass beliefs anywhere (global rules/decisions)
  30%  field- and neighbor-relevant beliefs
  10%  recency tail

Returns a packed text block ready to drop into a system prompt,
plus a manifest for programmatic use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .embed import embed, cosine
from .gene import Belief, REL_SUPPORT, REL_COUNTER, REL_CAUSE
from .principles import PRINCIPLES_FIELD

if TYPE_CHECKING:
    from .bella import Bella


# Name of the reserved self-model field. Populated by future EW for
# first-person assistant observations ("I tend to X"). Empty in v0.
SELF_MODEL_FIELD = "__self__"


# ~4 chars per token — coarse, fine for v0
CHARS_PER_TOKEN = 4


@dataclass
class PackLine:
    field_name: str
    belief: Belief
    score: float
    bucket: str  # "mass" | "relevant" | "dispute" | "recency"

    def render(self) -> str:
        b = self.belief
        marker = {REL_SUPPORT: " ", REL_COUNTER: "⊥", REL_CAUSE: "⇒"}.get(b.rel, " ")
        return f"{marker} [{self.field_name} m={b.mass:.2f} v={b.n_voices}] {b.desc}"


@dataclass
class Pack:
    focus: str
    budget_tokens: int
    lines: list[PackLine] = field(default_factory=list)

    def text(self) -> str:
        header = f"# bellamem context (focus: {self.focus!r}, budget: {self.budget_tokens}t)"
        body = "\n".join(ln.render() for ln in self.lines)
        return f"{header}\n{body}"

    def used_tokens(self) -> int:
        return sum(len(ln.render()) for ln in self.lines) // CHARS_PER_TOKEN


def _mass_rank(bella: "Bella", q_emb: list[float] | None = None
               ) -> list[tuple[str, Belief, float]]:
    """Rank by mass, tie-broken by focus relevance.

    Many principles share the same floor mass (m≈0.98). When the mass
    budget is tight, we want the most-relevant principles first — e.g.
    C10 "fail loud" should float above C2 "KISS" for a try/except query.
    """
    out: list[tuple[str, Belief, float, float]] = []
    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            rel = cosine(q_emb, b.embedding) if (q_emb and b.embedding) else 0.0
            out.append((fname, b, b.mass, rel))
    # Primary: mass desc. Secondary: relevance desc. Both reversed.
    out.sort(key=lambda t: (t[2], t[3]), reverse=True)
    return [(f, b, m) for f, b, m, _ in out]


def _relevance_rank(bella: "Bella", q_emb: list[float]
                    ) -> list[tuple[str, Belief, float]]:
    out: list[tuple[str, Belief, float]] = []
    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            if not b.embedding:
                continue
            s = cosine(q_emb, b.embedding)
            out.append((fname, b, s))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _disputes_touching(bella: "Bella", q_emb: list[float], min_sim: float = 0.2
                       ) -> list[tuple[str, Belief, float]]:
    out: list[tuple[str, Belief, float]] = []
    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            if b.rel != REL_COUNTER:
                continue
            # Check similarity of the dispute itself OR its target
            s = cosine(q_emb, b.embedding) if b.embedding else 0.0
            if b.parent and b.parent in g.beliefs:
                p = g.beliefs[b.parent]
                if p.embedding:
                    s = max(s, cosine(q_emb, p.embedding))
            if s >= min_sim:
                out.append((fname, b, s))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _recency_rank(bella: "Bella") -> list[tuple[str, Belief, float]]:
    out: list[tuple[str, Belief, float]] = []
    now = time.time()
    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            age = max(1.0, now - b.last_touched)
            out.append((fname, b, 1.0 / age))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def expand(bella: "Bella", focus: str, budget_tokens: int = 1200,
           *, high_mass_floor: float = 0.65) -> Pack:
    """Build a mass-weighted context pack for the given focus.

    Args:
        bella: the forest
        focus: free-text description of what the agent is about to do
        budget_tokens: soft cap on the size of the returned pack
        high_mass_floor: beliefs with mass ≥ this are candidates for the
            always-on global layer
    """
    q_emb = embed(focus) if focus else None

    q_mass = int(budget_tokens * 0.60)
    q_rel = int(budget_tokens * 0.30)
    q_disp_and_recent = budget_tokens - q_mass - q_rel  # 10%

    pack = Pack(focus=focus, budget_tokens=budget_tokens)
    seen: set[str] = set()

    def try_add(fname: str, b: Belief, score: float, bucket: str,
                quota_used: list[int], quota: int) -> bool:
        if b.id in seen:
            return False
        line = PackLine(field_name=fname, belief=b, score=score, bucket=bucket)
        cost = len(line.render()) // CHARS_PER_TOKEN + 1
        if quota_used[0] + cost > quota:
            return False
        pack.lines.append(line)
        seen.add(b.id)
        quota_used[0] += cost
        return True

    # 1) HIGH-MASS global layer — always-on rules/decisions.
    # Tie-broken by focus relevance, so the most-relevant principles
    # float to the top of a tight budget.
    mass_used = [0]
    for fname, b, m in _mass_rank(bella, q_emb):
        if m < high_mass_floor:
            break
        try_add(fname, b, m, "mass", mass_used, q_mass)
        if mass_used[0] >= q_mass:
            break

    # 2) Field/entity-relevant layer
    rel_used = [0]
    if q_emb:
        for fname, b, s in _relevance_rank(bella, q_emb):
            if s <= 0:
                break
            try_add(fname, b, s, "relevant", rel_used, q_rel)
            if rel_used[0] >= q_rel:
                break

    # 3) Disputes touching focus — prevent re-suggesting rejected approaches
    disp_used = [0]
    disp_budget = q_disp_and_recent // 2
    if q_emb:
        for fname, b, s in _disputes_touching(bella, q_emb):
            try_add(fname, b, s, "dispute", disp_used, disp_budget)
            if disp_used[0] >= disp_budget:
                break

    # 4) Recency tail
    recency_used = [0]
    recency_budget = q_disp_and_recent - disp_used[0]
    for fname, b, s in _recency_rank(bella):
        try_add(fname, b, s, "recency", recency_used, recency_budget)
        if recency_used[0] >= recency_budget:
            break

    # Sort final lines by (bucket_priority, score desc) for a readable pack
    bucket_order = {"mass": 0, "dispute": 1, "relevant": 2, "recency": 3}
    pack.lines.sort(key=lambda ln: (bucket_order.get(ln.bucket, 9), -ln.score))
    return pack


# ---------------------------------------------------------------------------
# expand_before_edit — the bandaid-blocker pack
# ---------------------------------------------------------------------------

# Budget split for before-edit mode. Recency is *absent* by design:
# recency biases toward the most recent bandaid, which is exactly what
# we want to avoid here (P21).
BE_BUDGET_INVARIANTS = 0.40   # principles + other high-mass rules
BE_BUDGET_DISPUTES = 0.20     # ⊥ edges touching focus
BE_BUDGET_CAUSES = 0.20       # CAUSE predecessors touching focus
BE_BUDGET_BRIDGES = 0.10      # entity co-mentions (R6)
BE_BUDGET_SELF = 0.10         # __self__ observations (R4)


def _causes_for(bella: "Bella", q_emb: list[float]
                ) -> list[tuple[str, Belief, float]]:
    """Walk the tree for any belief whose relation is CAUSE and whose
    embedding is similar to the focus. These are root causes the agent
    has previously recorded for something close to the current task.
    """
    out: list[tuple[str, Belief, float]] = []
    for fname, g in bella.fields.items():
        if fname in (PRINCIPLES_FIELD, SELF_MODEL_FIELD):
            continue
        for b in g.beliefs.values():
            if b.rel != REL_CAUSE:
                continue
            if not b.embedding:
                continue
            s = cosine(q_emb, b.embedding)
            if s > 0:
                out.append((fname, b, s))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _bridges_for(bella: "Bella", entity: str | None, q_emb: list[float] | None
                 ) -> list[tuple[str, Belief, float]]:
    """Find beliefs that co-mention the focus entity (R6 bridging).

    If entity is given, look it up in the entity index. If not, fall back
    to beliefs with any entity_refs that embed close to the focus text —
    a softer bridge.
    """
    out: list[tuple[str, Belief, float]] = []
    if entity:
        for fname, bid in bella.entity_index_for(entity):
            g = bella.fields.get(fname)
            if g is None:
                continue
            b = g.beliefs.get(bid)
            if b is None:
                continue
            rel_score = cosine(q_emb, b.embedding) if (q_emb and b.embedding) else 0.0
            out.append((fname, b, 0.5 + 0.5 * rel_score))  # base 0.5 for co-mention
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _self_model_for(bella: "Bella", q_emb: list[float] | None
                    ) -> list[tuple[str, Belief, float]]:
    g = bella.fields.get(SELF_MODEL_FIELD)
    if not g or q_emb is None:
        return []
    out: list[tuple[str, Belief, float]] = []
    for b in g.beliefs.values():
        if not b.embedding:
            continue
        s = cosine(q_emb, b.embedding)
        out.append((SELF_MODEL_FIELD, b, s))
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _invariants_for(bella: "Bella", q_emb: list[float] | None,
                    floor: float = 0.80
                    ) -> list[tuple[str, Belief, float]]:
    """All principles (m >= floor) plus any other field belief at or above
    the floor. Tie-broken by focus relevance so the most topical principles
    surface first.
    """
    out: list[tuple[str, Belief, float, float]] = []
    for fname, g in bella.fields.items():
        if fname == SELF_MODEL_FIELD:
            continue
        for b in g.beliefs.values():
            if b.mass < floor:
                continue
            rel = cosine(q_emb, b.embedding) if (q_emb and b.embedding) else 0.0
            out.append((fname, b, b.mass, rel))
    out.sort(key=lambda t: (t[2], t[3]), reverse=True)
    return [(f, b, m) for f, b, m, _ in out]


def expand_before_edit(bella: "Bella", focus: str,
                       budget_tokens: int = 1500,
                       *, focus_entity: str | None = None) -> Pack:
    """Build a before-edit pack for the given focus.

    Unlike the generic expand(), this mode:
      - Loads invariants as 40% of the pack
      - Surfaces disputes touching the focus (prevents re-bandaiding)
      - Walks CAUSE edges near the focus (root-cause awareness)
      - Loads entity-bridge neighbors (R6)
      - Loads self-model observations (R4)
      - Does NOT load recency — recency biases toward the last bandaid.

    focus_entity, if given, is used for the bridging layer (R6). If
    omitted, bridging falls back to embedding-only neighborhood.
    """
    q_emb = embed(focus) if focus else None

    q_inv = int(budget_tokens * BE_BUDGET_INVARIANTS)
    q_disp = int(budget_tokens * BE_BUDGET_DISPUTES)
    q_cause = int(budget_tokens * BE_BUDGET_CAUSES)
    q_bridge = int(budget_tokens * BE_BUDGET_BRIDGES)
    q_self = budget_tokens - q_inv - q_disp - q_cause - q_bridge

    header_suffix = f" entity={focus_entity}" if focus_entity else ""
    pack = Pack(focus=f"{focus}{header_suffix}", budget_tokens=budget_tokens)
    seen: set[str] = set()

    def try_add(fname: str, b: Belief, score: float, bucket: str,
                quota_used: list[int], quota: int) -> bool:
        if b.id in seen:
            return False
        line = PackLine(field_name=fname, belief=b, score=score, bucket=bucket)
        cost = len(line.render()) // CHARS_PER_TOKEN + 1
        if quota_used[0] + cost > quota:
            return False
        pack.lines.append(line)
        seen.add(b.id)
        quota_used[0] += cost
        return True

    # 1) Invariants — principles + other high-mass rules
    inv_used = [0]
    for fname, b, m in _invariants_for(bella, q_emb):
        try_add(fname, b, m, "mass", inv_used, q_inv)
        if inv_used[0] >= q_inv:
            break

    # 2) Disputes touching the focus
    disp_used = [0]
    if q_emb:
        for fname, b, s in _disputes_touching(bella, q_emb, min_sim=0.30):
            try_add(fname, b, s, "dispute", disp_used, q_disp)
            if disp_used[0] >= q_disp:
                break

    # 3) CAUSE predecessors near the focus
    cause_used = [0]
    if q_emb:
        for fname, b, s in _causes_for(bella, q_emb):
            try_add(fname, b, s, "cause", cause_used, q_cause)
            if cause_used[0] >= q_cause:
                break

    # 4) Entity bridges (R6)
    bridge_used = [0]
    for fname, b, s in _bridges_for(bella, focus_entity, q_emb):
        try_add(fname, b, s, "bridge", bridge_used, q_bridge)
        if bridge_used[0] >= q_bridge:
            break

    # 5) Self-model observations (R4) — agent's own patterns
    self_used = [0]
    for fname, b, s in _self_model_for(bella, q_emb):
        try_add(fname, b, s, "self", self_used, q_self)
        if self_used[0] >= q_self:
            break

    bucket_order = {"mass": 0, "dispute": 1, "cause": 2, "bridge": 3, "self": 4}
    pack.lines.sort(key=lambda ln: (bucket_order.get(ln.bucket, 9), -ln.score))
    return pack
