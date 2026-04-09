"""Principle loader — parse PRINCIPLES.md and seed the __principles__ field.

Principles are the project's constitution. They live in the reserved
`__principles__` field with mass_floor=0.95, so they dominate the
high-mass layer of every EXPAND pack and can never silently decay.

Seeding is idempotent: belief ids are stable hashes of (desc, parent),
so re-seeding on every ingest is a no-op for unchanged principles and
updates the desc (via AMEND) only when the file changes.

Format recognized:
    - **C1 YAGNI** — do not build for hypothetical future requirements.
      Speculative abstractions are rot waiting to happen.
    - **P1** `bellamem.core` must never import from `bellamem.adapters`.
      The core is domain-agnostic; adapters are where domain knowledge lives.

Continuation lines (indented) are folded into the principle body.
Lines outside the `- **ID ...**` pattern are ignored (headers, prose).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import ops
from .embed import embed
from .gene import Gene

if TYPE_CHECKING:
    from .bella import Bella


PRINCIPLES_FIELD = "__principles__"
CONSTITUTION_VOICE = "__constitution__"

# Initial mass ≈ 0.98 (log_odds ≈ 3.89). lr = e^3.89 ≈ 48.9.
# We pass this via the accumulate call so the belief lands at the
# intended mass on first insert. The floor prevents any later drop.
INITIAL_LR = 48.9
MASS_FLOOR = 0.95

# - **C1 YAGNI** — body starts here
# - **P1** body starts here (no title)
_HEAD_RE = re.compile(
    r"^-\s+\*\*([CP]\d+)(?:\s+([^*]+?))?\*\*"  # - **ID [title]**
    r"\s*[\s\u2013\u2014\-]*\s*"                # optional separator (—, –, -, spaces)
    r"(.*)$"                                     # rest of line = body
)


@dataclass
class ParsedPrinciple:
    id: str
    title: str
    body: str

    def to_desc(self) -> str:
        head = self.id if not self.title else f"{self.id} {self.title}"
        if self.body:
            return f"{head}: {self.body}".strip()
        # Title-only principle (the meaning lives entirely in the bold)
        return head.strip().rstrip(".") + "."


def parse_principles(text: str) -> list[ParsedPrinciple]:
    """Parse PRINCIPLES.md content into a list of principles.

    Handles multi-line principles whose continuation is indented by at
    least one space. Blank lines end a principle.
    """
    out: list[ParsedPrinciple] = []
    current: ParsedPrinciple | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            current.title = re.sub(r"\s+", " ", current.title).strip()
            current.body = re.sub(r"\s+", " ", current.body).strip()
            # Accept if either title or body has content. Some principles
            # live entirely inside the bold (e.g. "- **C6 Composition over
            # inheritance.**") with no trailing body.
            if current.title or current.body:
                out.append(current)
            current = None

    for raw in text.splitlines():
        if not raw.strip():
            close()
            continue
        m = _HEAD_RE.match(raw)
        if m:
            close()
            current = ParsedPrinciple(
                id=m.group(1),
                title=(m.group(2) or "").strip(),
                body=m.group(3).strip(),
            )
            continue
        # Continuation: starts with whitespace and we have an open principle
        if current is not None and (raw.startswith(" ") or raw.startswith("\t")):
            current.body = (current.body + " " + raw.strip()).strip()
            continue
        # Anything else closes the current principle
        close()
    close()
    return out


def load_principles_file(path: str) -> list[ParsedPrinciple]:
    with open(path) as f:
        return parse_principles(f.read())


def default_principles_path() -> str | None:
    """Find PRINCIPLES.md by walking up from the current working dir.

    Returns the path if found, else None. No env-var override here —
    the CLI can accept an explicit path if the user wants a different one.
    """
    here = os.path.abspath(os.getcwd())
    while True:
        candidate = os.path.join(here, "PRINCIPLES.md")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def seed_principles(bella: "Bella", path: str | None = None) -> dict:
    """Load principles from PRINCIPLES.md into bella's __principles__ field.

    Idempotent: re-seeding does not duplicate beliefs. Returns a small
    stats dict for the CLI to print.
    """
    p = path or default_principles_path()
    if not p or not os.path.isfile(p):
        return {"path": None, "count": 0, "added": 0, "reseeded": 0}

    parsed = load_principles_file(p)
    if PRINCIPLES_FIELD not in bella.fields:
        bella.fields[PRINCIPLES_FIELD] = Gene(name=PRINCIPLES_FIELD)
    g = bella.fields[PRINCIPLES_FIELD]

    added = 0
    reseeded = 0
    for pr in parsed:
        desc = pr.to_desc()
        emb = embed(desc)
        before = len(g.beliefs)
        ops.add(
            g, desc,
            parent=None,
            voice=CONSTITUTION_VOICE,
            lr=INITIAL_LR,
            embedding=emb,
            entity_refs=[pr.id],  # the principle id is its own entity
            mass_floor=MASS_FLOOR,
        )
        if len(g.beliefs) > before:
            added += 1
        else:
            reseeded += 1

    return {"path": p, "count": len(parsed), "added": added, "reseeded": reseeded}
