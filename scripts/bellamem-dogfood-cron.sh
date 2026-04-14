#!/usr/bin/env bash
#
# bellamem v0.2 dogfood cron wrapper.
#
# Runs `python -m bellamem.proto ingest` on the bellamem project itself
# every N minutes, incrementally ingesting new turns from the LATEST
# Claude Code session jsonl into .graph/v02.json. Turns already in the
# graph are skipped (see bellamem/proto/ingest.py:ingest_session), so
# steady-state cost is bounded to new turns only.
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
# Lock: /tmp/bellamem-dogfood-cron.lock (flock, non-blocking — overlapping
# ticks skip rather than queue).

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Use the pipx venv's python — it has bellamem installed editable AND
# has openai as a dependency (verified). The .venv may lack openai.
PYTHON="/home/im3/.local/share/pipx/venvs/bellamem/bin/python"
CLAUDE_PROJECT_DIR="/home/im3/.claude/projects/-media-im3-plus-labX-bellamem"
LOG_DIR="${PROJECT_DIR}/.graph"
LOG="${LOG_DIR}/dogfood-cron.log"
LOCK="/tmp/bellamem-dogfood-cron.lock"

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

exec 9>"$LOCK"
if ! flock -n 9; then
    echo "[$(date -Is)] skipped — previous run still holds the lock" >> "$LOG"
    exit 0
fi

# Find the most recently modified session jsonl WITH actual speaker
# content. An empty / metadata-only jsonl (e.g. a fresh session that
# only ran `/bellamem` once) can win the mtime race against an
# active session, causing the cron to ingest nothing. Filter by
# "contains at least one user/assistant record" — cheap grep.
LATEST_SESSION=""
for f in $(ls -1t "$CLAUDE_PROJECT_DIR"/*.jsonl 2>/dev/null); do
    if [ -f "$f" ] && grep -q '"type":"user"\|"type":"assistant"' "$f" 2>/dev/null; then
        LATEST_SESSION="$f"
        break
    fi
done

{
    echo "--- [$(date -Is)] python -m bellamem.proto ingest ---"
    if [ -z "$LATEST_SESSION" ]; then
        echo "[!] no session jsonl found in $CLAUDE_PROJECT_DIR"
    else
        echo "session: $(basename "$LATEST_SESSION")"
        "$PYTHON" -m bellamem.proto ingest "$LATEST_SESSION" 2>&1 \
            || echo "[!] ingest exited $?"
    fi
    echo "--- done ---"
} >> "$LOG" 2>&1
