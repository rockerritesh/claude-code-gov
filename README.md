# gov: local tracing and analytics for Claude Code sessions

`gov` records what Claude Code does in a session (every prompt, tool call, tool
result, subagent and notification) to plain JSONL files on your machine, then
turns them into CSV reports and charts. It is a few shell scripts plus one
Python analysis script. There is no server, and nothing leaves your computer.

Tracing is opt-in per session. The hooks are registered globally but do nothing
unless the session was started with `gov claude`, so your normal `claude`
sessions are not recorded.

> **Privacy:** the logs contain everything the hooks see: your prompts, shell
> commands, file paths, the content of files Claude writes or edits, and tool
> output. Treat `logs/`, `reports/` and `figures/` as private. They are in
> `.gitignore`; keep them out of any repository you share.

## Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code)
- `bash` and [`jq`](https://jqlang.org/) (macOS: `brew install jq`, Debian/Ubuntu: `apt install jq`)
- For the charts: Python 3.10+ and [`uv`](https://docs.astral.sh/uv/)

## Install

```bash
git clone https://github.com/rockerritesh/claude-code-gov.git
cd claude-code-gov
./install.sh
```

`install.sh` adds the logger hook to `~/.claude/settings.json` for ten events
(`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `SubagentStart`, `SubagentStop`, `Stop`, `Notification`,
`SessionEnd`), keeping any hooks you already have, and writes a backup to
`~/.claude/settings.json.bak-gov`. It symlinks `gov` into `~/.local/bin`; make
sure that directory is on your `PATH`. Re-running it is safe: each event keeps
exactly one gov hook, even if you moved the folder.

## Use

```bash
gov claude                 # start Claude Code with tracing (any claude args work)
gov logs                   # list traced sessions with event counts and time range
gov tail [session_id]      # live view of the latest (or given) session
gov report [session_id]    # CSV of all sessions (or one) -> reports/
gov report --json          # same, as one JSON array
uv run analyze.py          # charts + written findings for new reports -> figures/
```

`analyze.py` only processes reports it has not seen before, and rebuilds a
cross-report summary each time.

### What you get

Per report, in `figures/report_<timestamp>/`: event distribution, tool usage,
tool failure rate, tool latency, tool chains, activity timeline, hourly heatmap
and activity, session duration and breakdown, prompts per session, time between
events, permission modes, top directories and project activity, plus a
`findings.txt` summary (sessions, prompts, idle gaps, estimated active time,
most-used tools).

Across all reports, in `figures/summary/`: cumulative growth, overall tool usage
and its trend, events per report, overall failure rate, latency, hourly heatmap
and project activity.

## How it works

```
gov claude ──sets GOV_CLAUDE_LOG_DIR──> claude ──hook events──> hooks/gov-logger.sh
                                                                     │
                                       logs/session_<id>.jsonl <─────┘  (one JSON object per line)
gov report ──jq──> reports/report_<ts>.csv ──analyze.py──> figures/
```

- `hooks/gov-logger.sh` exits immediately when `GOV_CLAUDE_LOG_DIR` is unset.
  Otherwise it appends the hook payload plus a `logged_at` timestamp. Parallel
  tool calls fire hooks concurrently, so appends are serialized with `flock`
  (Linux) or `lockf` (macOS).
- `gov report` parses logs line by line and skips any malformed line (it tells
  you how many) instead of dropping the rest of the file.

## Limitations

- No token or cost data: Claude Code hooks do not expose it. For usage metrics,
  see Claude Code's built-in OpenTelemetry support.
- The CSV keeps a 200-character summary of each tool input; the full payload is
  in the JSONL logs.

## Uninstall

```bash
./uninstall.sh
```

Removes the gov hooks from `~/.claude/settings.json` (backup first) and the
`gov` symlink. Your logs, reports and figures stay where they are.

## License

MIT, see [LICENSE](LICENSE).
