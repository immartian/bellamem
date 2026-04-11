"""JSON snapshot persistence.

v4 split format (this file):
  <path>.json       graph structure — beliefs without embeddings
  <path>.emb.bin    float32-packed embeddings, keyed by belief id

v4 adds a `decayed_at` wall-clock timestamp to the graph JSON header,
consumed by core/decay.py to compute the decay Δt on the next save.
Pre-v4 snapshots are transparently backfilled: `load()` sets
`bella.decayed_at = saved_at`, so the first decay pass on an upgraded
snapshot operates on the real time-since-last-write, not "zero".

Rationale: ~68 of the ~81 MB of v2 snapshots was belief embeddings
stored inline as JSON lists of floats, and most operations (render,
audit, scrub, replay, guard, show, stats) do not need the vectors
loaded at all. Splitting them out drops the graph.json size to
~13 MB and reduces `load()` wall-time from ~2 s to well under
500 ms. Every operation that doesn't touch vectors benefits.

v2 → v3 migration: `load()` transparently reads a v2 snapshot
(embeddings inside beliefs) and on the next `save()` the v3 split
format is written. The pre-migration `<path>.json` is copied to
`<path>.json.bak` on the first v3 write so the user has an escape
hatch if anything looks wrong.

Atomic writes via tmp+rename (P8) for both files independently.
Snapshot records the embedder's name and dim in BOTH files; load()
cross-checks them and fails loud on any mismatch (C10).

The binary format is custom, stdlib-only (`struct`) — core stays
dependency-free. See `_write_embeddings_bin` / `_read_embeddings_bin`
for the on-disk layout.

Upgrade path: append-only claims log + periodic snapshot, then sqlite.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Iterable, Iterator, Optional

from .embed import current_embedder, EmbedderMismatch

if TYPE_CHECKING:
    from .bella import Bella


SNAPSHOT_VERSION = 4

# ---------------------------------------------------------------------------
# v3 binary embeddings file format
# ---------------------------------------------------------------------------
#
# Stdlib-only (`struct`), little-endian throughout. Layout:
#
#   offset  size  field
#   ------  ----  ----------------------------------------------------
#   0       9     magic bytes   "BELLAEMB\0"
#   9       1     format ver    uint8   (current: 1)
#   10      2     dim           uint16  (e.g. 1536 for openai-3-small)
#   12      4     count         uint32  (number of rows that follow)
#   16      64    embedder name utf-8, null-padded, right-truncated
#   80      *     rows          count rows, each:
#                                 16 bytes  belief id (utf-8, null-padded)
#                                 dim * 4   vector (float32 LE)
#
# Total row size: 16 + dim * 4 bytes.
# For dim=1536 that's 6160 bytes per belief → ~9.4 MB for 1593 beliefs.
#
# Rationale for 16-byte id field: bellamem's current belief ids are
# 12 hex chars (md5 prefix), but fixing the binary format at 16 leaves
# headroom without meaningful overhead (1593 × 4 = 6 KB slack).

_MAGIC = b"BELLAEMB\0"
_EMB_FORMAT_VERSION = 1
_EMB_HEADER_FMT = "<BHI64s"
_EMB_HEADER_SIZE = struct.calcsize(_EMB_HEADER_FMT)  # 71
_EMB_ID_SIZE = 16


def _emb_path(json_path: str) -> str:
    """Derive the embeddings file path from the graph file path.

    `.graph/default.json` → `.graph/default.emb.bin`
    `.graph/default`      → `.graph/default.emb.bin`
    """
    base, ext = os.path.splitext(json_path)
    if ext.lower() == ".json":
        return base + ".emb.bin"
    return json_path + ".emb.bin"


def _atomic_write_bytes(path: str, write_fn) -> None:
    """Write a file atomically via tmp+rename. `write_fn(file)` does the
    actual writing to a temporary file; this wrapper handles tmp creation,
    rename, and cleanup on error.
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bellamem_", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            write_fn(f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_embeddings_bin(path: str, beliefs: Iterable[tuple[str, Optional[list[float]]]],
                           emb_name: str, dim: int) -> int:
    """Write the embeddings.bin file atomically.

    `beliefs` is an iterable of (belief_id, embedding) pairs. Beliefs
    whose embedding is None or has the wrong dimension are skipped —
    they stay absent from the bin file, and load() leaves their
    Belief.embedding as None. Defensive: a bad embedding shouldn't
    block the rest of the save.

    Returns the number of rows actually written.
    """
    # Collect first so we know `count` for the header. The sort keeps
    # the on-disk order stable per save for deterministic diffs and to
    # make manual inspection easier.
    rows: list[tuple[str, list[float]]] = []
    for bid, emb in beliefs:
        if emb is None:
            continue
        if len(emb) != dim:
            continue
        rows.append((bid, emb))
    rows.sort(key=lambda r: r[0])
    count = len(rows)

    emb_name_bytes = emb_name.encode("utf-8")[:64]

    def _write(f) -> None:
        f.write(_MAGIC)
        f.write(struct.pack(_EMB_HEADER_FMT,
                            _EMB_FORMAT_VERSION, dim, count, emb_name_bytes))
        vec_fmt = f"<{dim}f"
        for bid, vec in rows:
            bid_bytes = bid.encode("utf-8")[:_EMB_ID_SIZE].ljust(_EMB_ID_SIZE, b"\0")
            f.write(bid_bytes)
            f.write(struct.pack(vec_fmt, *vec))

    _atomic_write_bytes(path, _write)
    return count


def _read_embeddings_bin(path: str) -> tuple[str, int, dict[str, list[float]]]:
    """Read the embeddings.bin file. Returns (embedder_name, dim, id→vec).

    Raises ValueError on bad magic, unsupported format version, or
    truncated data. The caller is responsible for deciding whether to
    propagate the error or continue without embeddings.
    """
    with open(path, "rb") as f:
        magic = f.read(len(_MAGIC))
        if magic != _MAGIC:
            raise ValueError(
                f"{path}: not a bellamem embeddings file (bad magic)")
        header = f.read(_EMB_HEADER_SIZE)
        if len(header) < _EMB_HEADER_SIZE:
            raise ValueError(f"{path}: truncated header")
        version, dim, count, emb_name_raw = struct.unpack(_EMB_HEADER_FMT, header)
        if version != _EMB_FORMAT_VERSION:
            raise ValueError(
                f"{path}: unsupported embeddings format version {version} "
                f"(expected {_EMB_FORMAT_VERSION})")
        emb_name = emb_name_raw.rstrip(b"\0").decode("utf-8", errors="replace")

        vec_fmt = f"<{dim}f"
        vec_size = dim * 4
        row_size = _EMB_ID_SIZE + vec_size
        embeddings: dict[str, list[float]] = {}
        for _ in range(count):
            row = f.read(row_size)
            if len(row) < row_size:
                # Silent truncation — unusual but don't crash, just stop.
                break
            bid = row[:_EMB_ID_SIZE].rstrip(b"\0").decode("utf-8", errors="replace")
            embeddings[bid] = list(struct.unpack(vec_fmt, row[_EMB_ID_SIZE:]))

    return emb_name, dim, embeddings


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------

def save(bella: "Bella", path: str) -> None:
    """Write the forest snapshot.

    v3 format: graph.json (no embeddings) + <base>.emb.bin (float32).
    Both are written atomically and independently. A pre-migration
    default.json from a v2 install is backed up to default.json.bak
    on the first v3 write.
    """
    emb = current_embedder()

    # Migration backup — copy the existing JSON if it's from an older
    # format version, before we overwrite it with v3. Best-effort; a
    # missing or unreadable file doesn't block the save.
    try:
        if os.path.exists(path):
            with open(path) as _f:
                _existing = json.load(_f)
            _existing_version = int(_existing.get("version", 0))
            if _existing_version < SNAPSHOT_VERSION:
                bak_path = path + ".bak"
                if not os.path.exists(bak_path):
                    shutil.copy2(path, bak_path)
                    print(
                        f"bellamem: backed up pre-v{SNAPSHOT_VERSION} "
                        f"snapshot to {bak_path}",
                        file=sys.stderr,
                    )
    except (OSError, json.JSONDecodeError):
        pass

    # Build the graph JSON (beliefs serialised without embeddings).
    d = {
        "version": SNAPSHOT_VERSION,
        "saved_at": time.time(),
        # decayed_at: wall-clock timestamp of the last decay pass. A
        # Bella loaded from a pre-v4 snapshot gets this backfilled from
        # saved_at on load, so the first decay pass operates on the
        # real time-since-save delta.
        "decayed_at": bella.decayed_at,
        "embedder": {"name": emb.name, "dim": emb.dim},
        "fields": {
            name: g.to_dict(strip_embedding=True)
            for name, g in bella.fields.items()
        },
        "field_order": list(bella.fields.keys()),
        "cursor": dict(bella.cursor),
    }

    # Write graph.json atomically.
    def _write_graph(f) -> None:
        f.write(json.dumps(d, indent=2).encode("utf-8"))

    _atomic_write_bytes(path, _write_graph)

    # Write embeddings.bin atomically (sibling of graph.json).
    def _iter_embeddings() -> Iterator[tuple[str, Optional[list[float]]]]:
        for g in bella.fields.values():
            for bid, b in g.beliefs.items():
                yield bid, b.embedding

    _write_embeddings_bin(_emb_path(path), _iter_embeddings(), emb.name, emb.dim)


def load_graph_only(path: str) -> "Bella":
    """Read the graph structure WITHOUT loading embeddings.

    Fast path for consumers that only need text, relations, mass, and
    jumps — no vectors. Skips:
      - embedder signature check (the caller may be using a different
        embedder, or no embedder at all, like the PreToolUse guard)
      - the `<base>.emb.bin` read (Belief.embedding stays None)
      - any network / import dependency beyond stdlib

    Returns a Bella where `belief.embedding is None` for every belief.
    Do NOT use this for operations that need vector similarity (expand,
    recall, ingest routing, audit near-duplicates, viz) — they will
    degrade silently. Use `load()` for those.

    Designed for `bellamem/guard.py` and anything that needs sub-second
    graph access without pulling in the embedder stack.
    """
    from .bella import Bella
    from .gene import Gene

    b = Bella()
    if not os.path.exists(path):
        return b

    with open(path) as f:
        d = json.load(f)

    order = d.get("field_order") or list(d.get("fields", {}).keys())
    for name in order:
        gd = d["fields"].get(name)
        if gd:
            b.fields[name] = Gene.from_dict(gd)
    b.cursor = dict(d.get("cursor", {}))
    b.decayed_at = float(d.get("decayed_at", d.get("saved_at", time.time())))
    return b


def load(path: str) -> "Bella":
    """Read the forest snapshot.

    Handles both v3 (split) and v2 (inline embeddings) formats. On v2,
    embeddings come straight out of the JSON; on v3, the graph JSON
    loads first and then `<base>.emb.bin` populates each belief's
    `.embedding` attribute.

    An absent `<base>.emb.bin` on a v3 snapshot is a soft failure:
    the graph loads normally, beliefs have .embedding = None, and
    operations that need vectors will degrade. A corrupt bin file
    emits a warning to stderr and loads the graph without vectors.
    Fail loud on embedder signature mismatch (C10).
    """
    from .bella import Bella
    from .gene import Gene

    b = Bella()
    if not os.path.exists(path):
        return b

    with open(path) as f:
        d = json.load(f)

    # Embedder signature check — same as v2. Must match the current
    # embedder or we refuse to continue.
    cur = current_embedder()
    saved_emb = d.get("embedder")
    if saved_emb is None:
        raise EmbedderMismatch(
            f"snapshot at {path} has no embedder signature (pre-v2 format). "
            f"Cannot safely use it with current embedder {cur.name!r} "
            f"(dim={cur.dim}). Run `bellamem reset` to start fresh."
        )
    if saved_emb.get("name") != cur.name or saved_emb.get("dim") != cur.dim:
        raise EmbedderMismatch(
            f"snapshot at {path} was built with embedder "
            f"{saved_emb.get('name')!r} (dim={saved_emb.get('dim')}); "
            f"current is {cur.name!r} (dim={cur.dim}). "
            f"Either set env to match, or run `bellamem reset` to start over."
        )

    version = int(d.get("version", 2))

    # Load the graph structure. For v2 this also populates embeddings
    # inline (Belief.from_dict reads d['embedding']); for v3 those
    # fields are absent so .embedding stays None until the bin pass.
    order = d.get("field_order") or list(d.get("fields", {}).keys())
    for name in order:
        gd = d["fields"].get(name)
        if gd:
            b.fields[name] = Gene.from_dict(gd)
    b.cursor = dict(d.get("cursor", {}))
    # decayed_at: v4+ field. For older snapshots, backfill from saved_at
    # so the first decay pass after upgrade computes Δt from the last
    # time the snapshot was written, not from the current load time.
    b.decayed_at = float(d.get("decayed_at", d.get("saved_at", time.time())))

    # v3: populate embeddings from the sibling bin file.
    if version >= 3:
        bin_path = _emb_path(path)
        if os.path.exists(bin_path):
            try:
                bin_emb_name, bin_dim, bin_map = _read_embeddings_bin(bin_path)
                if bin_emb_name != cur.name or bin_dim != cur.dim:
                    # Cross-check: bin file's embedder must agree with
                    # the graph JSON's. If not, treat as corrupt.
                    print(
                        f"bellamem: warning: embeddings file {bin_path} "
                        f"embedder ({bin_emb_name!r}, {bin_dim}) does not "
                        f"match graph snapshot ({cur.name!r}, {cur.dim}). "
                        f"Skipping embeddings load.",
                        file=sys.stderr,
                    )
                else:
                    for g in b.fields.values():
                        for bid, belief in g.beliefs.items():
                            vec = bin_map.get(bid)
                            if vec is not None:
                                belief.embedding = vec
            except (OSError, ValueError) as e:
                print(
                    f"bellamem: warning: could not read embeddings file "
                    f"{bin_path}: {e}. Graph loaded without vectors; "
                    f"operations that need them will degrade.",
                    file=sys.stderr,
                )
        else:
            print(
                f"bellamem: warning: v3 snapshot at {path} but no "
                f"embeddings file at {bin_path}. Graph loaded without "
                f"vectors; a fresh `bellamem save` will regenerate it.",
                file=sys.stderr,
            )

    return b
