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
from .core.expand import expand, expand_before_edit, CHARS_PER_TOKEN

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
    tokens_used: int


@dataclass
class BenchReport:
    items: list[BenchItem]
    results: dict[str, dict[str, HitResult]]  # item_id → contender → HitResult
    avg_tokens: dict[str, float]              # contender → avg tokens used
    exact_hit_rate: dict[str, float]          # contender → 0..1
    embed_hit_rate: dict[str, float]          # contender → 0..1


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
    """Greedily fit strings under a character-based token budget."""
    budget_chars = budget_tokens * CHARS_PER_TOKEN
    selected: list[str] = []
    used_chars = 0
    for s in items:
        cost = len(s) + 2
        if used_chars + cost > budget_chars:
            break
        selected.append(s)
        used_chars += cost
    return selected, used_chars // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Contender 1 — flat_tail: last-N turns
# ---------------------------------------------------------------------------

def contender_flat_tail(item: BenchItem, budget_tokens: int,
                        *, transcript_turns: list[tuple[str, str]],
                        **kw) -> BenchPack:
    """Classic flat-tail context window: the last N tokens of the transcript.

    Simulates what happens when an agent is handed "the last N characters
    of history" with no structure — which is what Claude Code / Cursor
    context window management does under the hood.
    """
    full = "\n\n".join(f"[{v}] {t}" for v, t in transcript_turns)
    budget_chars = budget_tokens * CHARS_PER_TOKEN
    tail = full[-budget_chars:] if len(full) > budget_chars else full
    lines = [ln for ln in tail.split("\n\n") if ln.strip()]
    return BenchPack(
        contender="flat_tail",
        focus=item.query,
        lines=lines,
        tokens_used=len(tail) // CHARS_PER_TOKEN,
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
    used_tokens = sum(len(ln) for ln in lines) // CHARS_PER_TOKEN
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
    # Score each turn
    scored: list[tuple[float, int]] = []
    for i, tv in enumerate(turn_embeddings):
        if tv:
            scored.append((cosine(q_vec, tv), i))
    scored.sort(key=lambda t: t[0], reverse=True)

    budget_chars = budget_tokens * CHARS_PER_TOKEN
    used = 0
    lines: list[str] = []
    for score, idx in scored:
        voice, text = transcript_turns[idx]
        entry = f"[{voice} sim={score:.2f}] {text}"
        if used + len(entry) > budget_chars:
            continue  # try smaller candidates
        lines.append(entry)
        used += len(entry)
        if used >= budget_chars * 0.95:
            break
    return BenchPack(
        contender="rag_topk",
        focus=item.query,
        lines=lines,
        tokens_used=used // CHARS_PER_TOKEN,
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
) -> BenchReport:
    """Run the full bench and return a BenchReport."""
    items = BENCH_ITEMS
    contender_names = contenders or list(CONTENDERS.keys())

    # Precompute shared resources
    transcript_turns = _read_transcript_turns(transcript_path)
    emb = current_embedder()
    # Embed each turn once (cached via the DiskCacheEmbedder if active)
    turn_embeddings = [emb.embed(t) for _v, t in transcript_turns]
    compact_cache: dict = {}

    results: dict[str, dict[str, HitResult]] = {}
    tokens_accum: dict[str, list[int]] = {c: [] for c in contender_names}
    exact_hits: dict[str, int] = {c: 0 for c in contender_names}
    embed_hits: dict[str, int] = {c: 0 for c in contender_names}

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
            results[item.id][name] = HitResult(
                item_id=item.id,
                contender=name,
                hit_exact=he,
                hit_embed=hm,
                tokens_used=pack.tokens_used,
            )
            tokens_accum[name].append(pack.tokens_used)
            if he:
                exact_hits[name] += 1
            if hm:
                embed_hits[name] += 1

    n = max(1, len(items))
    return BenchReport(
        items=items,
        results=results,
        avg_tokens={c: sum(tokens_accum[c]) / max(1, len(tokens_accum[c]))
                    for c in contender_names},
        exact_hit_rate={c: exact_hits[c] / n for c in contender_names},
        embed_hit_rate={c: embed_hits[c] / n for c in contender_names},
    )


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
            mark = "✓" if hr.hit_exact else ("~" if hr.hit_embed else "✗")
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

    tok_row = f"{'avg tokens used':<17}"
    for c in contenders:
        tok_row += f" {report.avg_tokens[c]:>12.0f}"
    lines.append(tok_row)
    lines.append("")
    lines.append("legend: ✓ exact hit  ~ embed-only hit  ✗ miss")
    return "\n".join(lines)
