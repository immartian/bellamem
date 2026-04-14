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

We also strip harness-injected meta-text (system reminders, command
echoes, interrupt sentinels) before passing content to the EW. These
are transport artifacts, not things anyone said. See `_strip_system_noise`.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Iterable, Iterator, Optional


# How often `ingest_session` fires its progress callback, measured in
# real turns processed. 500 is "often enough to show the process is
# alive on a 10 MB file, not so often that the output log churns."
PROGRESS_EVERY_N_TURNS = 500

from ..core.bella import Bella


# ---------------------------------------------------------------------------
# System-noise filter
# ---------------------------------------------------------------------------
#
# The transcript leaks three shapes of meta-text that must not become
# beliefs:
#
#   1. Bracketed sentinels       [Request interrupted by user for tool use]
#   2. XML-tagged injections     <system-reminder>...</system-reminder>,
#                                <command-name>, <local-command-stdout>,
#                                <user-prompt-submit-hook>, etc.
#   3. Slash-command echoes      Lines that are just "/clear", "/reset" —
#                                usually already inside <command-name>,
#                                but safe to drop explicitly.
#
# Drop-entire-line patterns are checked first (fast path). Then we strip
# tagged blocks (multiline) and tagged inline fragments. If the remaining
# text is empty, the caller skips the turn entirely.

_NOISE_LINE_RE = re.compile(
    r"^\s*\[(Request interrupted[^\]]*|"
    r"Tool (?:execution|use)[^\]]*|"
    r"Command output[^\]]*)\]\s*$",
    re.I,
)

_NOISE_TAGS = (
    "system-reminder",
    "local-command-stdout",
    "local-command-stderr",
    "local-command-caveat",
    "command-name",
    "command-message",
    "command-args",
    "user-prompt-submit-hook",
    "function_calls",
    "function_results",
    # Bella's own slash-command instruction block. The /bellamem
    # slash command renders a post-output synthesis prompt into the
    # transcript, telling the downstream Claude how to interpret the
    # bellamem output. That prompt is metadata — it shouldn't become
    # beliefs on subsequent ingests. Wrapping it in this tag in the
    # template (bellamem/templates/bellamem.md) lets the adapter strip
    # it here without any per-user configuration.
    "bellamem-instructions",
)

_NOISE_BLOCK_RE = re.compile(
    r"<(" + "|".join(_NOISE_TAGS) + r")\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)

# Self-closing / orphaned opening tags that sometimes appear when the
# harness truncates a block across turns.
_NOISE_OPEN_RE = re.compile(
    r"</?(" + "|".join(_NOISE_TAGS) + r")\b[^>]*/?>",
    re.IGNORECASE,
)

_SLASH_CMD_RE = re.compile(r"^\s*/[a-zA-Z][\w\-]*(\s.*)?$")


def _strip_system_noise(text: str) -> str:
    """Remove harness-injected meta-text before EW sees it.

    Returns the cleaned text (may be empty). Callers should treat an
    empty return as "skip this turn entirely".
    """
    if not text:
        return ""
    # 1. Strip tagged blocks (multiline-safe)
    cleaned = _NOISE_BLOCK_RE.sub("", text)
    # 2. Strip any orphaned open/close tags left over
    cleaned = _NOISE_OPEN_RE.sub("", cleaned)
    # 3. Drop noise lines and bare slash commands
    out_lines: list[str] = []
    for ln in cleaned.splitlines():
        if _NOISE_LINE_RE.match(ln):
            continue
        if _SLASH_CMD_RE.match(ln) and len(ln.strip()) < 40:
            # Bare `/clear`, `/reset`, `/loop 5m /foo` — commands, not claims
            continue
        out_lines.append(ln)
    return "\n".join(out_lines).strip()


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
    """Return paths to all .jsonl transcripts for a project directory,
    sorted by modification time ascending.

    The sort order matters for `ingest_project(latest_only=True)`, which
    picks `sessions[-1]` as the "active session" — that has to be the
    most recently written file, not the alphabetically-last UUID. Prior
    versions used `sorted()` on the file names, which produced surprising
    behaviour when a user's active session UUID didn't happen to sort
    last (the default case — transcript UUIDs are random).

    Order is otherwise irrelevant; the normal ingest loop processes every
    file regardless of order.
    """
    # Policy: always discover sessions from the project's canonical
    # Claude Code workspace (the one slugged from the git root), not
    # from whichever subfolder the user happened to launch Claude
    # Code in. The project has one memory, not one per subfolder.
    # If a caller needs to target a specific cwd — e.g. ingesting a
    # foreign workspace on purpose — they can still pass `cwd=`
    # explicitly.
    if cwd is None:
        from ..paths import project_root
        root = str(project_root())
        here = os.getcwd()
        if os.path.realpath(here) != os.path.realpath(root):
            import sys
            print(
                f"bellamem: ingesting from canonical workspace {root} "
                f"(not subfolder cwd {here}) — launch Claude Code from "
                f"the repo root to have the current session discoverable.",
                file=sys.stderr,
            )
        cwd = root
    d = project_dir_for(cwd)
    if not os.path.isdir(d):
        return []
    paths = [
        os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl")
    ]
    paths.sort(key=lambda p: os.path.getmtime(p))
    return paths


def latest_session_key(cwd: str | None = None) -> Optional[str]:
    """Return the graph session_key (`"jsonl:<path>"`) for the current
    project's most recently written transcript, or None if there are
    none. Used by CLI replay/resume to pin the replay target to the
    current project — without this, `replay._latest_session_key` can
    pick a cross-project jsonl that was ingested into the graph from
    a different cwd-encoded Claude Code project dir.
    """
    sessions = list_sessions(cwd)
    return f"jsonl:{sessions[-1]}" if sessions else None


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
    # encoding="utf-8" is explicit because Python's default is platform-
    # dependent (cp1252 on Windows) and Claude Code writes its .jsonl
    # transcripts as UTF-8. errors="replace" is defensive: undecodable
    # bytes in prose content become "?" rather than crashing the ingest
    # — the JSON structural characters are always ASCII-safe so parsing
    # continues. Fixes #3.
    with open(path, "r", encoding="utf-8", errors="replace") as f:
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
            text = _strip_system_noise(text)
            if not text:
                continue  # turn was pure meta-text — drop it
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
                    no_llm: bool = False,
                    on_progress: Optional[Callable[[int, int], None]] = None,
                    ) -> dict:
    """Ingest all new turns from a single transcript into bella.

    Regex EW (adapters.chat) runs first — it handles add/deny/rule/decision
    across both voices. If BELLAMEM_EW=hybrid, an LLM-backed extractor
    (adapters.llm_ew) also runs on assistant turns containing cause or
    self-observation markers, adding structured CAUSE edges and routing
    self-observations to __self__.

    Turn-pair retroactive ratification (P10) runs against ALL claims
    from the preceding assistant turn — both regex-extracted and
    LLM-extracted — so a user affirmation boosts both equally.

    Progress callback (`on_progress`) is called with (turns, claims)
    every PROGRESS_EVERY_N_TURNS turns so the CLI can render intra-file
    progress during long ingests. Not called if None. The callback
    runs on the main thread and should be cheap (e.g. a print).

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
    session_key = f"jsonl:{path}"

    # Materialize all new turns up-front so we can do a single batched
    # embedder warm-up before the per-turn ingest loop runs. This
    # matters because the loop used to pay a ~300 ms OpenAI round-trip
    # per new claim; with 30+ claims per save the embedder latency
    # dominated the whole save. One batched call via `embed_batch`
    # consolidates all misses into a single OpenAI request (up to 1024
    # texts per batch, per OpenAIEmbedder.BATCH_CAP), and the per-turn
    # `bella.ingest()` calls below become disk-cache hits.
    #
    # iter_new_turns has a side-effect (it advances the cursor) so we
    # only call it once — materialising into a list is essential here.
    all_new_turns: list[tuple[int, str, str]] = list(
        iter_new_turns(bella, path, tail=tail)
    )

    if all_new_turns:
        # Pre-warm: collect all claim texts from the regex EW across
        # both user and assistant turns. LLM EW extractions are NOT
        # warmed here — their claim texts depend on the LLM's output
        # and aren't known until after the LLM call. Batching those is
        # a separate concern (LLM EW parallelism, deferred).
        warm_texts: set[str] = set()
        for _, voice, text in all_new_turns:
            for claim in extract_claims(text, voice=voice):
                t = (claim.text or "").strip()
                if t:
                    warm_texts.add(t)
        if warm_texts:
            from ..core.embed import current_embedder
            emb = current_embedder()
            if hasattr(emb, "embed_batch"):
                # Return value is discarded — we're only here for the
                # cache side-effect. Misses go through a single batched
                # API call; hits are no-ops. DiskCacheEmbedder flushes
                # the cache to disk at the SAVE_INTERVAL threshold
                # automatically.
                try:
                    emb.embed_batch(list(warm_texts))
                except Exception as e:
                    # Never fail a save on a warm-up error — the
                    # per-turn loop will retry one claim at a time and
                    # surface any real problem there.
                    print(
                        f"bellamem: warning: embedder pre-warm failed "
                        f"({e}). Per-claim fallback will apply.",
                    )

    turns = 0
    claims_written = 0
    affirmed = 0
    corrected = 0
    causes_added = 0
    self_obs_added = 0
    assistant_pending: list[tuple[str, str]] = []

    def apply_reaction(pending: list[tuple[str, str]], lr: float,
                        user_line: int) -> int:
        """Retroactive ratification — targets the *primary* (decision-
        bearing) claim of the preceding assistant turn, not a positional
        default.

        History: the prior fix used `pending[-1]` to replace the worse
        prior fix that ratified EVERY claim in the preceding turn. That
        was a positional heuristic dressed as a structural fix. It
        worked on most turns because decisions tend to come last, but
        failed on turns where the decision-bearing claim was followed
        by a non-decision sentence (e.g. a confirmation of the plan,
        a status note, a "let me know if"). The Q4 production-
        correctness gap was the honest counter-evidence: the y-axis-
        change decision was followed by a closing remark, and the
        `[-1]` slot captured the closing remark, not the decision.

        Fix: rank each pending claim by decision-bearing markers, pick
        the highest-scoring one, fall back smoothly to `pending[-1]`
        when no claim carries decision markers (in which case there's
        nothing particularly worth ratifying anyway and `[-1]` is as
        good a tiebreak as any).

        Scoring uses the same regex primitives as `_classify_assistant`:
        _DECISION_RE (`let's`, `we should`, `i'll`, `go with`, ...),
        _RULE_RE (`must`, `never`, `invariant`, ...), and content
        markers (file references, backticked names, tech tokens).
        Each marker adds a fixed weight; the claim with the highest
        total score wins. Ties go to the latest claim (same as the
        old `[-1]` behavior).

        This is a SEMANTIC fix, not a positional one: it ratifies the
        claim that looks most like a decision, regardless of where it
        sits in the turn. A decision at position 1 of 3 now gets
        correctly ratified instead of losing to a content-free
        follow-up at position 3.

        The evidence event remains the user turn; source line stamped
        from that turn so `sources` queries stay correct.
        """
        if not pending:
            return 0

        # Import the scoring regexes lazily to keep this adapter's
        # import graph shallow.
        from .chat import (
            _DECISION_RE, _RULE_RE, _CONTENT_MARKER_RE, _has_real_denial,
        )

        def _decision_score(text: str) -> int:
            """Load-bearing score for a claim. Higher = more decision-like."""
            if not text:
                return 0
            score = 0
            if _DECISION_RE.search(text):
                score += 3
            if _RULE_RE.search(text):
                score += 3
            if _has_real_denial(text):
                score += 3
            if _CONTENT_MARKER_RE.search(text):
                score += 1
            return score

        # Score every pending claim by its belief text
        best_idx = len(pending) - 1  # default: positional fallback
        best_score = -1
        for i, (fname, bid) in enumerate(pending):
            g = bella.fields.get(fname)
            if g is None:
                continue
            b = g.beliefs.get(bid)
            if b is None:
                continue
            score = _decision_score(b.desc)
            # Strict > so later claims win ties (degrades to [-1]
            # behavior when no decision markers are present).
            if score > best_score:
                best_score = score
                best_idx = i

        fname, bid = pending[best_idx]
        g = bella.fields.get(fname)
        if g is None:
            return 0
        b = g.beliefs.get(bid)
        if b is None:
            return 0
        b.accumulate(lr, voice="user", source=(session_key, user_line))
        return 1

    def track(pending: list[tuple[str, str]], result) -> None:
        if result.belief and result.field:
            pending.append((result.field, result.belief.id))

    for lineno, voice, text in all_new_turns:
        turns += 1

        if on_progress is not None and turns % PROGRESS_EVERY_N_TURNS == 0:
            on_progress(turns, claims_written)

        # 1) User turn: react to the preceding assistant turn first.
        if voice == "user" and assistant_pending:
            reaction = classify_reaction(text)
            if reaction == "affirm":
                affirmed += apply_reaction(assistant_pending, lr=2.2,
                                            user_line=lineno)
            elif reaction == "correct":
                corrected += apply_reaction(assistant_pending, lr=0.4,
                                            user_line=lineno)
            assistant_pending = []

        # 2) Regex EW — handles the common cases for both voices.
        new_pending: list[tuple[str, str]] = []
        for claim in extract_claims(text, voice=voice):
            # Stamp source AFTER extraction so chat.py stays transport-
            # agnostic. The adapter is the only place that knows the
            # transcript line number, and it's authoritative.
            claim.source = (session_key, lineno)
            result = bella.ingest(claim)
            claims_written += 1
            track(new_pending, result)

        # 3) LLM EW — scoped to assistant turns with structural markers.
        if llm_ew is not None and voice == "assistant":
            # Causes: effect ingested first, cause attached via target_field
            cause_pairs = ingest_causes(bella, llm_ew, text, voice=voice,
                                         source=(session_key, lineno))
            causes_added += len(cause_pairs)
            # Self-observations: routed directly to __self__ by core
            obs = ingest_self_observations(bella, llm_ew, text, voice=voice,
                                            source=(session_key, lineno))
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
                    latest_only: bool = False,
                    on_session_start: Optional[Callable[[str], None]] = None,
                    on_progress: Optional[Callable[[int, int], None]] = None,
                    ) -> Iterator[dict]:
    """Yield ingest results one session at a time.

    This is a generator rather than a list-returning function so CLI
    callers can print per-session progress in real time. Programmatic
    callers that want the old list behaviour can wrap the call in
    `list(ingest_project(...))` — the yielded dicts have exactly the
    same shape as before.

    Flags:
      tail             — limit each session to its last N turns
      no_llm           — disable LLM-backed EW regardless of env
      latest_only      — only ingest the most recent session (the one
                         whose file has the newest mtime)
      on_session_start — optional callback fired with the session's
                         basename before its ingest begins. Use to
                         render a "starting session X" header.
      on_progress      — optional callback forwarded to ingest_session;
                         fires every PROGRESS_EVERY_N_TURNS turns with
                         (turns_so_far, claims_so_far). Use to render
                         intra-file progress on huge transcripts.
    """
    sessions = list_sessions(cwd)
    if latest_only and sessions:
        # list_sessions now returns sorted by mtime asc; take the last.
        sessions = [sessions[-1]]
    for path in sessions:
        if on_session_start is not None:
            on_session_start(os.path.basename(path))
        yield ingest_session(
            bella, path, tail=tail, no_llm=no_llm, on_progress=on_progress,
        )
