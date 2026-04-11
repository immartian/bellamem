"""bellamem PreToolUse guard — the hot path between Claude Code and the graph.

Registered as a console-script (`bellamem-guard` in pyproject.toml) so
Claude Code can invoke it as a PreToolUse hook. Two jobs:

  1. ADVISORY — emit a context pack before the edit runs. The model
     sees it as `additionalContext` (system reminder) alongside the
     tool invocation. Pack content: `__self__` anti-patterns, top
     ratified invariants, top ⊥ disputes — mass-ranked, no focus query.

  2. BLOCKING — if the intended edit text substring-matches a
     high-mass ⊥ dispute description, exit 2 with a reason. Claude
     Code treats exit 2 as "refuse the tool call, surface the stderr
     reason to the model." Rejected approaches become literally
     unreachable, not just discouraged. This is the feature that
     `is_reserved_field` does at the core level — the guard is its
     generalisation to any belief in the graph.

The guard must feel instant. To hit that budget:
  - stdlib only at module import time (no openai, no numpy, no umap)
  - loads graph.json via `load_graph_only` (no embeddings, no embed
    cache, no embedder signature check)
  - no focus-string embedding (so no network round-trip)
  - substring match on disputes (not semantic) — fast and obvious

Target latency: under 500 ms total (bellamem startup + graph load +
pack build + output). Measured ~160 ms for graph load alone on the
real 1792-belief dogfood forest.

Output contract (exit 0):
  JSON on stdout in the shape Claude Code expects:
    {
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "<pack text>"
      }
    }

Output contract (exit 2 = block):
  Human-readable reason on stderr, empty stdout. Claude Code surfaces
  the stderr message to the model, which then must either justify
  itself or pick a different approach.

Silent no-op (exit 0 with empty stdout) on any of:
  - missing `.graph/default.json` for the current project
  - stdin empty or malformed JSON
  - load failure (soft — don't block edits on bellamem errors)
  - no matching fields in the graph
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Tunables — intentionally conservative defaults for v1
# ---------------------------------------------------------------------------

# How many of each category to include in the advisory pack.
_MAX_SELF_OBS = 8        # anti-patterns from __self__
_MAX_INVARIANTS = 8      # ratified domain invariants (mass-ranked)
_MAX_DISPUTES = 12       # ⊥ edges surfaced as "rejected approaches"

# Only disputes at or above this mass are eligible to BLOCK an edit.
# Below this threshold they're still surfaced in the advisory pack,
# but they can't refuse the tool call. Prevents low-confidence
# disputes from gating edits unreasonably.
_BLOCK_MIN_MASS = 0.60

# Minimum length of a dispute description before we'll substring-match
# against it. Below this the match is too noisy (common short phrases
# would fire constantly). 10 chars is enough to require a specific
# reference, not a generic phrase.
_BLOCK_MIN_DESC_LEN = 10


# ---------------------------------------------------------------------------
# Snapshot discovery
# ---------------------------------------------------------------------------

def _find_snapshot(start: str) -> Optional[str]:
    """Walk up from `start` looking for `.graph/default.json`.

    Returns the absolute path if found, None if we hit the filesystem
    root without finding one. The guard operates on the innermost
    enclosing bellamem project, so it works in monorepos and nested
    checkouts.
    """
    p = os.path.abspath(start)
    while True:
        candidate = os.path.join(p, ".graph", "default.json")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


# ---------------------------------------------------------------------------
# Pack builder
# ---------------------------------------------------------------------------

def _build_pack(bella) -> str:
    """Compose the advisory context pack from a loaded Bella.

    Three sections, each mass-ranked:
      - __self__ anti-patterns
      - top ratified invariants (non-disputes, across all fields)
      - ⊥ rejected approaches (all fields)

    Returns a string suitable for `additionalContext`. Empty string
    if the graph has nothing worth showing (caller treats empty as
    "silent no-op, exit 0 with nothing on stdout").
    """
    from .core.gene import REL_COUNTER

    lines: list[str] = []

    # ----- __self__ (anti-patterns) -----
    self_field = bella.fields.get("__self__")
    if self_field and self_field.beliefs:
        top_self = sorted(
            self_field.beliefs.values(),
            key=lambda b: -b.mass,
        )[:_MAX_SELF_OBS]
        if top_self:
            lines.append("## self-observations (patterns to catch before the edit)")
            for b in top_self:
                lines.append(f"  m={b.mass:.2f}  {b.desc}")
            lines.append("")

    # ----- Top ratified invariants (not disputes, not __self__) -----
    all_invariants: list[tuple[float, str, str]] = []
    for fname, g in bella.fields.items():
        if fname == "__self__":
            continue
        for b in g.beliefs.values():
            if b.rel == REL_COUNTER:
                continue  # disputes go to their own section
            all_invariants.append((b.mass, fname, b.desc or ""))
    all_invariants.sort(reverse=True)
    if all_invariants:
        lines.append("## top ratified invariants (respect these)")
        for mass, fname, desc in all_invariants[:_MAX_INVARIANTS]:
            lines.append(f"  [{fname[:22]}] m={mass:.2f}  {desc}")
        lines.append("")

    # ----- Disputes (rejected approaches) -----
    all_disputes: list[tuple[float, str]] = []
    for g in bella.fields.values():
        for b in g.beliefs.values():
            if b.rel == REL_COUNTER:
                all_disputes.append((b.mass, b.desc or ""))
    all_disputes.sort(reverse=True)
    if all_disputes:
        lines.append("## ⊥ rejected approaches (do NOT re-suggest)")
        for mass, desc in all_disputes[:_MAX_DISPUTES]:
            lines.append(f"  m={mass:.2f}  {desc}")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Block check — substring match against high-mass disputes
# ---------------------------------------------------------------------------

def _extract_new_content(tool_name: str, tool_input: dict) -> str:
    """Pull the intended edit text out of the PreToolUse payload.

    Edit:       tool_input.new_string
    Write:      tool_input.content
    MultiEdit:  tool_input.edits[*].new_string   (joined)

    Unknown tool shapes return empty — we can't block what we can't see.
    """
    if tool_name == "Write":
        return tool_input.get("content") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string") or ""
    if tool_name == "MultiEdit":
        edits = tool_input.get("edits") or []
        parts = []
        for e in edits:
            if isinstance(e, dict):
                ns = e.get("new_string")
                if ns:
                    parts.append(ns)
        return "\n".join(parts)
    return ""


def _check_blocking(new_content: str, bella) -> Optional[tuple[float, str]]:
    """Substring-match the intended edit against high-mass ⊥ disputes.

    Returns (mass, desc) on first hit, or None. Case-insensitive,
    ignores disputes below _BLOCK_MIN_MASS or with short descriptions.

    v1 design: literal substring only. A past session's rejected
    phrase has to reappear verbatim for the guard to fire. False
    negatives (paraphrased re-suggestion) are acceptable because the
    advisory pack still surfaces the dispute; false positives (an
    incidental substring collision) are very unlikely given the
    10-char minimum + dispute phrasing tends to be specific. Semantic
    matching is a v0.0.4b+ refinement.
    """
    from .core.gene import REL_COUNTER

    if not new_content:
        return None
    haystack = new_content.lower()
    if not haystack.strip():
        return None

    # Scan all fields for high-mass disputes
    for g in bella.fields.values():
        for b in g.beliefs.values():
            if b.rel != REL_COUNTER:
                continue
            if b.mass < _BLOCK_MIN_MASS:
                continue
            needle = (b.desc or "").strip().lower()
            if len(needle) < _BLOCK_MIN_DESC_LEN:
                continue
            if needle in haystack:
                return (b.mass, b.desc)
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Read PreToolUse payload from stdin, emit guard output.

    Exits:
      0 — advisory (stdout has the JSON with additionalContext)
      0 — silent no-op (no snapshot, bad payload, load failure, etc.)
      2 — BLOCKING (stderr has the reason, edit is refused)
    """
    # ----- Parse stdin -----
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    cwd = payload.get("cwd") or os.getcwd()

    # ----- Find the snapshot for this project -----
    snapshot_path = _find_snapshot(cwd)
    if not snapshot_path:
        return 0

    # ----- Load graph-only (fast path, no openai / embed cache) -----
    try:
        from .core.store import load_graph_only
        bella = load_graph_only(snapshot_path)
    except Exception as e:
        # Soft failure: never block the edit on a bellamem error.
        print(f"bellamem-guard: skipped ({e})", file=sys.stderr)
        return 0

    if not bella.fields:
        return 0

    # ----- Blocking check (for Edit/Write/MultiEdit only) -----
    new_content = _extract_new_content(tool_name, tool_input)
    hit = _check_blocking(new_content, bella)
    if hit is not None:
        mass, desc = hit
        file_hint = tool_input.get("file_path") or "(unknown file)"
        print(
            f"bellamem-guard: BLOCKING {tool_name} on {file_hint}\n"
            f"  This edit re-introduces a ⊥ rejected approach "
            f"(mass={mass:.2f}):\n"
            f"    {desc}\n"
            f"  If you believe this is stale, re-ratify it in a user "
            f"turn and retry. Otherwise, pick a different approach.",
            file=sys.stderr,
        )
        return 2

    # ----- Advisory pack -----
    pack = _build_pack(bella)
    if not pack:
        return 0

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": pack,
        }
    }
    sys.stdout.write(json.dumps(output))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
