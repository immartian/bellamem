#!/usr/bin/env bash
# Rebuild the public GitHub Pages viz files from the live v0.2 graph.
#
# Writes three self-contained HTML renders into docs/viz/:
#   v02-d3.html         — D3 force-directed 2D
#   v02-cytoscape.html  — Cytoscape + fcose 2D
#   v02-3d.html         — Three.js + UMAP × mass 3D
#
# All three use `--min-mass 0.7 --no-hubs` so only the ratified
# structural spine renders. Turn-preview text (which might contain
# conversation fragments) is excluded by --no-hubs. Safe to publish.
#
# Run this before cutting a release that should ship a fresh demo
# to github pages. Commit the changed docs/viz/*.html along with
# the release.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Load the OpenAI key — needed by the 3D renderer to re-embed topics
# whose cache entries may have expired. Not needed for 2D.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PY="${PYTHON:-.venv/bin/python}"
GRAPH="${GRAPH:-.graph/v02.json}"
OUT_DIR="docs/viz"

if [ ! -f "$GRAPH" ]; then
  echo "graph not found at $GRAPH — run bellamem.proto ingest first" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

FLAGS=(--graph "$GRAPH" --min-mass 0.7 --no-hubs)

echo "→ D3"
$PY -m bellamem.proto viz --out "$OUT_DIR/v02-d3.html" --renderer d3 "${FLAGS[@]}"

echo "→ Cytoscape"
$PY -m bellamem.proto viz --out "$OUT_DIR/v02-cytoscape.html" --renderer cytoscape "${FLAGS[@]}"

echo "→ 3D"
$PY -m bellamem.proto viz --out "$OUT_DIR/v02-3d.html" --renderer 3d "${FLAGS[@]}"

echo ""
echo "Done. Files written to $OUT_DIR/"
ls -la "$OUT_DIR/"
