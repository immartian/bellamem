"""bellamem CLI.

    bellamem ingest-cc [--cwd PATH]   ingest Claude Code transcripts
    bellamem expand "focus" [-t N]    generic mass-weighted context pack
    bellamem before-edit "focus"      5-layer pack for a proposed edit
    bellamem audit                    bandaid piles / ratified / disputes
    bellamem bench                    empirical comparison vs flat/RAG
    bellamem entities [name]          R6 entity index inspection
    bellamem show / stats / reset     inspect / reset snapshot
    bellamem embedder                 show active embedder
"""

from __future__ import annotations

import argparse
import os
import sys

from .core import Bella, save, load
from .core.audit import audit, render_report
from .core.embed import (
    EmbedderMismatch,
    current_embedder,
    load_dotenv,
    make_embedder_from_env,
    set_embedder,
)
from .core.expand import expand, expand_before_edit


DEFAULT_SNAPSHOT = os.path.expanduser("~/.bellamem/default.json")


def _resolve_snapshot(arg: str | None) -> str:
    return arg or os.environ.get("BELLAMEM_SNAPSHOT") or DEFAULT_SNAPSHOT


def _setup_embedder() -> None:
    """Build and install the embedder specified by env vars.

    Commands that need to compute or compare embeddings (ingest, expand,
    any load() call that checks signatures) call this first. Commands
    that only touch the filesystem (reset) skip it.
    """
    try:
        set_embedder(make_embedder_from_env())
    except Exception as e:
        print(f"embedder setup failed: {e}", file=sys.stderr)
        raise SystemExit(2)


def cmd_ingest_cc(args: argparse.Namespace) -> int:
    from .adapters.claude_code import ingest_project
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    before = sum(len(g.beliefs) for g in bella.fields.values())
    results = ingest_project(
        bella, cwd=args.cwd,
        tail=args.tail, no_llm=args.no_llm, latest_only=args.latest_only,
    )
    save(bella, snap)
    after = sum(len(g.beliefs) for g in bella.fields.values())
    if not results:
        print(f"no Claude Code transcripts found for cwd={args.cwd or os.getcwd()}")
        return 1
    for r in results:
        line = f"  {r['session']}: +{r['turns']} turns → +{r['claims']} claims"
        if r.get("affirmed") or r.get("corrected"):
            line += f"  (affirmed:{r.get('affirmed', 0)} corrected:{r.get('corrected', 0)})"
        if r.get("causes") or r.get("self_obs"):
            line += f"  (causes:{r.get('causes', 0)} self_obs:{r.get('self_obs', 0)})"
        print(line)
    print(f"beliefs: {before} → {after}  (+{after - before})")
    print(f"snapshot: {snap}")
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — run `bellamem ingest-cc` first", file=sys.stderr)
        return 1
    pack = expand(bella, args.focus, budget_tokens=args.budget)
    print(pack.text())
    print()
    print(f"— {len(pack.lines)} lines, ~{pack.used_tokens()}t / {args.budget}t budget —")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory")
        return 0
    print(bella.render(max_mass_only=args.min_mass))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    s = bella.stats()
    print(f"snapshot: {snap}")
    print(f"fields:   {s['fields']}")
    print(f"beliefs:  {s['beliefs']}")
    print(f"roots:    {s['roots']}")
    print()
    print("top-mass beliefs:")
    for t in s["top_mass"]:
        print(f"  m={t['mass']:.2f} v={t['voices']}  {t['desc']}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    snap = _resolve_snapshot(args.snapshot)
    if os.path.exists(snap):
        os.unlink(snap)
        print(f"removed {snap}")
    else:
        print(f"no snapshot at {snap}")
    return 0


def cmd_before_edit(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — run `bellamem ingest-cc` first", file=sys.stderr)
        return 1
    pack = expand_before_edit(
        bella, args.focus,
        budget_tokens=args.budget,
        focus_entity=args.entity,
    )
    print(pack.text())
    print()
    print(f"— {len(pack.lines)} lines, ~{pack.used_tokens()}t / {args.budget}t budget "
          f"(before-edit mode: no recency) —")
    return 0


def cmd_entities(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    idx = bella.entity_index()
    if not idx:
        print("no entities indexed yet")
        return 0
    if args.name:
        refs = idx.get(args.name, [])
        print(f"{args.name}: {len(refs)} beliefs")
        for fname, bid in refs:
            g = bella.fields.get(fname)
            if g and bid in g.beliefs:
                b = g.beliefs[bid]
                print(f"  m={b.mass:.2f} v={b.n_voices} [{fname[:22]}] {b.desc[:100]}")
        return 0
    # Summary listing, sorted by mention count
    rows = sorted(idx.items(), key=lambda kv: len(kv[1]), reverse=True)
    for entity, refs in rows[:args.limit]:
        print(f"  {len(refs):3d}  {entity}")
    print(f"\n{len(idx)} entities, showing top {min(len(rows), args.limit)}")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — run `bellamem ingest-cc` first", file=sys.stderr)
        return 1

    # Resolve transcript path
    from .adapters.claude_code import list_sessions
    transcripts = list_sessions(args.cwd)
    if not transcripts:
        print("no Claude Code transcripts found for this project", file=sys.stderr)
        return 1
    # Use the most recent transcript for flat_tail/compact/rag
    transcript_path = transcripts[-1]

    # Build OpenAI client if compact contender is requested
    openai_client = None
    contenders = args.contenders.split(",") if args.contenders else None
    needs_llm = contenders is None or "compact" in contenders
    if needs_llm:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            print("compact contender requires openai package; "
                  "install with `pip install -e '.[openai]'` or pass "
                  "`--contenders flat_tail,rag_topk,expand,before_edit`",
                  file=sys.stderr)
            return 2
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("compact contender requires OPENAI_API_KEY in .env or env",
                  file=sys.stderr)
            return 2
        openai_client = OpenAI(api_key=api_key)

    from .bench import run_bench, render_report
    report = run_bench(
        bella,
        transcript_path=transcript_path,
        budget_tokens=args.budget,
        contenders=contenders,
        openai_client=openai_client,
        model=args.model,
        use_llm_judge=args.llm_judge,
    )
    print(render_report(report))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — run `bellamem ingest-cc` first", file=sys.stderr)
        return 1
    report = audit(bella, top_n=args.top)
    print(render_report(report, max_per_section=args.max_per_section))
    return 0 if report.is_clean() else (0 if args.no_exit_code else 4)


def cmd_embedder(args: argparse.Namespace) -> int:
    _setup_embedder()
    e = current_embedder()
    print(f"active embedder: {e.name}")
    print(f"dim:             {e.dim}")
    cache_path = os.environ.get(
        "BELLAMEM_EMBEDDER_CACHE_PATH",
        os.path.expanduser("~/.bellamem/embed_cache.json"),
    )
    kind = os.environ.get("BELLAMEM_EMBEDDER", "hash")
    print(f"kind (env):      {kind}")
    if kind != "hash":
        print(f"cache:           {cache_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bellamem",
                                 description="local accumulating memory for LLM agents")
    p.add_argument("--snapshot", help="snapshot path (default: ~/.bellamem/default.json)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ingest-cc", help="ingest Claude Code .jsonl transcripts")
    sp.add_argument("--cwd", help="project cwd (defaults to current working dir)")
    sp.add_argument("--tail", type=int, default=None,
                    help="limit each session to its last N turns (fast partial ingest)")
    sp.add_argument("--no-llm", action="store_true",
                    help="disable LLM-backed EW regardless of BELLAMEM_EW")
    sp.add_argument("--latest-only", action="store_true",
                    help="only ingest the most recent session (demos / quick tests)")
    sp.set_defaults(func=cmd_ingest_cc)

    sp = sub.add_parser("expand", help="print a mass-weighted context pack")
    sp.add_argument("focus", help="focus description (what the agent is about to do)")
    sp.add_argument("-t", "--budget", type=int, default=1200,
                    help="token budget (default 1200)")
    sp.set_defaults(func=cmd_expand)

    sp = sub.add_parser("show", help="render the whole forest")
    sp.add_argument("--min-mass", type=float, default=0.0,
                    help="hide beliefs with mass below this")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("stats", help="summary counts")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("reset", help="delete the snapshot")
    sp.set_defaults(func=cmd_reset)

    sp = sub.add_parser("embedder", help="show the active embedder config")
    sp.set_defaults(func=cmd_embedder)

    sp = sub.add_parser("before-edit",
                        help="bandaid-blocker pack for a focus entity")
    sp.add_argument("focus", help="description of the edit you're about to make")
    sp.add_argument("-e", "--entity",
                    help="focus entity (file/function/lib) for R6 bridging")
    sp.add_argument("-t", "--budget", type=int, default=1500,
                    help="token budget (default 1500)")
    sp.set_defaults(func=cmd_before_edit)

    sp = sub.add_parser("entities", help="list / inspect indexed entities")
    sp.add_argument("name", nargs="?", help="show beliefs mentioning this entity")
    sp.add_argument("--limit", type=int, default=30,
                    help="max entries when listing (default 30)")
    sp.set_defaults(func=cmd_entities)

    sp = sub.add_parser("bench", help="empirically compare pack strategies")
    sp.add_argument("--cwd", help="project cwd (defaults to current working dir)")
    sp.add_argument("-t", "--budget", type=int, default=1200,
                    help="token budget per pack (default 1200)")
    sp.add_argument("--contenders",
                    help="comma-separated subset: flat_tail,compact,rag_topk,expand,before_edit")
    sp.add_argument("--model", default="gpt-4o-mini",
                    help="model for compact contender + llm judge")
    sp.add_argument("--llm-judge", action="store_true",
                    help="enable the LLM judge metric (costs ~$0.005 per run)")
    sp.set_defaults(func=cmd_bench)

    sp = sub.add_parser("audit", help="bandaid piles + top ratified + disputes")
    sp.add_argument("--top", type=int, default=10,
                    help="rows per section (default 10)")
    sp.add_argument("--max-per-section", type=int, default=10,
                    help="max rows per section (default 10)")
    sp.add_argument("--no-exit-code", action="store_true",
                    help="always exit 0 even if bandaid piles are found")
    sp.set_defaults(func=cmd_audit)

    return p


def main(argv: list[str] | None = None) -> int:
    # Load .env from cwd if present. Explicit call, not on import.
    load_dotenv(".env")
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
