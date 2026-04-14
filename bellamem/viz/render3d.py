"""3D viz renderer — builds the payload and writes a self-contained HTML.

Design (mirrors `bellamem render` for the 2D graphviz path):

  1. build_payload(bella) — pure data transform. Walks the forest,
     collects beliefs + embeddings, reduces embeddings to 2D via UMAP,
     normalizes coordinates, and returns a JSON-serialisable dict.
     This is the data contract the HTML template consumes.

  2. render_html(bella, out_path) — loads the HTML template from
     package data, inlines the JSON payload, writes the result.
     Self-contained output — Three.js is pulled from CDN at open time,
     no build step, no server.

The Python layer precomputes UMAP so the snapshot view is honest
("forest is truth" invariant — layout is part of the observation).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from ..core.gene import REL_CAUSE, REL_COUNTER, REL_SUPPORT

if TYPE_CHECKING:
    from ..core.bella import Bella


# Distinct field colors. Fifteen is enough for a long time; anything
# beyond that is extremely rare and falls through to a deterministic
# hash-derived color so fields still get stable colors across runs.
_FIELD_PALETTE = [
    "#4c9aff", "#ff6b6b", "#51cf66", "#ffd43b", "#cc5de8",
    "#ff922b", "#22b8cf", "#845ef7", "#f06595", "#94d82d",
    "#339af0", "#fa5252", "#20c997", "#fab005", "#be4bdb",
]


def _color_for(field_name: str, idx: int) -> str:
    if idx < len(_FIELD_PALETTE):
        return _FIELD_PALETTE[idx]
    h = abs(hash(field_name)) & 0xFFFFFF
    return f"#{h:06x}"


def _compute_umap(embeddings: list[list[float]]) -> list[list[float]]:
    """Reduce embeddings to 2D. Deterministic via random_state.

    Tiny forests (< 10 beliefs) use a trivial first-two-dims fallback
    because UMAP is unstable at that size. The fallback is not
    meaningful as a semantic projection — it just keeps render_html
    working on empty/near-empty graphs.
    """
    n = len(embeddings)
    if n == 0:
        return []
    if n < 10:
        return [[float(v[0]) if len(v) > 0 else 0.0,
                 float(v[1]) if len(v) > 1 else 0.0]
                for v in embeddings]
    try:
        import numpy as np
        import umap  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "3D viz requires the 'viz3d' extra. Install with one of:\n"
            "  pipx inject bellamem 'umap-learn>=0.5' 'numpy>=1.21'\n"
            "  pip install 'bellamem[viz3d]'"
        ) from e
    arr = np.asarray(embeddings, dtype=np.float32)
    reducer = umap.UMAP(
        n_components=2,
        random_state=42,
        n_neighbors=min(15, n - 1),
        min_dist=0.1,
    )
    coords = reducer.fit_transform(arr)
    return coords.tolist()


def _normalize_coords(coords: list[list[float]],
                      extent: float = 30.0) -> list[list[float]]:
    """Center coords at origin and scale to fit in a square of side `extent`."""
    if not coords:
        return coords
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
    k = extent / span
    return [[(p[0] - cx) * k, (p[1] - cy) * k] for p in coords]


def _edge_type_for_rel(rel: str) -> str:
    """Map a bellamem relation constant to a viz edge type string."""
    if rel == REL_CAUSE:
        return "cause"
    if rel == REL_COUNTER:
        return "counter"
    return "support"  # REL_SUPPORT and any unknown relation


# Compressed wall-clock: idle gaps larger than this (seconds) are
# collapsed to _GAP_REPLACEMENT virtual seconds each. Keeps bursts
# fast, skips days of dormancy between sessions. Gource-style.
_GAP_THRESHOLD = 300.0   # 5 minutes
_GAP_REPLACEMENT = 1.0   # virtual seconds for each compressed gap


def _build_timeline(bella: "Bella", live_ids: set[str]) -> dict:
    """Extract a compressed wall-clock timeline from the forest.

    For each live belief, emits:
      - one "birth" event at belief.event_time
      - one "jump" event per entry in belief.jumps (Jaynes updates)

    Global events are then sorted by real time, and long idle gaps
    are collapsed so the viewer doesn't sit through days of dormancy
    between active sessions while the forest was not being ingested.

    Output shape (compact keys to keep payload small):
      {
        "duration": <virtual seconds to end of last event>,
        "events": [
          {"t": 0.0, "type": "birth", "id": "..."},
          {"t": 0.2, "type": "jump", "id": "...", "d": 0.79, "v": "user"},
          ...
        ]
      }

    JUMPS_MAX caveat: if a very active belief has had its oldest
    jumps dropped, the JS side reconstructs the initial log_odds as
    (current_log_odds - sum(kept_deltas)), so the belief appears at
    birth with the correct starting height instead of at 0.5. This
    means early history looks "jumpy" (one big initial step then the
    retained jumps) — accurate within what the data supports.
    """
    raw: list[dict] = []
    for fname, g in bella.fields.items():
        for bid, b in g.beliefs.items():
            if bid not in live_ids:
                continue
            raw.append({
                "real_t": float(b.event_time),
                "type": "birth",
                "id": bid,
            })
            for (ts, delta, voice) in (b.jumps or []):
                raw.append({
                    "real_t": float(ts),
                    "type": "jump",
                    "id": bid,
                    "d": float(delta),
                    "v": voice or "",
                })

    raw.sort(key=lambda e: e["real_t"])

    if not raw:
        return {"duration": 0.0, "events": []}

    events: list[dict] = []
    virtual_t = 0.0
    prev_real = raw[0]["real_t"]
    for ev in raw:
        gap = ev["real_t"] - prev_real
        if gap > _GAP_THRESHOLD:
            virtual_t += _GAP_REPLACEMENT
        else:
            virtual_t += max(gap, 0.0)
        out_ev: dict = {
            "t": round(virtual_t, 3),
            "type": ev["type"],
            "id": ev["id"],
        }
        if ev["type"] == "jump":
            out_ev["d"] = round(ev["d"], 4)
            if ev.get("v"):
                out_ev["v"] = ev["v"]
        events.append(out_ev)
        prev_real = ev["real_t"]

    return {
        "duration": events[-1]["t"] if events else 0.0,
        "events": events,
    }


def build_payload(bella: "Bella") -> dict:
    """Build the JSON payload the Three.js viz consumes.

    Shape:
      {
        "meta": { "beliefs": N, "fields": K },
        "fields": [ { name, color, belief_count } ],
        "beliefs": [ {
            id, field, desc, mass, voices, parent,
            pos: [x, y],   # UMAP-reduced, normalized
            z: float,      # == mass, kept as its own key so the viz
                           # can swap it for other height metrics later
        } ],
        "edges": [ { type: "support"|"cause"|"counter", from, to } ]
      }
    """
    rows: list[tuple[str, str, object]] = []  # (field, id, belief)
    embeddings: list[list[float]] = []
    fields_meta: list[dict] = []

    for idx, (fname, g) in enumerate(sorted(bella.fields.items())):
        fields_meta.append({
            "name": fname,
            "color": _color_for(fname, idx),
            "belief_count": len(g.beliefs),
        })
        for bid, b in g.beliefs.items():
            rows.append((fname, bid, b))
            # Fall back to a zero vector if an embedding is missing —
            # shouldn't happen with the normal ingest path, but the
            # viz must not crash on a legacy snapshot.
            embeddings.append(b.embedding or [])

    # UMAP needs vectors of uniform length; pad any stragglers to the
    # modal dimensionality.
    if embeddings:
        dim = max(len(v) for v in embeddings) or 2
        embeddings = [
            list(v) + [0.0] * (dim - len(v)) if len(v) < dim else list(v)
            for v in embeddings
        ]

    coords = _normalize_coords(_compute_umap(embeddings))

    beliefs_payload: list[dict] = []
    live_ids: set[str] = set()
    for (fname, bid, b), pos in zip(rows, coords):
        live_ids.add(bid)
        n_voices = int(getattr(b, "n_voices", 0)) or len(getattr(b, "voices", []) or [])
        beliefs_payload.append({
            "id": bid,
            "field": fname,
            "desc": (b.desc or "")[:240],
            "mass": float(b.mass),
            "voices": max(n_voices, 1),
            "parent": b.parent,
            "pos": [float(pos[0]), float(pos[1])],
            "z": float(b.mass),
        })

    # Timeline: compressed wall-clock event stream. The replay UI
    # consumes this to animate the forest's formation.
    timeline = _build_timeline(bella, live_ids)
    # Map belief id → virtual birth time, so edges can hide themselves
    # before their child belief has been born during replay.
    birth_virtual: dict[str, float] = {
        ev["id"]: ev["t"] for ev in timeline["events"] if ev["type"] == "birth"
    }

    # Edges. Every non-root belief has exactly one (parent, rel) edge.
    # We emit it only if both endpoints are in live_ids — keeps the
    # payload consistent when a snapshot has dangling parents (legacy
    # scrubbed graphs occasionally do).
    edges_payload: list[dict] = []
    for fname, g in bella.fields.items():
        for bid, b in g.beliefs.items():
            if b.parent and b.parent in live_ids and bid in live_ids:
                # Edge appears the moment the child belief is born —
                # the child carries the (parent, rel) relationship.
                edges_payload.append({
                    "type": _edge_type_for_rel(b.rel),
                    "from": b.parent,
                    "to": bid,
                    "birth_t": float(birth_virtual.get(bid, 0.0)),
                })

    return {
        "meta": {
            "beliefs": len(beliefs_payload),
            "fields": len(fields_meta),
        },
        "fields": fields_meta,
        "beliefs": beliefs_payload,
        "edges": edges_payload,
        "timeline": timeline,
    }


def _load_template() -> str:
    """Read the HTML template from package data."""
    from importlib.resources import files
    return (files("bellamem.viz") / "template.html").read_text(encoding="utf-8")


def _get_version() -> str:
    try:
        from importlib.metadata import version
        return version("bellamem")
    except Exception:
        return "unknown"


def render_html(bella: "Bella", out_path: str) -> int:
    """Write a self-contained HTML file containing the 3D viz.

    Returns the number of beliefs rendered. The output is a single file:
    Three.js is loaded from CDN at open time, no local assets needed.
    """
    payload = build_payload(bella)
    data_json = json.dumps(payload, separators=(",", ":"))

    template = _load_template()
    html = (template
            .replace("{{DATA_JSON}}", data_json)
            .replace("{{BELIEFS}}", str(payload["meta"]["beliefs"]))
            .replace("{{FIELDS}}", str(payload["meta"]["fields"]))
            .replace("{{BELLAMEM_VERSION}}", _get_version()))

    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    return int(payload["meta"]["beliefs"])
