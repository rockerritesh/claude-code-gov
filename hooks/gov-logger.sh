#!/bin/bash
# gov-logger.sh — Claude Code hook that logs all events to JSONL
# Only active when GOV_CLAUDE_LOG_DIR is set (via the `gov` wrapper)

if [ -z "$GOV_CLAUDE_LOG_DIR" ]; then
  exit 0
fi

INPUT=$(cat)

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // "unknown"')

mkdir -p "$GOV_CLAUDE_LOG_DIR"
LOG_FILE="$GOV_CLAUDE_LOG_DIR/session_${SESSION_ID}.jsonl"

# Build the entry in a temp file first, then append it under a lock.
# Parallel tool calls fire hooks concurrently; without the lock, entries
# larger than one stdio buffer (4 KB) get interleaved mid-line.
ENTRY=$(mktemp "$GOV_CLAUDE_LOG_DIR/.entry.XXXXXX") || exit 0
trap 'rm -f "$ENTRY"' EXIT
printf '%s' "$INPUT" | jq -c --arg ts "$TIMESTAMP" '. + {logged_at: $ts}' > "$ENTRY" || exit 0

LOCK="$GOV_CLAUDE_LOG_DIR/.gov.lock"
if command -v flock >/dev/null 2>&1; then        # Linux (util-linux)
  flock "$LOCK" sh -c 'cat "$1" >> "$2"' _ "$ENTRY" "$LOG_FILE"
elif command -v lockf >/dev/null 2>&1; then      # macOS / BSD
  lockf -k "$LOCK" sh -c 'cat "$1" >> "$2"' _ "$ENTRY" "$LOG_FILE"
else                                             # best effort
  cat "$ENTRY" >> "$LOG_FILE"
fi

exit 0
