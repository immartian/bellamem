"""bellamem bench — empirical comparison of context-pack strategies.

Runs a fixed corpus of (query, expected-facts) items through several
context-builder strategies under a shared token budget, then reports
how often each strategy's pack contains the decisive facts.

Contenders:
    flat_tail        last N turns of the transcript (no EW, no ranking)
    compact          LLM-summarized transcript
    rag_topk         top-k turns by cosine similarity to the query
    expand           bellamem generic mass-weighted expand()
    before_edit      bellamem structured 5-layer expand_before_edit()

Metrics:
    exact            substring match of any expected fact in any pack line
    embed            cosine(expected, line) >= threshold for any line-pair

The benchmark is deliberately self-contained: corpus is in
bench_corpus.py, transcript lives on disk. This is a retrieval test
under fixed budget, not an agent-behavior test. See the session
discussion (turn: "empirically compare") for the scope caveats.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from .bench_corpus import BENCH_ITEMS, BenchItem
from .core.embed import cosine, current_embedder
from .core.expand import expand, expand_before_edit
from .core.tokens import count_tokens, tail_tokens

if TYPE_CHECKING:
    from .core.bella import Bella


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class BenchPack:
    contender: str
    focus: str
    lines: list[str]
    tokens_used: int
    cost_usd: float = 0.0

    def as_text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class HitResult:
    item_id: str
    contender: str
    hit_exact: bool
    hit_embed: bool
    hit_llm: bool | None  # None if LLM judge was not run
    tokens_used: int


@dataclass
class BenchReport:
    items: list[BenchItem]
    results: dict[str, dict[str, HitResult]]  # item_id → contender → HitResult
    avg_tokens: dict[str, float]              # contender → avg tokens used
    exact_hit_rate: dict[str, float]          # contender → 0..1
    embed_hit_rate: dict[str, float]          # contender → 0..1
    llm_hit_rate: dict[str, float] | None = None  # None if LLM judge disabled


# ---------------------------------------------------------------------------
# Transcript helpers — shared by flat_tail, compact, rag
# ---------------------------------------------------------------------------

def _read_transcript_turns(transcript_path: str) -> list[tuple[str, str]]:
    """Return [(voice, text), ...] for each user/assistant turn in order."""
    if not transcript_path or not os.path.exists(transcript_path):
        return []
    out: list[tuple[str, str]] = []
    with open(transcript_path) as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t not in ("user", "assistant"):
                continue
            content = (msg.get("message") or {}).get("content")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        parts.append(blk.get("text", ""))
                text = "\n".join(parts)
            if text.strip():
                voice = "user" if t == "user" else "assistant"
                out.append((voice, text))
    return out


def _fit_budget(items: list[str], budget_tokens: int) -> tuple[list[str], int]:
    """Greedily fit strings under a real token budget."""
    selected: list[str] = []
    used_tokens = 0
    for s in items:
        cost = count_tokens(s) + 1  # +1 for the join separator
        if used_tokens + cost > budget_tokens:
            break
        selected.append(s)
        used_tokens += cost
    return selected, used_tokens


# ---------------------------------------------------------------------------
# Contender 1 — flat_tail: last-N turns
# ---------------------------------------------------------------------------

def contender_flat_tail(item: BenchItem, budget_tokens: int,
                        *, transcript_turns: list[tuple[str, str]],
                        **kw) -> BenchPack:
    """Classic flat-tail context window: the last N tokens of the transcript.

    Simulates what happens when an agent is handed "the last N tokens of
    history" with no structure — which is what Claude Code / Cursor
    context window management does under the hood.

    Uses the real tokenizer (tiktoken if installed) so the budget is
    enforced precisely, not by a char-count estimate.
    """
    full = "\n\n".join(f"[{v}] {t}" for v, t in transcript_turns)
    tail = tail_tokens(full, budget_tokens)
    lines = [ln for ln in tail.split("\n\n") if ln.strip()]
    return BenchPack(
        contender="flat_tail",
        focus=item.query,
        lines=lines,
        tokens_used=count_tokens(tail),
    )


# ---------------------------------------------------------------------------
# Contender 2 — compact: LLM summary of the transcript
# ---------------------------------------------------------------------------

_COMPACT_SYSTEM = (
    "You compress long chat transcripts into concise summaries that "
    "preserve load-bearing decisions, invariants, rejected approaches, "
    "and causes of behavior. Prefer specific rules over prose. "
    "Use bullet points."
)

_COMPACT_USER_TEMPLATE = """Summarize the following chat transcript in about {target_tokens}
tokens. Preserve: specific decisions, named principles, rejected
approaches, and causal claims. Drop: pleasantries, tool output, and
long code blocks. Use bullet points.

Transcript:
\"\"\"
{text}
\"\"\"
"""


def contender_compact(item: BenchItem, budget_tokens: int,
                      *, transcript_turns: list[tuple[str, str]],
                      openai_client, model: str = "gpt-4o-mini",
                      compact_cache: dict,
                      **kw) -> BenchPack:
    """Ask an LLM to compress the whole transcript into a budget-sized summary.

    The summary is cached per (model, budget, transcript_hash) so running
    the bench repeatedly doesn't re-pay the compact cost.
    """
    # Build full transcript text
    full = "\n\n".join(f"[{v}] {t}" for v, t in transcript_turns)
    # Truncate source if huge — keep the most recent 16k chars
    if len(full) > 16000:
        full = full[-16000:]

    import hashlib
    key = hashlib.md5(f"{model}|{budget_tokens}|{full}".encode()).hexdigest()
    if key in compact_cache:
        summary = compact_cache[key]
        cost = 0.0
    else:
        target_tokens = int(budget_tokens * 0.9)
        user_prompt = _COMPACT_USER_TEMPLATE.format(
            target_tokens=target_tokens, text=full
        )
        resp = openai_client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=min(1500, budget_tokens + 200),
            messages=[
                {"role": "system", "content": _COMPACT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
        summary = (resp.choices[0].message.content or "").strip()
        compact_cache[key] = summary
        # Rough cost estimate: gpt-4o-mini @ $0.15/M in, $0.60/M out
        in_tokens = len(user_prompt) // 4
        out_tokens = len(summary) // 4
        cost = in_tokens * 0.15e-6 + out_tokens * 0.60e-6

    # Treat each line of the summary as a pack line
    lines = [ln.strip() for ln in summary.splitlines() if ln.strip()]
    used_tokens = sum(count_tokens(ln) for ln in lines)
    return BenchPack(
        contender="compact",
        focus=item.query,
        lines=lines,
        tokens_used=used_tokens,
        cost_usd=cost,
    )


# ---------------------------------------------------------------------------
# Contender 3 — rag_topk: top-k turns by cosine to query
# ---------------------------------------------------------------------------

def contender_rag_topk(item: BenchItem, budget_tokens: int,
                       *, transcript_turns: list[tuple[str, str]],
                       turn_embeddings: list[list[float]],
                       **kw) -> BenchPack:
    """Classic RAG: embed each turn, rank by cosine to the query, pack top-k."""
    emb = current_embedder()
    q_vec = emb.embed(item.query)
    scored: list[tuple[float, int]] = []
    for i, tv in enumerate(turn_embeddings):
        if tv:
            scored.append((cosine(q_vec, tv), i))
    scored.sort(key=lambda t: t[0], reverse=True)

    used_tokens = 0
    lines: list[str] = []
    for score, idx in scored:
        voice, text = transcript_turns[idx]
        entry = f"[{voice} sim={score:.2f}] {text}"
        entry_cost = count_tokens(entry)
        if used_tokens + entry_cost > budget_tokens:
            continue  # try smaller candidates
        lines.append(entry)
        used_tokens += entry_cost
        if used_tokens >= int(budget_tokens * 0.95):
            break
    return BenchPack(
        contender="rag_topk",
        focus=item.query,
        lines=lines,
        tokens_used=used_tokens,
    )


# ---------------------------------------------------------------------------
# Contender 4 — bellamem expand()
# ---------------------------------------------------------------------------

def contender_bellamem_expand(item: BenchItem, budget_tokens: int,
                              *, bella: "Bella", **kw) -> BenchPack:
    pack = expand(bella, item.query, budget_tokens=budget_tokens)
    lines = [ln.render() for ln in pack.lines]
    return BenchPack(
        contender="expand",
        focus=item.query,
        lines=lines,
        tokens_used=pack.used_tokens(),
    )


# ---------------------------------------------------------------------------
# Contender 5 — bellamem expand_before_edit()
# ---------------------------------------------------------------------------

def contender_bellamem_before_edit(item: BenchItem, budget_tokens: int,
                                    *, bella: "Bella", **kw) -> BenchPack:
    pack = expand_before_edit(
        bella, item.query,
        budget_tokens=budget_tokens,
        focus_entity=item.focus_entity,
    )
    lines = [ln.render() for ln in pack.lines]
    return BenchPack(
        contender="before_edit",
        focus=item.query,
        lines=lines,
        tokens_used=pack.used_tokens(),
    )


CONTENDERS: dict[str, Callable] = {
    "flat_tail": contender_flat_tail,
    "compact": contender_compact,
    "rag_topk": contender_rag_topk,
    "expand": contender_bellamem_expand,
    "before_edit": contender_bellamem_before_edit,
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def hit_exact(pack: BenchPack, expected: list[str]) -> bool:
    text = pack.as_text().lower()
    return any(e.lower() in text for e in expected)


def hit_embed(pack: BenchPack, expected: list[str],
              *, threshold: float = 0.40) -> bool:
    """Union of exact match and cosine semantic match.

    embed is definitionally a superset of exact — a paraphrase that
    contains the exact string is still a paraphrase. Threshold is 0.40
    because we're comparing short facts to longer pack lines, and the
    cosine averages down across the length mismatch.
    """
    if hit_exact(pack, expected):
        return True
    emb = current_embedder()
    if not pack.lines:
        return False
    e_vecs = [emb.embed(e) for e in expected]
    for line in pack.lines:
        lv = emb.embed(line)
        for ev in e_vecs:
            if cosine(lv, ev) >= threshold:
                return True
    return False


# ---------------------------------------------------------------------------
# LLM judge metric
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = (
    "You judge whether a context pack contains information sufficient to "
    "answer a question correctly. You are deliberately conservative — "
    "if the pack contains the key fact in any phrasing (paraphrased, "
    "inverted, or expressed through a concrete example), answer YES. "
    "Only answer NO when the decisive information is truly absent."
)

_JUDGE_USER_TEMPLATE = """Question:
\"\"\"
{query}
\"\"\"

Pack content:
\"\"\"
{pack_text}
\"\"\"

Relevant reference phrases that would answer the question (for your
orientation — semantic match counts, not substring match):
{reference_list}

Does the pack contain information sufficient to answer the question
correctly? Return strict JSON: {{"sufficient": true|false, "why": "..."}}.
"""


def hit_llm_judge(pack: BenchPack, item: BenchItem, *,
                  openai_client, model: str,
                  cache: dict) -> bool:
    """One gpt-4o-mini call per (item, pack). Cached by content hash."""
    if not pack.lines:
        return False
    pack_text = pack.as_text()[:6000]  # bound input size
    import hashlib
    key = hashlib.md5(
        f"{model}|{item.query}|{pack_text}|{'|'.join(item.expected_any_of)}".encode()
    ).hexdigest()
    if key in cache:
        return bool(cache[key])
    reference_list = "\n".join(f"  - {e}" for e in item.expected_any_of)
    user = _JUDGE_USER_TEMPLATE.format(
        query=item.query,
        pack_text=pack_text,
        reference_list=reference_list,
    )
    resp = openai_client.chat.completions.create(
        model=model,
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=200,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    content = (resp.choices[0].message.content or "").strip()
    result = json.loads(content)
    hit = bool(result.get("sufficient", False))
    cache[key] = hit
    return hit


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_bench(
    bella: "Bella",
    *,
    transcript_path: str,
    budget_tokens: int = 1200,
    contenders: Optional[list[str]] = None,
    openai_client=None,
    model: str = "gpt-4o-mini",
    use_llm_judge: bool = False,
) -> BenchReport:
    """Run the full bench and return a BenchReport.

    If use_llm_judge is True, also run a third metric (llm_judge) using
    one gpt-4o-mini call per (item, contender) to semantically decide
    whether the pack contains sufficient information. Requires openai_client.
    """
    items = BENCH_ITEMS
    contender_names = contenders or list(CONTENDERS.keys())

    # Precompute shared resources
    transcript_turns = _read_transcript_turns(transcript_path)
    emb = current_embedder()
    # Embed each turn once (cached via the DiskCacheEmbedder if active)
    turn_embeddings = [emb.embed(t) for _v, t in transcript_turns]
    compact_cache: dict = {}
    judge_cache: dict = {}

    results: dict[str, dict[str, HitResult]] = {}
    tokens_accum: dict[str, list[int]] = {c: [] for c in contender_names}
    exact_hits: dict[str, int] = {c: 0 for c in contender_names}
    embed_hits: dict[str, int] = {c: 0 for c in contender_names}
    llm_hits: dict[str, int] = {c: 0 for c in contender_names}

    for item in items:
        results[item.id] = {}
        for name in contender_names:
            fn = CONTENDERS[name]
            pack = fn(
                item, budget_tokens,
                transcript_turns=transcript_turns,
                turn_embeddings=turn_embeddings,
                bella=bella,
                openai_client=openai_client,
                model=model,
                compact_cache=compact_cache,
            )
            he = hit_exact(pack, item.expected_any_of)
            hm = hit_embed(pack, item.expected_any_of)
            hl: bool | None = None
            if use_llm_judge and openai_client is not None:
                hl = hit_llm_judge(
                    pack, item,
                    openai_client=openai_client,
                    model=model,
                    cache=judge_cache,
                )
                if hl:
                    llm_hits[name] += 1
            results[item.id][name] = HitResult(
                item_id=item.id,
                contender=name,
                hit_exact=he,
                hit_embed=hm,
                hit_llm=hl,
                tokens_used=pack.tokens_used,
            )
            tokens_accum[name].append(pack.tokens_used)
            if he:
                exact_hits[name] += 1
            if hm:
                embed_hits[name] += 1

    n = max(1, len(items))
    report = BenchReport(
        items=items,
        results=results,
        avg_tokens={c: sum(tokens_accum[c]) / max(1, len(tokens_accum[c]))
                    for c in contender_names},
        exact_hit_rate={c: exact_hits[c] / n for c in contender_names},
        embed_hit_rate={c: embed_hits[c] / n for c in contender_names},
    )
    if use_llm_judge:
        report.llm_hit_rate = {c: llm_hits[c] / n for c in contender_names}
    return report


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def render_report(report: BenchReport) -> str:
    contenders = list(report.exact_hit_rate.keys())
    lines: list[str] = []
    lines.append("bellamem bench")
    lines.append("=" * 72)
    lines.append(f"items: {len(report.items)}")
    lines.append("")

    # Per-item table
    header = "id     category      " + " ".join(f"{c:>12}" for c in contenders)
    lines.append(header)
    lines.append("-" * len(header))
    for item in report.items:
        row = f"{item.id:<6} {item.category:<13}"
        for c in contenders:
            hr = report.results[item.id][c]
            # Mark priority: LLM judge > exact > embed > miss
            if hr.hit_llm is True:
                mark = "J"
            elif hr.hit_exact:
                mark = "✓"
            elif hr.hit_embed:
                mark = "~"
            else:
                mark = "✗"
            row += f" {mark:>12}"
        lines.append(row)
    lines.append("")

    # Summary row
    lines.append("## summary")
    lines.append(f"{'metric':<17} " + " ".join(f"{c:>12}" for c in contenders))
    lines.append("-" * (17 + 13 * len(contenders)))

    rate_row = f"{'exact hit rate':<17}"
    for c in contenders:
        pct = report.exact_hit_rate[c] * 100
        rate_row += f" {pct:>10.0f} %"
    lines.append(rate_row)

    embed_row = f"{'embed hit rate':<17}"
    for c in contenders:
        pct = report.embed_hit_rate[c] * 100
        embed_row += f" {pct:>10.0f} %"
    lines.append(embed_row)

    if report.llm_hit_rate is not None:
        llm_row = f"{'llm judge rate':<17}"
        for c in contenders:
            pct = report.llm_hit_rate[c] * 100
            llm_row += f" {pct:>10.0f} %"
        lines.append(llm_row)

    tok_row = f"{'avg tokens used':<17}"
    for c in contenders:
        tok_row += f" {report.avg_tokens[c]:>12.0f}"
    lines.append(tok_row)
    lines.append("")
    if report.llm_hit_rate is not None:
        lines.append("legend: J llm-judge hit  ✓ exact hit  ~ embed-only  ✗ miss")
    else:
        lines.append("legend: ✓ exact hit  ~ embed-only hit  ✗ miss")
    return "\n".join(lines)
