#!/usr/bin/env bash
# Single entry point for the /bellamem slash command.
# Dispatches to bellamem subcommands based on the first argument.
#
# Design: this script is invoked by .claude/commands/bellamem.md via
# the slash command's inline backtick execution. The output of this
# script becomes part of Claude's prompt, so the script formats its
# output as markdown-friendly sections.

set -e

# Resolve to project root regardless of where the slash command was
# invoked from. git rev-parse works when we're in a git repo; the
# dirname fallback handles the edge case of a detached checkout.
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || (cd "$(dirname "$0")/../.." && pwd))"
cd "$REPO_ROOT"

# Prefer a project-local editable install; fall back to PATH bellamem
# (pipx, user install, system package). Fail loud if neither exists.
if [[ -x ".venv/bin/bellamem" ]]; then
    BM=".venv/bin/bellamem"
elif command -v bellamem >/dev/null 2>&1; then
    BM="$(command -v bellamem)"
else
    cat >&2 <<'EOF'
error: bellamem not found.

install one of:
  • project-local venv:  python3 -m venv .venv && .venv/bin/pip install bellamem
  • global (recommended): pipx install bellamem
  • from source:         git clone https://github.com/immartian/bellamem && cd bellamem && pipx install -e .
EOF
    exit 1
fi

sub="${1:-resume}"
shift || true

case "$sub" in
    resume)
        echo "## Working memory (replay tail)"
        echo
        "$BM" replay -t 2500
        echo
        echo "## Long-term memory (ratified decisions, current focus)"
        echo
        "$BM" expand "current state of bellamem and open follow-ups" -t 1500
        echo
        echo "## What just mattered (surprises)"
        echo
        "$BM" surprises --top 8
        ;;
    save)
        echo "## Ingest with auto-consolidation"
        echo
        "$BM" ingest-cc
        echo
        echo "## Audit"
        echo
        "$BM" audit --max-per-section 3
        echo
        echo "## Top surprises after ingest"
        echo
        "$BM" surprises --top 5
        ;;
    recall)
        topic="$*"
        if [[ -z "$topic" ]]; then
            echo "usage: /bellamem recall <topic>"
            exit 1
        fi
        echo "## Recall: $topic"
        echo
        "$BM" expand "$topic" -t 1500
        ;;
    why)
        topic="$*"
        if [[ -z "$topic" ]]; then
            echo "usage: /bellamem why <topic>"
            exit 1
        fi
        echo "## Why: $topic"
        echo
        "$BM" before-edit "$topic" -t 1500
        ;;
    replay)
        "$BM" replay -t 2500
        ;;
    audit)
        "$BM" audit
        ;;
    ""|help|--help|-h)
        cat <<EOF
/bellamem — BellaMem graph memory

Subcommands:
  resume              working memory + long-term memory + signal (default)
  save                ingest current session + audit + surprises
  recall <topic>      mass-ranked beliefs about a topic
  why <topic>         causal chain + invariants + disputes for a focus
  replay              raw session replay (line-ordered)
  audit               full entropy audit
  help                show this message
EOF
        ;;
    *)
        echo "unknown subcommand: $sub"
        echo "try: /bellamem help"
        exit 1
        ;;
esac
