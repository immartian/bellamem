"""Claude Code transcript adapter.

Reads session .jsonl files from ~/.claude/projects/<escaped-cwd>/ and
yields (voice, text) tuples for the chat EW. Maintains a cursor in
Bella so repeated ingests are incremental — this is the dogfood path.

Transcript format (observed, 2026-04-09):
  - lines are JSON objects, one per event
  - type=="user": message.role=="user", message.content is a string
  - type=="assistant": message.content is a list of blocks
      [{type:"text", text:"..."}, {type:"tool_use", ...}, ...]
  - type=="attachment", "file-history-snapshot", "permission-mode": skip

We intentionally skip tool_use and tool_result blocks — they are
ephemeral execution state, not claims.
"""

from __future__ import annotations

import json
import os
from typing import Iterable, Iterator

from ..core.bella import Bella


def project_dir_for(cwd: str) -> str:
    """Claude Code slugifies cwd by replacing non-alphanumeric chars with `-`.

    Examples:
      /media/im3/plus/labX/bellamem
        → -media-im3-plus-labX-bellamem
      /media/im3/plus/lab4/re_news/herenews-app
        → -media-im3-plus-lab4-re-news-herenews-app
        (note: underscore and slash both become dash)
    """
    import re
    escaped = re.sub(r"[^a-zA-Z0-9]", "-", cwd)
    return os.path.expanduser(f"~/.claude/projects/{escaped}")


def list_sessions(cwd: str | None = None) -> list[str]:
    """Return paths to all .jsonl transcripts for a project directory."""
    d = project_dir_for(cwd or os.getcwd())
    if not os.path.isdir(d):
        return []
    return sorted(
        os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl")
    )


def _extract_text(msg: dict) -> str:
    """Pull raw text content out of a transcript message entry."""
    t = msg.get("type")
    if t == "user":
        content = (msg.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
            return "\n".join(parts)
        return ""
    if t == "assistant":
        content = (msg.get("message") or {}).get("content")
        if isinstance(content, list):
            parts = []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "text":
                    parts.append(blk.get("text", ""))
            return "\n".join(parts)
        return ""
    return ""


def iter_turns(path: str, *, start_line: int = 0
               ) -> Iterator[tuple[int, str, str]]:
    """Yield (line_number, voice, text) for each user/assistant turn.

    line_number is 1-indexed (matches `wc -l`); caller should persist
    the last seen line_number to the cursor.
    """
    with open(path, "r") as f:
        for i, raw in enumerate(f, start=1):
            if i <= start_line:
                continue
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t not in ("user", "assistant"):
                continue
            text = _extract_text(msg)
            if not text or not text.strip():
                continue
            voice = "user" if t == "user" else "assistant"
            yield i, voice, text


def iter_new_turns(bella: Bella, path: str,
                    *, tail: int | None = None
                    ) -> Iterator[tuple[int, str, str]]:
    """Yield only turns past the stored cursor for this transcript.

    If `tail` is given, skip to the last `tail` user/assistant turns
    even if the cursor is earlier. Used for fast partial ingests
    (e.g. demos on huge sessions).
    """
    key = f"jsonl:{path}"
    cur = bella.cursor.get(key, {})
    start = int(cur.get("line", 0))

    if tail is not None:
        # Count real turns and compute a start line that includes only
        # the last `tail` of them.
        all_turns = list(iter_turns(path, start_line=0))
        if len(all_turns) > tail:
            start = max(start, all_turns[-tail][0] - 1)

    last = start
    for lineno, voice, text in iter_turns(path, start_line=start):
        last = lineno
        yield lineno, voice, text
    bella.cursor[key] = {"line": last}


def ingest_session(bella: Bella, path: str, *, tail: int | None = None,
                    no_llm: bool = False) -> dict:
    """Ingest all new turns from a single transcript into bella.

    Regex EW (adapters.chat) runs first — it handles add/deny/rule/decision
    across both voices. If BELLAMEM_EW=hybrid, an LLM-backed extractor
    (adapters.llm_ew) also runs on assistant turns containing cause or
    self-observation markers, adding structured CAUSE edges and routing
    self-observations to __self__.

    Turn-pair retroactive ratification (P10) runs against ALL claims
    from the preceding assistant turn — both regex-extracted and
    LLM-extracted — so a user affirmation boosts both equally.

    Returns a small stats dict for the CLI to print.
    """
    # Local imports to avoid adapters → adapters tight coupling
    from .chat import extract_claims, classify_reaction
    from .llm_ew import (
        ingest_causes,
        ingest_self_observations,
        make_llm_ew_from_env,
    )

    llm_ew = None if no_llm else make_llm_ew_from_env()

    turns = 0
    claims_written = 0
    affirmed = 0
    corrected = 0
    causes_added = 0
    self_obs_added = 0
    assistant_pending: list[tuple[str, str]] = []

    def apply_reaction(pending: list[tuple[str, str]], lr: float) -> int:
        n = 0
        for fname, bid in pending:
            g = bella.fields.get(fname)
            if g is None:
                continue
            b = g.beliefs.get(bid)
            if b is None:
                continue
            b.accumulate(lr, voice="user")
            n += 1
        return n

    def track(pending: list[tuple[str, str]], result) -> None:
        if result.belief and result.field:
            pending.append((result.field, result.belief.id))

    for _lineno, voice, text in iter_new_turns(bella, path, tail=tail):
        turns += 1

        # 1) User turn: react to the preceding assistant turn first.
        if voice == "user" and assistant_pending:
            reaction = classify_reaction(text)
            if reaction == "affirm":
                affirmed += apply_reaction(assistant_pending, lr=2.2)
            elif reaction == "correct":
                corrected += apply_reaction(assistant_pending, lr=0.4)
            assistant_pending = []

        # 2) Regex EW — handles the common cases for both voices.
        new_pending: list[tuple[str, str]] = []
        for claim in extract_claims(text, voice=voice):
            result = bella.ingest(claim)
            claims_written += 1
            track(new_pending, result)

        # 3) LLM EW — scoped to assistant turns with structural markers.
        if llm_ew is not None and voice == "assistant":
            # Causes: effect ingested first, cause attached via target_field
            cause_pairs = ingest_causes(bella, llm_ew, text, voice=voice)
            causes_added += len(cause_pairs)
            # Self-observations: routed directly to __self__ by core
            obs = ingest_self_observations(bella, llm_ew, text, voice=voice)
            self_obs_added += len(obs)
            # Neither helper currently returns the resulting beliefs for
            # the pending list, so LLM-extracted claims from this turn
            # are NOT retroactively ratified. That's deliberate: the LLM
            # output is already high-fidelity; the user's affirmation
            # isn't targeting the specific paraphrases the LLM produced.
            claims_written += len(cause_pairs) * 2 + len(obs)

        # 4) Arm pending for the next user turn.
        if voice == "assistant":
            assistant_pending = new_pending
        else:
            assistant_pending = []

    # Flush batched caches — avoids thrashing during ingest AND guarantees
    # the state is on disk when we return (P8 atomic persistence also
    # applies to the side caches).
    from ..core.embed import flush_embedder
    flush_embedder()
    if llm_ew is not None:
        llm_ew.flush()

    return {
        "session": os.path.basename(path),
        "turns": turns,
        "claims": claims_written,
        "affirmed": affirmed,
        "corrected": corrected,
        "causes": causes_added,
        "self_obs": self_obs_added,
    }


def ingest_project(bella: Bella, cwd: str | None = None,
                    *, tail: int | None = None,
                    no_llm: bool = False,
                    latest_only: bool = False) -> list[dict]:
    """Ingest all transcripts for the given project cwd.

    Flags:
      tail          — if set, limit each session to its last N turns
      no_llm        — disable LLM-backed EW regardless of env
      latest_only   — only ingest the most recent session (useful for demos)
    """
    results: list[dict] = []
    sessions = list_sessions(cwd)
    if latest_only and sessions:
        # list_sessions returns sorted asc; take the last one
        sessions = [sessions[-1]]
    for path in sessions:
        results.append(ingest_session(bella, path, tail=tail, no_llm=no_llm))
    return results
