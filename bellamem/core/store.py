"""JSON snapshot persistence.

v0: one JSON file per forest. Atomic write via tmp+rename (P8).
Upgrade path: append-only claims log + periodic snapshot, then sqlite.

The snapshot records the embedder's name and dim. On load we refuse
to continue under a different embedder because the stored vectors
would be incompatible. Fail loud (C10) instead of silently mis-routing.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import TYPE_CHECKING

from .embed import current_embedder, EmbedderMismatch

if TYPE_CHECKING:
    from .bella import Bella


SNAPSHOT_VERSION = 2


def save(bella: "Bella", path: str) -> None:
    emb = current_embedder()
    d = {
        "version": SNAPSHOT_VERSION,
        "saved_at": time.time(),
        "embedder": {"name": emb.name, "dim": emb.dim},
        "fields": {name: g.to_dict() for name, g in bella.fields.items()},
        "field_order": list(bella.fields.keys()),
        "cursor": dict(bella.cursor),
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bellamem_", suffix=".json",
                                dir=os.path.dirname(os.path.abspath(path)) or ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: str) -> "Bella":
    from .bella import Bella
    from .gene import Gene
    b = Bella()
    if not os.path.exists(path):
        return b
    with open(path) as f:
        d = json.load(f)

    # Embedder signature check: fail loud if the vectors in the snapshot
    # were produced by a different embedder than the current one. Snapshots
    # without a signature (pre-v2) cannot be trusted under any non-default
    # embedder because their vectors might be a different dimension.
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

    order = d.get("field_order") or list(d.get("fields", {}).keys())
    for name in order:
        gd = d["fields"].get(name)
        if gd:
            b.fields[name] = Gene.from_dict(gd)
    b.cursor = dict(d.get("cursor", {}))
    return b
