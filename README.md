# polyclaude

> The entry point to Claude Code — pick any directory, resume any session.

Three tabs, one keystroke between them.

### New session

```
┌─[ New session ]─[ Resume ]─[ Settings ]──────────── Tab:switch  q:quit ─┐
│                                                                         │
│  Start a new Claude session in:                                         │
│                                                                         │
│   > ~/code/acme-api      ·  current dir  ·  12 sessions  ·  5m ago      │
│     ~/code/acme-web      ·  47 sessions  ·  2h ago                      │
│     ~/code/notes         ·  9 sessions   ·  1d ago                      │
│     ~/code/sandbox       ·  3 sessions   ·  4d ago                      │
│     ~/code/dotfiles      ·  1 session    ·  2w ago                      │
│     ...                                                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### Resume

```
┌─[ New session ]─[ Resume ]─[ Settings ]──── /:search  Enter:expand  q ─┐
│                                                                        │
│  > 1. ~/code/acme-api                                       5m ago     │
│       "fix flaky retry test in billing client"                         │
│       18 msgs  ·  42 tools  ·  3 agents  ·  24m                        │
│                                                                        │
│    2. ~/code/acme-web                                       2h ago     │
│       "add dark mode toggle to settings page"                          │
│       9 msgs   ·  11 tools  ·  0 agents  ·  8m                         │
│                                                                        │
│    3. ~/code/notes                                          1d ago     │
│       "summarize last week's meeting notes"                            │
│       4 msgs   ·  2 tools   ·  0 agents  ·  3m                         │
└────────────────────────────────────────────────────────────────────────┘
```

### Settings

```
┌─[ New session ]─[ Resume ]─[ Settings ]──── Space:toggle  Tab:switch ─┐
│                                                                       │
│  Settings                                                             │
│                                                                       │
│   > [x] Dangerously skip permissions                                  │
│         Pass --dangerously-skip-permissions to claude.                │
│         Disable to require per-tool approval.                         │
│                                                                       │
│  Saved to: ~/.config/polyclaude/config.json                           │
└───────────────────────────────────────────────────────────────────────┘
```

## Install

```sh
uv tool install git+https://github.com/serejke/polyclaude
```

Or:

```sh
curl -sSL https://raw.githubusercontent.com/serejke/polyclaude/main/install.sh | bash
```

Requires [uv](https://docs.astral.sh/uv/). Zero runtime deps — pure Python stdlib.

## Use

```sh
polyclaude              # tabbed UI
polyclaude 50           # 50 sessions on Resume tab (default 20)
polyclaude --safe       # skip-permissions OFF for this run
polyclaude -b my-claude # custom claude binary
```

## Keys

| Key               | Action                          |
| ----------------- | ------------------------------- |
| `Tab`             | cycle tabs                      |
| `j` `k` / `↑` `↓` | navigate                        |
| `Enter`           | launch / expand / toggle        |
| `Space`           | toggle (Settings)               |
| `/`               | search session text (Resume)    |
| `Esc` / `←`       | back / clear search             |
| `g` `G`           | top / bottom (in expanded view) |
| `q` / `^C` / `^D` | quit                            |

## Config

Lives at `~/.config/polyclaude/config.json`. Toggles auto-save when you flip them in the Settings tab.

```json
{
  "skip_permissions": true
}
```

## How it works

Claude Code stores sessions as JSONL in `~/.claude/projects/<path>/<uuid>.jsonl`. polyclaude:

1. Peeks the first lines of every jsonl to extract `cwd` → New tab list.
2. Parses the N most recent sessions for stats and previews → Resume tab.
3. On launch: `chdir` + `execvp` into `claude` — the resumed session inherits the right cwd.

No background threads, no persistent index, no daemon. Just stdlib.

## License

MIT
