"""bellamem PreToolUse guard — v0.2 hot path between Claude Code and the graph.

Registered as a console-script (`bellamem-guard` in pyproject.toml) so
Claude Code can invoke it as a PreToolUse hook. Two jobs:

  1. ADVISORY — emit a v0.2-native typed context pack before the edit
     runs. The model sees it as `additionalContext` (system reminder)
     alongside the tool invocation. Sections (epistemic priority):
     invariant × metaphysical, invariant × normative, open ephemerals,
     retracted approaches, dispute edges.

  2. BLOCKING — if the intended edit text substring-matches a
     retracted ephemeral's topic OR a dispute edge's target concept,
     exit 2 with a reason. Claude Code treats exit 2 as "refuse the
     tool call, surface the stderr reason to the model." Rejected
     approaches become literally unreachable, not just discouraged.

v0.2 only, no fallback. If `.graph/v02.json` doesn't exist for the
current project, the guard silently no-ops (exit 0, empty stdout).
Run `python -m bellamem.proto ingest` to populate the graph first.

The guard must feel instant. To hit that budget:
  - stdlib only at import time (no openai, no numpy, no bellamem.core)
  - JSON parse of .graph/v02.json (~562 KB for the dogfood graph)
  - no focus-string embedding (no network)
  - substring match on retracted/dispute topics (not semantic)

Target latency: under 300 ms total.

Output contract (exit 0):
  JSON on stdout in the shape Claude Code expects:
    {
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": "<pack text>"
      }
    }

Output contract (exit 2 = block):
  Human-readable reason on stderr, empty stdout.

Silent no-op (exit 0 with empty stdout) on any of:
  - no .graph/v02.json found for the current project
  - stdin empty or malformed JSON
  - load/parse failure (soft — don't block edits on bellamem errors)
  - graph is empty
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Tunables — conservative defaults
# ---------------------------------------------------------------------------

_MAX_INVARIANT_META = 8      # invariant × metaphysical — what the system IS
_MAX_INVARIANT_NORM = 8      # invariant × normative — what we commit to
_MAX_OPEN_EPHEMERAL = 6      # work in progress
_MAX_RETRACTED = 10          # rejected approaches
_MAX_DISPUTES = 8            # live contradictions

# Blocking threshold: a retracted/disputed concept must have at least
# this many source_refs before it can block an edit. Prevents single-
# voiced low-confidence rejections from blocking unreasonably.
_BLOCK_MIN_REFS = 2

# Minimum length of a topic before substring-matching against it.
# Below this the match is too noisy (short generic phrases).
_BLOCK_MIN_DESC_LEN = 10


# ---------------------------------------------------------------------------
# Snapshot discovery
# ---------------------------------------------------------------------------

def _find_v02(start: str) -> Optional[str]:
    """Walk up from `start` looking for `.graph/v02.json`.

    Returns the absolute path if found, None if we hit the filesystem
    root without finding one. Works in monorepos and nested checkouts.
    """
    p = os.path.abspath(start)
    while True:
        candidate = os.path.join(p, ".graph", "v02.json")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def _load_v02(path: str) -> Optional[dict]:
    """Parse the v02.json file directly. Keeps the guard's import
    surface small — no numpy, no bellamem.core at startup."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pack builder — v0.2-native typed summary
# ---------------------------------------------------------------------------

def _by_refs_desc(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda c: -len(c.get("source_refs") or []))


def _build_pack(data: dict) -> str:
    """Compose the v0.2-native advisory pack.

    Sections in epistemic priority order:
      - invariant × metaphysical (what the system IS)
      - invariant × normative (what we commit to)
      - open ephemerals (work in progress)
      - retracted approaches (rejected)
      - dispute edges (contradictions)
    """
    concepts: dict = data.get("concepts") or {}
    edges: list = data.get("edges") or []

    lines: list[str] = []

    # ----- invariant × metaphysical -----
    invar_meta = _by_refs_desc([
        c for c in concepts.values()
        if c.get("class") == "invariant" and c.get("nature") == "metaphysical"
    ])
    if invar_meta:
        lines.append("## what the system IS (invariant × metaphysical)")
        for c in invar_meta[:_MAX_INVARIANT_META]:
            refs = len(c.get("source_refs") or [])
            lines.append(f"  [{refs:2}r] {c.get('topic', '')}")
        lines.append("")

    # ----- invariant × normative -----
    invar_norm = _by_refs_desc([
        c for c in concepts.values()
        if c.get("class") == "invariant" and c.get("nature") == "normative"
    ])
    if invar_norm:
        lines.append("## what we commit to (invariant × normative)")
        for c in invar_norm[:_MAX_INVARIANT_NORM]:
            refs = len(c.get("source_refs") or [])
            lines.append(f"  [{refs:2}r] {c.get('topic', '')}")
        lines.append("")

    # ----- open ephemerals (recent work) -----
    open_eph = sorted(
        [c for c in concepts.values()
         if c.get("class") == "ephemeral" and c.get("state") == "open"],
        key=lambda c: c.get("last_touched_at") or "",
        reverse=True,
    )
    if open_eph:
        lines.append(f"## open work ({len(open_eph)} ephemerals, top recent)")
        for c in open_eph[:_MAX_OPEN_EPHEMERAL]:
            lines.append(f"  [open] {c.get('topic', '')}")
        lines.append("")

    # ----- retracted ephemerals (rejected approaches) -----
    retracted = [
        c for c in concepts.values()
        if c.get("class") == "ephemeral" and c.get("state") == "retracted"
    ]
    if retracted:
        lines.append(f"## retracted approaches — do NOT re-suggest ({len(retracted)})")
        for c in retracted[:_MAX_RETRACTED]:
            refs = len(c.get("source_refs") or [])
            lines.append(f"  [{refs}r] {c.get('topic', '')}")
        lines.append("")

    # ----- dispute edges -----
    disputes = [e for e in edges if e.get("type") == "dispute"]
    if disputes:
        lines.append(f"## disputes — ⊥ edges ({len(disputes)})")
        for e in disputes[:_MAX_DISPUTES]:
            tgt_id = e.get("target")
            tgt = concepts.get(tgt_id) if tgt_id else None
            tgt_topic = tgt.get("topic", tgt_id) if tgt else tgt_id
            lines.append(f"  ⊥ {tgt_topic}")

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Block check — substring match against retracted + dispute targets
# ---------------------------------------------------------------------------

def _extract_new_content(tool_name: str, tool_input: dict) -> str:
    """Pull the intended edit text out of the PreToolUse payload.

    Edit:       tool_input.new_string
    Write:      tool_input.content
    MultiEdit:  tool_input.edits[*].new_string   (joined)
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


def _check_blocking(
    new_content: str, data: dict
) -> Optional[tuple[int, str, str]]:
    """Substring-match edit against retracted ephemerals + dispute targets.

    Returns (refs, kind, topic) on first hit where kind is
    "retracted" or "dispute"; None otherwise. Case-insensitive.
    Ignores concepts below _BLOCK_MIN_REFS or with short topics.
    """
    if not new_content:
        return None
    haystack = new_content.lower()
    if not haystack.strip():
        return None

    concepts: dict = data.get("concepts") or {}
    edges: list = data.get("edges") or []

    # Retracted ephemerals
    for c in concepts.values():
        if c.get("class") != "ephemeral" or c.get("state") != "retracted":
            continue
        refs = len(c.get("source_refs") or [])
        if refs < _BLOCK_MIN_REFS:
            continue
        topic = (c.get("topic") or "").strip().lower()
        if len(topic) < _BLOCK_MIN_DESC_LEN:
            continue
        if topic in haystack:
            return (refs, "retracted", c.get("topic", ""))

    # Dispute-edge targets with enough refs
    disputed_ids = {e.get("target") for e in edges if e.get("type") == "dispute"}
    for cid in disputed_ids:
        c = concepts.get(cid)
        if not c:
            continue
        refs = len(c.get("source_refs") or [])
        if refs < _BLOCK_MIN_REFS:
            continue
        topic = (c.get("topic") or "").strip().lower()
        if len(topic) < _BLOCK_MIN_DESC_LEN:
            continue
        if topic in haystack:
            return (refs, "dispute", c.get("topic", ""))

    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Read PreToolUse payload from stdin, emit guard output.

    Exits:
      0 — advisory (stdout has JSON with additionalContext)
      0 — silent no-op (no v02.json, bad payload, load failure, etc.)
      2 — BLOCKING (stderr has reason, edit is refused)
    """
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

    v02_path = _find_v02(cwd)
    if not v02_path:
        return 0

    data = _load_v02(v02_path)
    if not data:
        print(f"bellamem-guard: skipped (failed to parse {v02_path})", file=sys.stderr)
        return 0

    if not (data.get("concepts") or {}):
        return 0

    # ----- Blocking check (Edit/Write/MultiEdit only) -----
    new_content = _extract_new_content(tool_name, tool_input)
    hit = _check_blocking(new_content, data)
    if hit is not None:
        refs, kind, topic = hit
        file_hint = tool_input.get("file_path") or "(unknown file)"
        print(
            f"bellamem-guard: BLOCKING {tool_name} on {file_hint}\n"
            f"  This edit re-introduces a {kind} concept "
            f"({refs} source refs):\n"
            f"    {topic}\n"
            f"  If you believe this is stale, re-voice it in a user "
            f"turn and retry. Otherwise, pick a different approach.",
            file=sys.stderr,
        )
        return 2

    # ----- Advisory pack -----
    pack = _build_pack(data)
    if not pack:
        return 0

    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": pack,
        }
    }))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
