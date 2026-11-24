#!/bin/bash
# uninstall.sh — Remove the governance hooks from ~/.claude/settings.json
# and the `gov` symlink. Logs, reports and figures are left untouched.

set -e

GOV_ROOT="$(cd "$(dirname "$0")" && pwd)"
SETTINGS_FILE="$HOME/.claude/settings.json"
LINK="$HOME/.local/bin/gov"

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required." >&2
  exit 1
fi

if [ -f "$SETTINGS_FILE" ]; then
  cp "$SETTINGS_FILE" "$SETTINGS_FILE.bak-gov"
  UPDATED=$(jq '
    def is_gov: (.command // "") | endswith("/hooks/gov-logger.sh");
    if .hooks then
      .hooks |= (
        with_entries(
          .value |= [ .[] | .hooks |= map(select(is_gov | not)) | select((.hooks | length) > 0) ]
        )
        | with_entries(select((.value | length) > 0))
      )
      | if (.hooks | length) == 0 then del(.hooks) else . end
    else . end
  ' "$SETTINGS_FILE")
  printf '%s\n' "$UPDATED" > "$SETTINGS_FILE"
  echo "Removed gov-logger hooks from $SETTINGS_FILE (backup: $SETTINGS_FILE.bak-gov)"
fi

if [ -L "$LINK" ] && [ "$(readlink "$LINK")" = "$GOV_ROOT/bin/gov" ]; then
  rm "$LINK"
  echo "Removed symlink $LINK"
fi

echo "Done. Your logs are still in $GOV_ROOT/logs/"
