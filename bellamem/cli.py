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
from .core.expand import expand, expand_before_edit
from .core.replay import replay
from .core.scrub import scrub
from .core.surprise import compute_surprises, render_surprise_report
from .paths import (
    default_embed_cache_path,
    default_snapshot_path,
    graph_dir,
    LEGACY_EMBED_CACHE,
    LEGACY_LLM_EW_CACHE,
    LEGACY_SNAPSHOT,
)


def _resolve_snapshot(arg: str | None) -> str:
    return arg or default_snapshot_path()


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

    # Stream each session's result as soon as ingest_session returns,
    # not at the end of the batch. Keeps the terminal responsive on
    # large first-run ingests.
    results: list[dict] = []
    for r in ingest_project(
        bella, cwd=args.cwd,
        tail=args.tail, no_llm=args.no_llm, latest_only=args.latest_only,
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
    result = replay(
        bella,
        focus=args.focus,
        session=args.session,
        since_line=args.since_line,
        budget_tokens=args.budget,
    )
    if result.session_key is None:
        print("no source-grounded beliefs yet — re-ingest to populate sources",
              file=sys.stderr)
        return 1
    print(result.text())
    print()
    shown = len(result.entries)
    total = result.total_candidates
    suffix = ""
    if shown < total:
        suffix = f" (tail-preserved {shown}/{total}, budget={args.budget}t)"
    print(f"— {shown} entries{suffix}, ~{result.used_tokens()}t —")
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
    """Alias for `bellamem expand` with the slash-command default budget.

    `bellamem recall "what did we decide about auth?"` is identical to
    `bellamem expand "what did we decide about auth?" -t 1500`. Exists
    so the /bellamem slash command can call `bellamem recall` directly
    without going through a shell dispatcher.
    """
    recall_args = argparse.Namespace(
        snapshot=args.snapshot,
        focus=args.topic,
        budget=args.budget,
    )
    return cmd_expand(recall_args)


def cmd_why(args: argparse.Namespace) -> int:
    """Alias for `bellamem before-edit` with the slash-command default budget.

    `bellamem why "the forgetting mechanism"` is identical to
    `bellamem before-edit "the forgetting mechanism" -t 1500`.
    """
    why_args = argparse.Namespace(
        snapshot=args.snapshot,
        focus=args.topic,
        entity=None,
        budget=args.budget,
    )
    return cmd_before_edit(why_args)


def cmd_resume(args: argparse.Namespace) -> int:
    """Composite working-memory pack for session start.

    Prints three sections to stdout:
      1. Working memory — replay tail of the most recent session
      2. Long-term memory — expand pack over the focus
      3. Signal — top surprises

    Replaces the shell dispatcher's `resume` subcommand. The section
    headers match what the slash command post-processing expects.
    """
    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if not bella.fields:
        print("empty memory — run `bellamem save` or `bellamem ingest-cc` first",
              file=sys.stderr)
        return 1

    # Section 1: working memory (replay tail)
    print("## Working memory (replay tail)")
    print()
    replay_result = replay(
        bella,
        focus=None,
        session=None,
        since_line=None,
        budget_tokens=args.replay_budget,
    )
    if replay_result.session_key is None:
        print("(no source-grounded beliefs yet — re-ingest to populate sources)")
    else:
        print(replay_result.text())
        print()
        shown = len(replay_result.entries)
        total = replay_result.total_candidates
        suffix = ""
        if shown < total:
            suffix = f" (tail-preserved {shown}/{total}, budget={args.replay_budget}t)"
        print(f"— {shown} entries{suffix}, ~{replay_result.used_tokens()}t —")

    print()
    print("## Long-term memory (ratified decisions, current focus)")
    print()
    pack = expand(bella, args.focus, budget_tokens=args.expand_budget)
    print(pack.text())
    print()
    print(f"— {len(pack.lines)} lines, ~{pack.used_tokens()}t / {args.expand_budget}t budget —")

    print()
    print("## What just mattered (surprises)")
    print()
    report = compute_surprises(bella, top_n=args.surprise_top)
    print(render_surprise_report(report, max_per_section=args.surprise_top))

    return 0


def cmd_save(args: argparse.Namespace) -> int:
    """Composite ingest + audit + surprises for session save.

    Replaces the shell dispatcher's `save` subcommand. Runs the same
    pipeline the low-level `ingest-cc` runs (with auto-emerge) and
    then prints an audit report and top surprises in the same
    section layout the slash command expects.
    """
    from .adapters.claude_code import ingest_project

    _setup_embedder()
    snap = _resolve_snapshot(args.snapshot)
    try:
        bella = load(snap)
    except EmbedderMismatch as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # Section 1: ingest with auto-consolidation
    print("## Ingest with auto-consolidation")
    print()
    before = sum(len(g.beliefs) for g in bella.fields.values())

    # Stream per-session results so the user sees progress on long
    # first-run ingests instead of staring at an empty output file.
    results: list[dict] = []
    for r in ingest_project(
        bella,
        cwd=args.cwd,
        tail=args.tail,
        no_llm=args.no_llm,
        latest_only=args.latest_only,
    ):
        results.append(r)
        print(_format_session_result(r))

    after_ingest = sum(len(g.beliefs) for g in bella.fields.values())

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
    print(
        f"beliefs: {before} → {after_ingest} → {after}"
        f"  (+{after_ingest - before} ingested, -{after_ingest - after} merged)"
    )
    print(f"snapshot: {snap}")

    # Section 2: audit
    print()
    print("## Audit")
    print()
    audit_report = audit(bella, top_n=args.audit_top)
    print(render_report(audit_report, max_per_section=args.audit_max_per_section))

    # Section 3: top surprises after ingest
    print()
    print("## Top surprises after ingest")
    print()
    surprise_report = compute_surprises(bella, top_n=args.surprise_top)
    print(render_surprise_report(surprise_report, max_per_section=args.surprise_top))

    return 0


def cmd_install_commands(args: argparse.Namespace) -> int:
    """Install the BellaMem Claude Code slash command into a commands dir.

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
            with open(dot_path, "w") as f:
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
    with open(out, "w") as f:
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
    p.add_argument("--snapshot", help="snapshot path (default: <project>/.graph/default.json)")
    # The subcommand is optional — `bellamem` with no args routes to
    # `bellamem resume`. This lets the Claude Code slash command pass
    # a (possibly empty) $ARGUMENTS through without a shell dispatcher.
    sub = p.add_subparsers(dest="cmd", required=False)

    sp = sub.add_parser("ingest-cc", help="ingest Claude Code .jsonl transcripts")
    sp.add_argument("--cwd", help="project cwd (defaults to current working dir)")
    sp.add_argument("--tail", type=int, default=None,
                    help="limit each session to its last N turns (fast partial ingest)")
    sp.add_argument("--no-llm", action="store_true",
                    help="disable LLM-backed EW regardless of BELLAMEM_EW")
    sp.add_argument("--latest-only", action="store_true",
                    help="only ingest the most recent session (demos / quick tests)")
    sp.add_argument("--no-emerge", action="store_true",
                    help="skip R3 auto-consolidation at end of ingest")
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
    sp.add_argument("--latest-only", action="store_true",
                    help="only ingest the most recent session")
    sp.add_argument("--no-emerge", action="store_true",
                    help="skip R3 auto-consolidation at end of ingest")
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
    sp.add_argument("--no-exit-code", action="store_true",
                    help="always exit 0 even if bandaid piles are found")
    sp.set_defaults(func=cmd_audit)

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
    # Load .env from cwd if present. Explicit call, not on import.
    load_dotenv(".env")
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
