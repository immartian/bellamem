"""Bella — the forest coordinator.

Holds all fields. Routes incoming claims to the right field via
embedding similarity. Creates new fields when no existing one is
close enough. Auto-confirms when a claim is very close to an
existing belief.

v0: pure programmatic routing, no LLM. A later version can call
an LLM to pick the right slot within a field (the PLACE_PROMPT
stage from grow.py).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from . import ops
from .embed import embed, cosine
from .gene import Gene, Belief, REL_SUPPORT, REL_COUNTER, REL_CAUSE


# Routing thresholds — tune with dogfooding
FIELD_MATCH = 0.25         # min similarity to route into an existing field
AUTO_CONFIRM = 0.85        # min similarity to auto-confirm an existing belief
CHILD_ATTACH = 0.35        # if sim to best belief is ≥ this, attach as child; else as root

# Reserved field prefix — these are system-owned and must never be routing
# targets for domain adapters. See PRINCIPLES.md P18.
RESERVED_PREFIX = "__"

# Reserved field names
SELF_MODEL_FIELD = "__self__"


def is_reserved_field(name: str) -> bool:
    return name.startswith(RESERVED_PREFIX)


@dataclass
class Claim:
    """Atomic input unit — what adapters produce.

    Domain-agnostic: no field knows about chat, code, news. Adapters
    translate their inputs into Claim objects.
    """
    text: str
    voice: str = ""                     # source id (e.g. "user", "assistant", file path)
    lr: float = 1.5                     # likelihood ratio — higher = stronger evidence
    relation: str = "add"               # add | confirm | deny | cause | amend | self_observation
    target_hint: Optional[str] = None   # optional belief id for deny/cause/amend
    target_field: Optional[str] = None  # field the target belongs to (disambiguates target_hint)
    entity_refs: list[str] = field(default_factory=list)
    event_time: float = field(default_factory=time.time)
    # Provenance: (session_key, line_number) tuple identifying where this
    # claim came from in its source transcript. None for claims that
    # don't have transcript provenance (tests, programmatic CLI actions).
    source: Optional[tuple[str, int]] = None
    extras: dict = field(default_factory=dict)


_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "are",
    "but", "not", "you", "was", "were", "have", "has", "had", "its",
    "their", "they", "them", "our", "your", "his", "her", "which", "when",
    "how", "why", "what", "who", "will", "just", "can", "all", "any",
    "one", "two", "more", "less", "also", "than", "then", "some", "about",
    "would", "should", "could", "must", "been", "being", "very", "even",
    "only", "really", "much", "many", "most", "other", "such", "here",
    "there", "each", "same", "through", "over", "under", "between",
}

_TECH_TOKENS = {
    "python", "rust", "bella", "bellamem", "openai", "typescript", "javascript",
    "go", "neo4j", "postgres", "sqlite", "jaynes", "networkx", "kuzudb",
    "pgvector", "hnswlib", "faiss", "cypher", "cli", "mcp", "claude",
    "embedder", "embedding", "expand", "ingest", "belief", "principle",
}

_CODE_IDENT_RE = re.compile(r"`([a-z_][a-z_0-9]{2,30})`")
_CAMEL_RE = re.compile(r"\b([A-Z][a-z]+[A-Z][A-Za-z0-9]+)\b")
_SNAKE_RE = re.compile(r"\b([a-z]+_[a-z0-9_]+)\b")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def _field_name_from(text: str) -> str:
    """Derive a human-recognizable field name from a claim's text.

    Priority (high to low):
      1. Backticked identifiers    — user-flagged code names
      2. snake_case / CamelCase    — likely identifiers
      3. Known tech/domain tokens  — Python, Rust, BELLA, etc.
      4. First non-stopword words  — last-resort fallback
    """
    t = (text or "").strip()
    picks: list[str] = []

    def add(word: str) -> None:
        lw = word.lower().strip("_-")
        if not lw or lw in _STOP_WORDS or lw in picks:
            return
        if len(lw) < 3:
            return
        picks.append(lw)

    # 1. Backticked identifiers
    for m in _CODE_IDENT_RE.findall(t):
        add(m)
        if len(picks) >= 3:
            break

    # 2. Snake / camel identifiers
    if len(picks) < 3:
        for m in _SNAKE_RE.findall(t):
            add(m)
            if len(picks) >= 3:
                break
    if len(picks) < 3:
        for m in _CAMEL_RE.findall(t):
            add(m)
            if len(picks) >= 3:
                break

    # 3. Tech tokens
    if len(picks) < 3:
        for w in t.lower().split():
            clean = re.sub(r"[^a-z0-9]", "", w)
            if clean in _TECH_TOKENS:
                add(clean)
            if len(picks) >= 3:
                break

    # 4. First non-stopword words
    if len(picks) < 2:
        for w in _WORD_RE.findall(t):
            add(w)
            if len(picks) >= 3:
                break

    if not picks:
        picks = ["field"]
    return "_".join(picks[:3])[:40]


class Bella:
    """The whole forest."""

    def __init__(self):
        self.fields: dict[str, Gene] = {}
        # cursor: opaque per-source position markers for streaming adapters
        # e.g. {"jsonl:/path/to/session.jsonl": {"offset": 12345}}
        self.cursor: dict[str, dict] = {}
        # decayed_at: wall-clock timestamp of the last decay pass (v4+).
        # Used by core/decay.py to compute Δt on the next save. A fresh
        # Bella starts at now(), so the first decay pass after load
        # operates on the wall-clock gap since that save.
        self.decayed_at: float = time.time()
        # entity_index: read-side cache, rebuilt from belief.entity_refs.
        # Maps entity_ref → list of (field_name, belief_id) that mention it.
        # Not persisted — rebuilt lazily on first access after load.
        self._entity_index: dict[str, list[tuple[str, str]]] | None = None

    # ----- routing ---------------------------------------------------------

    def find_field(self, q_emb: list[float], threshold: float = FIELD_MATCH,
                   *, include_reserved: bool = False
                   ) -> tuple[Optional[str], float, Optional[str]]:
        """Return (field_name, best_sim, best_belief_id) or (None, 0, None).

        Reserved fields (names starting with __) are excluded from routing
        by default. They are system-owned; see PRINCIPLES.md P18.
        """
        best_name, best_sim, best_bid = None, 0.0, None
        for name, g in self.fields.items():
            if not include_reserved and is_reserved_field(name):
                continue
            for bid, b in g.beliefs.items():
                if not b.embedding:
                    continue
                s = cosine(q_emb, b.embedding)
                if s > best_sim:
                    best_name, best_sim, best_bid = name, s, bid
        if best_sim >= threshold:
            return best_name, best_sim, best_bid
        return None, best_sim, best_bid

    # ----- the main write entry -------------------------------------------

    def ingest(self, claim: Claim) -> ops.OpResult:
        """Route and write a single claim. Returns the OpResult.

        The OpResult's `field` attribute is set so the caller can locate
        the landed belief later (e.g. for the turn-pair retroactive
        ratification pass in adapters/claude_code.py).
        """
        text = (claim.text or "").strip()
        if not text:
            return ops.OpResult("NOOP", None, "empty claim")

        emb = embed(text)

        def tag(result: ops.OpResult, fname: str) -> ops.OpResult:
            result.field = fname
            if result.belief and claim.entity_refs:
                self._touch_entity_index((fname, result.belief.id),
                                          claim.entity_refs)
            return result

        # Self-observation — R4, reserved field. Core decides routing
        # (P18: adapters express intent, core carries it out).
        if claim.relation == "self_observation":
            if SELF_MODEL_FIELD not in self.fields:
                self.fields[SELF_MODEL_FIELD] = Gene(name=SELF_MODEL_FIELD)
            g = self.fields[SELF_MODEL_FIELD]
            return tag(ops.add(g, text, parent=None, voice=claim.voice,
                               lr=claim.lr, embedding=emb,
                               entity_refs=claim.entity_refs,
                               source=claim.source),
                       SELF_MODEL_FIELD)

        # Relations with a known target — honor target_field if provided.
        if claim.relation in ("deny", "cause", "amend") and claim.target_hint:
            target_field = claim.target_field
            if target_field is None:
                # Scan all fields for the belief id (O(F) at our scale, fine)
                for fname, g in self.fields.items():
                    if claim.target_hint in g.beliefs:
                        target_field = fname
                        break
            if target_field and target_field in self.fields:
                g = self.fields[target_field]
                if claim.relation == "deny":
                    return tag(ops.deny(g, claim.target_hint, text,
                                        voice=claim.voice, lr=claim.lr,
                                        embedding=emb,
                                        source=claim.source), target_field)
                if claim.relation == "cause":
                    return tag(ops.cause(g, claim.target_hint, text,
                                         voice=claim.voice, lr=claim.lr,
                                         embedding=emb,
                                         source=claim.source), target_field)
                if claim.relation == "amend":
                    return tag(ops.amend(g, claim.target_hint, text,
                                         voice=claim.voice, lr=claim.lr,
                                         source=claim.source),
                               target_field)
            # Target not found — fall through to similarity-based routing.

        field_name, sim, best_bid = self.find_field(emb)

        # Implicit relations against the routed field (legacy path — kept
        # for regex EW that doesn't know target_hint yet)
        if claim.relation == "deny" and claim.target_hint and field_name:
            g = self.fields[field_name]
            return tag(ops.deny(g, claim.target_hint, text, voice=claim.voice,
                                lr=claim.lr, embedding=emb,
                                source=claim.source), field_name)
        if claim.relation == "cause" and claim.target_hint and field_name:
            g = self.fields[field_name]
            return tag(ops.cause(g, claim.target_hint, text, voice=claim.voice,
                                 lr=claim.lr, embedding=emb,
                                 source=claim.source), field_name)
        if claim.relation == "amend" and claim.target_hint and field_name:
            g = self.fields[field_name]
            return tag(ops.amend(g, claim.target_hint, text, voice=claim.voice,
                                 lr=claim.lr,
                                 source=claim.source), field_name)

        # No existing field close enough → birth a new one
        if field_name is None:
            name = _field_name_from(text)
            base = name
            i = 2
            while name in self.fields:
                name = f"{base}_{i}"
                i += 1
            g = Gene(name=name)
            self.fields[name] = g
            return tag(ops.add(g, text, parent=None, voice=claim.voice,
                               lr=claim.lr, embedding=emb,
                               entity_refs=claim.entity_refs,
                               source=claim.source), name)

        g = self.fields[field_name]

        # Auto-confirm on very high similarity
        if sim >= AUTO_CONFIRM and best_bid:
            if claim.relation == "deny":
                return tag(ops.deny(g, best_bid, text, voice=claim.voice,
                                    lr=claim.lr, embedding=emb,
                                    source=claim.source), field_name)
            return tag(ops.confirm(g, best_bid, voice=claim.voice,
                                   lr=claim.lr,
                                   source=claim.source), field_name)

        # Otherwise add as a child of the nearest belief (if close enough)
        if sim >= CHILD_ATTACH and best_bid:
            rel = REL_COUNTER if claim.relation == "deny" else REL_SUPPORT
            b = g.add(text, parent=best_bid, rel=rel, voice=claim.voice,
                      lr=claim.lr, embedding=emb, entity_refs=claim.entity_refs,
                      source=claim.source)
            return tag(ops.OpResult(ops.OP_ADD, b), field_name)

        # Loosely related — add as a new root in the same field
        b = g.add(text, parent=None, rel=REL_SUPPORT, voice=claim.voice,
                  lr=claim.lr, embedding=emb, entity_refs=claim.entity_refs,
                  source=claim.source)
        return tag(ops.OpResult(ops.OP_ADD, b), field_name)

    # ----- entity index (R6 bridging) -------------------------------------

    def rebuild_entity_index(self) -> None:
        """Rebuild the entity → beliefs index from current state.

        Called after load() and implicitly by entity_index_for() if the
        cache is missing. Also called when a new belief with entity_refs
        is ingested so lookups stay fresh within a session.
        """
        idx: dict[str, list[tuple[str, str]]] = {}
        for fname, g in self.fields.items():
            for bid, b in g.beliefs.items():
                for e in b.entity_refs:
                    idx.setdefault(e, []).append((fname, bid))
        self._entity_index = idx

    def entity_index(self) -> dict[str, list[tuple[str, str]]]:
        if self._entity_index is None:
            self.rebuild_entity_index()
        return self._entity_index  # type: ignore[return-value]

    def entity_index_for(self, entity: str) -> list[tuple[str, str]]:
        return self.entity_index().get(entity, [])

    def known_entities(self) -> list[str]:
        return sorted(self.entity_index().keys())

    def _touch_entity_index(self, belief_ref: tuple[str, str],
                            entity_refs: list[str]) -> None:
        """Incrementally update the index after an ingest."""
        if self._entity_index is None:
            return  # will be rebuilt on next access
        for e in entity_refs:
            self._entity_index.setdefault(e, []).append(belief_ref)

    # ----- inspection ------------------------------------------------------

    def stats(self) -> dict:
        n_beliefs = sum(len(g.beliefs) for g in self.fields.values())
        n_roots = sum(len(g.roots) for g in self.fields.values())
        by_mass = sorted(
            (b for g in self.fields.values() for b in g.beliefs.values()),
            key=lambda b: b.mass, reverse=True,
        )
        top = [{"desc": b.desc, "mass": round(b.mass, 3), "voices": b.n_voices}
               for b in by_mass[:5]]
        return {
            "fields": len(self.fields),
            "beliefs": n_beliefs,
            "roots": n_roots,
            "top_mass": top,
        }

    def render(self, *, max_mass_only: float = 0.0) -> str:
        out = []
        for name, g in sorted(self.fields.items()):
            out.append(f"\n=== {name} ({len(g.beliefs)} beliefs) ===")
            out.append(g.render(max_mass_only=max_mass_only))
        return "\n".join(out).strip()
