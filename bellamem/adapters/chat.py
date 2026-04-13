"""Rule-based EW for chat messages — voice-aware.

Two rules govern this file (see PRINCIPLES.md):

  P9  User is oracle; assistant is hypothesis. lr reflects this.
  P10 Retroactive ratification: the user's reaction to the preceding
      assistant turn adjusts the lr of claims from that turn.

So this module exposes two things:

  extract_claims(text, voice)     sentence-level EW, voice-aware
  classify_reaction(text)         classify a user turn as affirm/correct/
                                  neutral, used by the turn-pair pass in
                                  adapters/claude_code.py

Both are conservative. The goal is signal density, not coverage.
An LLM-backed EW (v0.5) can replace extract_claims without touching
the rest of the pipeline.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..core.bella import Claim


# ---------------------------------------------------------------------------
# Sentence splitting + text hygiene
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`*\"'\(\[\-])")


def _strip_code_blocks(text: str) -> str:
    """Remove fenced code blocks — we never want to ingest code as claims."""
    lines = []
    in_code = False
    for ln in (text or "").splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        lines.append(ln)
    return "\n".join(lines)


def _scrub_markdown(s: str) -> str:
    """Remove markdown noise that leaks into claim text."""
    # Drop surrounding bold/italic markers but keep the content
    s = re.sub(r"\*\*+", "", s)
    s = re.sub(r"(?<!\w)_(?=\w)|(?<=\w)_(?!\w)", "", s)
    # Drop leading list markers
    s = re.sub(r"^\s*[-*•]\s+", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_sentences(text: str) -> list[str]:
    """Split text into sentence-ish chunks, skipping code blocks and markdown noise."""
    text = _strip_code_blocks(text)
    if not text.strip():
        return []
    # Break list items onto their own pseudo-sentence boundary
    for token in ("- ", "* ", "• "):
        text = text.replace("\n" + token, ".\n" + token)
    flat = " ".join(ln.strip() for ln in text.splitlines() if ln.strip())
    parts = _SENT_SPLIT.split(flat)
    out: list[str] = []
    for p in parts:
        s = _scrub_markdown(p)
        if s:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Lexical markers
# ---------------------------------------------------------------------------

_QUESTION_RE = re.compile(r"\?\s*$")

_FILLER_RE = re.compile(
    r"^(thanks|thank you|ok|okay|sure|got it|sounds good|cool|nice|great|"
    r"hmm|yeah|yep|nope|hi|hello|wait|ah|oh|right|correct|alright)\b",
    re.I,
)

_DECISION_MARKERS = [
    r"\blet'?s\b",
    r"\bwe (should|must|will|need to|can|are going to)\b",
    r"\bi (will|should|must|prefer|picked|choose|decided)\b",
    r"\bgo with\b",
    r"\buse (the )?\w+ (for|as|to)\b",
    r"\bprefer(red)?\b",
    r"\bdecid(ed|e|ing)\b",
    r"\bfor v\d\b",
    r"\bfor now\b",
    r"\bstart(ing)? (with|in)\b",
    r"\bdefault(s)? to\b",
    r"\bstick with\b",
    r"\bthe (right|best|sensible|correct) (call|choice|pick|option|answer) is\b",
]
_DECISION_RE = re.compile("|".join(_DECISION_MARKERS), re.I)

# Denial = explicit rejection of something. "never" and "not" are intentionally
# NOT in this list — they're rule intensifiers far more often than rejections
# ("principles never decay" is a rule, not a denial). Only phrases that carry
# an unambiguous reject-this-thing meaning qualify.
_DENIAL_MARKERS = [
    r"\bdon'?t\b",
    r"\bdo not\b",
    r"\bavoid\b",
    r"\bstop (doing|using)\b",
    r"\binstead of\b",
    r"\brather than\b",
    r"\breject(ed)?\b",
    r"\bnot the right\b",
    r"\bnot that (way|approach)\b",
    r"\bwrong (approach|answer|call|choice)\b",
]
_DENIAL_RE = re.compile("|".join(_DENIAL_MARKERS), re.I)

# Contexts where a denial word is not actually a denial:
#  - inside backticks or quoted strings (it's a meta-reference to the token)
#  - following "if" (conditional warning, not rejection)
_DENIAL_IN_QUOTE_RE = re.compile(
    r"`[^`]*(don'?t|do not|avoid)[^`]*`|"
    r"[\"'][^\"']*(don'?t|do not|avoid)[^\"']*[\"']",
    re.I,
)
_CONDITIONAL_DENIAL_RE = re.compile(
    r"\bif\s+(we|you|i|they|someone)?\s*(don'?t|do not)\b",
    re.I,
)


def _has_real_denial(s: str) -> bool:
    """True if the sentence carries a denial that isn't quoted, conditional,
    or sitting inside a quoted-phrase fragment of the sentence.

    Rejection tests, in order:
      1. Strip inline backtick/quote-wrapped denial words (meta-reference)
      2. Strip conditional "if we don't" constructs (conditional warning)
      3. Require the remaining denial to appear BEFORE any sentence-break
         punctuation (comma, colon, em-dash, en-dash). "X, don't Y" is
         usually a quoted phrase of X ("Parse, don't validate" quoting
         a principle name, not a rejection).
    """
    if not _DENIAL_RE.search(s):
        return False
    stripped = _DENIAL_IN_QUOTE_RE.sub("", s)
    stripped = _CONDITIONAL_DENIAL_RE.sub("", stripped)
    m = _DENIAL_RE.search(stripped)
    if not m:
        return False
    pre = stripped[: m.start()]
    if re.search(r"[,:\u2014\u2013]", pre):
        return False
    return True

# Rule = stating an invariant. "never" and "always" are the canonical markers
# here; "immutable" / "cannot" / "may not" express the same semantic.
_RULE_MARKERS = [
    r"\bmust\b",
    r"\balways\b",
    r"\bnever\b",
    r"\brequired?\b",
    r"\bkey (rule|invariant|constraint)\b",
    r"\binvariant\b",
    r"\bcontract\b",
    r"\bimmutable\b",
    r"\bmay not\b",
    r"\bcan(?:not|'?t)\b",
    r"\bdo not (decay|change|drop|rise|mutate|pollute)\b",
]
_RULE_RE = re.compile("|".join(_RULE_MARKERS), re.I)

# Assistant preambles — execution state, not claims
_ASSISTANT_PREAMBLE_RE = re.compile(
    r"^(let me|i'?ll|i will|i'?m going to|i can|i'?ve|here'?s|here is|"
    r"now i|next|first|finally|acknowledged|got it|done\.?$|working on|"
    r"building|running|checking|reading|writing|editing|look at|"
    r"notice that|note that|compare|let'?s look|see how|observe)\b",
    re.I,
)

# Meta-sentences referring backward — almost always low content
_DEMONSTRATIVE_RE = re.compile(
    r"^(this|that|these|those|it|they|which|what)\b",
    re.I,
)

# Bellamem's own graph output fingerprints — if a sentence contains one
# of these patterns, it's a direct quote of audit/expand/surprise output,
# not a new claim. Filtering here prevents the graph from re-ingesting
# its own inspection reports as fresh beliefs (the "assistant quotes
# audit → gets re-extracted → appears in next audit" loop the graph
# has already ratified as a known rot pattern).
#
# The two signatures are unambiguous — neither appears in normal English
# or code:
#   m=0.XX v=N   →  bellamem's belief mass/voice format
#   score=X.XX Δ=  →  bellamem surprises output
#
# Same class as _ASSISTANT_PREAMBLE_RE and _DEMONSTRATIVE_RE: a
# structural pattern filter, not a hand-maintained stoplist.
_GRAPH_OUTPUT_RE = re.compile(
    r"m=\d\.\d{1,3}\s*v=\d|"     # bellamem mass/voice format
    r"score=\d\.\d{1,3}\s*Δ=",    # bellamem surprises format
)

# Markdown-heavy lines that are mostly formatting, not content
_CODE_DENSITY_RE = re.compile(r"`[^`]{1,60}`")

# Entity / content markers — sentences containing these are more likely
# to carry real signal and get a small lr boost.
_CONTENT_MARKER_RE = re.compile(
    r"(`[^`]{2,40}`|"                                 # backticked code/name
    r"\b[a-z_][a-z_0-9]*\.(py|ts|js|tsx|rs|md|toml)\b|"  # file reference
    r"\b(Python|Rust|TypeScript|JavaScript|Go|Neo4j|Postgres|SQLite|"
    r"BELLA|OpenAI|sentence-transformers|sqlite|KuzuDB|networkx|"
    r"Jaynes|pgvector|hnswlib)\b)",
    re.I,
)


# ---------------------------------------------------------------------------
# Voice-aware classification
# ---------------------------------------------------------------------------

def _classify_user(sent: str) -> tuple[str, float] | None:
    """User claims are oracle. Accept short declaratives, boost rules/decisions."""
    s = sent.strip()
    if len(s) < 6 or len(s) > 300:
        return None
    if _GRAPH_OUTPUT_RE.search(s):
        return None  # quoted graph output, not a new claim (see regex comment)
    if _QUESTION_RE.search(s):
        return None
    if _FILLER_RE.match(s):
        return None
    # Rule before deny: "never decay" is a rule, not a denial.
    if _RULE_RE.search(s):
        return ("add", 2.5)
    if _has_real_denial(s):
        return ("deny", 2.5)
    if _DECISION_RE.search(s):
        return ("add", 2.2)
    # User's short observations still carry real weight
    words = s.split()
    if 4 <= len(words) <= 40:
        return ("add", 1.8)
    return None


def _classify_assistant(sent: str) -> tuple[str, float] | None:
    """Assistant claims are hypothesis. Accept sparingly, at low lr.

    They earn mass retroactively when the user ratifies the turn (P10).

    Filters (in order of cost):
      1. Length bounds — skip tiny fragments and long explanations
      2. Questions and fillers
      3. Preambles and meta-directions ("let me", "look at", "notice")
      4. Demonstratives ("this", "that", "it") — usually backward-referring
      5. Markdown/table/blockquote starts
      6. Dense code/markdown formatting
      7. Lexical markers (denial / rule / decision) — accept with their lr
      8. Content markers (file / tech / backticked name) — accept at 1.15
      9. Otherwise: skip (no plain-declarative fallback)
    """
    s = sent.strip()
    words = s.split()
    n_words = len(words)
    if n_words < 8 or n_words > 28 or len(s) > 240:
        return None
    if _GRAPH_OUTPUT_RE.search(s):
        return None  # quoted graph output, not a new claim (see regex comment)
    if _QUESTION_RE.search(s):
        return None
    if _FILLER_RE.match(s):
        return None
    if _ASSISTANT_PREAMBLE_RE.match(s):
        return None
    if _DEMONSTRATIVE_RE.match(s):
        return None
    if s.startswith("#") or s.startswith("|") or s.startswith(">"):
        return None
    if len(_CODE_DENSITY_RE.findall(s)) >= 3:
        return None
    # Lexical markers — strong signal, accept at their lr.
    # Rule before deny: "never decay" is a rule, not a denial.
    if _RULE_RE.search(s):
        return ("add", 1.3)
    if _has_real_denial(s):
        return ("deny", 1.3)
    if _DECISION_RE.search(s):
        return ("add", 1.15)
    # Content markers — sentence names a file, function, library, or tech
    if _CONTENT_MARKER_RE.search(s):
        return ("add", 1.15)
    # No marker → skip. The assistant's prose alone is not evidence.
    return None


def classify(sent: str, voice: str = "user") -> tuple[str, float] | None:
    """Entry point — dispatches by voice."""
    if voice == "assistant":
        return _classify_assistant(sent)
    return _classify_user(sent)


# ---------------------------------------------------------------------------
# Turn-pair reaction classification (P10)
# ---------------------------------------------------------------------------

_REACTION_AFFIRM_WORDS = {
    "yes", "yeah", "ya", "yup", "yep", "exactly", "perfect", "right",
    "correct", "agreed", "agree", "let's", "lets", "proceed", "continue",
    "go", "sure", "ok", "okay", "fine", "good", "great", "nice",
    "advance", "ship", "approved", "done",
}

_REACTION_AFFIRM_PHRASES = (
    "let's move on", "let's advance", "let's proceed", "let's continue",
    "let's do", "do it", "ship it", "go ahead", "move on", "sounds good",
    "makes sense",
)

_REACTION_CORRECT_PHRASES = (
    "no,", "nope,", "not that", "not the", "wrong", "stop", "instead",
    "actually no", "rather", "don't", "do not",
)


def classify_reaction(text: str) -> str:
    """Classify a user turn as a reaction to the preceding assistant turn.

    Returns one of: "affirm", "correct", "neutral". Used by the turn-pair
    pass in adapters/claude_code.py to retroactively adjust lr on the
    beliefs extracted from the previous assistant turn (P10).
    """
    t = (text or "").strip().lower()
    if not t:
        return "neutral"
    words = t.split()
    n_words = len(words)

    # Correction: look for explicit denial in the first half of a short message.
    # Long messages dilute signal — treat them as neutral even if they contain "not".
    if n_words <= 40:
        head = " ".join(words[: max(1, n_words // 2 + 1)])
        for phrase in _REACTION_CORRECT_PHRASES:
            if phrase in head:
                return "correct"

    # Affirm: short message with affirm words or phrases.
    if n_words <= 20:
        # Strip punctuation for word comparison
        clean_words = {w.strip(".,!?;:'\"()[]{}") for w in words}
        if clean_words & _REACTION_AFFIRM_WORDS:
            return "affirm"
        for phrase in _REACTION_AFFIRM_PHRASES:
            if phrase in t:
                return "affirm"
        # Messages starting with "ya" or "ok" are almost always affirms
        if words[0].rstrip(",.") in ("ya", "ok", "okay", "yes", "yeah", "agreed"):
            return "affirm"

    return "neutral"


# ---------------------------------------------------------------------------
# Entity extraction (unchanged)
# ---------------------------------------------------------------------------

_ENTITY_PATTERNS = [
    (re.compile(r"`([^`]{2,80})`"), "code"),
    (re.compile(r"\b([a-z_][a-z_0-9]*\.(py|ts|js|tsx|rs|md))\b", re.I), "file"),
    (re.compile(r"\b(Python|Rust|TypeScript|JavaScript|Go|Neo4j|Postgres|SQLite|BELLA)\b"), "tech"),
]


def extract_entities(text: str) -> list[str]:
    out: list[str] = []
    for pat, _kind in _ENTITY_PATTERNS:
        for m in pat.finditer(text or ""):
            e = m.group(1).strip()
            if e and e not in out:
                out.append(e)
    return out[:16]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def extract_claims(text: str, *, voice: str = "user") -> list[Claim]:
    """Voice-aware EW: each qualifying sentence becomes one Claim."""
    out: list[Claim] = []
    ents = extract_entities(text)
    for s in split_sentences(text):
        cls = classify(s, voice=voice)
        if not cls:
            continue
        relation, lr = cls
        out.append(Claim(
            text=s,
            voice=voice,
            lr=lr,
            relation=relation,
            entity_refs=list(ents),
        ))
    return out


def extract_many(messages: Iterable[tuple[str, str]]) -> list[Claim]:
    out: list[Claim] = []
    for voice, text in messages:
        out.extend(extract_claims(text, voice=voice))
    return out
