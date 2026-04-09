"""Benchmark corpus — hand-written from this dogfood conversation.

Each item is a question an agent might ask mid-edit, paired with the
decisive fact(s) that MUST be in the context pack for the agent to
answer correctly. "Correctly" means the answer derives from something
the user and assistant actually committed to in the conversation.

A contender "hits" an item if its pack contains any of the expected
substrings. The point is not to cover every possible question — it's
to sample 15 archetypal cases that stress different layers of the
memory pipeline (invariants, causes, disputes, self-model, bridges).

Categories covered:
  - Constitutional (principles from PRINCIPLES.md)
  - Architectural decisions made mid-session
  - Bandaid-prone scenarios (C-layer + self-model)
  - Language / dep choices (P7, P6)
  - Reserved field rules (P18)
  - Voice asymmetry (P9, P10)
  - Audit semantics (is_clean, drift candidates)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BenchItem:
    id: str
    query: str
    expected_any_of: list[str]
    focus_entity: Optional[str] = None
    category: str = "general"


BENCH_ITEMS: list[BenchItem] = [
    BenchItem(
        id="Q01",
        query="Should I add a fallback default when the snapshot is missing "
              "the embedder signature so old files still load?",
        focus_entity="store.py",
        expected_any_of=[
            "C19",
            "P15",
            "break forward",
            "no backwards-compat shims",
            "no feature flags",
        ],
        category="bandaid",
    ),
    BenchItem(
        id="Q02",
        query="Should I rewrite the prototype in Rust instead of Python?",
        expected_any_of=[
            "P7",
            "Python for v0",
            "Rust for v1",
            "not for v0, probably yes for v1",
            "porting-while-learning",
        ],
        category="language",
    ),
    BenchItem(
        id="Q03",
        query="Can my chat adapter write directly into the __principles__ field?",
        expected_any_of=[
            "P18",
            "reserved field",
            "adapters must not write to reserved",
            "domain adapters",
        ],
        category="constitution",
    ),
    BenchItem(
        id="Q04",
        query="Should I wrap embed() in a try/except to swallow OpenAI errors?",
        focus_entity="embed.py",
        expected_any_of=[
            "C10",
            "fail loud",
            "never swallow",
            "i tend to reach for try/except",
            "silence is the worst bug",
        ],
        category="bandaid",
    ),
    BenchItem(
        id="Q05",
        query="Should we add networkx as a dependency for graph algorithms?",
        expected_any_of=[
            "our graph is dicts",
            "no graph lib",
            "P6",
            "zero runtime deps",
            "one-hop",
        ],
        category="dependency",
    ),
    BenchItem(
        id="Q06",
        query="Should the audit command exit non-zero when it finds drift candidates?",
        expected_any_of=[
            "drift candidates are informational",
            "not errors",
            "only contradictions and bandaid piles",
            "is_clean",
        ],
        category="audit",
    ),
    BenchItem(
        id="Q07",
        query="Should CAUSE extraction be implemented with regex patterns?",
        expected_any_of=[
            "LLM",
            "structural",
            "regex can't",
            "the boundary between cause and effect",
            "two spans",
        ],
        category="extraction",
    ),
    BenchItem(
        id="Q08",
        query="Can core/bella.py import helpers from adapters/chat.py "
              "for the LLM EW integration?",
        expected_any_of=[
            "P1",
            "core must never import from adapters",
            "bellamem.core must never import",
            "domain-agnostic core",
        ],
        category="constitution",
    ),
    BenchItem(
        id="Q09",
        query="Should I use Neo4j or KuzuDB as the persistence layer "
              "when JSON gets too slow?",
        expected_any_of=[
            "sqlite",
            "SQLite is the right next persistence step",
            "no graph db",
            "cypher",
            "one-hop",
        ],
        category="persistence",
    ),
    BenchItem(
        id="Q10",
        query="How should a user's affirmation affect preceding assistant claims?",
        expected_any_of=[
            "P10",
            "retroactive ratification",
            "lr=2.2",
            "independent voice",
            "user.*affirms",
        ],
        category="epistemics",
    ),
    BenchItem(
        id="Q11",
        query="Should principles decay in mass over time like other beliefs?",
        expected_any_of=[
            "P13",
            "immutable_mass",
            "never decay below",
            "mass_floor",
            "0.95",
        ],
        category="constitution",
    ),
    BenchItem(
        id="Q12",
        query="What does BELLAMEM_EW=hybrid actually do beyond regex EW?",
        expected_any_of=[
            "cause",
            "self-observation",
            "LLM",
            "structural",
            "gpt-4o-mini",
        ],
        category="extraction",
    ),
    BenchItem(
        id="Q13",
        query="Should expand_before_edit include a recency layer?",
        expected_any_of=[
            "P21",
            "recency is actively harmful",
            "no recency",
            "biases toward the last bandaid",
        ],
        category="expand",
    ),
    BenchItem(
        id="Q14",
        query="Can I add a new mutation method like `gene.touch(bid)` "
              "directly on the Gene class?",
        expected_any_of=[
            "P2",
            "seven operations",
            "complete mutation API",
            "no direct writes to gene",
        ],
        category="constitution",
    ),
    BenchItem(
        id="Q15",
        query="Should the EW classifier treat the user and assistant the same way?",
        expected_any_of=[
            "P9",
            "user is oracle",
            "assistant is hypothesis",
            "asymmetry",
        ],
        category="epistemics",
    ),
]
