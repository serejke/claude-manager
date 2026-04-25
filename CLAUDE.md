# polyclaude

The entry point to Claude Code — pick any directory, resume any session. Built with Python curses, single file, zero deps.

## Project structure

```
src/polyclaude/
  __init__.py          # package marker
  main.py              # all logic — config, parsing, TUI, entry point
pyproject.toml         # hatch-based build, entry point: polyclaude -> main:main
install.sh             # interactive installer (asks binary name, runs uv tool install)
```

Single-file architecture — everything lives in `main.py`. No external dependencies.

## Tabs

- **New session** (tab 0): list of cwds extracted from session history, current dir pinned at top. Enter launches `claude` (with `--dangerously-skip-permissions` per config).
- **Resume** (tab 1): existing picker. `/` to search session text, Enter to expand, Enter again to resume.
- **Settings** (tab 2): list of toggles. Space/Enter toggles; saved immediately to `~/.config/polyclaude/config.json`.

`Tab` cycles 0 → 1 → 2 → 0.

## Key design decisions

- **Pure stdlib**: only `curses`, `json`, `argparse`, `textwrap`, `dataclasses`. No pip deps.
- **Lazy message loading**: initial scan only parses metadata (fast). Full conversation is loaded on expand.
- **Cheap cwd peek**: New tab walks all session jsonl files but only reads the first ~5 lines per file to extract `cwd`. Sub-second across hundreds of sessions.
- **Binary configurable**: `--binary` flag or `CLAUDE_BINARY` env var. Defaults to `claude`.
- **exec-based launch**: uses `os.execvp` after `os.chdir` — replaces the process entirely, so the resumed Claude session inherits the correct cwd.
- **Persistent config** at `~/.config/polyclaude/config.json` (XDG-aware via `XDG_CONFIG_HOME`). Schema: `{"skip_permissions": bool}`. Toggles auto-save.
- **`--safe` CLI flag** forces `--dangerously-skip-permissions` OFF for one run, regardless of config.

## Session data format

Sessions live in `~/.claude/projects/<path-with-dashes>/<uuid>.jsonl`. Each line is a JSON object with:

- `type`: "user" | "assistant" | "progress" | "file-history-snapshot"
- `message.content`: string or array of `{type: "text", text: "..."}` / `{type: "tool_use", name: "..."}` / `{type: "tool_result", ...}` blocks
- `sessionId`, `cwd`, `timestamp` (ISO 8601)
- `isSidechain`, `agentId` for sub-agent messages

## Conventions

- Keep it a single file (`main.py`). Don't split unless it exceeds ~1500 lines.
- Colors are defined via curses color pairs in `init_colors()`. Pair constants: `C_IDX`, `C_PATH`, `C_LABEL`, `C_TEXT`, `C_SELECTED`, `C_ASSISTANT`, `C_AGENT`, `C_HEADER`.
- Use `safe_addstr()` for all curses writes — it handles screen boundary clipping.
- Tab bar lives at row 0; rendered by `draw_tab_bar(stdscr, active_tab, help_text)` and called from each tab's draw function.
- New settings: add a row to `SETTINGS_ITEMS` (key, label, help text) and a field to `Config`. Save/load round-trips JSON. The UI iterates `SETTINGS_ITEMS` automatically.
- Stats (user_msgs, assistant_msgs, agent_msgs, tool_uses, duration_min) are computed during initial parse, not during lazy load.

## Local development

- **Install locally**: `uv tool install --force --reinstall .` — must use `--reinstall` to bust wheel cache when version hasn't been bumped.
- `--force` alone reuses cached wheels if the version string is unchanged.
- After renaming the package, the old binary (`claude-manager`) is no longer installed by uv; only `polyclaude` exists.
