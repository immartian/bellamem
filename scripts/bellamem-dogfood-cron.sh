#!/usr/bin/env bash
#
# bellamem dogfood cron wrapper.
#
# Runs `BELLAMEM_DECAY=on bellamem save` on the bellamem project itself
# every N minutes, so decay + reinforcement run simultaneously against
# the live forest. Designed for the v0.1.0 dogfood week: validates the
# steady-state hypothesis from THEORY.md under real traffic.
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
# ticks skip rather than queue, so the steady-state cost is bounded).

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BELLAMEM="${PROJECT_DIR}/.venv/bin/bellamem"
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

{
    echo "--- [$(date -Is)] BELLAMEM_DECAY=on bellamem save ---"
    BELLAMEM_DECAY=on "$BELLAMEM" save 2>&1 || echo "[!] bellamem save exited $?"
    echo "--- done ---"
} >> "$LOG" 2>&1
