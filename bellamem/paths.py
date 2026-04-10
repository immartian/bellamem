"""Project-local path resolution for bellamem runtime state.

Runtime state (belief snapshot + embedder cache + LLM EW cache) lives in
`<project_root>/.graph/` by default, where `project_root` is the git repo
root if we're inside one, otherwise the current working directory.

Each path respects its corresponding environment variable override:

    BELLAMEM_SNAPSHOT             → the belief graph snapshot
    BELLAMEM_EMBEDDER_CACHE_PATH  → the on-disk embedding cache
    BELLAMEM_EW_LLM_CACHE_PATH    → the LLM-backed EW cache

Legacy state: prior versions stored these under `~/.bellamem/`. v0.0.3 does
NOT silently read from `~/.bellamem/` — that would re-introduce the
cross-project contamination the per-project graph is designed to fix. If
legacy files are detected, we emit a one-time warning per file pointing at
`bellamem migrate`, and return the project-local path regardless. Users
must explicitly migrate (or set `BELLAMEM_SNAPSHOT` themselves) to inherit
their old graph into a new project.

This module is a leaf utility: it imports only from stdlib and is safe to
import from both core/ and adapters/.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


_GRAPH_DIRNAME = ".graph"

LEGACY_DIR = Path("~/.bellamem").expanduser()
LEGACY_SNAPSHOT = LEGACY_DIR / "default.json"
LEGACY_EMBED_CACHE = LEGACY_DIR / "embed_cache.json"
LEGACY_LLM_EW_CACHE = LEGACY_DIR / "llm_ew_cache.json"

_warned_legacy: set[str] = set()


def project_root() -> Path:
    """Return the git repo root walking up from cwd, or cwd if not a repo."""
    start = Path.cwd().resolve()
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    return start


def graph_dir() -> Path:
    """`<project_root>/.graph/` — where new-style runtime state lives."""
    return project_root() / _GRAPH_DIRNAME


def _resolve(env_var: str, basename: str, legacy: Path) -> str:
    """Resolve a runtime-state file path.

    Resolution order:
      1. $env_var if set (explicit user override).
      2. <project_root>/.graph/<basename> — always, even if it doesn't
         exist yet (ingest will create it). Legacy state is NOT used as
         a read fallback: silently reading another project's graph is
         exactly the cross-project contamination we're trying to fix.

    If a legacy file exists alongside a fresh project path, we emit a
    one-time-per-basename warning pointing at `bellamem migrate`, so the
    user can inherit their old graph deliberately rather than by accident.
    """
    override = os.environ.get(env_var)
    if override:
        return os.path.expanduser(override)

    project_path = graph_dir() / basename

    if (
        not project_path.exists()
        and legacy.exists()
        and basename not in _warned_legacy
    ):
        _warned_legacy.add(basename)
        print(
            f"bellamem: legacy state found at {legacy} but not loaded. "
            f"Run `bellamem migrate` in this project to copy it into "
            f"{project_path}.",
            file=sys.stderr,
        )

    return str(project_path)


def default_snapshot_path() -> str:
    return _resolve("BELLAMEM_SNAPSHOT", "default.json", LEGACY_SNAPSHOT)


def default_embed_cache_path() -> str:
    return _resolve(
        "BELLAMEM_EMBEDDER_CACHE_PATH", "embed_cache.json", LEGACY_EMBED_CACHE
    )


def default_llm_ew_cache_path() -> str:
    return _resolve(
        "BELLAMEM_EW_LLM_CACHE_PATH", "llm_ew_cache.json", LEGACY_LLM_EW_CACHE
    )
