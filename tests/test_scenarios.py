"""Smoke + assertion tests for docs/scenarios.py.

Pins each scenario's structural-preservation, surfacing, and (for the
long-debug scenario) token-compression promise. If a future change to
ingest, emerge, prune, or expand silently breaks any scenario's story,
this test fails loudly so the change can be reviewed against the
illustrated mechanism.

The numeric assertions use ranges (not exact values) so the test
tolerates small variation from tokenizer version drift while still
catching real regressions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# docs/scenarios.py imports from example_session, so the docs/ directory
# has to be on sys.path before either module loads.
_DOCS = Path(__file__).resolve().parent.parent / "docs"
if str(_DOCS) not in sys.path:
    sys.path.insert(0, str(_DOCS))


@pytest.fixture
def results():
    """Run all scenarios once per test invocation."""
    from scenarios import SCENARIOS, run_scenario  # type: ignore[import-not-found]
    return [run_scenario(s) for s in SCENARIOS]


def _by_name(results, name):
    for r in results:
        if r.name == name:
            return r
    raise KeyError(f"no scenario named {name!r} in results")


def test_all_scenarios_preserve_structure(results):
    """Every scenario must keep all disputes, causes, ratifications,
    and __self__ observations through compression. None lost."""
    for r in results:
        assert r.structure_preserved, (
            f"scenario {r.name!r} lost load-bearing structure during "
            f"compression: disputes {r.disputes_in}->{r.disputes_out}, "
            f"causes {r.causes_in}->{r.causes_out}, "
            f"multi-voice {r.multi_voice_in}->{r.multi_voice_out}, "
            f"self-obs {r.self_obs_in}->{r.self_obs_out}"
        )


def test_all_scenarios_surface_load_bearing_claims(results):
    """The expand pack must contain every must_surface substring from
    the scenario — the future-session retrieval correctness check."""
    for r in results:
        assert r.all_surfaced, (
            f"scenario {r.name!r} expand pack missed required "
            f"substrings: {r.missed}"
        )


def test_flaky_test_entropy_drop(results):
    """The flaky-test scenario should drop entropy by ≥0.5 bits and
    cut belief count by at least 25% — the README's worked-example
    headline numbers."""
    r = _by_name(results, "flaky-test")
    entropy_drop = r.entropy_in - r.entropy_out
    belief_drop = (r.beliefs_in - r.beliefs_out) / r.beliefs_in
    assert entropy_drop >= 0.5, (
        f"flaky-test entropy drop too small: {r.entropy_in:.2f} -> "
        f"{r.entropy_out:.2f} (drop {entropy_drop:.2f}, expected >= 0.5)"
    )
    assert belief_drop >= 0.25, (
        f"flaky-test belief reduction too small: "
        f"{r.beliefs_in} -> {r.beliefs_out} ({100*belief_drop:.0f}%, "
        f"expected >= 25%)"
    )


def test_long_debug_actually_compresses_tokens(results):
    """The long-debug scenario is sized specifically to show positive
    token compression — raw tokens > expand pack tokens. This pins
    the token-win promise: at scale, Bella expand fits the decisive
    context in fewer tokens than the raw transcript would.
    """
    r = _by_name(results, "long-debug")
    assert r.compression_ratio > 1.0, (
        f"long-debug must show positive token compression "
        f"(raw / expand > 1.0); got raw={r.raw_tokens}, "
        f"expand={r.expand_tokens}, ratio={r.compression_ratio:.2f}"
    )
    # Sanity: the dialogue itself should be substantial.
    assert r.raw_tokens >= 400, (
        f"long-debug raw transcript too short to be a meaningful "
        f"compression test: {r.raw_tokens} tokens"
    )


def test_compression_curve_break_even_under_300_tokens(results):
    """The headline metric: a linear fit of expand_tokens against
    raw_tokens predicts a break-even point — the raw transcript size
    above which Bella starts saving tokens. This pins it at <300
    raw tokens, matching the documented "use Bella for conversations
    longer than ~200 tokens" guidance.

    If a future change to expand, ingest, or prune drives the
    break-even point above 300, the rule of thumb is wrong and
    docs/scenarios.md (and the README pitch) need to be updated.
    """
    from scenarios import compression_fit  # type: ignore[import-not-found]
    fit = compression_fit(results)
    assert fit.intercept > 0, (
        f"linear fit must have positive intercept (fixed overhead "
        f"per session); got {fit.intercept:.2f}"
    )
    assert 0 < fit.slope < 1, (
        f"linear fit slope must be between 0 and 1 — Bella IS "
        f"compressing each marginal raw token; got {fit.slope:.3f}"
    )
    assert fit.break_even_raw < 300, (
        f"break-even point drifted above 300 raw tokens "
        f"({fit.break_even_raw:.0f}); the 'use Bella for "
        f"conversations >~200 tokens' rule of thumb no longer holds. "
        f"Either fix the regression or update docs/scenarios.md and "
        f"the README pitch."
    )
    assert fit.break_even_raw > 100, (
        f"break-even point dropped suspiciously low "
        f"({fit.break_even_raw:.0f}) — verify the linear fit isn't "
        f"degenerate"
    )


def test_compression_ratio_grows_with_scale(results):
    """The empirical tendency: as raw transcript size grows, the
    compression ratio improves because Bella's per-belief metadata
    overhead amortizes over more dialogue. This pins the trend so
    future regressions in expand, ingest, or prune that flatten
    the curve fail loudly.

    Sort scenarios by raw transcript size and assert that the
    sprint scenario (the largest) has a strictly higher ratio than
    the long-debug scenario (the previous largest), which in turn
    has a higher ratio than the small scenarios.
    """
    by_size = sorted(results, key=lambda r: r.raw_tokens)
    smallest = by_size[0]
    long_debug = _by_name(results, "long-debug")
    sprint = _by_name(results, "sprint")

    assert sprint.raw_tokens > long_debug.raw_tokens, (
        f"sprint ({sprint.raw_tokens} raw) must be larger than "
        f"long-debug ({long_debug.raw_tokens} raw) to test the trend"
    )
    assert sprint.compression_ratio > long_debug.compression_ratio, (
        f"compression ratio must improve as dialogue grows; "
        f"long-debug={long_debug.compression_ratio:.2f}× at "
        f"{long_debug.raw_tokens} raw, sprint="
        f"{sprint.compression_ratio:.2f}× at {sprint.raw_tokens} raw"
    )
    assert long_debug.compression_ratio > smallest.compression_ratio, (
        f"compression ratio must improve as dialogue grows; "
        f"smallest={smallest.compression_ratio:.2f}× at "
        f"{smallest.raw_tokens} raw, long-debug="
        f"{long_debug.compression_ratio:.2f}× at "
        f"{long_debug.raw_tokens} raw"
    )


def test_ask_mode_correctness_matches_or_beats_expand(tmp_path):
    """Regression test: running scenarios through `ask` instead of
    `expand` should produce correctness numbers at least as good as
    expand on synthetic scenarios.

    The production-correctness measurement (on real Claude Code
    sessions) showed expand top-3 at 0/10 and ask top-3 at ~4-8/10.
    That's the headline motivation. But synthetic scenarios pass
    expand at 83% top-3 because the hand-authored decisions happen
    to land in the mass layer. We need to verify ask doesn't
    REGRESS the synthetic correctness — otherwise we'd be trading
    production quality for synthetic quality.

    This test rebuilds each scenario's graph, runs `ask()` instead
    of `expand()`, and asserts the same correctness floors (every
    correct answer in the pack, at least 50% per-scenario top-3,
    aggregate ≥80%). If ask regresses below these floors on any
    scenario, the bucket-order inversion has made the synthetic
    case worse and the tradeoff needs revisiting.
    """
    import sys
    from pathlib import Path
    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    if str(docs_dir) not in sys.path:
        sys.path.insert(0, str(docs_dir))
    from scenarios import (  # type: ignore[import-not-found]
        SCENARIOS, _ingest_dialogue, measure, age_beliefs, compress,
        correctness_check,
    )
    from bellamem.core import Bella
    from bellamem.core.embed import HashEmbedder, set_embedder
    from bellamem.core.expand import ask

    total_checked = 0
    total_in_pack = 0
    total_top_3 = 0

    for scenario in SCENARIOS:
        set_embedder(HashEmbedder())
        bella = Bella()
        tags = _ingest_dialogue(bella, scenario.dialogue)
        age_beliefs(bella, days=60)
        compress(bella)

        # Run ASK instead of expand
        pack = ask(bella, scenario.test_question,
                   budget_tokens=scenario.expand_budget)

        correctness = correctness_check(
            bella, tags, scenario.correct_answer_tags,
            scenario.dialogue, pack.text(),
        )

        # Every correct answer in the pack (same floor as expand)
        in_pack_rate = correctness.n_in_pack / correctness.n_checked
        assert in_pack_rate >= 1.0 - 1e-6, (
            f"ask() on scenario {scenario.name!r} only surfaced "
            f"{correctness.n_in_pack}/{correctness.n_checked} "
            f"correct-answer beliefs (expected 100%). Missing: "
            f"{[b.tag for b in correctness.beliefs if not b.in_pack]}"
        )

        # At least 50% per scenario in top-3 (same floor as expand)
        top_3_rate = correctness.n_top_3 / correctness.n_checked
        assert top_3_rate >= 0.5, (
            f"ask() on scenario {scenario.name!r} placed only "
            f"{correctness.n_top_3}/{correctness.n_checked} correct-"
            f"answer beliefs in the top-3 ({100 * top_3_rate:.0f}%, "
            f"floor 50%). Ranks: "
            f"{[b.rank_in_pack for b in correctness.beliefs]}"
        )

        total_checked += correctness.n_checked
        total_in_pack += correctness.n_in_pack
        total_top_3 += correctness.n_top_3

    # Aggregate floor (same as expand)
    aggregate_top_3 = total_top_3 / total_checked if total_checked else 0
    assert aggregate_top_3 >= 0.8, (
        f"ask() aggregate top-3 rate dropped to {100 * aggregate_top_3:.0f}% "
        f"({total_top_3}/{total_checked}); floor is 80%. The ask "
        f"bucket-inversion has regressed the synthetic correctness "
        f"case below what expand achieves."
    )


def test_correctness_all_answers_in_pack_most_in_top_3(results):
    """Correctness check — does the pack contain the RIGHT answer?

    For each scenario, we hand-authored the dialogue and know which turn
    tags correspond to the ratified decisions. This test asserts that
    those specific beliefs (or their merged successors) appear in the
    expand pack when an agent asks the test question, and that most of
    them rank in the top-3.

    Non-circular: the ground truth is hand-authored BEFORE ingest runs.
    A stable set of wrong beliefs would fail this test even if it
    passed the rephrasing-robustness test.

    Floors:
      - 100% of correct-answer beliefs must appear in the pack
      - At least 50% per scenario must rank in top-3
      - At least 80% aggregate must rank in top-3
    """
    total_checked = 0
    total_in_pack = 0
    total_top_3 = 0

    for r in results:
        c = r.correctness
        assert c is not None, (
            f"scenario {r.name!r} has no correct_answer_tags configured"
        )
        assert c.n_checked >= 1, (
            f"scenario {r.name!r} has zero correct-answer beliefs to check"
        )

        # Every correct answer must appear somewhere in the pack —
        # strict floor. A correct answer that vanished from the pack
        # entirely is a retrieval failure we care about deeply.
        in_pack_rate = c.n_in_pack / c.n_checked
        assert in_pack_rate >= 1.0 - 1e-6, (
            f"{r.name!r} only surfaced {c.n_in_pack}/{c.n_checked} "
            f"correct-answer beliefs in the expand pack "
            f"(expected 100%). Missing: "
            f"{[b.tag for b in c.beliefs if not b.in_pack]}"
        )

        # At least half of the correct-answer beliefs must rank in the
        # top-3 per scenario — catches regressions where decisions get
        # buried.
        top_3_rate = c.n_top_3 / c.n_checked
        assert top_3_rate >= 0.5, (
            f"{r.name!r} only placed {c.n_top_3}/{c.n_checked} "
            f"correct-answer beliefs in the top-3 "
            f"({100 * top_3_rate:.0f}%, floor 50%). "
            f"Ranks: {[b.rank_in_pack for b in c.beliefs]}"
        )

        total_checked += c.n_checked
        total_in_pack += c.n_in_pack
        total_top_3 += c.n_top_3

    # Aggregate floor — catches regressions that quietly degrade one
    # scenario without falling below the per-scenario floor.
    aggregate_top_3_rate = total_top_3 / total_checked if total_checked else 0
    assert aggregate_top_3_rate >= 0.8, (
        f"aggregate top-3 rate across all scenarios dropped to "
        f"{100 * aggregate_top_3_rate:.0f}% ({total_top_3}/{total_checked}); "
        f"floor is 80%. Retrieval correctness regressed."
    )


def test_rephrasing_robustness_core_stable_above_floor(results):
    """Semantic-quality checkpoint.

    For each scenario, the same underlying question is asked 5 different
    ways. We measure how much the `expand` packs overlap across rephrasings
    via pairwise Jaccard. If the graph represents MEANING, overlap is high;
    if it's just cosine matching surface text, overlap is low.

    Floor: every scenario must produce mean pairwise Jaccard >= 0.4.
    This is a conservative threshold — the test should loudly fail if
    the graph becomes essentially cosine-driven (e.g. after a regression
    that breaks mass-weighting or field routing).

    Also asserts that on the larger scenarios (where pack << graph,
    so phrasing can genuinely change what comes back), there's a
    non-trivial stable core — at least 25% of the belief union
    appears in EVERY rephrasing's pack. This is the real semantic
    stability signal; the small scenarios trivially pass because
    their packs equal their whole graphs.

    Note: the harness uses HashEmbedder (zero-dep deterministic), which
    is the weakest semantic signal. OpenAI embeddings would likely
    produce higher numbers. 0.4 is a floor, not a target.
    """
    for r in results:
        assert r.rephrasing is not None, (
            f"scenario {r.name!r} has no rephrasings configured"
        )
        rp = r.rephrasing
        assert rp.n_rephrasings >= 5, (
            f"{r.name!r} has only {rp.n_rephrasings} rephrasings "
            f"(need >= 5 for a meaningful signal)"
        )
        assert rp.mean_jaccard >= 0.4, (
            f"{r.name!r} rephrasing mean Jaccard dropped to "
            f"{rp.mean_jaccard:.2f} (floor: 0.4). The graph may have "
            f"become cosine-driven — check expand's routing or mass "
            f"differentiation."
        )
        # On non-trivial scenarios (pack < 30 beliefs means budget
        # isn't fitting the whole graph), the core should be non-empty.
        if rp.union_size >= 10:
            assert rp.core_fraction >= 0.25, (
                f"{r.name!r} has a large union ({rp.union_size}) but "
                f"only {rp.core_fraction:.2f} core fraction — less "
                f"than 25% of beliefs are stable across all 5 "
                f"rephrasings. Semantic robustness regressed."
            )


def test_rejected_refactor_dispute_survives(results):
    """The rejected-refactor scenario's whole point is that a single
    user denial creates a durable dispute. After compression, the
    dispute count must not drop to zero, and the rejection's reason
    must surface in the expand pack."""
    r = _by_name(results, "rejected-refactor")
    assert r.disputes_out >= 1, (
        f"rejected-refactor lost its dispute during compression: "
        f"{r.disputes_in} -> {r.disputes_out}"
    )
    assert "cycles" in [s.lower() for s in r.surfaced], (
        f"rejected-refactor expand pack missing the rejection reason "
        f"(cycles); surfaced={r.surfaced}"
    )
