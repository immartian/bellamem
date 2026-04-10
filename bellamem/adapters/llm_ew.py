"""LLM-backed extraction for the structural cases regex can't handle.

Two tasks — both scoped, both cheap:

  find_cause_pairs(text)         → list[(cause, effect)]
      Extracts causal claims. The LLM splits the span — regex can't.

  find_self_observations(text)   → list[str]
      Extracts first-person habit statements ("I tend to X when Y").
      Routes to __self__ via relation="self_observation".

Both use gpt-4o-mini at temperature=0 with JSON mode for deterministic,
cache-friendly output. Each call is cached on disk by md5(task + model
+ text) so re-ingest is free.

This module is optional — it's behind the `[openai]` extra and only
activates when `BELLAMEM_EW=hybrid` is set. The regex EW in chat.py
is the zero-dep default; this is a quality upgrade.

Cost: ~$0.002 per typical session (see PRINCIPLES discussion turn).
Fails loud (C10) on malformed JSON or API errors — no silent fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from typing import TYPE_CHECKING

from ..core.bella import Claim

if TYPE_CHECKING:
    from ..core.bella import Bella


# ---------------------------------------------------------------------------
# Marker gates — cheap filters so we only call the LLM when worth it
# ---------------------------------------------------------------------------

_CAUSE_MARKERS = re.compile(
    r"\b(because|caused? by|root cause|the reason|due to|"
    r"as a result|leading to|results? in|"
    r"why (this|that|it) (happens|happened|is|was)|"
    r"comes? from|stems? from|owing to)\b",
    re.I,
)

_SELF_MARKERS = re.compile(
    r"\b(i tend to|i often|i usually|i always|my default|"
    r"i'?m prone to|i reach for|i reflexively|"
    r"i find myself|i keep (doing|getting)|"
    r"i have a (habit|tendency|pattern) of)\b",
    re.I,
)


def has_cause_markers(text: str) -> bool:
    return bool(_CAUSE_MARKERS.search(text or ""))


def has_self_markers(text: str) -> bool:
    return bool(_SELF_MARKERS.search(text or ""))


# ---------------------------------------------------------------------------
# Prompts — kept in one place, edited here if results need tuning
# ---------------------------------------------------------------------------

_CAUSE_SYSTEM = (
    "You extract causal claims from text. A causal claim asserts that "
    "one thing happened or is true BECAUSE of another thing. "
    "Only extract actual claims — skip hypotheticals, definitions, and "
    "pure explanations of how something works. "
    "Return strict JSON."
)

_CAUSE_USER_TEMPLATE = """Extract all causal pairs from the text below.

For each real causal claim, return an object with:
  cause:  the causal predecessor, 3-15 words, paraphrased from the text
  effect: the consequence, 3-15 words, paraphrased from the text

Return: {{"pairs": [{{"cause": "...", "effect": "..."}}, ...]}}
Empty array if there are no causal claims.

Do NOT extract:
- Hypothetical/conditional statements ("if X then Y")
- Definitions or tautologies
- Generic process descriptions

Text:
\"\"\"
{text}
\"\"\"
"""

_SELF_SYSTEM = (
    "You identify first-person statements about HABITUAL behavior patterns — "
    "the speaker's tendencies, defaults, reflexes, or typical responses. "
    "Do NOT extract one-time decisions, preferences, or opinions. "
    "Return strict JSON."
)

_FIELD_NAME_SYSTEM = (
    "You name clusters of beliefs about software engineering. You receive "
    "the descriptions of the highest-mass beliefs in one cluster. Your job "
    "is to output a short snake_case identifier (2-3 tokens, max 40 chars) "
    "that captures the cluster's topic. "
    "Prefer concrete domain nouns (auth, embeddings, ingest, routing). "
    "Avoid filler words (memory, session, system, without, with, the). "
    "Return strict JSON."
)

_FIELD_NAME_USER_TEMPLATE = """Here are the top beliefs in one cluster. \
Give a 2-3 word snake_case name for the cluster.

Beliefs:
{beliefs}

Return: {{"name": "example_snake_case"}}
Rules:
- 2 or 3 underscore-separated tokens
- lowercase letters, digits, underscores only
- max 40 characters total
- concrete (e.g. auth_tokens, bench_corpus, embedder_config) not abstract
  (e.g. memory_session, system_design, project_state)
"""


_SELF_USER_TEMPLATE = """Identify habitual self-observations in the text below.

Extract statements like:
  "I tend to reach for try/except when I hit a KeyError"
  "My default is to add a null check"
  "I often forget to batch API calls"

SKIP statements like:
  "I will do X" (one-time decision)
  "I chose Y" (past decision)
  "I think Z" (opinion)

For each self-observation, return a first-person paraphrase (5-20 words).

Return: {{"observations": ["I tend to ...", ...]}}
Empty array if none.

Text:
\"\"\"
{text}
\"\"\"
"""


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class LLMExtractor:
    """Wraps an OpenAI client + disk cache for CAUSE and self-observation
    extraction. Single instance reused across a whole ingest session.
    """

    SAVE_INTERVAL = 20  # llm_ew cache is smaller; more frequent flushes fine

    def __init__(self, *, model: str = "gpt-4o-mini",
                 api_key: str | None = None,
                 cache_path: str | None = None):
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "openai is not installed. Install with one of:\n"
                "  pipx inject bellamem 'openai>=1.0'   (if you pipx-installed bellamem — recommended)\n"
                "  pip install 'bellamem[openai]'       (if you used a regular pip install)\n"
                "  pip install -e '.[openai]'           (if you're running from a source checkout)"
            ) from e
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Put it in .env or your shell env."
            )
        self._client = OpenAI(api_key=key)
        self._model = model
        if cache_path is None:
            from ..paths import default_llm_ew_cache_path
            cache_path = default_llm_ew_cache_path()
        self._cache_path = cache_path
        self._cache: dict[str, dict] = {}
        self._dirty = 0
        self._load_cache()

    @property
    def model(self) -> str:
        return self._model

    # --- cache ------------------------------------------------------------

    def _key(self, task: str, text: str) -> str:
        h = hashlib.md5(f"{task}|{self._model}|{text or ''}".encode())
        return h.hexdigest()

    def _load_cache(self) -> None:
        if not os.path.exists(self._cache_path):
            return
        try:
            with open(self._cache_path) as f:
                self._cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._cache = {}

    def _save_cache(self) -> None:
        d = os.path.dirname(os.path.abspath(self._cache_path)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".llmewcache_", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._cache, f)
            os.replace(tmp, self._cache_path)
            self._dirty = 0
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _mark_dirty(self) -> None:
        self._dirty += 1
        if self._dirty >= self.SAVE_INTERVAL:
            self._save_cache()

    def flush(self) -> None:
        """Force a save if there are unsaved writes."""
        if self._dirty > 0:
            self._save_cache()

    # --- LLM call wrapper -------------------------------------------------

    def _call_json(self, system: str, user: str) -> dict:
        """Single API call, JSON mode, temp=0, fail loud on malformed."""
        resp = self._client.chat.completions.create(
            model=self._model,
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=800,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        return json.loads(content)  # JSON mode guarantees validity; if not, raise

    # --- public extraction methods ---------------------------------------

    def find_cause_pairs(self, text: str) -> list[tuple[str, str]]:
        """Return [(cause, effect), ...]. Cache-first."""
        if not text or not has_cause_markers(text):
            return []
        key = self._key("cause", text)
        if key in self._cache:
            entry = self._cache[key]
        else:
            user = _CAUSE_USER_TEMPLATE.format(text=text[:4000])
            entry = self._call_json(_CAUSE_SYSTEM, user)
            self._cache[key] = entry
            self._mark_dirty()
        pairs = entry.get("pairs", []) if isinstance(entry, dict) else []
        out: list[tuple[str, str]] = []
        for p in pairs:
            if not isinstance(p, dict):
                continue
            c = (p.get("cause") or "").strip()
            e = (p.get("effect") or "").strip()
            if c and e and len(c) < 200 and len(e) < 200:
                out.append((c, e))
        return out

    def suggest_field_name(self, belief_descs: list[str]) -> str:
        """Return a snake_case field name from the top-mass belief descs.

        Single LLM call, cached. Returns empty string on failure so
        callers can fall back to the previous name. The output is
        sanitized client-side — we don't trust the model to produce
        exactly the format requested.
        """
        if not belief_descs:
            return ""
        # Build a deterministic cache key from the joined descs
        joined = "\n".join(f"- {d[:200]}" for d in belief_descs[:12])
        key = self._key("fieldname", joined)
        if key in self._cache:
            entry = self._cache[key]
        else:
            user = _FIELD_NAME_USER_TEMPLATE.format(beliefs=joined)
            entry = self._call_json(_FIELD_NAME_SYSTEM, user)
            self._cache[key] = entry
            self._mark_dirty()
        if not isinstance(entry, dict):
            return ""
        raw = entry.get("name", "")
        if not isinstance(raw, str):
            return ""
        # Sanitize to the contract: lowercase, alnum+underscore, ≤ 40
        sanitized = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")[:40]
        return sanitized

    def find_self_observations(self, text: str) -> list[str]:
        """Return paraphrased self-observations. Cache-first."""
        if not text or not has_self_markers(text):
            return []
        key = self._key("self", text)
        if key in self._cache:
            entry = self._cache[key]
        else:
            user = _SELF_USER_TEMPLATE.format(text=text[:4000])
            entry = self._call_json(_SELF_SYSTEM, user)
            self._cache[key] = entry
            self._mark_dirty()
        obs = entry.get("observations", []) if isinstance(entry, dict) else []
        return [s.strip() for s in obs if isinstance(s, str) and s.strip()]


# ---------------------------------------------------------------------------
# Compound ingest helpers — thread effect→cause target ids correctly
# ---------------------------------------------------------------------------

def ingest_causes(bella: "Bella", extractor: LLMExtractor, text: str,
                  *, voice: str = "assistant", lr: float = 1.3,
                  source: "tuple[str, int] | None" = None
                  ) -> list[tuple[str, str]]:
    """Extract cause pairs from text and ingest them as structured beliefs.

    For each (cause, effect) pair:
      1. Ingest the effect as a regular belief, capture its (field, id)
      2. Ingest the cause with relation="cause", target_hint=effect_id,
         target_field=effect_field so the CAUSE edge lands under the effect

    `source` (session_key, line_number) is stamped on both the effect
    and cause claims so their provenance matches the assistant turn
    they were extracted from.

    Returns the list of (cause, effect) texts actually ingested.
    """
    pairs = extractor.find_cause_pairs(text)
    ingested: list[tuple[str, str]] = []
    for cause_text, effect_text in pairs:
        effect_claim = Claim(text=effect_text, voice=voice, lr=lr,
                             relation="add", source=source)
        effect_result = bella.ingest(effect_claim)
        if not (effect_result.belief and effect_result.field):
            continue
        cause_claim = Claim(
            text=cause_text, voice=voice, lr=lr, relation="cause",
            target_hint=effect_result.belief.id,
            target_field=effect_result.field,
            source=source,
        )
        cause_result = bella.ingest(cause_claim)
        if cause_result.belief:
            ingested.append((cause_text, effect_text))
    return ingested


def ingest_self_observations(bella: "Bella", extractor: LLMExtractor,
                              text: str, *, voice: str = "assistant",
                              lr: float = 1.5,
                              source: "tuple[str, int] | None" = None
                              ) -> list[str]:
    """Extract self-observations and route them to __self__."""
    obs = extractor.find_self_observations(text)
    ingested: list[str] = []
    for o in obs:
        claim = Claim(text=o, voice=voice, lr=lr,
                      relation="self_observation", source=source)
        result = bella.ingest(claim)
        if result.belief:
            ingested.append(o)
    return ingested


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_llm_ew_from_env() -> LLMExtractor | None:
    """Return an LLMExtractor if BELLAMEM_EW=hybrid, else None."""
    mode = (os.environ.get("BELLAMEM_EW", "regex") or "regex").lower()
    if mode != "hybrid":
        return None
    model = os.environ.get("BELLAMEM_EW_LLM_MODEL") or "gpt-4o-mini"
    cache_path = os.environ.get("BELLAMEM_EW_LLM_CACHE_PATH") or None
    return LLMExtractor(model=model, cache_path=cache_path)


def make_llm_name_fn(extractor: LLMExtractor, *, top_k: int = 12):
    """Return a NameFn for core.emerge.emerge() backed by an LLMExtractor.

    Called at most once per garbage field. The contrastive-rate baseline
    runs first and is passed as the fallback if the LLM returns something
    unusable (empty, same as original, contains the literal old name).
    """
    from ..core.emerge import derive_field_name

    def name_fn(bella, field_name: str) -> str:
        # Try the deterministic baseline first — if it produces a better
        # name, we prefer it (cheaper, auditable).
        baseline = derive_field_name(bella, field_name)
        if baseline != field_name:
            return baseline
        # Baseline failed. Use LLM.
        g = bella.fields.get(field_name)
        if g is None:
            return field_name
        top = sorted(g.beliefs.values(), key=lambda b: b.mass, reverse=True)[:top_k]
        descs = [b.desc for b in top if b.desc]
        suggestion = extractor.suggest_field_name(descs)
        if not suggestion or suggestion == field_name:
            return field_name
        return suggestion

    return name_fn
