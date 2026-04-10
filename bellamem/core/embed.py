"""Embedder — pluggable, stdlib-only default, optional upgrades.

Design (respecting P1, P4, P6):

  HashEmbedder                 zero-dep default, always available
  SentenceTransformerEmbedder  local, free, high quality
                               (pip install bellamem[st])
  OpenAIEmbedder               API, best quality
                               (pip install bellamem[openai] + OPENAI_API_KEY)
  DiskCacheEmbedder            wraps any of the above with JSON-on-disk cache
                               keyed by md5(text). Idempotent re-ingest is
                               then free.

Each embedder exposes `name` and `dim` so the snapshot can record which
embedder produced its vectors and refuse to load under a mismatched one.
That check is in core/store.py — we fail loud (C10) instead of mixing
dimensions silently.

Configuration is via environment variables (optionally loaded from a
.env file by the CLI at startup — explicit, not on import, per C11):

    BELLAMEM_EMBEDDER           hash | st | openai        (default: hash)
    BELLAMEM_EMBEDDER_MODEL     model id                  (default depends on kind)
    BELLAMEM_EMBEDDER_CACHE     0 or 1                    (default: 1 for non-hash)
    BELLAMEM_EMBEDDER_CACHE_PATH  path to cache file      (default: <project>/.graph/embed_cache.json)
    OPENAI_API_KEY              for openai backend
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from typing import Protocol


# ---------------------------------------------------------------------------
# Tokenization helpers (used only by HashEmbedder)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _trigrams(text: str) -> list[str]:
    toks = _tokens(text)
    grams: list[str] = []
    grams.extend(toks)  # unigrams
    for t in toks:
        s = f" {t} "
        for i in range(len(s) - 2):
            grams.append(s[i:i + 3])
    return grams


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class Embedder(Protocol):
    dim: int
    name: str
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Embedders here always normalize; dot == cosine for unit vectors.
    return max(-1.0, min(1.0, dot))


# ---------------------------------------------------------------------------
# HashEmbedder — zero-dep default
# ---------------------------------------------------------------------------

class HashEmbedder:
    """Character-trigram hashing trick. Low quality but instant and zero-dep.

    The floor for correctness: structural machinery (routing, emergence,
    EXPAND) works on top of whatever signal this produces.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.name = f"hash-{dim}"

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        if not text:
            return vec
        for g in _trigrams(text):
            h = int(hashlib.md5(g.encode()).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 32) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


# ---------------------------------------------------------------------------
# SentenceTransformerEmbedder — lazy, optional
# ---------------------------------------------------------------------------

class SentenceTransformerEmbedder:
    """Local neural embedder. pip install bellamem[st]."""

    def __init__(self, model: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install with: pip install -e '.[st]'"
            ) from e
        self._model = SentenceTransformer(model)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = f"st-{model}"

    def embed(self, text: str) -> list[float]:
        v = self._model.encode(text or "", normalize_embeddings=True)
        return v.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vs = self._model.encode(texts, normalize_embeddings=True,
                                 show_progress_bar=False)
        return [v.tolist() for v in vs]


# ---------------------------------------------------------------------------
# OpenAIEmbedder — lazy, optional, API-backed
# ---------------------------------------------------------------------------

# OpenAI model dimension table (as of 2025 API).
_OPENAI_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedder:
    """OpenAI API embedder. pip install bellamem[openai] + OPENAI_API_KEY.

    Batches up to 1024 texts per request. Normalizes to unit length so
    cosine can be computed by dot product like the other backends.
    """

    BATCH_CAP = 1024

    def __init__(self, model: str = "text-embedding-3-small",
                 api_key: str | None = None):
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
        self.dim = _OPENAI_DIMS.get(model, 1536)
        self.name = f"openai-{model}"

    @staticmethod
    def _normalize(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text or " "])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # OpenAI rejects empty strings; substitute a single space.
        safe = [t if (t and t.strip()) else " " for t in texts]
        out: list[list[float]] = []
        for i in range(0, len(safe), self.BATCH_CAP):
            chunk = safe[i:i + self.BATCH_CAP]
            resp = self._client.embeddings.create(model=self._model, input=chunk)
            # The API returns rows in input order.
            for row in resp.data:
                out.append(self._normalize(list(row.embedding)))
        return out


# ---------------------------------------------------------------------------
# DiskCacheEmbedder — wraps any embedder with a JSON-on-disk cache
# ---------------------------------------------------------------------------

class DiskCacheEmbedder:
    """JSON-on-disk cache keyed by md5(inner_name + text).

    Written atomically via tmp+rename (P8). Cache key includes the inner
    embedder name so a different backend can't see another backend's vectors.

    Saves are batched: every SAVE_INTERVAL dirty misses trigger a flush,
    and callers can force a final flush via flush(). Rewriting a multi-MB
    JSON on every single miss is O(N²) in disk I/O and makes ingest thrash.
    """

    SAVE_INTERVAL = 50  # persist after this many dirty inserts

    def __init__(self, inner: Embedder, path: str):
        self.inner = inner
        self.dim = inner.dim
        self.name = inner.name  # signature tracks the inner, not the cache
        self.path = path
        self._cache: dict[str, list[float]] = {}
        self._dirty = 0
        self._load()

    def _key(self, text: str) -> str:
        h = hashlib.md5(((self.inner.name or "") + "\0" + (text or "")).encode())
        return h.hexdigest()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                self._cache = json.load(f)
        except (OSError, json.JSONDecodeError):
            self._cache = {}

    def _save(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".embedcache_", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._cache, f)
            os.replace(tmp, self.path)
            self._dirty = 0
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _mark_dirty(self, n: int = 1) -> None:
        self._dirty += n
        if self._dirty >= self.SAVE_INTERVAL:
            self._save()

    def flush(self) -> None:
        """Force a save if there are unsaved writes. Call at end of ingest."""
        if self._dirty > 0:
            self._save()

    def prune_to(self, texts) -> int:
        """Keep only cache entries whose key matches one of `texts`.

        Bounds the cache to what the live forest still needs. Without
        this, every `expand`/`recall`/`surprise`/`emerge` call leaves a
        query embedding behind forever, and the cache grows monotonically
        with session activity — not with belief count.

        Returns the number of entries dropped. Writes the cache file
        atomically if anything was removed.
        """
        keep = {self._key(t) for t in texts}
        before = len(self._cache)
        if before == 0:
            return 0
        self._cache = {k: v for k, v in self._cache.items() if k in keep}
        dropped = before - len(self._cache)
        if dropped:
            self._save()
        return dropped

    def embed(self, text: str) -> list[float]:
        k = self._key(text)
        if k in self._cache:
            return self._cache[k]
        v = self.inner.embed(text)
        self._cache[k] = v
        self._mark_dirty()
        return v

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        keys = [self._key(t) for t in texts]
        out: list[list[float] | None] = [None] * len(texts)
        missing_idx: list[int] = []
        missing_texts: list[str] = []
        for i, (k, t) in enumerate(zip(keys, texts)):
            if k in self._cache:
                out[i] = self._cache[k]
            else:
                missing_idx.append(i)
                missing_texts.append(t)
        if missing_texts:
            vecs = self.inner.embed_batch(missing_texts)
            for idx, vec in zip(missing_idx, vecs):
                out[idx] = vec
                self._cache[keys[idx]] = vec
            self._mark_dirty(len(missing_texts))
        return [v for v in out if v is not None]  # mypy: list is now fully populated


# ---------------------------------------------------------------------------
# .env loader — stdlib only, explicit (call from CLI)
# ---------------------------------------------------------------------------

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_dotenv(path: str = ".env") -> int:
    """Minimal .env parser. Only sets keys not already in os.environ.

    Returns the number of keys loaded. Absence of the file is NOT an error
    — dotenv is optional infrastructure, not a contract (P11: explicit
    applies to the call site, not to the existence of the file).
    """
    if not os.path.isfile(path):
        return 0
    loaded = 0
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = _ENV_LINE_RE.match(line)
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key not in os.environ:
                os.environ[key] = val
                loaded += 1
    return loaded


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() not in ("", "0", "false", "no", "off")


def make_embedder_from_env() -> Embedder:
    """Build the embedder specified by BELLAMEM_EMBEDDER and friends.

    Default is HashEmbedder. Non-hash backends are wrapped in DiskCacheEmbedder
    unless BELLAMEM_EMBEDDER_CACHE=0.
    """
    kind = (os.environ.get("BELLAMEM_EMBEDDER", "hash") or "hash").lower()
    model = os.environ.get("BELLAMEM_EMBEDDER_MODEL") or None
    if kind == "hash":
        return HashEmbedder()
    if kind == "st":
        inner: Embedder = SentenceTransformerEmbedder(model=model or "all-MiniLM-L6-v2")
    elif kind == "openai":
        inner = OpenAIEmbedder(model=model or "text-embedding-3-small")
    else:
        raise ValueError(f"unknown BELLAMEM_EMBEDDER={kind!r} (hash|st|openai)")
    if not _truthy(os.environ.get("BELLAMEM_EMBEDDER_CACHE", "1")):
        return inner
    from ..paths import default_embed_cache_path
    cache_path = default_embed_cache_path()
    return DiskCacheEmbedder(inner, cache_path)


# ---------------------------------------------------------------------------
# Module-level default + accessors
# ---------------------------------------------------------------------------

default_embedder: Embedder = HashEmbedder()


def embed(text: str) -> list[float]:
    return default_embedder.embed(text)


def embed_batch(texts: list[str]) -> list[list[float]]:
    return default_embedder.embed_batch(texts)


def set_embedder(e: Embedder) -> None:
    global default_embedder
    default_embedder = e


def current_embedder() -> Embedder:
    return default_embedder


def flush_embedder() -> None:
    """Force any pending cache writes to disk. Safe no-op for backends
    that don't buffer (HashEmbedder, raw ST/OpenAI without DiskCache)."""
    flush = getattr(default_embedder, "flush", None)
    if callable(flush):
        flush()


def prune_embedder(texts) -> int:
    """Bound the disk cache to the set of texts passed in.

    Safe no-op for backends without a cache. Returns entries dropped.
    """
    prune = getattr(default_embedder, "prune_to", None)
    if callable(prune):
        return int(prune(texts))
    return 0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class EmbedderMismatch(RuntimeError):
    """Raised when the active embedder does not match the snapshot's."""
    pass
