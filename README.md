# claude-manager

Interactive TUI session picker for [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

## Features

- Scans all Claude Code sessions across every project (`~/.claude/projects/`)
- Shows the 20 most recent sessions with first/last message preview
- Session stats: duration, your messages, Claude's messages, tool calls, agent spawns
- Expand any session to scroll through the full conversation before resuming
- `cd`s to the correct directory and runs `claude --resume` automatically
- Zero dependencies — pure Python, uses only the standard library (`curses`, `json`)
- Configurable binary name via `--binary` flag or `CLAUDE_BINARY` env var

## Install

Requires [uv](https://docs.astral.sh/uv/):

```bash
# one-liner
curl -sSL https://raw.githubusercontent.com/serejke/claude-manager/main/install.sh | bash

# or manually
uv tool install git+https://github.com/serejke/claude-manager
```

The installer asks which Claude binary you use (`claude` by default) and configures `CLAUDE_BINARY` in your shell if needed.

### Run without installing

```bash
uvx --from git+https://github.com/serejke/claude-manager claude-manager
```

## Usage

```
claude-manager              # show 20 most recent sessions
claude-manager 50           # show 50 most recent sessions
claude-manager -b my-claude # use a custom binary name
```

### Controls

| Key                      | Action                               |
| ------------------------ | ------------------------------------ |
| `j` / `k` or Up / Down   | Navigate sessions                    |
| Enter or Right           | Expand session — view conversation   |
| Esc or Left              | Collapse back to list                |
| Enter (in expanded view) | Resume the selected session          |
| `g` / `G`                | Jump to top / bottom (expanded view) |
| PgUp / PgDn              | Scroll page (expanded view)          |
| `q` / Ctrl+C / Ctrl+D    | Quit                                 |

### Custom binary

If you use a wrapper or alias instead of bare `claude`:

```bash
# per-run
claude-manager --binary claude-unsafe

# permanent (add to ~/.zshrc or ~/.bashrc)
export CLAUDE_BINARY="claude-unsafe"
```

## How it works

Claude Code stores session data as JSONL files in `~/.claude/projects/<project-path>/`. Each file contains the full conversation: user messages, assistant responses, tool calls, and agent interactions.

claude-manager:

1. Finds all `.jsonl` session files, sorted by last modification time
2. Parses metadata (timestamps, message counts, first/last user message) for each
3. On expand, lazily loads the full conversation for that session
4. On resume, `cd`s to the session's original working directory and `exec`s `claude --resume <session-id>`

## License

MIT
