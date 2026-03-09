# Claude Manager

Interactive TUI session picker for Claude Code, built with Python curses.

## Project structure

```
src/claude_manager/
  __init__.py          # package marker
  main.py              # all logic — parsing, TUI, entry point
pyproject.toml         # hatch-based build, entry point: claude-manager -> main:main
install.sh             # interactive installer (asks binary name, runs uv tool install)
```

Single-file architecture — everything lives in `main.py`. No external dependencies.

## Key design decisions

- **Pure stdlib**: only `curses`, `json`, `argparse`, `textwrap`, `dataclasses`. No pip deps.
- **Lazy message loading**: initial scan only parses metadata (fast). Full conversation is loaded on expand.
- **Binary configurable**: `--binary` flag or `CLAUDE_BINARY` env var. Defaults to `claude`.
- **exec-based resume**: uses `os.execvp` after `os.chdir` — replaces the process entirely, so the resumed Claude session inherits the correct cwd.

## Session data format

Sessions live in `~/.claude/projects/<path-with-dashes>/<uuid>.jsonl`. Each line is a JSON object with:

- `type`: "user" | "assistant" | "progress" | "file-history-snapshot"
- `message.content`: string or array of `{type: "text", text: "..."}` / `{type: "tool_use", name: "..."}` / `{type: "tool_result", ...}` blocks
- `sessionId`, `cwd`, `timestamp` (ISO 8601)
- `isSidechain`, `agentId` for sub-agent messages

## Conventions

- Keep it a single file (`main.py`). Don't split unless it exceeds ~800 lines.
- Colors are defined via curses color pairs in `init_colors()`. Pair constants: `C_IDX`, `C_PATH`, `C_LABEL`, `C_TEXT`, `C_SELECTED`, `C_ASSISTANT`, `C_AGENT`, `C_HEADER`.
- Use `safe_addstr()` for all curses writes — it handles screen boundary clipping.
- Stats (user_msgs, assistant_msgs, agent_msgs, tool_uses, duration_min) are computed during initial parse, not during lazy load.
