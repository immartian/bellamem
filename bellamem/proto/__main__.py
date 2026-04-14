"""Subcommand dispatcher for `python -m bellamem.proto`.

    python -m bellamem.proto ingest [SNAPSHOT]      # run ingest on a session
    python -m bellamem.proto resume [--graph PATH]  # print typed summary
    python -m bellamem.proto viz [--out PATH] ...   # render 2D/3D visualization

`python -m bellamem.proto.ingest` and `python -m bellamem.proto.resume`
are also directly runnable for their individual commands.
"""
from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m bellamem.proto <ingest|resume|viz> [args...]",
              file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    rest = sys.argv[2:]
    # rewrite argv so downstream main() sees the right shape
    sys.argv = [sys.argv[0]] + rest
    if cmd == "ingest":
        from bellamem.proto.ingest import main as ingest_main
        return ingest_main()
    if cmd == "resume":
        from bellamem.proto.resume import main as resume_main
        return resume_main()
    if cmd == "viz":
        return _viz_main(rest)
    if cmd == "rebuild-mass":
        return _rebuild_mass_main(rest)
    if cmd == "audit":
        return _audit_main(rest)
    print(
        f"unknown subcommand: {cmd!r} "
        f"(expected ingest | resume | viz | rebuild-mass | audit)",
        file=sys.stderr,
    )
    return 2


def _audit_main(argv: list[str]) -> int:
    """Print entropy + health signals over .graph/v02.json."""
    import argparse
    from pathlib import Path
    from bellamem.proto.audit import audit, format_audit
    from bellamem.proto.store import DEFAULT_GRAPH_PATH, load_graph

    parser = argparse.ArgumentParser(prog="bellamem.proto audit")
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH_PATH)
    args = parser.parse_args(argv)

    graph = load_graph(args.graph)
    if not graph.concepts:
        print(f"graph at {args.graph} is empty", file=sys.stderr)
        return 1
    report = audit(graph)
    print(format_audit(report))
    return 1 if report.any_hard() else 0


def _rebuild_mass_main(argv: list[str]) -> int:
    """One-shot: replay source_refs through cite() to repair graphs
    built by a pipeline that bypassed R1 (experiments/proto_tree.py).
    Writes the repaired graph back atomically."""
    import argparse
    from pathlib import Path
    from bellamem.proto.store import DEFAULT_GRAPH_PATH, load_graph, save_graph

    parser = argparse.ArgumentParser(prog="bellamem.proto rebuild-mass")
    parser.add_argument(
        "--graph", type=Path, default=DEFAULT_GRAPH_PATH,
        help="path to .graph/v02.json (default: ./.graph/v02.json)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="compute the repair stats but don't save the graph back",
    )
    args = parser.parse_args(argv)

    graph = load_graph(args.graph)
    if not graph.concepts:
        print(f"graph at {args.graph} is empty", file=sys.stderr)
        return 1

    before_floor = sum(1 for c in graph.concepts.values() if c.mass <= 0.501)
    before_voices = sum(1 for c in graph.concepts.values() if len(c.voices) == 0)

    repaired = graph.rebuild_mass_from_source_refs()

    after_floor = sum(1 for c in graph.concepts.values() if c.mass <= 0.501)
    after_voices = sum(1 for c in graph.concepts.values() if len(c.voices) == 0)
    top = sorted(graph.concepts.values(), key=lambda c: -c.mass)[:5]

    print(f"rebuild-mass on {args.graph}:")
    print(f"  concepts moved off floor: {repaired}")
    print(f"  at floor (m≤0.501): {before_floor} → {after_floor}")
    print(f"  zero-voice concepts: {before_voices} → {after_voices}")
    print(f"  top mass after repair:")
    for c in top:
        print(f"    m={c.mass:.3f}  v={len(c.voices)}  {c.topic[:50]}")

    if args.dry_run:
        print("  [dry-run] graph NOT saved")
        return 0

    save_graph(graph, args.graph)
    print(f"  saved → {args.graph}")
    return 0


def _viz_main(argv: list[str]) -> int:
    """CLI entry for `bellamem.proto viz`.

    Format dispatch is by --out extension (.svg, .png, .dot for 2D;
    .html for 3D — Phase A of the 3D port isn't implemented yet, so
    .html currently errors with a pointer to the spec). Filters apply
    to all formats.
    """
    import argparse
    from pathlib import Path

    from bellamem.proto.store import DEFAULT_GRAPH_PATH, load_graph
    from bellamem.proto.viz import Filters

    parser = argparse.ArgumentParser(
        prog="bellamem.proto viz",
        description="Render the v0.2 graph as SVG (2D) or HTML (3D)."
    )
    parser.add_argument(
        "--graph", type=Path, default=DEFAULT_GRAPH_PATH,
        help="path to .graph/v02.json (default: ./.graph/v02.json)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="output file. Extension drives format: "
             ".html (interactive, default), .svg/.png/.dot (static graphviz). "
             "Default: .graph/v02.html",
    )
    parser.add_argument(
        "--renderer", choices=["d3", "cytoscape", "3d"], default="d3",
        help="HTML renderer for .html output: d3 (2D force-directed), "
             "cytoscape (2D fcose), 3d (Three.js + UMAP × mass). Default: d3.",
    )
    parser.add_argument(
        "--min-mass", type=float, default=0.55,
        help="drop concepts below this mass (default 0.55)",
    )
    parser.add_argument(
        "--class", dest="classes", action="append", default=None,
        choices=["invariant", "decision", "observation", "ephemeral"],
        help="restrict to one or more classes (repeatable)",
    )
    parser.add_argument(
        "--state", dest="states", action="append", default=None,
        choices=["open", "consumed", "retracted", "stale"],
        help="restrict ephemeral states (repeatable)",
    )
    parser.add_argument(
        "--session", type=str, default=None,
        help="restrict to concepts cited in this session id",
    )
    parser.add_argument(
        "--max-concepts", type=int, default=None,
        help="hard cap on rendered concepts (mass-sorted)",
    )
    parser.add_argument(
        "--min-turn-degree", type=int, default=3,
        help="turn-hub minimum degree — a turn becomes a hub iff it touches ≥N "
             "concepts via any hyperedge type (default 3)",
    )
    parser.add_argument(
        "--no-hubs", action="store_true",
        help="disable turn-hub rendering (concept-only view)",
    )
    parser.add_argument(
        "--engine", type=str, default="dot",
        choices=["dot", "neato", "fdp", "sfdp", "circo", "twopi"],
        help="graphviz layout engine (2D only, default dot)",
    )
    args = parser.parse_args(argv)

    out_path = args.out or (args.graph.parent / "v02.html")
    suffix = out_path.suffix.lower()
    if suffix not in {".html", ".svg", ".png", ".dot"}:
        print(
            f"unknown output extension {suffix!r} — expected .html, .svg, .png, .dot",
            file=sys.stderr,
        )
        return 2

    graph = load_graph(args.graph)
    if not graph.concepts:
        print(f"graph at {args.graph} is empty — nothing to render", file=sys.stderr)
        return 1

    filters = Filters(
        min_mass=args.min_mass,
        classes=frozenset(args.classes) if args.classes else None,
        states=frozenset(args.states) if args.states else None,
        session=args.session,
        max_concepts=args.max_concepts,
        include_turn_hubs=not args.no_hubs,
        min_turn_degree=args.min_turn_degree,
    )

    if suffix == ".html":
        from bellamem.proto.viz_html import render as render_html
        payload = render_html(
            graph, out_path, renderer=args.renderer, filters=filters
        )
        renderer_note = f" [{args.renderer}]"
    else:
        from bellamem.proto.viz_2d import render as render_2d
        payload = render_2d(graph, out_path, filters=filters, engine=args.engine)
        renderer_note = f" [{args.engine}]"

    print(
        f"wrote {out_path}{renderer_note} — "
        f"{len(payload.concepts)}/{payload.n_total_concepts} concepts, "
        f"{len(payload.edges)}/{payload.n_total_edges} edges "
        f"(min_mass={filters.min_mass})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
