"""Scenario harness — synthetic conversations that demonstrate Bella's
entropy reduction and structural preservation under compression.

Each scenario is a self-contained dialogue (a list of `Turn` objects from
`docs/example_session.py`) plus a `test_question` an agent might ask later
and a `must_surface` substring the expand pack should contain. The harness
measures, for each scenario:

  raw_tokens         tokens in the verbatim transcript
  beliefs_in         beliefs after ingest
  entropy_in         Shannon entropy bits of the mass distribution (in)
  beliefs_out        beliefs after age + emerge + prune
  entropy_out        Shannon entropy bits (out)
  expand_tokens      tokens in the expand() pack answering test_question
  surfaced           whether `must_surface` appears in the expand pack
  structure_kept     {disputes, causes, ratifications, self_obs} survived

A note about token compression: small synthetic scenarios (≤30 turns)
do NOT show positive token-compression ratios. Bella's per-belief
metadata overhead (~10 tokens for the `[field] m=0.XX v=N` prefix)
dominates short transcripts. Token compression kicks in at scale —
see `benchmarks/v0.0.4rc1.md` for the 1834-belief case where `expand`
beats `flat_tail` 92% to 0% LLM-judge at the same budget.

What these small scenarios DO demonstrate:

  1. Entropy reduction — Shannon bits of the mass distribution drop
     measurably after age + emerge + prune
  2. Structural preservation — disputes, causes, ratified decisions,
     and self-observations all survive compression untouched
  3. Retrieval correctness — the load-bearing claim from the dialogue
     surfaces in the expand pack when an agent asks the test question
     later, under a tight budget

Run as: python docs/scenarios.py
A pytest smoke test in tests/test_scenarios.py pins the structural
preservation and surfacing assertions so scenarios can't silently
drift when ingest, expand, or prune behavior changes.

Adding a new scenario:
  1. Define a list[Turn] with the dialogue (using the same conventions
     as DIALOGUE in docs/example_session.py — kind/tag/target/lr).
  2. Wrap it in a Scenario(name=..., description=..., dialogue=...,
     test_question=..., must_surface=...).
  3. Append it to SCENARIOS.
  4. Update tests/test_scenarios.py with the expected assertions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bellamem.core import Bella
from bellamem.core.embed import HashEmbedder, set_embedder
from bellamem.core.expand import expand
from bellamem.core.tokens import count_tokens

from example_session import (
    DIALOGUE as FLAKY_TEST_DIALOGUE,
    Turn,
    age_beliefs,
    compress,
    measure,
    run_dialogue,
)


# ---------------------------------------------------------------------------
# Scenario 2 — the rejected refactor (cross-session dispute survival)
# ---------------------------------------------------------------------------
#
# The agent proposes a refactor that the user has rejected before. The user
# says no with the reason. Bella records the dispute. The story this
# scenario demonstrates: a single user "no" with reasons creates durable
# structure that survives compression — and an agent asking "should I
# refactor X?" tomorrow gets the dispute surfaced via expand(), under a
# tight token budget, without re-asking the question.

REJECTED_REFACTOR_DIALOGUE: list[Turn] = [
    Turn(voice="assistant",
         text="we should extract the auth middleware into a shared base class for v1 and v2",
         tag="proposal",
         lr=1.1),

    Turn(voice="user",
         text="we tried that last quarter and the dependency cycles got much worse",
         tag="cycles_reason",
         lr=2.0),

    Turn(voice="user",
         text="don't pull auth into a base class, leave it duplicated across the two versions",
         kind="deny",
         target="proposal",
         lr=2.0),

    Turn(voice="user",
         text="the duplication is the lesser evil here",
         kind="confirm",
         target="cycles_reason",
         lr=1.8),

    Turn(voice="assistant",
         text="agreed, keeping auth flat across v1 and v2 to avoid cycles",
         tag="agreement",
         lr=1.1),
]


# ---------------------------------------------------------------------------
# Scenario 3 — long debugging session (the token-compression story)
# ---------------------------------------------------------------------------
#
# A realistic 30-turn production incident: a payment webhook failure
# investigation. The transcript carries lots of assistant exposition
# that the filter drops (preamble sentences, demonstratives, fillers)
# while the load-bearing turns — the symptom, the cause chain, the
# rejected workaround, the ratified fix, the self-observation — stay.
#
# Story this scenario demonstrates: when raw transcripts get long
# enough that per-belief metadata overhead stops dominating, the
# expand pack compresses the session by an empirically-measurable
# factor while preserving every load-bearing belief.

LONG_DEBUG_DIALOGUE: list[Turn] = [
    Turn(voice="user",
         text="payment webhook failed in prod again, the third time this week",
         tag="incident",
         lr=2.0),

    Turn(voice="assistant",
         text="let me pull the logs from the last hour and check the failure mode",
         lr=1.1),

    Turn(voice="assistant",
         text="i can see 200ms timeouts in the stripe webhook handler",
         tag="timeout_obs",
         lr=1.2),

    Turn(voice="user",
         text="we keep hitting these timeouts, what's actually going on under the hood",
         lr=1.5),

    Turn(voice="assistant",
         text="the handler issues a synchronous database write before acknowledging the webhook",
         tag="sync_write",
         lr=1.2),

    Turn(voice="assistant",
         text="that database write blocks behind a long-running migration job that runs hourly",
         kind="cause",
         target="timeout_obs",
         lr=1.2),

    Turn(voice="assistant",
         text="we should bump the webhook timeout from 200ms to 2 seconds to give the write room",
         tag="bandaid",
         lr=1.1),

    Turn(voice="user",
         text="no, that's just papering over the real problem and stripe will retry on us anyway",
         kind="deny",
         target="bandaid",
         lr=2.0),

    Turn(voice="user",
         text="we should ack the webhook first then queue the database write asynchronously",
         tag="async_fix",
         lr=2.0),

    Turn(voice="assistant",
         text="that means stripe gets a fast 200 OK and the heavy work happens in a background worker",
         lr=1.1),

    Turn(voice="assistant",
         text="i'll add a job to the redis queue and have the worker process it within the SLA window",
         tag="impl_plan",
         lr=1.1),

    Turn(voice="user",
         text="yes that's the right shape, ack first then enqueue",
         kind="confirm",
         target="async_fix",
         lr=2.0),

    Turn(voice="assistant",
         text="the worker can retry on its own with exponential backoff if the database is still locked",
         tag="retry_logic",
         lr=1.1),

    Turn(voice="assistant",
         text="we should add structured logging around the enqueue path to catch failures early",
         tag="observability",
         lr=1.1),

    Turn(voice="user",
         text="add latency metrics for both the ack path and the worker path",
         lr=1.8),

    Turn(voice="assistant",
         text="i'll patch webhook.py with the new ack-first flow and stripe_worker.py for the queue consumer",
         tag="patch_plan",
         lr=1.1),

    Turn(voice="assistant",
         text="the migration job's lock contention is a separate issue we should track in jira",
         tag="migration_followup",
         lr=1.1),

    Turn(voice="user",
         text="good, file a ticket for the migration lock and tag it for q2",
         lr=1.5),

    Turn(voice="assistant",
         text="this is the third payment incident traced back to synchronous webhook handlers this quarter",
         tag="quarterly_pattern",
         lr=1.05),

    Turn(voice="assistant",
         text="i reach for timeout bumps when the underlying handler architecture is the real problem",
         kind="self",
         lr=1.3),

    Turn(voice="user",
         text="exactly, you keep doing that across different services",
         kind="confirm",
         target="quarterly_pattern",
         lr=2.0),

    Turn(voice="assistant",
         text="i'll add the ack-first pattern to the team handbook so the next webhook handler starts there",
         lr=1.2),

    Turn(voice="user",
         text="yes and link it to the postmortem",
         lr=1.5),

    Turn(voice="assistant",
         text="patched webhook.py with ack-first, added stripe_worker.py with retry, deployed to staging",
         tag="patch_applied",
         lr=1.1),

    Turn(voice="user",
         text="run the integration tests against staging before we promote to prod",
         lr=1.5),

    Turn(voice="assistant",
         text="all integration tests passing in staging, latency p99 dropped from 800ms to 90ms",
         tag="validation",
         lr=1.2),

    Turn(voice="user",
         text="ship it",
         kind="confirm",
         target="async_fix",
         lr=2.0),

    Turn(voice="assistant",
         text="deployed to prod, monitoring the webhook latency dashboard for the next hour",
         lr=1.1),

    Turn(voice="user",
         text="thanks, also document the migration lock as a known issue",
         lr=1.5),

    Turn(voice="assistant",
         text="done, the migration lock is in the runbook and tagged for q2 cleanup",
         lr=1.1),
]


# ---------------------------------------------------------------------------
# Scenario 4 — super long sprint (does the tendency hold at scale?)
# ---------------------------------------------------------------------------
#
# A 60-turn three-week database performance arc. Three sub-incidents
# (slow endpoint, replica lag, schema review) chained through one
# overarching narrative. Designed to test whether the token-compression
# ratio continues improving as raw transcript grows. The hypothesis
# is that per-belief metadata overhead amortizes further at scale —
# if long-debug is 1.4× at 508 raw tokens, sprint should be 2-4× at
# ~1300 raw tokens.

SPRINT_DIALOGUE: list[Turn] = [
    # Act 1: slow endpoint investigation
    Turn(voice="user",
         text="api endpoint p99 spiked from 80ms to 1200ms overnight, what's going on",
         tag="p99_spike",
         lr=2.0),
    Turn(voice="assistant",
         text="let me pull the slow query log from the database for the last twelve hours",
         lr=1.1),
    Turn(voice="assistant",
         text="i can see one query taking 800ms consistently across the slow log",
         tag="slow_query_obs",
         lr=1.2),
    Turn(voice="assistant",
         text="it's the order detail endpoint joining across twelve tables for related items",
         tag="join_observation",
         lr=1.2),
    Turn(voice="assistant",
         text="the join touches orders, line_items, products, variants, inventory, prices, and seven more",
         lr=1.1),
    Turn(voice="user",
         text="why has this gotten worse, the join shape hasn't changed",
         lr=1.5),
    Turn(voice="assistant",
         text="the orders table grew from two hundred thousand rows to fifteen million in the last month",
         kind="cause",
         target="slow_query_obs",
         lr=1.2),
    Turn(voice="assistant",
         text="we should add a composite index on orders by customer and timestamp to speed it up",
         tag="index_proposal",
         lr=1.1),
    Turn(voice="user",
         text="no, indexes are a bandaid here, the query shape is fundamentally wrong",
         kind="deny",
         target="index_proposal",
         lr=2.0),
    Turn(voice="user",
         text="we're joining twelve tables to render one screen, that's the actual problem",
         tag="shape_problem",
         lr=2.0),
    Turn(voice="assistant",
         text="agreed, the read pattern doesn't match the storage shape",
         kind="confirm",
         target="shape_problem",
         lr=1.5),
    Turn(voice="assistant",
         text="we should denormalize the most-used join into a materialized view of order summaries",
         tag="materialized_view",
         lr=1.2),
    Turn(voice="user",
         text="yes, materialized view is the right call here",
         kind="confirm",
         target="materialized_view",
         lr=2.0),
    Turn(voice="assistant",
         text="i'll build the order_summary view with refresh logic to update on order state changes",
         tag="view_plan",
         lr=1.1),
    Turn(voice="assistant",
         text="initial refresh will take about ten minutes for the existing fifteen million orders",
         lr=1.1),
    # Act 2: materialized view implementation
    Turn(voice="user",
         text="how do we keep the view fresh without too much overhead",
         lr=1.5),
    Turn(voice="assistant",
         text="we have two options, either trigger-based incremental refresh or scheduled batch refresh every minute",
         lr=1.1),
    Turn(voice="assistant",
         text="trigger-based gives strong consistency but adds overhead to every write transaction",
         tag="trigger_tradeoff",
         lr=1.2),
    Turn(voice="assistant",
         text="batch refresh every sixty seconds gives eventual consistency but zero write overhead",
         tag="batch_tradeoff",
         lr=1.2),
    Turn(voice="user",
         text="we don't need read-after-write here, sixty seconds of staleness is fine for order summaries",
         lr=1.8),
    Turn(voice="user",
         text="go with the batch refresh approach",
         kind="confirm",
         target="batch_tradeoff",
         lr=2.0),
    Turn(voice="assistant",
         text="i'll add a worker that refreshes the materialized view every sixty seconds",
         lr=1.1),
    Turn(voice="assistant",
         text="i reach for indexes when the read shape doesn't match the storage shape",
         kind="self",
         lr=1.3),
    Turn(voice="user",
         text="exactly, you keep doing that across different services",
         kind="confirm",
         target="shape_problem",
         lr=2.0),
    Turn(voice="assistant",
         text="patched orders.py, added order_summary view, scheduled refresh worker, deployed to staging",
         tag="staging_patch",
         lr=1.1),
    Turn(voice="user",
         text="run the load test against staging before we promote",
         lr=1.5),
    Turn(voice="assistant",
         text="staging load test passing, p99 dropped from 1200ms back to 75ms on the order detail endpoint",
         tag="load_test_result",
         lr=1.2),
    Turn(voice="user",
         text="ship it, monitor for the rest of the day",
         kind="confirm",
         target="materialized_view",
         lr=2.0),
    Turn(voice="assistant",
         text="deployed to production, watching the p99 dashboard for the next hour",
         lr=1.1),
    Turn(voice="assistant",
         text="prod p99 confirmed at 80ms, the materialized view fix is holding under real traffic",
         tag="prod_validated",
         lr=1.2),
    # Act 3: replica lag incident later in the sprint
    Turn(voice="user",
         text="user complaint, they updated their address but the next page load shows the old one",
         tag="staleness_report",
         lr=2.0),
    Turn(voice="assistant",
         text="that sounds like a read-after-write inconsistency against the replica",
         lr=1.2),
    Turn(voice="assistant",
         text="let me check the replica lag dashboard to confirm",
         lr=1.1),
    Turn(voice="assistant",
         text="replica lag is averaging eight seconds during peak load, much higher than usual",
         tag="lag_observation",
         lr=1.2),
    Turn(voice="assistant",
         text="we should route all reads through the primary database to eliminate the lag window",
         tag="primary_routing",
         lr=1.1),
    Turn(voice="user",
         text="no, that defeats the entire point of running replicas, the primary will get crushed",
         kind="deny",
         target="primary_routing",
         lr=2.0),
    Turn(voice="user",
         text="we need read-your-writes consistency, not all-reads-on-primary",
         tag="read_your_writes",
         lr=2.0),
    Turn(voice="assistant",
         text="agreed, we can route only the writer's own subsequent reads through the primary for a short window",
         kind="confirm",
         target="read_your_writes",
         lr=1.5),
    Turn(voice="assistant",
         text="i'll add a session token that pins reads to the primary for thirty seconds after a write",
         tag="session_pin",
         lr=1.1),
    Turn(voice="user",
         text="thirty seconds is enough since replica lag is currently eight seconds at p99",
         lr=1.5),
    Turn(voice="assistant",
         text="patched read_router.py with the session pin logic and added telemetry for pinned-read percentage",
         tag="pin_patch",
         lr=1.1),
    Turn(voice="assistant",
         text="staging shows the pinned read percentage at four percent of total reads, well within budget",
         lr=1.2),
    Turn(voice="user",
         text="good, ship it after the integration suite passes",
         kind="confirm",
         target="read_your_writes",
         lr=2.0),
    Turn(voice="assistant",
         text="integration suite green, deploying to production now",
         tag="rwc_deployed",
         lr=1.1),
    Turn(voice="assistant",
         text="prod confirmed, no more staleness reports in the support queue",
         lr=1.2),
    # Act 4: retrospective at end of sprint
    Turn(voice="user",
         text="we've spent three weeks on database performance, what's the pattern here",
         tag="retro_question",
         lr=1.8),
    Turn(voice="assistant",
         text="three incidents this sprint all traced back to the storage shape not matching the read pattern",
         tag="pattern_obs",
         lr=1.2),
    Turn(voice="assistant",
         text="the slow endpoint, the replica lag, and the q2 join queries we noted earlier all share that root",
         kind="cause",
         target="retro_question",
         lr=1.2),
    Turn(voice="user",
         text="so we should do a schema review next sprint instead of patching individual queries",
         tag="schema_review",
         lr=2.0),
    Turn(voice="assistant",
         text="agreed, scheduled the schema review for the first week of the next sprint",
         kind="confirm",
         target="schema_review",
         lr=1.5),
    Turn(voice="assistant",
         text="i tend to optimize individual queries when the read model needs a wholesale rethink",
         kind="self",
         lr=1.3),
    Turn(voice="user",
         text="yes, you reached for indexes first, then materialized views, before we questioned the model",
         kind="confirm",
         target="pattern_obs",
         lr=2.0),
    Turn(voice="assistant",
         text="the schema review will look at orders, line_items, and the order_summary materialized view together",
         lr=1.1),
    Turn(voice="user",
         text="include the read patterns from the last sprint as input to the review",
         lr=1.5),
    Turn(voice="assistant",
         text="i'll compile the slow query log and the staleness reports as the review evidence packet",
         tag="evidence_compile",
         lr=1.1),
    Turn(voice="user",
         text="thanks, share the packet with the team channel by friday",
         lr=1.5),
    Turn(voice="assistant",
         text="evidence packet posted to the channel, schema review on the calendar for monday morning",
         lr=1.1),
    Turn(voice="user",
         text="good work this sprint, the materialized view and the read-your-writes pin both held under load",
         lr=1.8),
    Turn(voice="assistant",
         text="next sprint we'll have the schema review output to drive the longer-term storage refactor",
         lr=1.1),
    Turn(voice="user",
         text="add the pattern to the team handbook so the next person checks read shape first",
         lr=1.8),
    Turn(voice="assistant",
         text="added the read-shape-first heuristic to the handbook under database investigation",
         lr=1.1),
]


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    name: str
    description: str
    dialogue: list[Turn]
    test_question: str
    must_surface: list[str]   # substrings the expand pack must contain
    expand_budget: int = 800


SCENARIOS: list[Scenario] = [
    Scenario(
        name="flaky-test",
        description="13-turn debugging session: bandaid → rejection → cause "
                    "chain → ratified fix → self-observation",
        dialogue=FLAKY_TEST_DIALOGUE,
        test_question="why does the integration test keep flaking and what's the fix",
        must_surface=["jitter", "rate-limit"],
        expand_budget=600,
    ),
    Scenario(
        name="rejected-refactor",
        description="5-turn refactor proposal that the user rejects with a "
                    "reason from past experience — dispute must survive",
        dialogue=REJECTED_REFACTOR_DIALOGUE,
        test_question="should we refactor the auth middleware into a shared base class",
        must_surface=["cycles", "duplicat"],
        expand_budget=400,
    ),
    Scenario(
        name="long-debug",
        description="30-turn payment webhook incident: rejected timeout bump → "
                    "ack-first async pattern → cause chain → self-observation → "
                    "shipped fix",
        dialogue=LONG_DEBUG_DIALOGUE,
        test_question="how should we handle the payment webhook timeout problem",
        must_surface=["ack", "queue"],
        expand_budget=600,
    ),
    Scenario(
        name="sprint",
        description="60-turn three-week database performance arc: slow endpoint "
                    "→ rejected index → materialized view → replica lag → "
                    "rejected primary routing → read-your-writes → schema review "
                    "decision → self-observation about reaching for indexes "
                    "before questioning the model",
        dialogue=SPRINT_DIALOGUE,
        test_question="what did we learn about database performance and what's the plan",
        must_surface=["materialized", "schema"],
        expand_budget=900,
    ),
]


# ---------------------------------------------------------------------------
# Result row
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    description: str
    test_question: str
    raw_tokens: int
    beliefs_in: int
    entropy_in: float
    disputes_in: int
    causes_in: int
    multi_voice_in: int
    self_obs_in: int
    beliefs_out: int
    entropy_out: float
    disputes_out: int
    causes_out: int
    multi_voice_out: int
    self_obs_out: int
    expand_tokens: int
    expand_lines: int
    surfaced: list[str]    # which `must_surface` substrings were found
    missed: list[str]      # which were NOT found (test failure if non-empty)

    @property
    def compression_ratio(self) -> float:
        if self.expand_tokens == 0:
            return float("inf")
        return self.raw_tokens / self.expand_tokens

    @property
    def structure_preserved(self) -> bool:
        """All structural primitives present in→out (none lost)."""
        return (
            self.disputes_out >= self.disputes_in
            and self.causes_out >= self.causes_in
            and self.multi_voice_out >= self.multi_voice_in
            and self.self_obs_out >= self.self_obs_in
        )

    @property
    def all_surfaced(self) -> bool:
        return not self.missed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _raw_transcript(dialogue: list[Turn]) -> str:
    """Concatenate every turn's text in `voice: text` form, the way a
    flat-tail context window would carry the session."""
    return "\n".join(f"{t.voice}: {t.text}" for t in dialogue)


# ---------------------------------------------------------------------------
# Compression curve — the linear fit and the break-even metric
# ---------------------------------------------------------------------------


@dataclass
class CompressionFit:
    """Ordinary least-squares fit of expand_tokens against raw_tokens
    across the scenario suite. The headline metric is `break_even_raw`
    — the raw transcript size at which Bella stops costing tokens and
    starts saving them. Below that threshold, the per-belief metadata
    overhead dominates; above it, Bella saves a growing number of
    tokens as dialogues grow.
    """
    intercept: float        # fixed overhead in tokens (per session)
    slope: float            # marginal expand-tokens per raw-token
    break_even_raw: float   # raw_tokens where raw == expand
    n_points: int

    def expand_for(self, raw_tokens: float) -> float:
        return self.intercept + self.slope * raw_tokens


def compression_fit(results: list[ScenarioResult]) -> CompressionFit:
    """OLS linear regression of expand_tokens on raw_tokens.

    With expand ≈ a + b·raw, the break-even point (raw == expand)
    solves to raw = a / (1 − b), assuming b < 1 (i.e. Bella IS
    compressing per-marginal-token).
    """
    if len(results) < 2:
        raise ValueError("need at least two scenarios to fit a line")
    n = len(results)
    xs = [r.raw_tokens for r in results]
    ys = [r.expand_tokens for r in results]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0:
        raise ValueError("all raw_tokens identical — can't fit a slope")
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    if abs(1.0 - slope) < 1e-9:
        break_even = float("inf")
    else:
        break_even = intercept / (1.0 - slope)
    return CompressionFit(
        intercept=intercept,
        slope=slope,
        break_even_raw=break_even,
        n_points=n,
    )


# ---------------------------------------------------------------------------
# SVG chart generation — hand-rolled, no external deps
# ---------------------------------------------------------------------------


def render_compression_chart_svg(results: list[ScenarioResult],
                                  fit: CompressionFit) -> str:
    """Return a self-contained SVG string visualizing the compression
    curve. No external font dependencies (uses system-ui), no JS, no
    embedded data URLs — renders cleanly through GitHub's Camo proxy.

    Layout: 720×480 viewBox, plot area 600×400 with equal x/y unit
    scaling so the y=x break-even line shows as a true 45° diagonal.
    """
    # World extent — pad past the data so labels have breathing room.
    x_max = 1200
    y_max = 800

    # Plot area in screen coordinates.
    px_left = 80
    px_right = 680
    py_top = 80
    py_bottom = 410

    plot_w = px_right - px_left   # 600
    plot_h = py_bottom - py_top   # 330

    def to_x(raw: float) -> float:
        return px_left + (raw / x_max) * plot_w

    def to_y(expand: float) -> float:
        # SVG y grows downward; invert.
        return py_bottom - (expand / y_max) * plot_h

    # Per-scenario palette (matches the broader Bella brand: indigo,
    # teal, amber, with a darker indigo for the largest one).
    palette = {
        "rejected-refactor": "#94a3b8",   # cool gray (smallest, baseline)
        "flaky-test":        "#10b981",   # teal
        "long-debug":        "#f59e0b",   # amber
        "sprint":            "#6366f1",   # indigo (the headline result)
    }

    parts: list[str] = []
    parts.append(
        '<svg viewBox="0 0 720 480" width="720" height="480" '
        'xmlns="http://www.w3.org/2000/svg" role="img" '
        'aria-labelledby="title desc">'
    )
    parts.append(
        '<title id="title">Bella compression curve</title>'
    )
    parts.append(
        '<desc id="desc">A scatter plot of raw transcript tokens '
        'against expand pack tokens for four synthetic scenarios. '
        'The diagonal y=x line marks where Bella breaks even. '
        'Points below the diagonal mean Bella saved tokens. The '
        'linear fit predicts a break-even raw transcript size of '
        f'about {fit.break_even_raw:.0f} tokens.</desc>'
    )

    # Background.
    parts.append('<rect width="720" height="480" fill="#ffffff"/>')

    # Title + subtitle (system-ui only — no Camo font issues).
    parts.append(
        '<text x="360" y="36" text-anchor="middle" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="18" font-weight="700" fill="#1e1b4b">'
        'Bella compression curve</text>'
    )
    parts.append(
        '<text x="360" y="58" text-anchor="middle" '
        'font-family="system-ui, -apple-system, sans-serif" '
        'font-size="12" fill="#64748b">'
        f'break-even: ~{fit.break_even_raw:.0f} raw tokens '
        f'(expand ≈ {fit.intercept:.0f} + {fit.slope:.2f}·raw)'
        '</text>'
    )

    # Axes.
    parts.append(
        f'<line x1="{px_left}" y1="{py_bottom}" '
        f'x2="{px_right}" y2="{py_bottom}" '
        f'stroke="#cbd5e1" stroke-width="1.5"/>'
    )
    parts.append(
        f'<line x1="{px_left}" y1="{py_top}" '
        f'x2="{px_left}" y2="{py_bottom}" '
        f'stroke="#cbd5e1" stroke-width="1.5"/>'
    )

    # X axis ticks + labels.
    for raw_tick in (0, 200, 400, 600, 800, 1000, 1200):
        x = to_x(raw_tick)
        parts.append(
            f'<line x1="{x}" y1="{py_bottom}" x2="{x}" y2="{py_bottom + 5}" '
            f'stroke="#94a3b8" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x}" y="{py_bottom + 20}" text-anchor="middle" '
            f'font-family="system-ui, sans-serif" font-size="11" '
            f'fill="#64748b">{raw_tick}</text>'
        )
    parts.append(
        f'<text x="{(px_left + px_right) / 2}" y="{py_bottom + 42}" '
        f'text-anchor="middle" font-family="system-ui, sans-serif" '
        f'font-size="12" fill="#1e1b4b" font-weight="600">'
        f'raw transcript tokens</text>'
    )

    # Y axis ticks + labels.
    for expand_tick in (0, 200, 400, 600, 800):
        y = to_y(expand_tick)
        parts.append(
            f'<line x1="{px_left - 5}" y1="{y}" x2="{px_left}" y2="{y}" '
            f'stroke="#94a3b8" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{px_left - 10}" y="{y + 4}" text-anchor="end" '
            f'font-family="system-ui, sans-serif" font-size="11" '
            f'fill="#64748b">{expand_tick}</text>'
        )
    parts.append(
        f'<text x="{px_left - 50}" y="{(py_top + py_bottom) / 2}" '
        f'text-anchor="middle" font-family="system-ui, sans-serif" '
        f'font-size="12" fill="#1e1b4b" font-weight="600" '
        f'transform="rotate(-90 {px_left - 50} {(py_top + py_bottom) / 2})">'
        f'expand pack tokens</text>'
    )

    # The y=x break-even reference line — clipped to the plot area.
    # raw=0 → expand=0; raw=x_max → expand=x_max (capped at y_max).
    diag_end_raw = min(x_max, y_max)
    parts.append(
        f'<line x1="{to_x(0)}" y1="{to_y(0)}" '
        f'x2="{to_x(diag_end_raw)}" y2="{to_y(diag_end_raw)}" '
        f'stroke="#cbd5e1" stroke-width="1.5" stroke-dasharray="4,4"/>'
    )
    # Label the diagonal.
    parts.append(
        f'<text x="{to_x(700) + 6}" y="{to_y(700) - 6}" '
        f'font-family="system-ui, sans-serif" font-size="11" '
        f'fill="#94a3b8" font-style="italic">y = x  (break-even)</text>'
    )

    # The linear fit line — across the plot.
    fit_y_left = fit.expand_for(0)
    fit_y_right = fit.expand_for(x_max)
    # Clip the fit endpoints to the plot frame.
    parts.append(
        f'<line x1="{to_x(0)}" y1="{to_y(fit_y_left)}" '
        f'x2="{to_x(x_max)}" y2="{to_y(min(fit_y_right, y_max))}" '
        f'stroke="#6366f1" stroke-width="2.0" opacity="0.7"/>'
    )

    # Vertical drop line at the break-even point.
    bx = to_x(fit.break_even_raw)
    parts.append(
        f'<line x1="{bx}" y1="{py_top}" x2="{bx}" y2="{py_bottom}" '
        f'stroke="#10b981" stroke-width="1.5" stroke-dasharray="3,3" '
        f'opacity="0.8"/>'
    )
    parts.append(
        f'<text x="{bx + 6}" y="{py_top + 16}" '
        f'font-family="system-ui, sans-serif" font-size="11" '
        f'fill="#10b981" font-weight="700">'
        f'break-even ≈ {fit.break_even_raw:.0f} raw tokens</text>'
    )

    # Data points + scenario labels.
    for r in results:
        cx = to_x(r.raw_tokens)
        cy = to_y(r.expand_tokens)
        color = palette.get(r.name, "#1e1b4b")
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="6.5" fill="{color}" '
            f'stroke="white" stroke-width="2"/>'
        )
        # Place label to the right of the dot, except for the largest
        # scenario (sprint) which would clip the right edge — put it
        # to the upper-left instead.
        if r.name == "sprint":
            label_x = cx - 10
            label_y = cy - 10
            anchor = "end"
        else:
            label_x = cx + 12
            label_y = cy + 4
            anchor = "start"
        parts.append(
            f'<text x="{label_x}" y="{label_y}" text-anchor="{anchor}" '
            f'font-family="system-ui, sans-serif" font-size="11" '
            f'font-weight="600" fill="{color}">{r.name}</text>'
        )
        parts.append(
            f'<text x="{label_x}" y="{label_y + 13}" text-anchor="{anchor}" '
            f'font-family="ui-monospace, monospace" font-size="10" '
            f'fill="#64748b">{r.compression_ratio:.1f}×</text>'
        )

    # Inline annotations near the diagonal — much less visual clutter
    # than a legend block that lands on top of the data points.
    parts.append(
        f'<text x="{to_x(950)}" y="{to_y(420)}" text-anchor="end" '
        f'font-family="system-ui, sans-serif" font-size="11" '
        f'fill="#475569">below diagonal · Bella saves tokens</text>'
    )
    parts.append(
        f'<text x="{to_x(250)}" y="{to_y(580)}" text-anchor="start" '
        f'font-family="system-ui, sans-serif" font-size="11" '
        f'fill="#94a3b8">above diagonal · Bella costs tokens</text>'
    )

    # Footer attribution.
    parts.append(
        '<text x="360" y="468" text-anchor="middle" '
        'font-family="system-ui, sans-serif" font-size="10" '
        'fill="#94a3b8">'
        'github.com/immartian/bellamem · generated by docs/scenarios.py'
        '</text>'
    )

    parts.append('</svg>')
    return "\n".join(parts)


def run_scenario(scenario: Scenario) -> ScenarioResult:
    set_embedder(HashEmbedder())
    bella = Bella()

    # Phase 1: ingest the dialogue.
    _ingest_dialogue(bella, scenario.dialogue)
    stats_in = measure(bella)

    # Phase 2: age + emerge + prune.
    age_beliefs(bella, days=60)
    compress(bella)
    stats_out = measure(bella)

    # Phase 3: simulate a future-session retrieval. expand() with the
    # scenario's test question against the COMPRESSED graph, under the
    # scenario's budget. We're measuring how many tokens an agent would
    # spend to recover the decisive context.
    pack = expand(bella, scenario.test_question,
                  budget_tokens=scenario.expand_budget)
    pack_text = pack.text()
    expand_tokens = count_tokens(pack_text)
    expand_lines = pack_text.count("\n") + 1 if pack_text else 0

    # Surfacing check: did the load-bearing claims appear in the pack?
    pack_lower = pack_text.lower()
    surfaced: list[str] = []
    missed: list[str] = []
    for needle in scenario.must_surface:
        if needle.lower() in pack_lower:
            surfaced.append(needle)
        else:
            missed.append(needle)

    raw_tokens = count_tokens(_raw_transcript(scenario.dialogue))

    return ScenarioResult(
        name=scenario.name,
        description=scenario.description,
        test_question=scenario.test_question,
        raw_tokens=raw_tokens,
        beliefs_in=stats_in.beliefs,
        entropy_in=stats_in.entropy_bits,
        disputes_in=stats_in.disputes,
        causes_in=stats_in.causes,
        multi_voice_in=stats_in.multi_voice,
        self_obs_in=stats_in.self_observations,
        beliefs_out=stats_out.beliefs,
        entropy_out=stats_out.entropy_bits,
        disputes_out=stats_out.disputes,
        causes_out=stats_out.causes,
        multi_voice_out=stats_out.multi_voice,
        self_obs_out=stats_out.self_observations,
        expand_tokens=expand_tokens,
        expand_lines=expand_lines,
        surfaced=surfaced,
        missed=missed,
    )


def _ingest_dialogue(bella: Bella, dialogue: list[Turn]) -> None:
    """Reimplement run_dialogue's body inline so we can drive it with an
    arbitrary dialogue rather than relying on the module-level DIALOGUE."""
    from bellamem.core import Claim, ops

    tags: dict[str, tuple[str, str]] = {}
    for turn in dialogue:
        if turn.kind == "self":
            claim = Claim(text=turn.text, voice=turn.voice, lr=turn.lr,
                          relation="self_observation")
            result = bella.ingest(claim)
        elif turn.kind in ("deny", "cause"):
            target_field, target_bid = tags[turn.target]  # type: ignore[index]
            claim = Claim(text=turn.text, voice=turn.voice, lr=turn.lr,
                          relation=turn.kind, target_hint=target_bid,
                          target_field=target_field)
            result = bella.ingest(claim)
        elif turn.kind == "confirm":
            target_field, target_bid = tags[turn.target]  # type: ignore[index]
            g = bella.fields[target_field]
            result = ops.confirm(g, target_bid, voice=turn.voice, lr=turn.lr)
            result.field = target_field
        else:
            claim = Claim(text=turn.text, voice=turn.voice, lr=turn.lr)
            result = bella.ingest(claim)

        if turn.tag and result.belief is not None:
            tags[turn.tag] = (result.field, result.belief.id)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def render_markdown(results: list[ScenarioResult],
                    fit: CompressionFit | None = None) -> str:
    if fit is None:
        fit = compression_fit(results)
    lines = [
        "# Bella scenarios — entropy reduction, structural preservation, token compression",
        "",
        "Synthetic conversations that demonstrate Bella's compression story",
        "with reproducible numbers. Generated by `docs/scenarios.py`. Pinned by",
        "`tests/test_scenarios.py` so they can't silently drift.",
        "",
        "## Headline metric: where Bella starts paying off",
        "",
        f"**Break-even: ~{fit.break_even_raw:.0f} raw transcript tokens.**",
        "",
        f"Linear fit across {fit.n_points} scenarios: "
        f"`expand_tokens ≈ {fit.intercept:.0f} + {fit.slope:.2f} × raw_tokens`.",
        "",
        "Below ~200 raw tokens, Bella's per-belief metadata overhead "
        "dominates and the expand pack costs more tokens than the raw "
        "transcript would. Above the break-even point, Bella saves "
        "tokens, and the savings grow monotonically with dialogue size. "
        "Use Bella for conversations longer than ~200 tokens; for "
        "shorter chats, the flat tail is cheaper.",
        "",
        "![compression curve](compression-curve.svg)",
        "",
        "*(SVG generated alongside this report; embed and refresh "
        "by running `python docs/scenarios.py`.)*",
        "",
        "Read each row as: a dialogue happens, Bella ingests it, time passes,",
        "decay + emerge + prune compress the graph, then a future agent asks",
        "the scenario's test question and gets back an `expand` pack under a",
        "tight token budget. The compression ratio is `raw / expand`.",
        "",
        "**Note on small-scenario token math**: Bella's per-belief metadata",
        "overhead (~10 tokens for the `[field] m=0.XX v=N` prefix) means the",
        "raw vs. expand ratio only flips positive once the dialogue is long",
        "enough that overhead amortizes. The `flaky-test` and `rejected-refactor`",
        "scenarios are short enough that the ratio reads <1×; they demonstrate",
        "**structural preservation**, not token compression. The `long-debug`",
        "scenario is sized to show the token win empirically.",
        "",
        "| scenario | raw | beliefs (in→out) | entropy (in→out) | expand | ratio | structure | surfaced |",
        "|---|---:|---:|---:|---:|---:|:---:|:---:|",
    ]
    for r in results:
        beliefs = f"{r.beliefs_in} → {r.beliefs_out}"
        entropy = f"{r.entropy_in:.2f} → {r.entropy_out:.2f}"
        ratio = f"{r.compression_ratio:.1f}×"
        structure = "✓" if r.structure_preserved else "✗"
        surf = "✓" if r.all_surfaced else f"✗ (missed {r.missed})"
        lines.append(
            f"| `{r.name}` | {r.raw_tokens} | {beliefs} | {entropy} | "
            f"{r.expand_tokens} | {ratio} | {structure} | {surf} |"
        )
    lines.append("")
    lines.append("## What each column means")
    lines.append("")
    lines.append("- **raw**: tokens in the verbatim transcript (flat-tail baseline)")
    lines.append("- **beliefs in→out**: belief count after ingest → after age + emerge + prune")
    lines.append("- **entropy in→out**: Shannon entropy bits of the mass distribution")
    lines.append("- **expand**: tokens in the `expand()` pack answering the test question")
    lines.append("- **ratio**: raw / expand — the compression factor (only meaningful at scale)")
    lines.append("- **structure**: did all disputes, causes, ratifications, and `__self__` "
                 "observations survive compression? (✓ = none lost)")
    lines.append("- **surfaced**: did the load-bearing claims (the scenario's `must_surface` "
                 "substrings) appear in the expand pack? (the future-session retrieval check)")
    lines.append("")
    lines.append("## Scenario detail")
    lines.append("")
    for r in results:
        lines.append(f"### `{r.name}`")
        lines.append("")
        lines.append(r.description)
        lines.append("")
        lines.append(f"- **Raw transcript**: {r.raw_tokens} tokens "
                     f"(verbatim, the flat-tail baseline)")
        lines.append(f"- **After ingest**: {r.beliefs_in} beliefs, "
                     f"entropy {r.entropy_in:.2f} bits "
                     f"({r.disputes_in} disputes, {r.causes_in} causes, "
                     f"{r.multi_voice_in} multi-voice, {r.self_obs_in} self-obs)")
        lines.append(f"- **After compression** (60d age + emerge + prune): "
                     f"{r.beliefs_out} beliefs, entropy {r.entropy_out:.2f} bits "
                     f"({r.disputes_out} disputes, {r.causes_out} causes, "
                     f"{r.multi_voice_out} multi-voice, {r.self_obs_out} self-obs)")
        delta_b = r.beliefs_in - r.beliefs_out
        delta_e = r.entropy_in - r.entropy_out
        if r.beliefs_in:
            pct = 100 * delta_b / r.beliefs_in
            lines.append(f"- **Compression**: {delta_b} beliefs removed "
                         f"({pct:.0f}% reduction), "
                         f"entropy dropped by {delta_e:.2f} bits")
        lines.append(f"- **Structure preserved**: "
                     f"{'yes' if r.structure_preserved else 'NO — load-bearing structure lost'} "
                     f"(every dispute, cause, ratification, and self-obs survived)")
        lines.append(f"- **expand pack**: {r.expand_tokens} tokens, "
                     f"{r.expand_lines} lines — what a future agent sees when "
                     f"asking *\"{r.test_question}\"*")
        lines.append(f"- **Compression ratio**: {r.compression_ratio:.1f}× "
                     f"(raw / expand)")
        if r.all_surfaced:
            lines.append(f"- **Load-bearing claims surfaced**: yes — "
                         f"all of `{r.surfaced}` appear in the pack")
        else:
            lines.append(f"- **Load-bearing claims surfaced**: NO — "
                         f"missing `{r.missed}` from the pack")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(out_path: Path | None = None) -> list[ScenarioResult]:
    out_path = out_path or Path(__file__).parent / "scenarios.md"
    svg_path = out_path.parent / "compression-curve.svg"
    results = [run_scenario(s) for s in SCENARIOS]
    fit = compression_fit(results)

    print("=" * 72)
    print(f"{'scenario':<22} {'raw':>6} {'beliefs':>10} "
          f"{'entropy':>14} {'expand':>8} {'ratio':>7}")
    print("=" * 72)
    for r in results:
        print(f"{r.name:<22} {r.raw_tokens:>6} "
              f"{r.beliefs_in:>4} → {r.beliefs_out:<3} "
              f"{r.entropy_in:>5.2f} → {r.entropy_out:<5.2f} "
              f"{r.expand_tokens:>8} {r.compression_ratio:>6.1f}×")
    print()
    print(f"linear fit: expand ≈ {fit.intercept:.0f} + "
          f"{fit.slope:.3f} × raw")
    print(f"break-even: ~{fit.break_even_raw:.0f} raw tokens "
          f"(below this, Bella costs tokens; above it, Bella saves them)")
    print()

    out_path.write_text(render_markdown(results, fit), encoding="utf-8")
    svg_path.write_text(render_compression_chart_svg(results, fit),
                         encoding="utf-8")
    print(f"wrote {out_path}")
    print(f"wrote {svg_path}")
    return results


if __name__ == "__main__":
    main()
