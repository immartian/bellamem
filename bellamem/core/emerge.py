"""R3 emerge — structural self-healing pass.

Two moves, both conservative:

  1. Near-duplicate merge (within field)
     For each pair of beliefs in the same field whose embeddings are
     cosine ≥ `min_cosine`, fold the lower-mass one into the higher.
     Uses ops.merge, which transfers voices, log_odds, entities, and
     children. No mass is discarded. Same-voice attenuation already
     applied during original accumulate, so summing log_odds is safe.

  2. Garbage field rename via TF-IDF
     Fields matching the audit's "garbage name" heuristic get renamed
     from their own content. We compute token frequencies inside the
     field's top-mass beliefs, then weight them by inverse document
     frequency *across all fields* — a token that appears in every
     field's top beliefs (like "the", "session", "thing") gets
     IDF ≈ 0 and drops out automatically. No maintained stopword
     list — the corpus tunes itself.

     If the new name collides with an existing field, we append a
     numeric suffix. We do NOT auto-merge fields across renames —
     cross-field reorganization is R2's job, not R3's.

Design notes:

- No cross-field merges. Routing chose the original field; if the
  routing was wrong, the fix is R2 move, not R3 merge.
- No field splits. If a field's centroid variance is too high, that
  suggests the routing threshold is too loose — address the cause,
  not the symptom. Splitting is a future pass.
- No hand-maintained word lists. A maintained list is a bandaid —
  it grows without bound and fails on every new corpus. TF-IDF
  achieves the same thing with zero maintenance.
- Dry-run mode produces the same report without mutating anything.
- Idempotent: running emerge twice on an already-healed tree is a
  no-op.

Domain-agnostic: does not import from adapters.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from .audit import (
    NEAR_DUP_COSINE,
    _is_garbage_field_name,
)
from .embed import cosine
from .gene import Belief, Gene
from . import ops

if TYPE_CHECKING:
    from .bella import Bella


# A NameFn takes (bella, field_name) and returns either a new name
# or the original field_name if it can't improve on it. Used as a
# plug-in point so core/emerge.py stays zero-dep while the CLI can
# pass in an LLM-backed refiner.
NameFn = Callable[["Bella", str], str]


# Content tokens: 4+ letter alphanumeric runs starting with a letter.
# This filters digits, single chars, and punctuation without needing a
# maintained word list.
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]{3,}")


@dataclass
class MergePair:
    field_name: str
    survivor_desc: str
    absorbed_desc: str
    cosine: float


@dataclass
class FieldRename:
    old_name: str
    new_name: str
    n_beliefs: int


@dataclass
class EmergeReport:
    merges: list[MergePair] = field(default_factory=list)
    renames: list[FieldRename] = field(default_factory=list)
    beliefs_before: int = 0
    beliefs_after: int = 0
    fields_before: int = 0
    fields_after: int = 0

    def render(self) -> str:
        lines = [
            "bellamem emerge (R3)",
            "=" * 64,
            f"beliefs: {self.beliefs_before} → {self.beliefs_after}  "
            f"(-{self.beliefs_before - self.beliefs_after})",
            f"fields:  {self.fields_before} → {self.fields_after}",
            f"merges:  {len(self.merges)}",
            f"renames: {len(self.renames)}",
            "",
        ]
        if self.renames:
            lines.append("## field renames")
            for r in self.renames:
                lines.append(f"  {r.old_name}")
                lines.append(f"    → {r.new_name}  ({r.n_beliefs} beliefs)")
            lines.append("")
        if self.merges:
            lines.append("## near-duplicate merges (first 20)")
            for m in self.merges[:20]:
                lines.append(f"  cos={m.cosine:.2f}  [{m.field_name[:24]}]")
                lines.append(f"      + {m.survivor_desc[:80]}")
                lines.append(f"      − {m.absorbed_desc[:80]}")
            if len(self.merges) > 20:
                lines.append(f"  … {len(self.merges) - 20} more")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Content-based field renaming
# ---------------------------------------------------------------------------

def _content_tokens(desc: str) -> list[str]:
    """Extract lowercase 4+ letter tokens from a belief description.

    No stopword list — TF-IDF will suppress generic tokens later.
    """
    return [m.lower() for m in _WORD_RE.findall(desc or "")]


def _belief_token_set(b: Belief) -> set[str]:
    """The set of content tokens appearing in a belief's description."""
    return set(_content_tokens(b.desc))


def derive_field_name(bella: "Bella", field_name: str, *,
                      top_k_beliefs: int = 30,
                      min_rate_delta: float = 0.15) -> str:
    """Contrastive-rate field name.

    For each token that appears in the field's top-k beliefs, compute:

        rate_in  = beliefs in the field containing the token /
                   total beliefs in the field
        rate_out = beliefs outside the field containing the token /
                   total beliefs outside the field
        score    = rate_in - rate_out

    Tokens with score ≥ `min_rate_delta` are considered distinctive.
    The top three by score become the new field name.

    This answers the question "which tokens are way more common
    inside this field than outside?" — which is the actual naming
    question. TF-IDF answers a different question ("which tokens
    are rare globally?") and fails on filler words that happen to
    be dense inside *any* prose-heavy field.

    No maintained stopword list is used. The corpus decides what's
    distinctive purely from the in/out rate contrast.
    """
    from .bella import is_reserved_field

    g = bella.fields.get(field_name)
    if g is None or not g.beliefs:
        return field_name

    # 1. Collect all non-reserved beliefs, partitioned in/out of target
    in_beliefs: list[Belief] = list(g.beliefs.values())
    out_beliefs: list[Belief] = []
    for fname, gf in bella.fields.items():
        if fname == field_name or is_reserved_field(fname):
            continue
        out_beliefs.extend(gf.beliefs.values())

    n_in = len(in_beliefs)
    n_out = len(out_beliefs)
    if n_in == 0:
        return field_name

    # 2. Token presence counts (per-belief, not per-occurrence)
    in_counts: dict[str, int] = {}
    for b in in_beliefs:
        for tok in _belief_token_set(b):
            in_counts[tok] = in_counts.get(tok, 0) + 1

    out_counts: dict[str, int] = {}
    for b in out_beliefs:
        for tok in _belief_token_set(b):
            out_counts[tok] = out_counts.get(tok, 0) + 1

    # 3. Candidate tokens: restrict to the top-k mass beliefs' vocabulary
    #    (don't try to name a field off low-mass noise).
    top = sorted(g.beliefs.values(), key=lambda b: b.mass, reverse=True)[:top_k_beliefs]
    candidate_tokens: set[str] = set()
    for b in top:
        candidate_tokens.update(_belief_token_set(b))

    # 4. Contrastive rate score
    scored: list[tuple[float, str]] = []
    for tok in candidate_tokens:
        in_n = in_counts.get(tok, 0)
        out_n = out_counts.get(tok, 0)
        rate_in = in_n / n_in
        rate_out = (out_n / n_out) if n_out > 0 else 0.0
        delta = rate_in - rate_out
        if delta < min_rate_delta:
            continue
        scored.append((delta, tok))

    if not scored:
        return field_name

    # Sort by delta desc, tiebreak by token asc for determinism
    scored.sort(key=lambda t: (-t[0], t[1]))
    picks = [tok for _score, tok in scored[:3]]
    return "_".join(picks)[:40]


def _unique_field_name(candidate: str, existing: set[str]) -> str:
    """Resolve collisions by appending _2, _3, ..."""
    if candidate not in existing:
        return candidate
    i = 2
    while f"{candidate}_{i}" in existing:
        i += 1
    return f"{candidate}_{i}"


# ---------------------------------------------------------------------------
# Near-duplicate merge
# ---------------------------------------------------------------------------

def _find_merge_pairs(g: Gene, min_cosine: float) -> list[tuple[str, str, float]]:
    """Return (survivor_bid, absorbed_bid, cosine) for near-dup pairs.

    Pairs are greedy: iterate beliefs in mass desc order; for each
    belief that still survives, find any other surviving belief with
    cos ≥ min_cosine and mark the lower-mass one for absorption.

    Each belief can be absorbed at most once per run; the caller
    applies merges in the returned order.
    """
    embs = [(bid, b) for bid, b in g.beliefs.items() if b.embedding]
    # Sort by mass desc — highest-mass beliefs act as survivors by default
    embs.sort(key=lambda t: t[1].mass, reverse=True)

    absorbed: set[str] = set()
    pairs: list[tuple[str, str, float]] = []
    for i, (bid_i, b_i) in enumerate(embs):
        if bid_i in absorbed:
            continue
        for bid_j, b_j in embs[i + 1:]:
            if bid_j in absorbed:
                continue
            sim = cosine(b_i.embedding, b_j.embedding)
            if sim >= min_cosine:
                pairs.append((bid_i, bid_j, sim))
                absorbed.add(bid_j)
    return pairs


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def emerge(bella: "Bella", *, min_cosine: float = NEAR_DUP_COSINE,
           dry_run: bool = False,
           name_fn: Optional[NameFn] = None) -> EmergeReport:
    """Run one R3 pass. Returns an EmergeReport.

    Args:
        min_cosine: threshold for near-duplicate merge
        dry_run: report without mutating
        name_fn: optional field-namer callback; defaults to the zero-dep
                 contrastive-rate derivation. Pass an LLM-backed callback
                 for cases where the deterministic baseline can't find
                 distinctive vocabulary (e.g. corpus is a single topic).
    """
    from .bella import is_reserved_field

    if name_fn is None:
        name_fn = derive_field_name

    report = EmergeReport()
    report.beliefs_before = sum(len(g.beliefs) for g in bella.fields.values())
    report.fields_before = len(bella.fields)

    # --- 1. Near-duplicate merges (per field) ------------------------------
    for fname, g in list(bella.fields.items()):
        # __self__ is allowed — it's the highest-value field to heal
        if is_reserved_field(fname) and fname != "__self__":
            continue
        pairs = _find_merge_pairs(g, min_cosine)
        for survivor_bid, absorbed_bid, sim in pairs:
            survivor = g.beliefs.get(survivor_bid)
            absorbed = g.beliefs.get(absorbed_bid)
            if survivor is None or absorbed is None:
                continue
            report.merges.append(MergePair(
                field_name=fname,
                survivor_desc=survivor.desc,
                absorbed_desc=absorbed.desc,
                cosine=sim,
            ))
            if not dry_run:
                ops.merge(g, survivor_bid, absorbed_bid)

    # --- 2. Field renames ---------------------------------------------------
    # Compute all renames first, then apply together so iteration is safe.
    planned_renames: list[tuple[str, str]] = []
    existing_names = set(bella.fields.keys())
    for fname in list(bella.fields.keys()):
        if is_reserved_field(fname):
            continue
        if not _is_garbage_field_name(fname):
            continue
        g = bella.fields[fname]
        new_name = name_fn(bella, fname)
        if new_name == fname:
            continue  # couldn't derive better name
        # Sanitize the LLM output / namer output to a valid field identifier:
        # lower, alnum + underscore only, ≤ 40 chars.
        new_name = re.sub(r"[^a-z0-9_]+", "_", new_name.lower()).strip("_")[:40]
        if not new_name or new_name == fname:
            continue
        # Resolve collision against everything we won't be renaming away
        avoid = (existing_names - {fname}) | {n for _, n in planned_renames}
        new_name = _unique_field_name(new_name, avoid)
        planned_renames.append((fname, new_name))
        report.renames.append(FieldRename(
            old_name=fname,
            new_name=new_name,
            n_beliefs=len(g.beliefs),
        ))

    if not dry_run:
        for old, new in planned_renames:
            g = bella.fields.pop(old)
            g.name = new
            bella.fields[new] = g
        # Entity index has stale field names embedded in it; force rebuild.
        if planned_renames:
            bella._entity_index = None

    report.beliefs_after = sum(len(g.beliefs) for g in bella.fields.values())
    report.fields_after = len(bella.fields)
    return report
