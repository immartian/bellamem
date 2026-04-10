"""bellamem audit — health report for the belief tree.

Read-only. Walks the forest and reports the entropy signals that
matter for context quality:

  1. Bandaid piles (R2 entropy signal)
     A belief with 3+ children whose descriptions contain
     recognisable fix / workaround / guard / special-case language.
     A pile of small patches around a single parent means the
     parent is a structural problem, not a series of isolated bugs.

  2. Root glut (per field)
     A field whose root-to-belief ratio is high has failed to
     grow structure — most beliefs are unconnected fragments.
     Healthy fields grow trees; unhealthy ones are lists.

  3. Single-voice rate (global)
     Fraction of non-reserved beliefs with n_voices=1. High rates
     mean the assistant is talking to itself — nothing got
     ratified by the user. Suspicious if ≫ 70%.

  4. Near-duplicate pairs (per field)
     Pairs of beliefs within the same field whose embeddings are
     cosine ≥ NEAR_DUP. R3 emerge should have merged these;
     if they exist, emerge is overdue.

  5. Mass limbo
     Beliefs stuck at mass ∈ [0.45, 0.55] — decisions that never
     landed. Not errors, but candidates for review.

  6. Garbage field names
     Fields named by auto-generated regex tokens over many
     beliefs. E.g. `log_odds_accumulate_log` housing 349 beliefs
     is a "misc" field with no semantic coherence.

  7. Top ratified decisions (multi-voice, non-reserved)
     What the user has explicitly affirmed across the session.

  8. Disputes (⊥ edges) summary
     Rejected approaches preserved in the tree.

The audit mutates nothing. It's a report surface for inspecting
how much useful structure the tree has accumulated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .gene import Belief, REL_COUNTER
from .embed import cosine

if TYPE_CHECKING:
    from .bella import Bella


BANDAID_MIN_CHILDREN = 3

_BANDAID_RE = re.compile(
    r"\b(fix|workaround|work around|guard|hack|bandaid|band-aid|"
    r"patch(ing)?|special[- ]case|edge case|kludge|monkey[- ]patch)\b",
    re.I,
)

# Entropy thresholds — tuned against the dogfood snapshot.
ROOT_GLUT_RATIO = 0.40         # roots / beliefs above this = structural failure
ROOT_GLUT_MIN_BELIEFS = 20     # ignore tiny fields; they're always "gluty"
NEAR_DUP_COSINE = 0.92         # merge candidate threshold
NEAR_DUP_MAX_PAIRS_PER_FIELD = 8  # cap scan output so large fields don't flood
MASS_LIMBO_LO = 0.45
MASS_LIMBO_HI = 0.55
GARBAGE_FIELD_MIN_BELIEFS = 50


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------

@dataclass
class BandaidPile:
    parent: Belief
    field_name: str
    children: list[Belief]


@dataclass
class RootGlut:
    field_name: str
    n_beliefs: int
    n_roots: int
    ratio: float


@dataclass
class DuplicatePair:
    field_name: str
    a: Belief
    b: Belief
    cosine: float


@dataclass
class GarbageField:
    field_name: str
    n_beliefs: int
    reason: str


@dataclass
class AuditReport:
    fields: int
    beliefs: int
    multi_voice: int
    dispute_count: int
    single_voice_rate: float = 0.0
    bandaid_piles: list[BandaidPile] = field(default_factory=list)
    root_gluts: list[RootGlut] = field(default_factory=list)
    near_duplicates: list[DuplicatePair] = field(default_factory=list)
    mass_limbo: list[tuple[str, Belief]] = field(default_factory=list)
    garbage_fields: list[GarbageField] = field(default_factory=list)
    top_ratified: list[tuple[str, Belief]] = field(default_factory=list)
    top_disputes: list[tuple[str, Belief]] = field(default_factory=list)

    def is_clean(self) -> bool:
        # "Clean" now means: no bandaid piles AND no root gluts AND no
        # garbage-named megafields AND single-voice rate reasonable.
        # Disputes and limbo are not errors.
        if self.bandaid_piles:
            return False
        if self.root_gluts:
            return False
        if self.garbage_fields:
            return False
        if self.single_voice_rate > 0.80:
            return False
        return True


# ---------------------------------------------------------------------------
# Core audit
# ---------------------------------------------------------------------------

def _is_garbage_field_name(name: str) -> bool:
    """Heuristic: a field name that looks auto-generated from regex picks.

    Signals:
      - three underscore-separated tokens (the `_field_name_from` cap)
      - any obvious sentinel segments ("log_odds", "missing", "accumulate")
      - mixed underscores and hyphens (snake + dash is suspicious)

    A field name this ugly is usually a "misc" bucket that the routing
    threshold could not reject. Flagging it invites a rename via R3.
    """
    if not name or name.startswith("__"):
        return False
    parts = name.split("_")
    if len(parts) >= 3:
        # Classic three-token garbage
        suspicious_tokens = {"log", "odds", "accumulate", "missing", "coding",
                             "agent", "lo", "stats", "fields"}
        if any(p in suspicious_tokens for p in parts):
            return True
    if "-" in name and "_" in name:
        return True
    return False


def audit(bella: "Bella", *, top_n: int = 10) -> AuditReport:
    from .bella import is_reserved_field

    total = 0
    multi_voice = 0
    dispute_count = 0
    other_beliefs: list[tuple[str, Belief]] = []
    disputes: list[tuple[str, Belief]] = []

    for fname, g in bella.fields.items():
        for b in g.beliefs.values():
            total += 1
            if is_reserved_field(fname):
                continue
            other_beliefs.append((fname, b))
            if b.n_voices >= 2:
                multi_voice += 1
            if b.rel == REL_COUNTER:
                dispute_count += 1
                disputes.append((fname, b))

    non_reserved_total = len(other_beliefs)
    single_voice_total = sum(1 for _, b in other_beliefs if b.n_voices < 2)
    single_voice_rate = (
        single_voice_total / non_reserved_total if non_reserved_total else 0.0
    )

    report = AuditReport(
        fields=len(bella.fields),
        beliefs=total,
        multi_voice=multi_voice,
        dispute_count=dispute_count,
        single_voice_rate=single_voice_rate,
    )

    # 1) Bandaid piles
    for fname, g in bella.fields.items():
        if is_reserved_field(fname):
            continue
        for parent in g.beliefs.values():
            if len(parent.children) < BANDAID_MIN_CHILDREN:
                continue
            flagged: list[Belief] = []
            for cid in parent.children:
                c = g.beliefs.get(cid)
                if c and _BANDAID_RE.search(c.desc):
                    flagged.append(c)
            if len(flagged) >= BANDAID_MIN_CHILDREN:
                report.bandaid_piles.append(
                    BandaidPile(parent=parent, field_name=fname, children=flagged)
                )
    report.bandaid_piles.sort(key=lambda bp: len(bp.children), reverse=True)

    # 2) Root glut per field
    for fname, g in bella.fields.items():
        if is_reserved_field(fname):
            continue
        n = len(g.beliefs)
        if n < ROOT_GLUT_MIN_BELIEFS:
            continue
        r = len(g.roots)
        ratio = r / n if n else 0.0
        if ratio >= ROOT_GLUT_RATIO:
            report.root_gluts.append(
                RootGlut(field_name=fname, n_beliefs=n, n_roots=r, ratio=ratio)
            )
    report.root_gluts.sort(key=lambda g: g.ratio, reverse=True)

    # 3) Near-duplicate pairs (within each non-reserved field)
    for fname, g in bella.fields.items():
        if is_reserved_field(fname) and fname != "__self__":
            # __self__ is allowed to be inspected — it's the most
            # obvious place where near-duplicates accumulate and we
            # actively want to see them.
            continue
        beliefs_with_emb = [
            b for b in g.beliefs.values() if b.embedding
        ]
        if len(beliefs_with_emb) < 2:
            continue
        # O(n²) within-field; we cap results per field to keep the
        # audit fast even on the garbage megafields.
        pairs: list[DuplicatePair] = []
        seen_ids: set[frozenset[str]] = set()
        # Sort by mass desc so the highest-mass pairs surface first
        sorted_b = sorted(beliefs_with_emb, key=lambda b: b.mass, reverse=True)
        for i, a in enumerate(sorted_b):
            if len(pairs) >= NEAR_DUP_MAX_PAIRS_PER_FIELD:
                break
            for b in sorted_b[i + 1:]:
                if len(pairs) >= NEAR_DUP_MAX_PAIRS_PER_FIELD:
                    break
                key = frozenset((a.id, b.id))
                if key in seen_ids:
                    continue
                sim = cosine(a.embedding, b.embedding)
                if sim >= NEAR_DUP_COSINE:
                    seen_ids.add(key)
                    pairs.append(DuplicatePair(field_name=fname, a=a, b=b, cosine=sim))
        report.near_duplicates.extend(pairs)
    report.near_duplicates.sort(key=lambda p: p.cosine, reverse=True)

    # 4) Mass limbo — beliefs stuck near the uncertainty center
    for fname, b in other_beliefs:
        if MASS_LIMBO_LO <= b.mass <= MASS_LIMBO_HI:
            report.mass_limbo.append((fname, b))
    # Show the oldest-touched (staleness = most abandoned)
    report.mass_limbo.sort(key=lambda t: t[1].last_touched)

    # 5) Garbage field names
    for fname, g in bella.fields.items():
        if is_reserved_field(fname):
            continue
        n = len(g.beliefs)
        if n < GARBAGE_FIELD_MIN_BELIEFS:
            continue
        if _is_garbage_field_name(fname):
            reason = (
                f"auto-generated name ({len(fname.split('_'))} tokens) "
                f"hosting {n} beliefs — likely a misc bucket"
            )
            report.garbage_fields.append(
                GarbageField(field_name=fname, n_beliefs=n, reason=reason)
            )
    report.garbage_fields.sort(key=lambda gf: gf.n_beliefs, reverse=True)

    # 6) Top ratified (multi-voice, non-reserved)
    ratified = [(fname, b) for fname, b in other_beliefs if b.n_voices >= 2]
    ratified.sort(key=lambda t: t[1].mass, reverse=True)
    report.top_ratified = ratified[:top_n]

    # 7) Top disputes by mass
    disputes.sort(key=lambda t: t[1].mass, reverse=True)
    report.top_disputes = disputes[:top_n]

    return report


# ---------------------------------------------------------------------------
# Rendering — plain text, suitable for print
# ---------------------------------------------------------------------------

def render_report(r: AuditReport, *, max_per_section: int = 10) -> str:
    lines: list[str] = []
    lines.append("bellamem audit")
    lines.append("=" * 64)
    lines.append(f"fields:            {r.fields}")
    lines.append(f"beliefs:           {r.beliefs}")
    lines.append(f"multi-voice:       {r.multi_voice}")
    lines.append(f"single-voice rate: {r.single_voice_rate:.0%}")
    lines.append(f"disputes:          {r.dispute_count}")
    lines.append("")

    problems: list[str] = []
    if r.bandaid_piles:
        problems.append(f"{len(r.bandaid_piles)} bandaid pile(s)")
    if r.root_gluts:
        problems.append(f"{len(r.root_gluts)} root-glut field(s)")
    if r.garbage_fields:
        problems.append(f"{len(r.garbage_fields)} garbage-named field(s)")
    if r.near_duplicates:
        problems.append(f"{len(r.near_duplicates)} near-duplicate pair(s)")
    if r.single_voice_rate > 0.80:
        problems.append(f"single-voice rate is {r.single_voice_rate:.0%}")

    if r.is_clean():
        lines.append("clean. no entropy signals detected.")
    else:
        lines.append("found: " + ", ".join(problems))
    lines.append("")

    # Bandaid piles
    lines.append("## bandaid piles (R2 entropy signal)")
    if not r.bandaid_piles:
        lines.append("  (none)")
    else:
        for bp in r.bandaid_piles[:max_per_section]:
            lines.append(f"  {len(bp.children)} patch-shaped children under "
                         f"[{bp.field_name}] {bp.parent.desc[:80]}")
            for c in bp.children[:5]:
                lines.append(f"      - {c.desc[:100]}")
    lines.append("")

    # Root gluts
    lines.append("## root glut (fields that failed to grow structure)")
    if not r.root_gluts:
        lines.append("  (none)")
    else:
        for rg in r.root_gluts[:max_per_section]:
            lines.append(
                f"  ratio={rg.ratio:.0%}  {rg.n_roots}/{rg.n_beliefs} roots  "
                f"[{rg.field_name[:40]}]"
            )
    lines.append("")

    # Near duplicates
    lines.append("## near-duplicate pairs (R3 merge candidates)")
    if not r.near_duplicates:
        lines.append("  (none)")
    else:
        for dp in r.near_duplicates[:max_per_section]:
            lines.append(
                f"  cos={dp.cosine:.2f}  [{dp.field_name[:22]}]"
            )
            lines.append(f"      a: {dp.a.desc[:96]}")
            lines.append(f"      b: {dp.b.desc[:96]}")
    lines.append("")

    # Garbage fields
    lines.append("## garbage field names (R3 rename candidates)")
    if not r.garbage_fields:
        lines.append("  (none)")
    else:
        for gf in r.garbage_fields[:max_per_section]:
            lines.append(f"  {gf.n_beliefs:4d}  {gf.field_name}")
            lines.append(f"         → {gf.reason}")
    lines.append("")

    # Mass limbo (shown compactly — often many)
    lines.append("## mass limbo (beliefs stuck near 0.50)")
    if not r.mass_limbo:
        lines.append("  (none)")
    else:
        lines.append(f"  {len(r.mass_limbo)} beliefs in the 0.45–0.55 band")
        for fname, b in r.mass_limbo[:5]:
            lines.append(f"    m={b.mass:.2f}  [{fname[:22]}]  {b.desc[:80]}")
        if len(r.mass_limbo) > 5:
            lines.append(f"    … {len(r.mass_limbo) - 5} more")
    lines.append("")

    # Top ratified decisions
    lines.append("## top ratified decisions (multi-voice)")
    if not r.top_ratified:
        lines.append("  (none — no user affirmations registered)")
    else:
        for fname, b in r.top_ratified[:max_per_section]:
            lines.append(f"  m={b.mass:.2f} v={b.n_voices}  [{fname[:22]}]  {b.desc[:100]}")
    lines.append("")

    # Top disputes
    lines.append("## top disputes (⊥ edges — rejected approaches)")
    if not r.top_disputes:
        lines.append("  (none)")
    else:
        for fname, b in r.top_disputes[:max_per_section]:
            lines.append(f"  ⊥ m={b.mass:.2f} v={b.n_voices}  [{fname[:22]}]  {b.desc[:100]}")
    lines.append("")

    return "\n".join(lines)
