#!/usr/bin/env bash
#
# bellamem v0.3 dogfood cron wrapper (Node port).
#
# Runs `bellamem save` on the bellamem project itself every N minutes,
# incrementally ingesting new turns from the latest Claude Code session
# jsonl into .graph/v02.json. Turns already in the graph are skipped
# (see packages/bellamem/src/ingest.ts), so steady-state cost is
# bounded to new turns only.
#
# Install (from the repo root):
#     ( crontab -l 2>/dev/null ; echo "*/5 * * * * $PWD/scripts/bellamem-dogfood-cron.sh" ) | crontab -
#
# Verify:
#     crontab -l | grep bellamem-dogfood-cron
#     tail -f .graph/dogfood-cron.log
#
# Uninstall:
#     crontab -l | grep -v bellamem-dogfood-cron | crontab -
#
# Lock: <PROJECT_DIR>/.graph/.save.lock (flock, non-blocking — overlapping
# ticks skip rather than queue). Per-project, so a save in another
# project doesn't block this project's cron and vice versa.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="${PROJECT_DIR}/packages/bellamem/dist/bin/bellamem.js"
LOG_DIR="${PROJECT_DIR}/.graph"
LOG="${LOG_DIR}/dogfood-cron.log"
LOCK="${LOG_DIR}/.save.lock"

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

if [ ! -f "$BIN" ]; then
    echo "[$(date -Is)] bellamem binary not built at $BIN — run 'cd packages/bellamem && npm run build'" >> "$LOG"
    exit 1
fi

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[$(date -Is)] skipped — previous run still holds the lock" >> "$LOG"
    exit 0
fi

# Load OPENAI_API_KEY from the user config if the cron env lacks it.
if [ -z "${OPENAI_API_KEY:-}" ] && [ -f "$HOME/.config/bellamem/.env" ]; then
    set -a
    . "$HOME/.config/bellamem/.env"
    set +a
fi

{
    echo "--- [$(date -Is)] bellamem save ---"
    node "$BIN" save --graph "$LOG_DIR/v02.json" 2>&1 \
        || echo "[!] save exited $?"
    echo "--- done ---"
} >> "$LOG" 2>&1
