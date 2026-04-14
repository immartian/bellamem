"""Interactive HTML renderer — D3+Dagre or Cytoscape.js, CDN-loaded.

Takes a filtered VizPayload and emits a self-contained HTML file
with the graph data inlined as JSON. Two template variants:

- `d3` — dagre-d3 (D3 v5 + dagre-d3), matches the shape of the
  herenews inquiry graph. Left-to-right layered layout.
- `cytoscape` — Cytoscape.js + cytoscape-dagre. Same visual
  vocabulary with built-in interaction primitives.

Both load libraries from CDN, so rendering requires no build step
but does need internet on first open. Output is a single HTML file
you double-click to view.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from bellamem.proto.graph import Graph
from bellamem.proto.viz import Filters, VizPayload, build_payload, payload_to_dict


Renderer = Literal["d3", "cytoscape", "3d"]

_TEMPLATE_FILES: dict[Renderer, str] = {
    "d3": "viz_template_d3.html",
    "cytoscape": "viz_template_cytoscape.html",
    "3d": "viz_template_3d.html",
}

# Payload placeholder injected by the template at build time. The
# delimiters are comment-wrapped so the template is still valid JS
# on disk (PAYLOAD = null) and a straightforward string replacement
# swaps in the real data.
_PAYLOAD_TOKEN = "/*__PAYLOAD__*/ null /*__END_PAYLOAD__*/"


def render(
    graph: Graph,
    out_path: Path,
    *,
    renderer: Renderer = "d3",
    filters: Optional[Filters] = None,
) -> VizPayload:
    """Build the payload, inline it into the chosen template, write the HTML.

    For `renderer == "3d"`, the payload additionally carries per-concept
    `pos_3d` coordinates from UMAP × mass, and per-turn-hub `pos_3d`
    placed at the centroid of its spokes. This requires `umap-learn`
    (declared as the `[viz3d]` extra) and an Embedder cache.
    """
    if renderer not in _TEMPLATE_FILES:
        raise ValueError(
            f"unknown renderer {renderer!r} (expected d3 | cytoscape | 3d)"
        )

    if renderer == "3d":
        from bellamem.proto.viz_3d import build_3d_payload
        result = build_3d_payload(graph, filters=filters)
        payload_dict = result.payload
        # Rehydrate a VizPayload for the return signature (caller logs
        # concept/edge counts from it).
        payload = build_payload(graph, filters)
    else:
        payload = build_payload(graph, filters)
        payload_dict = payload_to_dict(payload)
    payload_json = json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":"))

    template_path = Path(__file__).parent / _TEMPLATE_FILES[renderer]
    html = template_path.read_text(encoding="utf-8")
    if _PAYLOAD_TOKEN not in html:
        raise RuntimeError(
            f"template {template_path} missing payload placeholder — "
            f"cannot inject data"
        )
    html = html.replace(_PAYLOAD_TOKEN, payload_json)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return payload
