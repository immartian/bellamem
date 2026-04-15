"""bellamem CLI.

    bellamem ingest-cc [--cwd PATH]   ingest Claude Code transcripts
    bellamem expand "focus" [-t N]    generic mass-weighted context pack
    bellamem before-edit "focus"      5-layer pack for a proposed edit
    bellamem audit                    entropy report: piles / duplicates / glut
    bellamem surprises                top Jaynes step surprises + sign flips
    bellamem scrub                    remove system-noise beliefs (migration)
    bellamem emerge                   R3: merge near-duplicates + rename fields
    bellamem replay [focus]           chronological belief timeline (by source line)
    bellamem bench                    empirical comparison vs flat/RAG
    bellamem entities [name]          R6 entity index inspection
    bellamem show / stats / reset     inspect / reset snapshot
    bellamem embedder                 show active embedder
    bellamem migrate                  ~/.bellamem/ → <project>/.graph/
    bellamem render                   graphviz diagram of the belief forest
    bellamem prune                    remove orphan leaves (structural forgetting)
    bellamem resume                   session start pack (working + long-term + signal)
    bellamem save                     session end: ingest + audit + surprises
    bellamem install-commands         install /bellamem slash command into Claude Code
"""

from __future__ import annotations

import argparse
import io
import os
import sys

from . import __version__
from .core import Bella, save, load
from .core.audit import audit, render_report
from .core.embed import (
    EmbedderMismatch,
    current_embedder,
    load_dotenv,
    make_embedder_from_env,
    set_embedder,
)
from .core.emerge import emerge
from .core.expand import expand, expand_before_edit, ask
from .core.replay import replay
from .core.scrub import scrub
from .core.surprise import compute_surprises, render_surprise_report
from .paths import (
    default_embed_cache_path,
    default_snapshot_path,
    graph_dir,
    project_root,
    LEGACY_EMBED_CACHE,
    LEGACY_LLM_EW_CACHE,
    LEGACY_SNAPSHOT,
)


def _resolve_snapshot(arg: str | None) -> str:
    return arg or default_snapshot_path()


def _user_config_env_path() -> "Path":
    """User-level bellamem config `.env` path (cross-platform).

    Uses `platformdirs.user_config_dir` so the path is native on
    Linux (`~/.config/bellamem/.env`, or `$XDG_CONFIG_HOME` if set),
    macOS (`~/Library/Application Support/bellamem/.env`), and
    Windows (`%APPDATA%\\bellamem\\.env`). The intended use is a
    single place to drop `OPENAI_API_KEY` and other shared secrets
    so you don't have to repeat them in every project's `.env`.

    Returns the path whether or not the file exists — load_dotenv
    treats a missing file as a no-op, so callers can rely on this
    being side-effect-free.
    """
    from pathlib import Path
    from platformdirs import user_config_dir
    return Path(user_config_dir("bellamem")) / ".env"


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


def _format_session_result(r: dict) -> str:
    """Format one per-session ingest result for streaming display."""
    line = f"  {r['session']}: +{r['turns']} turns → +{r['claims']} claims"
    if r.get("affirmed") or r.get("corrected"):
        line += f"  (affirmed:{r.get('affirmed', 0)} corrected:{r.get('corrected', 0)})"
    if r.get("causes") or r.get("self_obs"):
        line += f"  (causes:{r.get('causes', 0)} self_obs:{r.get('self_obs', 0)})"
    return line


def _print_session_start(session_name: str) -> None:
    """Callback: print a header line as each session's ingest begins."""
    print(f"  {session_name}")


def _print_session_progress(turns: int, claims: int) -> None:
    """Callback: print intra-file progress during a long ingest."""
    print(f"      ... {turns} turns, {claims} claims")


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

    # Default is "current session only" (the mtime-latest transcript).
    # --all-sessions flips to the old "every transcript" behaviour, used
    # for first-run backfill or recovery after `bellamem reset`.
    # --latest-only is kept as a compat alias but is now redundant.
    latest_only = not args.all_sessions

    # Stream each session's result as soon as ingest_session returns,
    # not at the end of the batch. Also print a header when each
    # session starts and intra-file progress every N turns so huge
    # transcripts don't look hung.
    results: list[dict] = []
    for r in ingest_project(
        bella, cwd=args.cwd,
        tail=args.tail, no_llm=args.no_llm, latest_only=latest_only,
        on_session_start=_print_session_start,
        on_progress=_print_session_progress,
    ):
        results.append(r)
        print(_format_session_result(r))

    after_ingest = sum(len(g.beliefs) for g in bella.fields.values())

    # R3 auto-emerge: consolidation is part of the ingest pipeline, not a
    # separate step. Recent beliefs enter as "unconsolidated" and become
    # "consolidated" through R3 running over them. Runs without the LLM
    # on the hot path — merges only, no field renames. `--no-emerge`
    # skips this if a session explicitly wants to inspect the raw output.
    merged = 0
    if not args.no_emerge and results:
        emerge_report = emerge(bella)
        merged = len(emerge_report.merges)

    save(bella, snap)
    after = sum(len(g.beliefs) for g in bella.fields.values())
    if not results:
        print(f"no Claude Code transcripts found for cwd={args.cwd or os.getcwd()}")
        return 1
    if merged:
        print(f"emerge (auto): merged {merged} near-duplicate pair(s)")
    print(f"beliefs: {before} → {after_ingest} → {after}"
          f"  (+{after_ingest - before} ingested, -{after_ingest - after} merged)")
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


def cmd_ask(args: argparse.Namespace) -> int:
    """Session Q&A retriever — v0.2 walker over .graph/v02.json.

    Replaces the flat-graph `bellamem.core.expand.ask` path, which
    reads a snapshot (`default.json`) that `save` stopped updating
    during the v0.2 migration. The walker scores concepts by
    mass-weighted relevance (substring + cosine when embeddings
    hydrate from cache) and walks one hop of typed edges
    (dispute/retract/cause/support/elaborate) to show the
    neighborhood.

    Optional `--class` filter restricts seeds to one class — the
    mechanism GitHub issue #2's §2 "intent classifier" can dispatch
    through once it lands. Without the filter, ask returns all
    classes mass-weighted.
    """
    from bellamem.proto import load_graph, ask_text
    graph = load_graph()
    if not graph.concepts:
        print(
            "empty v0.2 graph — run `bellamem save` first",
            file=sys.stderr,
        )
        return 1

    # Optional embedder — cache-backed, same one ingest uses. When
    # the cache file is missing or the OpenAI key is absent, the
    # walker falls back to substring-on-topic scoring, which is
    # actually fine for curated concept topics.
    embedder = None
    try:
        import tempfile
        from pathlib import Path
        from bellamem.proto.clients import Embedder
        scratch = Path(tempfile.gettempdir()) / "bellamem-proto-tree"
        cache_path = scratch / "proto-embed-cache.json"
        if cache_path.exists():
            embedder = Embedder(cache_path)
    except Exception:
        embedder = None

    class_filter = getattr(args, "class_filter", None)
    text = ask_text(
        graph,
        args.focus,
        embedder=embedder,
        class_filter=class_filter,
    )
    print(text)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """v0.2: print every concept grouped by class × nature. Legacy
    --min-mass arg is accepted but ignored."""
    from bellamem.proto import load_graph
    g = load_graph()
    if not g.concepts:
        print("empty v0.2 graph")
        return 0
    for cls in ("invariant", "decision", "observation", "ephemeral"):
        for nat in ("metaphysical", "normative", "factual"):
            bucket = [c for c in g.concepts.values()
                      if c.class_ == cls and c.nature == nat]
            if not bucket:
                continue
            print(f"\n== {cls} × {nat} ({len(bucket)}) ==")
            for c in sorted(bucket, key=lambda c: -len(c.source_refs)):
                state = f" [{c.state}]" if c.state else ""
                print(f"  [{len(c.source_refs):2}r]{state} {c.topic}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """v0.2: summary counts by class / nature / state / edge type."""
    from bellamem.proto import load_graph
    from bellamem.proto.store import DEFAULT_GRAPH_PATH
    g = load_graph()
    path = DEFAULT_GRAPH_PATH
    print(f"snapshot:  {path}")
    print(f"sources:   {len(g.sources)}")
    print(f"concepts:  {len(g.concepts)}")
    print(f"edges:     {len(g.edges)}")
    print()
    print("by class:")
    for k in ("invariant", "decision", "observation", "ephemeral"):
        print(f"  {k}: {len(g.by_class.get(k, ()))}")
    print("by nature:")
    for k in ("factual", "normative", "metaphysical"):
        print(f"  {k}: {len(g.by_nature.get(k, ()))}")
    eph_states: dict[str, int] = {}
    for c in g.concepts.values():
        if c.class_ == "ephemeral":
            eph_states[c.state or "?"] = eph_states.get(c.state or "?", 0) + 1
    print(f"ephemeral states: {eph_states}")
    etypes: dict[str, int] = {}
    for e in g.edges.values():
        etypes[e.type] = etypes.get(e.type, 0) + 1
    print(f"edge types: {etypes}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """v0.2: wipe .graph/v02.json. Does NOT touch the legacy flat
    graph at .graph/default.json — use `rm .graph/default.json`
    directly if you want to remove that."""
    from bellamem.proto.store import DEFAULT_GRAPH_PATH
    target = args.snapshot or str(DEFAULT_GRAPH_PATH)
    if os.path.exists(target):
        os.unlink(target)
        print(f"removed {target}")
    else:
        print(f"no v0.2 graph at {target}")
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

    # Build OpenAI client if any LLM-using feature is active:
    # the `compact` contender needs it, and so does --llm-judge.
    openai_client = None
    contenders = args.contenders.split(",") if args.contenders else None
    needs_llm = (
        (contenders is None or "compact" in contenders)
        or args.llm_judge
    )
    if needs_llm:
        try:
            from openai import OpenAI  # type: ignore
        except ImportError:
            print("this configuration requires the openai package; "
                  "install with `pip install -e '.[openai]'` or drop "
                  "--llm-judge and the compact contender",
                  file=sys.stderr)
            return 2
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("OpenAI features require OPENAI_API_KEY in .env or env",
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
    """Entropy + health report over .graph/v02.json.

    Was reading the flat `Bella` snapshot (`default.json`), which
    `save` stopped updating during the v0.2 migration — same shape
    as the ask/recall/why retrieval gap. Rewired to
    `bellamem.proto.audit.audit`, which computes v0.2-native
    signals: concept_density, structural_edge_ratio,
    mass_floor_fraction, mass_spread, orphan_refs, plus ephemeral
    state counts.

    Legacy args (--top, --max-per-section) are accepted but ignored
    — the v0.2 audit has no top-N knob because each signal is a
    single number, not a ranked list.
    """
    from bellamem.proto import load_graph
    from bellamem.proto.audit import audit as audit_v02, format_audit

    graph = load_graph()
    if not graph.concepts:
        print(
            "empty v0.2 graph — run `bellamem save` first",
            file=sys.stderr,
        )
        return 1

    report = audit_v02(graph)
    print(format_audit(report))

    # --strict keeps the old linter-style CI exit contract: non-zero
    # when any signal is hard. Default stays diagnostic (exit 0 with
    # findings in stdout) so dogfood workflows don't break.
    if getattr(args, "strict", False) and report.any_hard():
        return 4
    return 0


def cmd_emerge(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — nothing to emerge")
        return 0

    name_fn = None
    llm_extractor = None
    if args.llm:
        # LLM-backed refiner for fields the baseline can't name.
        from .adapters.llm_ew import make_llm_ew_from_env, make_llm_name_fn, LLMExtractor
        # Force-construct an extractor even if BELLAMEM_EW isn't hybrid —
        # --llm is an explicit opt-in for this command.
        try:
            llm_extractor = make_llm_ew_from_env()
            if llm_extractor is None:
                llm_extractor = LLMExtractor()
        except RuntimeError as e:
            print(f"--llm requested but: {e}", file=sys.stderr)
            return 2
        name_fn = make_llm_name_fn(llm_extractor)

    report = emerge(bella, min_cosine=args.min_cosine,
                    dry_run=args.dry_run, name_fn=name_fn)
    if llm_extractor is not None:
        llm_extractor.flush()

    print(report.render())
    if args.dry_run:
        print("(dry run — snapshot not saved)")
        return 0
    if not report.merges and not report.renames:
        print("(no changes — snapshot not rewritten)")
        return 0
    save(bella, snap)
    print(f"snapshot: {snap}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    """Chronological timeline — v0.2 walker over .graph/v02.json.

    Replaces `bellamem.core.replay` (flat `Bella` snapshot, stale
    since the v0.2 migration). Picks the most-recent-activity session
    by default and prints each turn with the concepts it cited.

    Legacy args (--focus, --since-line) are accepted but only
    --since-line maps cleanly onto the v0.2 model (as since_turn);
    --focus is ignored — the v0.2 replay is unfiltered by design
    since `ask` already handles focus-scoped retrieval.
    """
    from bellamem.proto import load_graph, replay_text

    graph = load_graph()
    if not graph.sources:
        print(
            "empty v0.2 graph — run `bellamem save` first",
            file=sys.stderr,
        )
        return 1

    since_turn = 0
    if getattr(args, "since_line", None):
        try:
            since_turn = int(args.since_line)
        except (TypeError, ValueError):
            since_turn = 0
    # Soft cap: the flat replay used a token budget; v0.2 uses a line
    # cap because each turn is ~1 line of summary. 120 matches the
    # flat budget's typical output size at default 2500t.
    max_lines = 120
    if getattr(args, "budget", None):
        max_lines = max(20, args.budget // 20)

    print(replay_text(
        graph,
        session=getattr(args, "session", None),
        since_turn=since_turn,
        max_lines=max_lines,
    ))
    return 0


def cmd_surprises(args: argparse.Namespace) -> int:
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
    window = None
    if args.since_hours is not None:
        window = args.since_hours * 3600
    report = compute_surprises(bella, top_n=args.top,
                               recent_window_seconds=window)
    print(render_surprise_report(report, max_per_section=args.top))
    return 0


def cmd_scrub(args: argparse.Namespace) -> int:
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — nothing to scrub")
        return 0
    before = sum(len(g.beliefs) for g in bella.fields.values())
    report = scrub(bella)
    after = sum(len(g.beliefs) for g in bella.fields.values())
    print(report.render())
    print()
    print(f"beliefs: {before} → {after}  (-{before - after})")
    if args.dry_run:
        print("(dry run — snapshot not saved)")
        return 0
    if report.beliefs_removed == 0 and not report.fields_removed:
        print("(nothing changed — snapshot not rewritten)")
        return 0
    save(bella, snap)
    print(f"snapshot: {snap}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """Copy legacy ~/.bellamem/ runtime state into <project_root>/.graph/.

    Copies rather than moves, so the legacy files stay in place until the
    user has verified the migration. Skips files that would overwrite an
    existing target — migration is safe to re-run.
    """
    import shutil

    target = graph_dir()
    target.mkdir(parents=True, exist_ok=True)

    plan = [
        (LEGACY_SNAPSHOT, target / "default.json", "snapshot"),
        (LEGACY_EMBED_CACHE, target / "embed_cache.json", "embed cache"),
        (LEGACY_LLM_EW_CACHE, target / "llm_ew_cache.json", "llm ew cache"),
    ]

    print(f"migrating legacy ~/.bellamem/ → {target}/")
    copied = 0
    for src, dst, label in plan:
        if not src.exists():
            print(f"  skip {label}: {src} not found")
            continue
        if dst.exists():
            print(f"  skip {label}: {dst} already exists")
            continue
        shutil.copy2(src, dst)
        print(f"  copied {label}: {src} → {dst}")
        copied += 1

    if copied == 0:
        print("nothing to migrate.")
        return 0

    print()
    print(f"copied {copied} file(s). legacy files at ~/.bellamem/ are unchanged —")
    print(f"delete them manually once you've confirmed bellamem works from .graph/.")
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    """Session Q&A — focus-scoped v0.2 walker pack.

    Was parked on `cmd_resume` during the v0.2 migration because the
    walker query API didn't exist yet and `resume_text` had no focus
    filter. With `bellamem.proto.walker.ask_text` now live, recall
    dispatches to the same v0.2 walker as `ask`, carrying the topic
    through as the focus argument.
    """
    ask_args = argparse.Namespace(
        snapshot=args.snapshot,
        focus=args.topic,
        budget=args.budget,
        class_filter=getattr(args, "class_filter", None),
    )
    return cmd_ask(ask_args)


def cmd_why(args: argparse.Namespace) -> int:
    """Causal pack — v0.2 walker scoped to invariants + cause edges.

    Before v0.2, why routed to `cmd_before_edit` which reads the flat
    `Bella` snapshot — stale since the v0.2 migration. Now dispatches
    to the v0.2 walker with no class filter: the walker already
    surfaces dispute/retract/cause/support/elaborate edges, and the
    agent reading the pack can trace causal chains from the `⇒ causes`
    section.

    A tighter "only invariants + causes" mode can ship as a class
    filter once issue #2's intent classifier lands — the schema
    already types the data.
    """
    ask_args = argparse.Namespace(
        snapshot=args.snapshot,
        focus=args.topic,
        budget=args.budget,
        class_filter=getattr(args, "class_filter", None),
    )
    return cmd_ask(ask_args)


def cmd_resume(args: argparse.Namespace) -> int:
    """Session start pack — v0.2-native typed structural summary.

    Reads .graph/v02.json and prints the resume text from
    bellamem.proto.resume_text, which organizes concepts by class ×
    nature and surfaces open ephemerals, retracted approaches, and
    dispute edges. Replaces the flat-graph replay/expand/surprises
    sections which lost the epistemic structure in narrative prose.

    Legacy args (--focus, --replay-budget, --expand-budget,
    --surprise-top) are accepted but ignored — the v0.2 resume uses
    its own section caps. Kept for CLI compatibility; can be removed
    in a follow-up once callers stop passing them.
    """
    from bellamem.proto import load_graph, resume_text

    graph = load_graph()
    if not graph.concepts:
        print(
            "empty v0.2 graph — run `bellamem save` or "
            "`python -m bellamem.proto ingest <SESSION>` first",
            file=sys.stderr,
        )
        return 1
    print(resume_text(graph))
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    """Composite ingest + audit + surprises for session save.

    Replaces the shell dispatcher's `save` subcommand. Runs the same
    pipeline the low-level `ingest-cc` runs (with auto-emerge) and
    then prints an audit report and top surprises in the same
    section layout the slash command expects.
    """
    # v0.2-native save: incrementally ingest the latest Claude Code
    # session into .graph/v02.json via bellamem.proto. Audit / emerge /
    # decay / surprises are flat-graph concerns that have no v0.2
    # equivalent yet — deferred to follow-up work. Legacy args
    # (--snapshot, --no-emerge, --audit-top, --surprise-top,
    # --all-sessions, --no-llm, --tail, --force-audit) are accepted
    # for CLI compatibility but mostly ignored.
    import fcntl
    from pathlib import Path
    from bellamem.proto import load_graph, save_graph, ingest_session
    from bellamem.proto.clients import Embedder, TurnClassifier
    from bellamem.proto.store import DEFAULT_GRAPH_PATH
    import tempfile

    # Acquire a per-graph flock so cmd_save and the cron serialize on
    # one process at a time for the SAME graph file. Without this, a
    # user-invoked `bellamem save` can race a concurrent cron tick,
    # both load the graph at different times, and whichever saves
    # last silently drops the other's in-flight concepts.
    #
    # The lock lives inside the project's .graph/ dir so it's
    # inherently scoped to that project — a save in /project-a and
    # a save in /project-b don't contend. The earlier /tmp/bellamem-
    # dogfood-cron.lock was a single global path, which meant every
    # project on the machine serialized against every other, and a
    # slow first-run ingest in one project would block the cron in
    # another. Non-blocking: if the lock is held, bail with a clear
    # message rather than queueing.
    graph_path_pre = Path(args.snapshot) if args.snapshot else DEFAULT_GRAPH_PATH
    graph_path_pre.parent.mkdir(parents=True, exist_ok=True)
    lock_path = graph_path_pre.parent / ".save.lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(
            f"bellamem save: another ingest is already running "
            f"against this project (lock: {lock_path}).\n"
            f"  Likely the dogfood cron is mid-tick, or another "
            f"`bellamem save` is in progress in the same project. "
            f"Wait for it to finish and retry.",
            file=sys.stderr,
        )
        return 2

    # Find latest session jsonl. The claude-code projects dir encodes
    # the cwd in its name. Delegate to `adapters.claude_code.project_dir_for`
    # so the encoding rule (every non-alphanumeric char → `-`, including
    # underscores) stays in one place. The earlier inline `replace("/", "-")`
    # missed underscores and silently looked at the wrong dir for any
    # project whose path contained an underscore.
    from .adapters.claude_code import project_dir_for
    cwd = Path(args.cwd or os.getcwd()).resolve()
    claude_project_dir = Path(project_dir_for(str(cwd)))
    if not claude_project_dir.is_dir():
        print(
            f"no Claude Code project dir found for {cwd}\n"
            f"  expected: {claude_project_dir}",
            file=sys.stderr,
        )
        return 1

    jsonls = sorted(
        claude_project_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not jsonls:
        print(f"no session jsonls in {claude_project_dir}", file=sys.stderr)
        return 1
    latest = jsonls[0]

    print("## Ingest with auto-consolidation")
    print()
    print(f"session: {latest.name}")

    scratch = Path(tempfile.gettempdir()) / "bellamem-proto-tree"
    scratch.mkdir(parents=True, exist_ok=True)
    embedder = Embedder(scratch / "proto-embed-cache.json")
    classifier = TurnClassifier(scratch / "proto-llm-cache.json")

    graph_path = Path(args.snapshot) if args.snapshot else DEFAULT_GRAPH_PATH
    graph = load_graph(graph_path)
    before_concepts = len(graph.concepts)
    before_edges = len(graph.edges)

    def _progress(n_turns, n_concepts, n_edges, n_llm):
        print(f"  [{n_turns}] concepts={n_concepts} edges={n_edges} llm={n_llm}",
              flush=True)

    stats = ingest_session(
        graph, latest,
        embedder=embedder, classifier=classifier,
        on_progress=_progress, save_every=25, save_to=graph_path,
        tail=getattr(args, "tail", None),
    )
    save_graph(graph, graph_path)

    delta_concepts = len(graph.concepts) - before_concepts
    delta_edges = len(graph.edges) - before_edges
    print()
    print(
        f"concepts: {before_concepts} → {len(graph.concepts)} "
        f"(+{delta_concepts})  "
        f"edges: {before_edges} → {len(graph.edges)} (+{delta_edges})"
    )
    print(
        f"turns: {stats['total_turns']}  "
        f"llm: {stats['llm_calls']}  "
        f"cached: {stats['cache_hits']}  "
        f"skipped: {stats.get('skipped_already_ingested', 0)}"
    )
    print(f"acts: {stats['act_counts']}")
    print(f"snapshot: {graph_path}")
    return 0


def cmd_install_commands(args: argparse.Namespace) -> int:
    """Install the Bella Claude Code slash command into a commands dir.

    Default destination is `~/.claude/commands/bellamem.md` (global, works
    in every project). `--project` switches to `./.claude/commands/` for a
    per-project install. `--dry-run` prints the target without writing;
    `--force` overwrites an existing file.
    """
    from pathlib import Path

    try:
        from importlib.resources import files  # type: ignore
    except ImportError:  # pragma: no cover
        print("error: importlib.resources not available (needs Python 3.9+)",
              file=sys.stderr)
        return 2

    try:
        template = files("bellamem.templates").joinpath("bellamem.md").read_text()
    except (FileNotFoundError, ModuleNotFoundError) as e:
        print(f"error: could not read bundled template: {e}", file=sys.stderr)
        print("hint: is bellamem installed? try `pip install -e .`", file=sys.stderr)
        return 2

    if args.project:
        target_dir = Path.cwd() / ".claude" / "commands"
        scope = "project"
    else:
        target_dir = Path.home() / ".claude" / "commands"
        scope = "global"
    target = target_dir / "bellamem.md"

    if args.dry_run:
        print(f"would install ({scope}): {target}")
        print(f"template size: {len(template)} bytes")
        if target.exists() and not args.force:
            print(f"(target exists — would need --force to overwrite)")
        return 0

    if target.exists() and not args.force:
        print(f"error: {target} already exists. Pass --force to overwrite.",
              file=sys.stderr)
        return 1

    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(template)
    print(f"installed ({scope}): {target}")
    print()
    print("you can now use /bellamem in Claude Code:")
    print("  /bellamem           — resume: working memory + long-term memory + signal")
    print("  /bellamem save      — ingest current session + audit + surprises")
    print("  /bellamem recall X  — mass-ranked beliefs about X")
    print("  /bellamem why X     — pre-edit pack: invariants, disputes, causes")
    print("  /bellamem replay    — narrative timeline")
    print("  /bellamem audit     — entropy signals")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    """Structural forgetting: remove leaf beliefs that never earned their place.

    Dry-run is the default. Users must pass `--apply` to actually mutate
    the snapshot. The report counts all candidates and shows the weakest
    by mass so the user can sanity-check before committing.
    """
    from .core.prune import (
        PruneCriteria,
        apply_prune,
        identify_prune_candidates,
    )

    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — nothing to prune")
        return 0

    criteria = PruneCriteria(
        age_days=args.age_days,
        grace_days=args.grace_days,
        mass_low=args.mass_low,
        mass_high=args.mass_high,
        max_voices=args.max_voices,
    )
    report = identify_prune_candidates(bella, criteria)

    header = (
        f"bellamem prune  (age≥{criteria.age_days:g}d, "
        f"grace≥{criteria.grace_days:g}d, "
        f"mass∈[{criteria.mass_low:.2f},{criteria.mass_high:.2f}], "
        f"voices≤{criteria.max_voices})"
    )
    print(header)
    print("=" * len(header))
    print(report.render(top=args.top))

    if not args.apply:
        print()
        print("(dry run — pass --apply to actually remove these beliefs)")
        return 0

    if report.n_candidates == 0:
        print()
        print("nothing to remove.")
        return 0

    before = sum(len(g.beliefs) for g in bella.fields.values())
    removed = apply_prune(bella, report)
    after = sum(len(g.beliefs) for g in bella.fields.values())
    save(bella, snap)
    print()
    print(f"removed {removed} beliefs. total: {before} → {after}")
    print(f"snapshot: {snap}")
    return 0


def cmd_decay(args: argparse.Namespace) -> int:
    """Log-odds decay: exponentially regress non-exempt beliefs toward the prior.

    Dry-run is the default — users pass `--apply` to actually mutate
    the snapshot. `--dt-override` simulates a specific wall-clock gap
    instead of reading (now - bella.decayed_at), which makes the
    command useful for "what would 30 days of decay do to the current
    forest?" sanity checks.
    """
    import time
    from .core.decay import (
        DEFAULT_HALF_LIFE_DAYS,
        SECONDS_PER_DAY,
        apply_decay,
        decay_factor,
    )
    from .core.gene import mass_of

    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — nothing to decay")
        return 0

    now = time.time()
    if args.dt_override is not None:
        dt_seconds = args.dt_override * SECONDS_PER_DAY
        dt_source = f"--dt-override {args.dt_override:g}d"
    else:
        dt_seconds = now - bella.decayed_at
        dt_source = f"now - bella.decayed_at"

    if args.stats:
        days = dt_seconds / SECONDS_PER_DAY
        factor = decay_factor(dt_seconds, args.half_life)
        print(f"decayed_at: {bella.decayed_at:.0f}  "
              f"(age {days:.2f}d)")
        print(f"half_life:  {args.half_life:g}d")
        print(f"factor:     {factor:.6f}  "
              f"(applied to log_odds)")
        n = sum(len(g.beliefs) for g in bella.fields.values())
        print(f"beliefs:    {n}")
        return 0

    # Snapshot the top-N most affected beliefs before mutating, so we
    # can show a preview of what moves. We track by mass delta so a
    # high-mass belief dropping a lot shows up even if its log_odds
    # delta is modest.
    pre_mass: dict[tuple[str, str], float] = {}
    for field_name, g in bella.fields.items():
        for bid, belief in g.beliefs.items():
            pre_mass[(field_name, bid)] = mass_of(belief.log_odds)

    report = apply_decay(bella, dt_seconds, args.half_life)

    header = (
        f"bellamem decay  (dt={dt_seconds / SECONDS_PER_DAY:.2f}d "
        f"[{dt_source}], half_life={args.half_life:g}d, "
        f"factor={report.factor:.4f})"
    )
    print(header)
    print("=" * len(header))
    print(f"decayed:     {report.decayed}")
    print(f"exempt:      {report.exempt}  "
          f"(reserved fields + mass_floor pins)")
    print(f"quiet fades: {report.quiet_fades}  "
          f"(ratified → limbo via decay alone)")

    if report.quiet_fade_entries:
        print()
        print("quiet-fade details (used to matter, now in limbo):")
        for qf in report.quiet_fade_entries[:args.top]:
            print(f"  {qf.old_mass:.3f} → {qf.new_mass:.3f}  "
                  f"[{qf.field_name[:20]}]  {qf.desc[:60]}")

    # Show top movers by |Δmass|.
    movers: list[tuple[float, str, str, float, float, str]] = []
    for field_name, g in bella.fields.items():
        for bid, belief in g.beliefs.items():
            post = mass_of(belief.log_odds)
            pre = pre_mass[(field_name, bid)]
            if pre == post:
                continue
            movers.append((abs(pre - post), field_name, bid, pre, post, belief.desc))
    movers.sort(key=lambda t: -t[0])
    if movers:
        print()
        print("top movers:")
        for _, fn, _bid, pre, post, desc in movers[:args.top]:
            short = desc[:60]
            print(f"  {pre:.3f} → {post:.3f}  [{fn[:20]}]  {short}")

    if not args.apply:
        print()
        print("(dry run — pass --apply to persist the decayed snapshot)")
        return 0

    bella.decayed_at = now
    save(bella, snap)
    print()
    print(f"persisted. snapshot: {snap}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Render the belief forest to an image (or DOT) via graphviz.

    The output format is inferred from the `--out` extension:
      .dot                          → plain DOT source (no graphviz needed)
      .svg | .png | .pdf | .gv      → rendered via the `graphviz` Python binding
    Any other extension is treated as DOT.

    If `graphviz` (the Python package) isn't installed, we fall back to
    writing the DOT source next to the requested path and print the
    shell command to rasterize it. That keeps `bellamem render` useful
    even without the `[viz]` extra.
    """
    from .core.visualize import (
        RenderOptions,
        focus_ids as compute_focus_ids,
        to_dot,
        count_selected,
    )

    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — nothing to render", file=sys.stderr)
        return 1

    # 3D viz path — `.html` output is a Three.js self-contained file,
    # handled by bellamem.viz (optional, via the `viz3d` extra). It's
    # its own layout (UMAP × mass) and ignores the focus/dispute/min-mass
    # filters the graphviz path uses — those assume a 2D subgraph, not
    # a 3D projection of the whole forest.
    out_ext_early = os.path.splitext(args.out)[1].lower().lstrip(".")
    if out_ext_early == "html":
        try:
            from .viz.render3d import render_html
        except ImportError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        try:
            n_rendered = render_html(bella, args.out)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 3
        print(f"wrote 3D viz ({n_rendered} beliefs) → {args.out}")
        return 0

    # Resolve the focus filter first so count_selected reports the final size.
    fids: set[str] | None = None
    if args.focus:
        fids = compute_focus_ids(
            bella.fields,
            args.focus,
            top=args.focus_top,
            depth=args.depth,
            embedder=current_embedder(),
        )
        if not fids:
            print(f"no beliefs matched focus {args.focus!r}", file=sys.stderr)
            return 1

    opts = RenderOptions(
        fields=[args.field] if args.field else None,
        disputes_only=args.disputes_only,
        min_mass=args.min_mass,
        max_nodes=args.max_nodes,
        focus_ids=fids,
        title=args.title,
        dpi=args.dpi,
    )

    n = count_selected(bella.fields, opts)
    dot_source = to_dot(bella.fields, opts)

    out = args.out
    ext = os.path.splitext(out)[1].lower().lstrip(".")
    raster_formats = {"svg", "png", "pdf", "gv"}

    if ext in raster_formats:
        try:
            import graphviz  # type: ignore[import-not-found]
        except ImportError:
            dot_path = os.path.splitext(out)[0] + ".dot"
            os.makedirs(os.path.dirname(os.path.abspath(dot_path)) or ".", exist_ok=True)
            with open(dot_path, "w", encoding="utf-8") as f:
                f.write(dot_source)
            print(
                f"graphviz Python package not installed — wrote DOT source to "
                f"{dot_path} ({n} nodes). Install the viz extra with "
                f"`pip install bellamem[viz]` to render directly, or run "
                f"`dot -T{ext} -o {out} {dot_path}` with the graphviz system "
                f"tool if you already have it.",
                file=sys.stderr,
            )
            return 0

        src = graphviz.Source(dot_source, engine=args.engine)
        out_dir = os.path.dirname(os.path.abspath(out)) or "."
        os.makedirs(out_dir, exist_ok=True)
        basename = os.path.splitext(os.path.basename(out))[0]
        # graphviz.Source.render writes <basename>.<format>; we accept that.
        rendered = src.render(
            filename=basename,
            directory=out_dir,
            format=ext,
            cleanup=True,
        )
        print(f"rendered {n} nodes → {rendered}")
        return 0

    # DOT fallback — write the source verbatim.
    out_dir = os.path.dirname(os.path.abspath(out)) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(dot_source)
    print(f"wrote DOT source ({n} nodes) → {out}")
    return 0


def cmd_embedder(args: argparse.Namespace) -> int:
    _setup_embedder()
    e = current_embedder()
    print(f"active embedder: {e.name}")
    print(f"dim:             {e.dim}")
    cache_path = default_embed_cache_path()
    kind = os.environ.get("BELLAMEM_EMBEDDER", "hash")
    print(f"kind (env):      {kind}")
    if kind != "hash":
        print(f"cache:           {cache_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bellamem",
                                 description="local accumulating memory for LLM agents")
    p.add_argument("--version", action="version", version=f"bellamem {__version__}")
    p.add_argument("--snapshot", help="snapshot path (default: <project>/.graph/default.json)")
    # The subcommand is optional — `bellamem` with no args routes to
    # `bellamem resume`. This lets the Claude Code slash command pass
    # a (possibly empty) $ARGUMENTS through without a shell dispatcher.
    sub = p.add_subparsers(dest="cmd", required=False)

    sp = sub.add_parser(
        "ingest-cc",
        help="ingest Claude Code .jsonl transcripts "
             "(default: current session only)",
    )
    sp.add_argument("--cwd", help="project cwd (defaults to current working dir)")
    sp.add_argument("--tail", type=int, default=None,
                    help="limit each session to its last N turns (fast partial ingest)")
    sp.add_argument("--no-llm", action="store_true",
                    help="disable LLM-backed EW regardless of BELLAMEM_EW")
    sp.add_argument("--all-sessions", action="store_true",
                    help="ingest every transcript in the project, not just "
                         "the current session (use for first-run backfill "
                         "or after `bellamem reset`)")
    sp.add_argument("--latest-only", action="store_true",
                    help="(compat alias; the default is now 'current session "
                         "only' so this flag is redundant)")
    sp.add_argument("--no-emerge", action="store_true",
                    help="skip R3 auto-consolidation at end of ingest")
    sp.set_defaults(func=cmd_ingest_cc)

    sp = sub.add_parser("expand", help="print a mass-weighted context pack")
    sp.add_argument("focus", help="focus description (what the agent is about to do)")
    sp.add_argument("-t", "--budget", type=int, default=1200,
                    help="token budget (default 1200)")
    sp.set_defaults(func=cmd_expand)

    sp = sub.add_parser(
        "ask",
        help="session Q&A retriever — relevance-first (complementary to expand)",
    )
    sp.add_argument("focus", help="the question the user is asking")
    sp.add_argument("-t", "--budget", type=int, default=1200,
                    help="token budget (default 1200)")
    sp.set_defaults(func=cmd_ask)

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

    sp = sub.add_parser(
        "migrate",
        help="copy legacy ~/.bellamem/ runtime state into <project>/.graph/",
    )
    sp.set_defaults(func=cmd_migrate)

    # --- composite commands for slash-command flow ---------------------------

    sp = sub.add_parser(
        "recall",
        help="alias: mass-ranked beliefs about a topic (wraps `expand`)",
    )
    sp.add_argument("topic", nargs="+", help="topic description")
    sp.add_argument("-t", "--budget", type=int, default=1500,
                    help="token budget (default 1500)")
    sp.set_defaults(func=lambda args: cmd_recall(
        argparse.Namespace(
            snapshot=args.snapshot,
            topic=" ".join(args.topic),
            budget=args.budget,
        )
    ))

    sp = sub.add_parser(
        "why",
        help="alias: pre-edit pack (wraps `before-edit`)",
    )
    sp.add_argument("topic", nargs="+", help="focus description")
    sp.add_argument("-t", "--budget", type=int, default=1500,
                    help="token budget (default 1500)")
    sp.set_defaults(func=lambda args: cmd_why(
        argparse.Namespace(
            snapshot=args.snapshot,
            topic=" ".join(args.topic),
            budget=args.budget,
        )
    ))

    sp = sub.add_parser(
        "resume",
        help="session start: working memory + long-term memory + signal",
    )
    sp.add_argument("--focus", default="current state and open follow-ups",
                    help="focus string for the expand pack "
                         "(default: 'current state and open follow-ups')")
    sp.add_argument("--replay-budget", type=int, default=2500,
                    help="token budget for the replay tail (default 2500)")
    sp.add_argument("--expand-budget", type=int, default=1500,
                    help="token budget for the expand pack (default 1500)")
    sp.add_argument("--surprise-top", type=int, default=8,
                    help="number of surprise rows per section (default 8)")
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser(
        "save",
        help="session end: ingest current session + auto-emerge + audit + surprises",
    )
    sp.add_argument("--cwd",
                    help="project cwd (defaults to current working dir)")
    sp.add_argument("--tail", type=int, default=None,
                    help="limit each session to its last N turns")
    sp.add_argument("--no-llm", action="store_true",
                    help="disable LLM-backed EW regardless of BELLAMEM_EW")
    sp.add_argument("--all-sessions", action="store_true",
                    help="ingest every transcript in the project, not just "
                         "the current session (use for first-run backfill "
                         "or after `bellamem reset`)")
    sp.add_argument("--latest-only", action="store_true",
                    help="(compat alias; the default is now 'current session "
                         "only' so this flag is redundant)")
    sp.add_argument("--no-emerge", action="store_true",
                    help="skip R3 auto-consolidation at end of ingest")
    sp.add_argument("--force-audit", action="store_true",
                    help="run audit + surprises even if ingest produced "
                         "nothing new (default: skip them on empty ingests)")
    sp.add_argument("--audit-top", type=int, default=10,
                    help="rows per audit section (default 10)")
    sp.add_argument("--audit-max-per-section", type=int, default=3,
                    help="max rows rendered per audit section (default 3)")
    sp.add_argument("--surprise-top", type=int, default=5,
                    help="number of surprise rows per section (default 5)")
    sp.set_defaults(func=cmd_save)

    sp = sub.add_parser(
        "install-commands",
        help="install the /bellamem Claude Code slash command "
             "(default: global at ~/.claude/commands/)",
    )
    sp.add_argument("--project", action="store_true",
                    help="install into ./.claude/commands/ instead of "
                         "~/.claude/commands/")
    sp.add_argument("--force", action="store_true",
                    help="overwrite an existing bellamem.md")
    sp.add_argument("--dry-run", action="store_true",
                    help="print the target path without writing")
    sp.set_defaults(func=cmd_install_commands)

    sp = sub.add_parser(
        "prune",
        help="remove leaf beliefs that never earned their place "
             "(dry-run by default)",
    )
    sp.add_argument("--apply", action="store_true",
                    help="actually remove candidates (default: dry run)")
    sp.add_argument("--age-days", type=float, default=30.0,
                    help="last_touched must be older than this (default 30)")
    sp.add_argument("--grace-days", type=float, default=14.0,
                    help="event_time must be older than this (default 14)")
    sp.add_argument("--mass-low", type=float, default=0.48,
                    help="lower bound of the base-mass band (default 0.48)")
    sp.add_argument("--mass-high", type=float, default=0.55,
                    help="upper bound of the base-mass band (default 0.55)")
    sp.add_argument("--max-voices", type=int, default=1,
                    help="max n_voices for a prune candidate (default 1)")
    sp.add_argument("--top", type=int, default=10,
                    help="number of candidates to preview (default 10)")
    sp.set_defaults(func=cmd_prune)

    sp = sub.add_parser(
        "render",
        help="render the belief forest as a graphviz diagram (SVG/PNG/PDF/DOT)",
    )
    sp.add_argument("--out", default="graph.svg",
                    help="output path; extension sets format "
                         "(.svg/.png/.pdf/.dot, default graph.svg)")
    sp.add_argument("--focus", default=None,
                    help="focus description — renders a subgraph around the "
                         "top matches plus --depth steps of neighbors")
    sp.add_argument("--focus-top", type=int, default=20,
                    help="number of closest-to-focus seed beliefs (default 20)")
    sp.add_argument("--depth", type=int, default=2,
                    help="BFS expansion depth from seeds (default 2)")
    sp.add_argument("--field", default=None,
                    help="restrict to one field by name")
    sp.add_argument("--disputes-only", action="store_true",
                    help="only show ⊥ edges and their endpoints")
    sp.add_argument("--min-mass", type=float, default=0.0,
                    help="drop beliefs below this mass (default 0.0)")
    sp.add_argument("--max-nodes", type=int, default=400,
                    help="hard cap on rendered nodes (default 400)")
    sp.add_argument("--engine", default="sfdp",
                    help="graphviz layout engine "
                         "(dot|neato|fdp|sfdp|twopi|circo; default sfdp)")
    sp.add_argument("--title", default=None,
                    help="title label on the diagram")
    sp.add_argument("--dpi", type=int, default=150,
                    help="raster resolution for PNG/PDF output (default 150)")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("scrub",
                        help="remove system-noise beliefs from the snapshot")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what would be removed without saving")
    sp.set_defaults(func=cmd_scrub)

    sp = sub.add_parser("replay",
                        help="narrative replay: beliefs from a session in line order")
    sp.add_argument("focus", nargs="?", default=None,
                    help="optional focus to filter by relevance")
    sp.add_argument("-t", "--budget", type=int, default=1500,
                    help="token budget (default 1500)")
    sp.add_argument("--session",
                    help="session key override (default: most recently active)")
    sp.add_argument("--since-line", type=int, default=None,
                    help="only include beliefs from line ≥ N")
    sp.set_defaults(func=cmd_replay)

    sp = sub.add_parser("surprises",
                        help="top Jaynes step surprises, sign flips, disputes")
    sp.add_argument("--top", type=int, default=10,
                    help="rows per section (default 10)")
    sp.add_argument("--since-hours", type=float, default=None,
                    help="only consider jumps in the last N hours")
    sp.set_defaults(func=cmd_surprises)

    sp = sub.add_parser("emerge",
                        help="R3: merge near-duplicates + rename garbage fields")
    sp.add_argument("--dry-run", action="store_true",
                    help="report what would change without saving")
    sp.add_argument("--min-cosine", type=float, default=0.92,
                    help="minimum cosine similarity for a merge (default 0.92)")
    sp.add_argument("--llm", action="store_true",
                    help="use gpt-4o-mini to name fields the baseline can't "
                         "(needs [openai] extra + OPENAI_API_KEY)")
    sp.set_defaults(func=cmd_emerge)

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
    sp.add_argument("--strict", action="store_true",
                    help="exit non-zero (4) when entropy signals are "
                         "found — for CI linter pipelines. Default is "
                         "exit 0 (audit is a diagnostic, not a linter)")
    sp.add_argument("--no-exit-code", action="store_true",
                    help=argparse.SUPPRESS)  # backwards-compat no-op
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser(
        "decay",
        help="exponential log_odds regress toward prior (v0.1 forgetting)",
    )
    sp.add_argument("--apply", action="store_true",
                    help="persist the decayed snapshot (default: dry run)")
    sp.add_argument("--half-life", type=float, default=30.0,
                    help="half-life in days (default 30)")
    sp.add_argument("--dt-override", type=float, default=None,
                    help="simulate a specific Δt in days instead of "
                         "(now - bella.decayed_at); useful for asking "
                         "'what would 30 days of decay do right now?'")
    sp.add_argument("--stats", action="store_true",
                    help="print current decayed_at + factor without mutating")
    sp.add_argument("--top", type=int, default=15,
                    help="top-N movers to show in the preview (default 15)")
    sp.set_defaults(func=cmd_decay)

    return p


def main(argv: list[str] | None = None) -> int:
    # Line-buffer stdout so per-file / per-section prints appear in real
    # time when our output is piped (e.g. captured by Claude Code's
    # background task runner). Python's default for a pipe is full-buffer
    # with a 4-8 KB block, which means a long-running ingest writes zero
    # bytes of visible output until many lines accumulate — it looks hung
    # even when it's grinding at 96% CPU. Reconfiguring here is cheap and
    # only affects this process.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, io.UnsupportedOperation):
        pass
    # Env loading precedence: shell env > project .env > user .env.
    # load_dotenv uses setdefault semantics, so whichever file loads
    # first wins for keys still absent from os.environ. We load
    # project first so per-project overrides take priority, then
    # fall back to the user-level config for multi-project setups
    # (e.g. OPENAI_API_KEY set once in ~/.config/bellamem/.env
    # instead of in every project root).
    load_dotenv(str(project_root() / ".env"))
    load_dotenv(str(_user_config_env_path()))
    args = build_parser().parse_args(argv)
    # No subcommand → implicit `resume`. Fill in the resume defaults
    # so cmd_resume can read them off args the same way it would if
    # the user had typed `bellamem resume` explicitly.
    if args.cmd is None:
        args.focus = "current state and open follow-ups"
        args.replay_budget = 2500
        args.expand_budget = 1500
        args.surprise_top = 8
        return cmd_resume(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
