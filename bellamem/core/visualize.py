"""Render a Bella forest to graphviz DOT source.

Domain-agnostic: walks beliefs, fields, and typed edges (→ / ⊥ / ⇒)
and emits a DOT string. Does not depend on the `graphviz` Python
package — that binding is only used by the CLI to rasterize the DOT
into SVG/PNG/PDF. If the binding isn't installed, the CLI writes the
DOT file directly and the user can render it however they like.

Visual encoding:
    node size      ~ mass (sigmoid(log_odds))
    node fill      ~ field (hashed to a palette)
    node opacity   ~ mass (low-mass beliefs fade)
    edge solid     → support
    edge red dash  ⊥ dispute
    edge blue      ⇒ cause

The render can scope to: one field, a focus subgraph (BFS from the top-N
beliefs closest to a focus string, expanded by depth), disputes-only
(only ⊥ edges and their incident nodes), a minimum mass threshold, or
any combination. Default is the whole forest — which is usually too big
to read past a few hundred beliefs, hence the filters.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Optional

from .gene import Belief, Gene, REL_COUNTER, REL_CAUSE, REL_SUPPORT


# Palette for field colors — chosen for readability on white, distinct hues.
_PALETTE = [
    "#4e79a7",  # blue
    "#f28e2b",  # orange
    "#e15759",  # red
    "#76b7b2",  # teal
    "#59a14f",  # green
    "#edc948",  # yellow
    "#b07aa1",  # purple
    "#ff9da7",  # pink
    "#9c755f",  # brown
    "#bab0ac",  # grey
]


def _field_color(name: str) -> str:
    """Stable per-field hue so the same field gets the same color across runs."""
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    return _PALETTE[h % len(_PALETTE)]


def _node_penwidth(mass: float) -> float:
    """Mass → border thickness in points. Heavier beliefs get thicker outlines."""
    return 1.0 + 4.0 * mass


def _node_fontsize(mass: float) -> int:
    """Label font size in points. Heavier beliefs get bigger labels."""
    return int(10 + 6 * mass)


def _trim(s: str, n: int = 36) -> str:
    """Trim a belief description to fit in a node label."""
    s = (s or "").strip().replace("\n", " ").replace("\r", "")
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _escape(s: str) -> str:
    """Escape a string for a DOT label attribute."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


@dataclass
class RenderOptions:
    """Knobs for `to_dot`. See module docstring for what each means."""

    fields: Optional[list[str]] = None     # restrict to these field names
    disputes_only: bool = False             # only show ⊥ edges + their endpoints
    min_mass: float = 0.0                   # drop beliefs below this mass
    max_nodes: int = 400                    # hard cap to keep rendering sane
    include_orphans: bool = True            # include beliefs with no parent AND no children
    focus_ids: Optional[set[str]] = None    # pre-filtered belief ids (caller decides)
    show_labels: bool = True                # emit text labels on nodes
    title: Optional[str] = None             # graph title rendered as a label
    dpi: int = 150                          # raster resolution for PNG output
    pad: float = 0.5                        # graph padding in inches


# ---------------------------------------------------------------------------
# Focus + filtering
# ---------------------------------------------------------------------------


def _gather(fields: dict[str, Gene], opts: RenderOptions) -> list[tuple[str, Belief]]:
    """Return [(field_name, belief)] in a stable order after applying filters."""
    selected_fields = opts.fields or list(fields.keys())
    out: list[tuple[str, Belief]] = []
    for fname in selected_fields:
        g = fields.get(fname)
        if g is None:
            continue
        for b in g.beliefs.values():
            if opts.focus_ids is not None and b.id not in opts.focus_ids:
                continue
            if b.mass < opts.min_mass:
                continue
            if opts.disputes_only and b.rel != REL_COUNTER:
                # Only keep dispute-marked beliefs; we'll pull their parents
                # back in below so the ⊥ arrow has something to point at.
                continue
            out.append((fname, b))
    # If disputes_only, also include each dispute's parent so the edge
    # can be drawn. Without this you'd get free-floating ⊥ nodes.
    if opts.disputes_only:
        keep = {(f, b.id) for f, b in out}
        parents: list[tuple[str, Belief]] = []
        for fname, b in out:
            if b.parent and fname in fields:
                g = fields[fname]
                p = g.beliefs.get(b.parent)
                if p is not None and (fname, p.id) not in keep:
                    parents.append((fname, p))
                    keep.add((fname, p.id))
        out.extend(parents)
    # Sort by mass descending so the max_nodes cap keeps the most
    # informative beliefs when the forest is too big.
    out.sort(key=lambda fb: fb[1].mass, reverse=True)
    if len(out) > opts.max_nodes:
        out = out[: opts.max_nodes]
    return out


def focus_ids(
    fields: dict[str, Gene],
    focus: str,
    *,
    top: int = 20,
    depth: int = 2,
    embedder=None,
) -> set[str]:
    """Pick the top-N beliefs most similar to `focus`, then BFS `depth` steps
    outward through parent and children edges.

    `embedder` must have an `.embed(str) -> list[float]` method. If None,
    the module-level default embedder is used (hash, by default). Callers
    that want openai/st embeddings should pass their configured embedder.
    """
    from .embed import embed as default_embed, cosine

    def _embed(text: str) -> list[float]:
        if embedder is None:
            return default_embed(text)
        return embedder.embed(text)

    fvec = _embed(focus)
    scored: list[tuple[float, Belief]] = []
    for g in fields.values():
        for b in g.beliefs.values():
            if b.embedding is None:
                continue
            scored.append((cosine(fvec, b.embedding), b))
    scored.sort(key=lambda sb: sb[0], reverse=True)
    seeds = {b.id for _, b in scored[:top]}

    # BFS expansion: walk parent and children relations depth times.
    all_beliefs: dict[str, Belief] = {}
    for g in fields.values():
        all_beliefs.update(g.beliefs)

    frontier = set(seeds)
    expanded = set(seeds)
    for _ in range(depth):
        next_frontier: set[str] = set()
        for bid in frontier:
            b = all_beliefs.get(bid)
            if b is None:
                continue
            if b.parent and b.parent in all_beliefs and b.parent not in expanded:
                next_frontier.add(b.parent)
            for c in b.children:
                if c in all_beliefs and c not in expanded:
                    next_frontier.add(c)
        expanded |= next_frontier
        frontier = next_frontier
    return expanded


# ---------------------------------------------------------------------------
# DOT emission
# ---------------------------------------------------------------------------


def _edge_attrs(rel: str) -> str:
    """DOT attributes for an edge keyed by the child's relation-to-parent."""
    if rel == REL_COUNTER:
        return '[color="#c0392b", style="dashed", penwidth=2.5, arrowhead="tee"]'
    if rel == REL_CAUSE:
        return '[color="#1f5f9f", penwidth=2.5, arrowhead="vee"]'
    return '[color="#555555", penwidth=1.5, arrowhead="normal"]'


def _node_attrs(field_name: str, b: Belief) -> str:
    """DOT attributes for one belief node.

    We use `shape="ellipse"` and let graphviz auto-size to the label, so
    short descriptions stay compact while long ones expand horizontally.
    Mass is encoded via border thickness + fill opacity + font size —
    not via the box dimensions, which are driven by the label.
    """
    mass = b.mass
    color = _field_color(field_name)
    penwidth = _node_penwidth(mass)
    fontsize = _node_fontsize(mass)
    label = _escape(_trim(b.desc))
    # Fade low-mass nodes via fillcolor alpha (hex RRGGBBAA).
    alpha = max(48, int(96 + 159 * mass))  # 96..255
    fill = f"{color}{alpha:02x}"
    # High-mass beliefs get a bold label; low-mass stay normal weight.
    fontweight = "bold" if mass >= 0.7 else "normal"
    return (
        f'[label="{label}", shape="ellipse", style="filled", '
        f'fillcolor="{fill}", color="{color}", penwidth={penwidth:.1f}, '
        f'fontsize={fontsize}, fontcolor="#222222", fontname="Helvetica-{fontweight}", '
        f'margin="0.15,0.08", '
        f'tooltip="m={mass:.2f} v={b.n_voices} field={field_name}"]'
    )


def to_dot(fields: dict[str, Gene], opts: Optional[RenderOptions] = None) -> str:
    """Produce a DOT source string for the given Bella forest.

    `fields` is `Bella.fields`. `opts` controls filtering and encoding.
    The output is safe to pass to `dot -Tsvg -o out.svg` or any DOT
    renderer.
    """
    opts = opts or RenderOptions()
    selected = _gather(fields, opts)
    selected_ids = {b.id for _, b in selected}

    lines: list[str] = []
    lines.append("digraph Bella {")
    lines.append("  graph [")
    lines.append('    bgcolor="white",')
    lines.append("    overlap=false,")
    lines.append("    splines=true,")
    lines.append(f'    dpi={opts.dpi},')
    lines.append(f'    pad="{opts.pad}",')
    lines.append('    nodesep="0.4",')
    lines.append('    ranksep="0.6",')
    lines.append('    fontname="Helvetica",')
    if opts.title:
        lines.append(f'    label="{_escape(opts.title)}",')
        lines.append('    labelloc="t",')
        lines.append("    fontsize=18,")
    lines.append("  ];")
    lines.append('  node [fontname="Helvetica"];')
    lines.append('  edge [fontname="Helvetica"];')
    lines.append("")

    # Nodes — grouped into invisible subgraphs per field so they cluster.
    by_field: dict[str, list[Belief]] = {}
    for fname, b in selected:
        by_field.setdefault(fname, []).append(b)

    for fname in by_field:
        cluster_id = "cluster_" + hashlib.md5(fname.encode()).hexdigest()[:6]
        lines.append(f"  subgraph {cluster_id} {{")
        lines.append(f'    label="{_escape(fname)}";')
        lines.append("    style=dotted;")
        lines.append(f'    color="{_field_color(fname)}";')
        lines.append('    fontcolor="#555555";')
        lines.append("    fontsize=10;")
        for b in by_field[fname]:
            lines.append(f'    "{b.id}" {_node_attrs(fname, b)};')
        lines.append("  }")
        lines.append("")

    # Edges — only when both endpoints are in the selected set.
    for fname, b in selected:
        if not b.parent:
            continue
        if b.parent not in selected_ids:
            continue
        attrs = _edge_attrs(b.rel)
        lines.append(f'  "{b.parent}" -> "{b.id}" {attrs};')

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience wrappers for the CLI
# ---------------------------------------------------------------------------


def count_selected(fields: dict[str, Gene], opts: Optional[RenderOptions] = None) -> int:
    """How many nodes would `to_dot` emit? Used for CLI status messages."""
    return len(_gather(fields, opts or RenderOptions()))
