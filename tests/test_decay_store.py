"""Store round-trip tests for decayed_at (v3 → v4 migration).

v4 adds one field to the graph JSON header. Load/save must preserve
it across a round-trip, and pre-v4 snapshots must backfill from
saved_at so the first decay pass operates on real time-since-save.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from bellamem.core import Bella, Claim
from bellamem.core.embed import HashEmbedder, set_embedder
from bellamem.core.store import load, save, SNAPSHOT_VERSION


def _snapshot_dir():
    return tempfile.mkdtemp(prefix="bellamem-test-decay-store-")


def test_snapshot_version_is_four():
    # Tripwire: if someone bumps the version again, review this module.
    assert SNAPSHOT_VERSION == 4


def test_round_trip_preserves_decayed_at():
    set_embedder(HashEmbedder())
    d = _snapshot_dir()
    path = os.path.join(d, "default.json")

    b = Bella()
    b.ingest(Claim(text="a belief", voice="user", lr=1.5))
    b.decayed_at = 1_700_000_000.0  # a fixed, non-now value
    save(b, path)

    b2 = load(path)
    assert b2.decayed_at == 1_700_000_000.0


def test_v4_snapshot_writes_decayed_at_in_header():
    set_embedder(HashEmbedder())
    d = _snapshot_dir()
    path = os.path.join(d, "default.json")

    b = Bella()
    b.ingest(Claim(text="header test", voice="user", lr=1.5))
    b.decayed_at = 1_700_000_000.0
    save(b, path)

    with open(path) as f:
        header = json.load(f)
    assert header["version"] == 4
    assert header["decayed_at"] == 1_700_000_000.0


def test_pre_v4_snapshot_backfills_decayed_at_from_saved_at():
    """Simulate a v3 snapshot by stripping decayed_at and writing
    version=3 into the header, then loading it."""
    set_embedder(HashEmbedder())
    d = _snapshot_dir()
    path = os.path.join(d, "default.json")

    # Write a fresh v4 snapshot first.
    b = Bella()
    b.ingest(Claim(text="legacy belief", voice="user", lr=1.5))
    save(b, path)

    # Rewrite the JSON header to look like v3.
    with open(path) as f:
        header = json.load(f)
    header["version"] = 3
    header["saved_at"] = 1_650_000_000.0
    del header["decayed_at"]
    with open(path, "w") as f:
        json.dump(header, f)

    b2 = load(path)
    assert b2.decayed_at == 1_650_000_000.0


def test_fresh_bella_has_recent_decayed_at():
    """A freshly constructed Bella should start near-now so the first
    save writes a sensible timestamp even before decay has ever run."""
    before = time.time()
    b = Bella()
    after = time.time()
    assert before <= b.decayed_at <= after
