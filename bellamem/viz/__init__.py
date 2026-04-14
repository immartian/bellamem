"""bellamem.viz — 3D graph visualizer.

Optional component. Writes a self-contained HTML file that renders the
belief forest with Three.js, using UMAP(embeddings) × mass as the layout.
Client of the snapshot — never mutates the forest. Install with:

    pip install 'bellamem[viz3d]'

Entry point is `bellamem render --out graph.html`.
"""

from .render3d import build_payload, render_html

__all__ = ["build_payload", "render_html"]
