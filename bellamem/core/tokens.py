"""Token counting — real tokenizers with a stdlib fallback.

bellamem measures context-pack sizes in *tokens*, which is what an LLM
agent's context window actually budgets. Earlier versions used a
`len(text) // 4` heuristic that's off by ±30% depending on content
(code, markdown, non-ASCII). That's fine for ordering but lies when
reported as a precise budget. This module fixes that.

Backends:

  HeuristicTokenizer   len(text) // 4 — stdlib, ~±30% error, always available
  TiktokenTokenizer    cl100k_base via tiktoken — opt-in via `[tokens]` extra

cl100k_base is the encoding used by gpt-4 / gpt-3.5-turbo / text-embedding-3-*.
It's also a close proxy for Claude's tokenization (±5-10% on typical
English prose and code). Good enough for budget accounting without
requiring an Anthropic API call per measurement.

Configuration:

    BELLAMEM_TOKENIZER=auto       (default — prefers tiktoken if installed)
    BELLAMEM_TOKENIZER=heuristic  (force the stdlib 4-char heuristic)
    BELLAMEM_TOKENIZER=tiktoken   (force tiktoken; error if not installed)
"""

from __future__ import annotations

import functools
import os
from typing import Protocol


# ---------------------------------------------------------------------------
# Protocol + implementations
# ---------------------------------------------------------------------------

class Tokenizer(Protocol):
    name: str
    def count(self, text: str) -> int: ...


class HeuristicTokenizer:
    """Zero-dep fallback. ~4 chars/token is a rough OpenAI/Claude proxy."""

    name = "heuristic-4"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // 4)


class TiktokenTokenizer:
    """Real BPE tokenizer via tiktoken. cl100k_base covers gpt-4,
    gpt-3.5-turbo, and text-embedding-3-*. Close enough to Claude's
    tokenization for budget accounting."""

    def __init__(self, encoding: str = "cl100k_base"):
        try:
            import tiktoken  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "tiktoken not installed. Install with: pip install -e '.[tokens]'"
            ) from e
        self._enc = tiktoken.get_encoding(encoding)
        self.name = f"tiktoken-{encoding}"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._enc.encode(text))

    def encode(self, text: str) -> list[int]:
        return self._enc.encode(text or "")

    def decode(self, tokens: list[int]) -> str:
        return self._enc.decode(tokens)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def get_tokenizer() -> Tokenizer:
    kind = (os.environ.get("BELLAMEM_TOKENIZER", "auto") or "auto").lower()
    if kind == "heuristic":
        return HeuristicTokenizer()
    if kind == "tiktoken":
        return TiktokenTokenizer()
    # auto: prefer tiktoken if available, fall back to heuristic
    try:
        return TiktokenTokenizer()
    except RuntimeError:
        return HeuristicTokenizer()


def reset_tokenizer_cache() -> None:
    """Force re-resolution of the active tokenizer. Tests use this."""
    get_tokenizer.cache_clear()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def count_tokens(text: str) -> int:
    return get_tokenizer().count(text)


def tail_tokens(text: str, budget: int) -> str:
    """Return the last `budget` tokens of `text` as a string.

    Uses the active tokenizer's encode/decode round-trip when available
    (tiktoken), falls back to a character-budget approximation otherwise.
    """
    if not text or budget <= 0:
        return ""
    tok = get_tokenizer()
    if isinstance(tok, TiktokenTokenizer):
        ids = tok.encode(text)
        if len(ids) <= budget:
            return text
        return tok.decode(ids[-budget:])
    # Heuristic fallback: ~4 chars per token
    est_chars = budget * 4
    if len(text) <= est_chars:
        return text
    return text[-est_chars:]
