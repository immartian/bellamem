"""Smoke tests — verify the package imports and the public API is callable.

These intentionally use the HashEmbedder (the zero-dep default) so CI
can run them without any API keys or model downloads. They are not
coverage tests; they're a "does anything work at all" guard.

Run: pytest tests/
"""

from __future__ import annotations

from bellamem.core import Bella, Claim, expand, save, load
from bellamem.core.bella import SELF_MODEL_FIELD, is_reserved_field
from bellamem.core.embed import HashEmbedder, set_embedder, current_embedder
from bellamem.core.expand import expand_before_edit
from bellamem.core.gene import mass_of


def _fresh_bella() -> Bella:
    """Return a Bella with the stdlib HashEmbedder set as default.

    Tests must not touch the real OpenAI embedder. Resetting the
    module-level default is fine because each test creates its own
    Bella and we don't share state.
    """
    set_embedder(HashEmbedder())
    return Bella()


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------

def test_public_api_imports():
    """core's __init__ exports the documented names."""
    from bellamem import core
    for name in ("Bella", "Claim", "Belief", "Gene", "expand",
                 "save", "load", "seed_principles", "PRINCIPLES_FIELD"):
        assert hasattr(core, name), f"missing: core.{name}"


def test_cli_entry_point_imports():
    """The console script target imports cleanly."""
    from bellamem import cli
    assert callable(cli.main)


# ---------------------------------------------------------------------------
# Core math
# ---------------------------------------------------------------------------

def test_mass_of_sigmoid():
    assert mass_of(0.0) == 0.5
    assert mass_of(10.0) > 0.99
    assert mass_of(-10.0) < 0.01


def test_reserved_field_detection():
    assert is_reserved_field("__principles__")
    assert is_reserved_field("__self__")
    assert not is_reserved_field("authentication")
    assert not is_reserved_field("database")


# ---------------------------------------------------------------------------
# Ingest + expand round-trip
# ---------------------------------------------------------------------------

def test_ingest_creates_field_and_belief():
    b = _fresh_bella()
    r = b.ingest(Claim(text="errors should never be silently swallowed",
                       voice="user", lr=2.5, relation="add"))
    assert r.belief is not None
    assert r.field is not None
    assert r.field in b.fields

    g = b.fields[r.field]
    assert r.belief.id in g.beliefs
    assert "swallow" in g.beliefs[r.belief.id].desc.lower()


def test_ingest_accumulates_same_claim():
    """Same desc + same parent → same belief id → mass accumulates."""
    b = _fresh_bella()
    r1 = b.ingest(Claim(text="use sqlite for local persistence",
                        voice="user", lr=2.0))
    r2 = b.ingest(Claim(text="use sqlite for local persistence",
                        voice="assistant", lr=1.5))
    assert r1.belief is not None and r2.belief is not None
    assert r1.belief.id == r2.belief.id                     # same belief
    assert r1.belief.n_voices == 2                          # two distinct voices


def test_self_observation_routes_to_reserved_field():
    b = _fresh_bella()
    r = b.ingest(Claim(
        text="I tend to reach for try/except when I hit a KeyError",
        voice="assistant", lr=1.5, relation="self_observation",
    ))
    assert r.belief is not None
    assert r.field == SELF_MODEL_FIELD
    assert SELF_MODEL_FIELD in b.fields


def test_expand_returns_pack_under_budget():
    b = _fresh_bella()
    for text in [
        "tokens must never be stored in session cookies",
        "jwt is the canonical auth token format",
        "password hashes use argon2id by default",
        "login rate limit is 5 attempts per minute",
    ]:
        b.ingest(Claim(text=text, voice="user", lr=2.0))

    pack = expand(b, "how should auth tokens be stored", budget_tokens=300)
    assert pack.lines  # non-empty
    assert pack.used_tokens() <= 300


def test_expand_before_edit_surfaces_invariants():
    """before_edit loads its invariants layer from mass-floored beliefs.

    We construct a principle-like belief directly (mass_floor=0.95) so
    the test doesn't depend on seeding from PRINCIPLES.md. The before_edit
    pack's invariants layer requires mass ≥ 0.80 — ordinary ingest at
    lr=2.5 only reaches mass ≈ 0.71, which is why we seed explicitly.
    """
    b = _fresh_bella()
    from bellamem.core.gene import Gene
    from bellamem.core import ops

    g = Gene(name="__principles__")
    b.fields["__principles__"] = g
    # High lr + mass_floor seeds a principle-shape belief
    ops.add(
        g, "errors must surface immediately and never be silently swallowed",
        parent=None, voice="__constitution__", lr=50.0,
        embedding=current_embedder().embed(
            "errors must surface immediately and never be silently swallowed"
        ),
        mass_floor=0.95,
    )

    pack = expand_before_edit(
        b, "should I wrap this in try/except",
        budget_tokens=400,
        focus_entity="api.py",
    )
    assert pack.lines, "before_edit should surface the invariant layer"
    text = "\n".join(ln for ln in [l.belief.desc for l in pack.lines])
    assert "swallow" in text.lower()


def test_expand_before_edit_empty_on_empty_forest():
    """If nothing qualifies for any layer, the pack is legitimately empty.

    This is the opposite of the previous test: before_edit does NOT
    invent content. Low-mass ordinary claims don't reach the 0.80
    invariant floor and there are no edges / entities / self-model
    entries to populate the other layers.
    """
    b = _fresh_bella()
    b.ingest(Claim(text="retry with exponential backoff",
                   voice="user", lr=2.0))
    pack = expand_before_edit(b, "should I retry the request",
                              budget_tokens=400)
    # Empty pack is correct behavior — no layer qualifies.
    assert pack.lines == []


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

def test_save_load_round_trip(tmp_path):
    b = _fresh_bella()
    b.ingest(Claim(text="prefer composition over inheritance",
                   voice="user", lr=2.5))
    path = str(tmp_path / "snap.json")
    save(b, path)

    loaded = load(path)
    assert len(loaded.fields) == len(b.fields)
    total_before = sum(len(g.beliefs) for g in b.fields.values())
    total_after = sum(len(g.beliefs) for g in loaded.fields.values())
    assert total_before == total_after


def test_embedder_signature_mismatch_fails_loud(tmp_path):
    """Loading a snapshot under a different-dim embedder must raise."""
    from bellamem.core.embed import EmbedderMismatch

    b = _fresh_bella()  # HashEmbedder 256d
    b.ingest(Claim(text="always validate at the boundary", voice="user", lr=2.0))
    path = str(tmp_path / "snap.json")
    save(b, path)

    # Switch to a different-dim hash embedder
    set_embedder(HashEmbedder(dim=128))
    try:
        try:
            load(path)
        except EmbedderMismatch:
            pass  # expected
        else:
            raise AssertionError("expected EmbedderMismatch")
    finally:
        set_embedder(HashEmbedder())  # restore


# ---------------------------------------------------------------------------
# Adapters — regex EW smoke
# ---------------------------------------------------------------------------

def test_regex_ew_user_rule_is_strong():
    from bellamem.adapters.chat import extract_claims

    claims = extract_claims(
        "authentication tokens must never be stored in session cookies",
        voice="user",
    )
    assert claims
    # Rule markers get the strong user lr (2.5)
    assert any(c.lr >= 2.0 for c in claims)


def test_regex_ew_drops_assistant_preamble():
    from bellamem.adapters.chat import extract_claims

    # Assistant preambles should be filtered — they're execution state,
    # not claims.
    claims = extract_claims("Let me read the file and check.", voice="assistant")
    assert not claims


def test_regex_ew_reaction_classifier():
    from bellamem.adapters.chat import classify_reaction

    assert classify_reaction("ya, let's move on") == "affirm"
    assert classify_reaction("agreed") == "affirm"
    assert classify_reaction("no, not that way") == "correct"
    assert classify_reaction("what about the database layer?") == "neutral"
