"""Atomic load/save for proto graph.

The graph serializes to a single JSON file at .graph/v02.json by
default. Atomic writes via temp-then-rename so partial writes can't
corrupt state.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from bellamem.proto.graph import Graph


DEFAULT_GRAPH_PATH = Path.cwd() / ".graph" / "v02.json"


def save_graph(graph: Graph, path: Optional[Path] = None) -> Path:
    """Write the graph atomically. Returns the path written to."""
    target = Path(path) if path is not None else DEFAULT_GRAPH_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    data = graph.to_json()
    # Atomic: write to sibling temp file, then rename
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=".v02-", suffix=".json.tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path_str, target)
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except FileNotFoundError:
            pass
        raise
    return target


def load_graph(path: Optional[Path] = None) -> Graph:
    """Load the graph from disk. Returns an empty Graph if the file
    doesn't exist — first-run behavior."""
    target = Path(path) if path is not None else DEFAULT_GRAPH_PATH
    if not target.exists():
        return Graph()
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    return Graph.from_json(data)
