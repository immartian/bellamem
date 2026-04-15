#!/usr/bin/env bash
# Phase 1 acceptance harness.
#
# Runs a v0.2 read-side subcommand against the same .graph/v02.json with
# both the Python reference (`python -m bellamem.proto.<cmd>`) and the
# Node port (`node packages/bellamem/dist/bin/bellamem.js <cmd>`), then
# diffs the outputs. Any drift is a bug in the port.
#
# Usage:
#   scripts/diff-python-vs-ts.sh                 # runs the default set
#   scripts/diff-python-vs-ts.sh resume audit
#   GRAPH=/other/path scripts/diff-python-vs-ts.sh recall walker
#
# Exit 0 if every diff is empty, 1 otherwise. Per-op diffs are printed.

set -u
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

GRAPH="${GRAPH:-$ROOT/.graph/v02.json}"
OUT="${OUT:-$ROOT/.diff-harness}"
mkdir -p "$OUT"

PY="python3 -m bellamem.proto"
TS="node $ROOT/packages/bellamem/dist/bin/bellamem.js"

if [[ ! -f "$GRAPH" ]]; then
  echo "graph not found: $GRAPH" >&2
  exit 2
fi

# Normalize stderr noise (node deprecation warnings, python cli warnings)
# out of the comparison. Only stdout is expected to be canonical.
normalize() {
  # Strip trailing whitespace on every line — both impls should already
  # match, but this protects against an accidental ' \n' vs '\n'.
  sed -E 's/[[:space:]]+$//'
}

run_py() {
  local cmd="$1"; shift
  ( cd "$ROOT" && $PY.$cmd --graph "$GRAPH" "$@" 2>/dev/null ) | normalize
}

run_ts() {
  local cmd="$1"; shift
  $TS "$cmd" --graph "$GRAPH" "$@" 2>/dev/null | normalize
}

run_diff() {
  local label="$1"; shift
  local py_file="$OUT/${label}.py.txt"
  local ts_file="$OUT/${label}.ts.txt"
  "$@" py > "$py_file" &
  "$@" ts > "$ts_file"
  wait
  if diff -u "$py_file" "$ts_file" > "$OUT/${label}.diff"; then
    echo "  OK    $label"
    return 0
  fi
  echo "  DRIFT $label — see $OUT/${label}.diff"
  return 1
}

do_resume() {
  if [[ "$1" == "py" ]]; then run_py resume; else run_ts resume; fi
}

do_audit() {
  # Python has no `bellamem.proto.audit` CLI entrypoint — call via a
  # short python -c shim. TS has `audit` as a subcommand.
  if [[ "$1" == "py" ]]; then
    ( cd "$ROOT" && python3 -c "
from bellamem.proto.store import load_graph
from bellamem.proto.audit import audit, format_audit
print(format_audit(audit(load_graph('$GRAPH'))))
" 2>/dev/null ) | normalize
  else
    run_ts audit
  fi
}

do_replay() {
  # Python v0.2-ref proto __main__ has no `replay` subcommand — the
  # v0.2 replay_text() is library-only. Shim it the same way we shim
  # audit so the diff still compares the reference implementation.
  if [[ "$1" == "py" ]]; then
    ( cd "$ROOT" && python3 -c "
from bellamem.proto.store import load_graph
from bellamem.proto.replay import replay_text
print(replay_text(load_graph('$GRAPH'), max_lines=20))
" 2>/dev/null ) | normalize
  else
    run_ts replay --max-lines 20
  fi
}

echo "diff harness"
echo "  graph: $GRAPH"
echo "  output: $OUT"
echo

fail=0
ops=("$@")
if [[ ${#ops[@]} -eq 0 ]]; then
  ops=(resume audit replay)
fi

for op in "${ops[@]}"; do
  case "$op" in
    resume) run_diff resume do_resume || fail=1 ;;
    audit)  run_diff audit  do_audit  || fail=1 ;;
    replay) run_diff replay do_replay || fail=1 ;;
    *) echo "  SKIP  $op (no harness wired)" ;;
  esac
done

echo
if [[ $fail -eq 0 ]]; then
  echo "all diffs clean"
else
  echo "drift detected — inspect $OUT/*.diff"
fi
exit $fail
