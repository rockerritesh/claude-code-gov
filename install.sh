#!/bin/bash
# install.sh — Set up governance hooks in ~/.claude/settings.json
# and symlink the `gov` command to PATH.

set -e

GOV_ROOT="$(cd "$(dirname "$0")" && pwd)"
HOOK_SCRIPT="$GOV_ROOT/hooks/gov-logger.sh"
SETTINGS_FILE="$HOME/.claude/settings.json"
BIN_DIR="$HOME/.local/bin"
EVENTS='["SessionStart","UserPromptSubmit","PreToolUse","PostToolUse","PostToolUseFailure","SubagentStart","SubagentStop","Stop","Notification","SessionEnd"]'

echo "╔═══════════════════════════════════════════╗"
echo "║   Claude Code Governance Tracker Setup    ║"
echo "╚═══════════════════════════════════════════╝"
echo ""

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required (macOS: brew install jq, Debian/Ubuntu: apt install jq)." >&2
  exit 1
fi

# 1. Make scripts executable
chmod +x "$GOV_ROOT/bin/gov" "$GOV_ROOT/bin/gov-report" "$HOOK_SCRIPT" "$GOV_ROOT/uninstall.sh"
echo "[1/4] Made scripts executable"

# 2. Ensure ~/.claude/settings.json exists, and back it up
mkdir -p "$HOME/.claude"
if [ ! -f "$SETTINGS_FILE" ]; then
  echo '{}' > "$SETTINGS_FILE"
fi
cp "$SETTINGS_FILE" "$SETTINGS_FILE.bak-gov"

# 3. Register the logger for every trackable event. Safe to re-run: any
# existing gov-logger entry (including one from an old install path) is
# removed first, so each event ends up with exactly one.
UPDATED=$(jq --arg cmd "$HOOK_SCRIPT" --argjson events "$EVENTS" '
  def is_gov: (.command // "") | endswith("/hooks/gov-logger.sh");
  .hooks = (
    reduce $events[] as $e (.hooks // {};
      .[$e] = (
        [ (.[$e] // [])[]
          | .hooks |= map(select(is_gov | not))
          | select((.hooks | length) > 0) ]
        + [ { hooks: [ { type: "command", command: $cmd } ] } ]
      )
    )
  )
' "$SETTINGS_FILE")
printf '%s\n' "$UPDATED" > "$SETTINGS_FILE"
echo "[2/4] Hooks registered in $SETTINGS_FILE (backup: $SETTINGS_FILE.bak-gov)"

# 4. Symlink gov to PATH
mkdir -p "$BIN_DIR"
ln -sf "$GOV_ROOT/bin/gov" "$BIN_DIR/gov"
echo "[3/4] Symlinked 'gov' → $BIN_DIR/gov"

# 5. Create logs and reports dirs
mkdir -p "$GOV_ROOT/logs" "$GOV_ROOT/reports"
echo "[4/4] Created logs/ and reports/ directories"

echo ""
echo "Setup complete!"
echo ""
echo "Usage:"
echo "  gov claude              Launch Claude with governance tracing"
echo "  gov logs                List traced sessions"
echo "  gov report              Generate CSV report of all sessions"
echo "  gov report --json       Generate JSON report"
echo "  gov tail                Live-tail the latest session"
echo ""
echo "Logs directory: $GOV_ROOT/logs/"
echo "Reports directory: $GOV_ROOT/reports/"

# Check if BIN_DIR is in PATH
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
  echo ""
  echo "NOTE: $BIN_DIR is not in your PATH."
  echo "Add this to your ~/.zshrc:"
  echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
