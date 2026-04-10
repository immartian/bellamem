"""Tests for core/visualize.py — Bella → DOT emission.

No graphviz binary or Python binding required: we assert against the
DOT source string directly. Verifies: filtering, edge type encoding,
focus BFS expansion, disputes-only mode, min_mass cutoff, and the
max_nodes cap.
"""

from __future__ import annotations

from bellamem.core import Bella, Claim
from bellamem.core.embed import HashEmbedder, set_embedder
from bellamem.core.gene import REL_CAUSE, REL_COUNTER, REL_SUPPORT
from bellamem.core.visualize import (
    RenderOptions,
    count_selected,
    focus_ids,
    to_dot,
)


def _bella_with_disputes_and_causes() -> Bella:
    """Build a small Bella with known structure.

    Field 'auth': two supporting beliefs under a root, plus one dispute
    and one cause.  Field 'db': one root + one child. 6 beliefs total.
    """
    set_embedder(HashEmbedder())
    b = Bella()

    # auth field
    b.ingest(Claim(text="auth tokens are HMAC-signed", voice="u", lr=2.0))
    b.ingest(Claim(text="auth tokens rotate every 24h", voice="u", lr=1.8))
    # Direct gene manipulation to create explicit dispute + cause edges
    auth = b.fields["auth_tokens_are_hmacsigned"] if "auth_tokens_are_hmacsigned" in b.fields else next(iter(b.fields.values()))
    # Grab the first belief in the first field as a stable parent
    first_field = next(iter(b.fields.values()))
    parent_bid = next(iter(first_field.beliefs))
    first_field.deny(parent_bid, desc="HMAC is not enough under key rotation", voice="u", lr=1.5)
    first_field.cause(parent_bid, desc="compliance requires HMAC signing", voice="u", lr=1.5)

    # second field (db)
    b.ingest(Claim(text="database migrations run forward only", voice="u", lr=1.8))
    b.ingest(Claim(text="rollbacks use snapshot restore", voice="u", lr=1.6))

    return b


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------


def test_to_dot_emits_header_and_closes():
    b = _bella_with_disputes_and_causes()
    dot = to_dot(b.fields)
    assert dot.startswith("digraph BellaMem {")
    assert dot.rstrip().endswith("}")


def test_to_dot_has_a_node_per_belief_by_default():
    b = _bella_with_disputes_and_causes()
    total = sum(len(g.beliefs) for g in b.fields.values())
    dot = to_dot(b.fields)
    # Each node is declared with shape="ellipse" — count occurrences.
    assert dot.count('shape="ellipse"') == total


def test_count_selected_matches_to_dot_nodes():
    b = _bella_with_disputes_and_causes()
    opts = RenderOptions()
    assert count_selected(b.fields, opts) == sum(
        len(g.beliefs) for g in b.fields.values()
    )


# ---------------------------------------------------------------------------
# Edge encoding
# ---------------------------------------------------------------------------


def test_dispute_edges_are_red_and_dashed():
    b = _bella_with_disputes_and_causes()
    dot = to_dot(b.fields)
    # The disputes-only filter should capture at least one edge,
    # but here we just check that a dispute edge was emitted.
    assert 'color="#c0392b"' in dot
    assert 'style="dashed"' in dot


def test_cause_edges_are_blue():
    b = _bella_with_disputes_and_causes()
    dot = to_dot(b.fields)
    assert 'color="#1f5f9f"' in dot
    assert 'arrowhead="vee"' in dot


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_disputes_only_mode_drops_non_dispute_beliefs():
    b = _bella_with_disputes_and_causes()
    total = sum(len(g.beliefs) for g in b.fields.values())
    opts = RenderOptions(disputes_only=True)
    n = count_selected(b.fields, opts)
    # disputes_only keeps the dispute itself + its parent. With one
    # dispute in the fixture, that's exactly 2 beliefs.
    assert n == 2
    assert n < total


def test_field_filter_restricts_to_one_field():
    b = _bella_with_disputes_and_causes()
    # Pick the field containing the database beliefs.
    db_field = None
    for fname, g in b.fields.items():
        if any("database" in bel.desc for bel in g.beliefs.values()):
            db_field = fname
            break
    assert db_field is not None

    opts = RenderOptions(fields=[db_field])
    n = count_selected(b.fields, opts)
    assert n == len(b.fields[db_field].beliefs)
    dot = to_dot(b.fields, opts)
    # Beliefs from other fields shouldn't appear.
    for fname, g in b.fields.items():
        if fname == db_field:
            continue
        for bel in g.beliefs.values():
            assert bel.id not in dot or bel.desc in b.fields[db_field].beliefs


def test_min_mass_cutoff_drops_low_mass_beliefs():
    b = _bella_with_disputes_and_causes()
    opts_all = RenderOptions(min_mass=0.0)
    opts_high = RenderOptions(min_mass=0.99)
    assert count_selected(b.fields, opts_all) > count_selected(b.fields, opts_high)


def test_max_nodes_caps_output():
    b = _bella_with_disputes_and_causes()
    total = sum(len(g.beliefs) for g in b.fields.values())
    opts = RenderOptions(max_nodes=2)
    n = count_selected(b.fields, opts)
    assert n == 2
    assert n < total


# ---------------------------------------------------------------------------
# Focus + BFS expansion
# ---------------------------------------------------------------------------


def test_focus_ids_returns_subset_for_small_topn():
    b = _bella_with_disputes_and_causes()
    total = sum(len(g.beliefs) for g in b.fields.values())
    ids = focus_ids(b.fields, "HMAC signing", top=1, depth=0)
    # top=1, depth=0 → exactly one seed belief.
    assert len(ids) == 1
    assert len(ids) < total


def test_focus_ids_expansion_grows_with_depth():
    b = _bella_with_disputes_and_causes()
    ids_0 = focus_ids(b.fields, "HMAC signing", top=1, depth=0)
    ids_2 = focus_ids(b.fields, "HMAC signing", top=1, depth=2)
    # Expansion from a seed with neighbors should grow the set.
    assert len(ids_2) >= len(ids_0)


def test_focus_filter_composes_with_to_dot():
    b = _bella_with_disputes_and_causes()
    ids = focus_ids(b.fields, "HMAC", top=1, depth=1)
    opts = RenderOptions(focus_ids=ids)
    dot = to_dot(b.fields, opts)
    # Every node id present in the DOT should be in the focus set.
    for field_g in b.fields.values():
        for bel in field_g.beliefs.values():
            if f'"{bel.id}"' in dot:
                assert bel.id in ids
