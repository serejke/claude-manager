#!/usr/bin/env python3
"""
polyclaude — the entry point to Claude Code: pick any directory, resume any session.

Usage:
    polyclaude [--binary NAME] [--unsafe] [--cwd-limit N] [count]

Tabs:
    New session  - launch claude in a recent cwd (current dir pinned)
    Resume       - pick from recent sessions; expand and resume
    Settings     - toggle persistent options (e.g. skip-permissions)

Controls:
    Tab               - cycle tabs
    Up/Down or j/k    - navigate within a tab
    Enter             - launch / expand / toggle
    Space             - toggle setting (Settings tab)
    /                 - search sessions (Resume tab)
    Esc or Left       - back to list / clear search
    q / Ctrl+C / Ctrl+D - quit
"""

import argparse
import curses
import json
import locale
import os
import sys
import textwrap
import time
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

try:
    __version__ = _pkg_version("polyclaude")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"


CLAUDE_DIR = Path.home() / ".claude" / "projects"
SESSIONS_DIR = Path.home() / ".claude" / "sessions"
HOME = str(Path.home())
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "polyclaude"
CONFIG_PATH = CONFIG_DIR / "config.json"


# ── Config ───────────────────────────────────────────────────────────────────


@dataclass
class Config:
    skip_permissions: bool = False


def load_config() -> Config:
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return Config()
    return Config(skip_permissions=bool(data.get("skip_permissions", False)))


def save_config(cfg: Config) -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump({"skip_permissions": cfg.skip_permissions}, f, indent=2)
    except OSError:
        pass


SETTINGS_ITEMS: list[tuple[str, str, str]] = [
    (
        "skip_permissions",
        "Dangerously skip permissions",
        "Pass --dangerously-skip-permissions to claude. Disable to require per-tool approval.",
    ),
]


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class ChatMessage:
    role: str          # "user" or "assistant"
    text: str
    timestamp: str
    is_agent: bool = False
    agent_id: str = ""


@dataclass
class SessionInfo:
    session_id: str
    cwd: str
    file_path: Path
    mtime: float
    first_user_msg: str
    last_user_msg: str
    timestamp_first: str
    timestamp_last: str
    name: str = ""
    user_msgs: int = 0
    assistant_msgs: int = 0
    agent_msgs: int = 0
    tool_uses: int = 0
    duration_min: int = 0
    messages: list[ChatMessage] = field(default_factory=list)
    messages_loaded: bool = False
    raw_content: str = ""


@dataclass
class CwdEntry:
    cwd: str
    session_count: int
    last_used_epoch: float


# ── Parsing ──────────────────────────────────────────────────────────────────


def extract_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return " ".join(parts).strip()
    return ""


def is_tool_result_only(content) -> bool:
    """Check if content is purely tool_result (automated response, not human)."""
    if isinstance(content, list):
        types = {c.get("type") for c in content if isinstance(c, dict)}
        return types <= {"tool_result", "text"} and "tool_result" in types
    return False


def is_noise(text: str) -> bool:
    return (
        text.startswith("<local-command-")
        or text.startswith("<command-name>")
        or text.startswith("<command-message>")
        or not text
    )


def truncate(text: str, length: int = 120) -> str:
    text = " ".join(text.split())
    return text[:length] + "..." if len(text) > length else text


def format_ts(ts: str) -> str:
    if not ts or "T" not in ts:
        return ts or "?"
    return ts.replace("T", " ").split(".")[0].replace("Z", "")


def parse_ts_epoch(ts: str) -> float:
    """Parse ISO timestamp to epoch seconds, or 0 on failure."""
    if not ts:
        return 0.0
    try:
        from datetime import datetime
        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
        return 0.0


def human_relative_time(epoch: float) -> str:
    if epoch <= 0:
        return ""
    delta = time.time() - epoch
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def peek_session_cwd(path: Path, max_lines: int = 5) -> str | None:
    """Read first few JSONL lines to extract cwd cheaply."""
    try:
        with open(path, "r") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cwd = obj.get("cwd")
                if cwd:
                    return cwd
    except (OSError, UnicodeDecodeError):
        return None
    return None


def collect_recent_cwds(limit: int, current_cwd: str | None) -> list[CwdEntry]:
    """Walk all session files, group by cwd, return recent ones with current pinned."""
    if not CLAUDE_DIR.is_dir():
        return [CwdEntry(current_cwd, 0, 0.0)] if current_cwd else []

    stats: dict[str, dict] = {}
    for project_dir in CLAUDE_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.iterdir():
            if f.suffix != ".jsonl" or not f.is_file():
                continue
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            cwd = peek_session_cwd(f)
            if not cwd:
                continue
            d = stats.setdefault(cwd, {"count": 0, "last": 0.0})
            d["count"] += 1
            if mtime > d["last"]:
                d["last"] = mtime

    entries = [CwdEntry(cwd, d["count"], d["last"]) for cwd, d in stats.items()]
    entries.sort(key=lambda e: e.last_used_epoch, reverse=True)

    if current_cwd:
        existing = next((e for e in entries if e.cwd == current_cwd), None)
        if existing:
            entries.remove(existing)
            entries.insert(0, existing)
        else:
            entries.insert(0, CwdEntry(current_cwd, 0, 0.0))

    return entries[:limit]


def parse_session(jsonl_path: Path, load_messages: bool = False) -> SessionInfo | None:
    first_user = last_user = session_id = cwd = ts_first = ts_last = None
    custom_title = ""
    messages: list[ChatMessage] = []
    first_ts_epoch = 0.0
    last_ts_epoch = 0.0
    n_user = n_assistant = n_agent = n_tools = 0

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if session_id is None and obj.get("sessionId"):
                    session_id = obj["sessionId"]
                if cwd is None and obj.get("cwd"):
                    cwd = obj["cwd"]

                # User-set session name lives in a "custom-title" line (newer
                # Claude Code). Last one wins — names can be changed mid-session.
                if obj.get("type") == "custom-title":
                    title = obj.get("customTitle")
                    if isinstance(title, str) and title.strip():
                        custom_title = title.strip()

                # Track timestamps for duration
                ts_raw = obj.get("timestamp", "")
                if ts_raw:
                    epoch = parse_ts_epoch(ts_raw)
                    if epoch:
                        if first_ts_epoch == 0.0:
                            first_ts_epoch = epoch
                        last_ts_epoch = epoch

                msg_type = obj.get("type", "")
                if msg_type not in ("user", "assistant"):
                    continue

                msg = obj.get("message", {})
                content = msg.get("content", "")
                is_sidechain = obj.get("isSidechain", False)
                agent_id = obj.get("agentId", "")

                # Count tool_use blocks in assistant messages
                if msg_type == "assistant" and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            n_tools += 1
                            if c.get("name") == "Agent":
                                n_agent += 1

                # For user type: skip tool results (automated)
                if msg_type == "user":
                    if is_tool_result_only(content):
                        continue
                    text = extract_text(content)
                    if is_noise(text):
                        continue
                    n_user += 1
                    ts = obj.get("timestamp", "")
                    if first_user is None:
                        first_user = text
                        ts_first = ts
                    last_user = text
                    ts_last = ts
                    if load_messages:
                        messages.append(ChatMessage(
                            role="user", text=text, timestamp=ts,
                        ))

                elif msg_type == "assistant":
                    if isinstance(content, list):
                        text_parts = []
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                t = c.get("text", "").strip()
                                if t:
                                    text_parts.append(t)
                        text = "\n".join(text_parts)
                    else:
                        text = str(content).strip()

                    n_assistant += 1

                    if text and load_messages:
                        messages.append(ChatMessage(
                            role="agent" if (is_sidechain or agent_id) else "assistant",
                            text=text,
                            timestamp=obj.get("timestamp", ""),
                            is_agent=bool(is_sidechain or agent_id),
                            agent_id=agent_id or "",
                        ))

    except (OSError, UnicodeDecodeError):
        return None

    if not first_user or not session_id:
        return None

    duration = int((last_ts_epoch - first_ts_epoch) / 60) if last_ts_epoch > first_ts_epoch else 0

    return SessionInfo(
        session_id=session_id,
        cwd=cwd or "?",
        file_path=jsonl_path,
        mtime=jsonl_path.stat().st_mtime,
        name=custom_title,
        first_user_msg=first_user,
        last_user_msg=last_user or first_user,
        timestamp_first=ts_first or "",
        timestamp_last=ts_last or "",
        user_msgs=n_user,
        assistant_msgs=n_assistant,
        agent_msgs=n_agent,
        tool_uses=n_tools,
        duration_min=duration,
        messages=messages,
        messages_loaded=load_messages,
    )


def load_messages(session: SessionInfo):
    """Lazily load full conversation for a session."""
    if session.messages_loaded:
        return
    full = parse_session(session.file_path, load_messages=True)
    if full:
        session.messages = full.messages
    session.messages_loaded = True


def load_session_names() -> dict[str, str]:
    """User-set session names from the legacy ``~/.claude/sessions/<pid>.json``
    store (older Claude Code). These files are keyed by live PID and ephemeral,
    so they only cover running/recent sessions. Newer Claude Code persists the
    name as a ``custom-title`` line inside each session jsonl instead — that is
    read in ``parse_session`` and takes precedence. This is kept as a fallback
    so names set by older versions still show for past chats.
    """
    names: dict[str, str] = {}
    if not SESSIONS_DIR.is_dir():
        return names
    for f in SESSIONS_DIR.iterdir():
        if f.suffix != ".json" or not f.is_file():
            continue
        try:
            with open(f) as fp:
                obj = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        sid = obj.get("sessionId")
        nm = obj.get("name")
        if sid and isinstance(nm, str) and nm.strip():
            names[sid] = nm.strip()
    return names


def find_sessions(top_n: int) -> list[SessionInfo]:
    if not CLAUDE_DIR.is_dir():
        print(f"Claude projects dir not found: {CLAUDE_DIR}", file=sys.stderr)
        sys.exit(1)

    jsonl_files: list[tuple[float, Path]] = []
    for project_dir in CLAUDE_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.iterdir():
            if f.suffix == ".jsonl" and f.is_file():
                jsonl_files.append((f.stat().st_mtime, f))

    jsonl_files.sort(key=lambda x: x[0], reverse=True)
    candidates = jsonl_files[: top_n * 3]

    legacy_names = load_session_names()
    sessions = []
    for _, path in candidates:
        info = parse_session(path)
        if info:
            # New format: name parsed from the jsonl's custom-title line.
            # Old format: fall back to the legacy per-pid sessions store.
            if not info.name:
                info.name = legacy_names.get(info.session_id, "")
            try:
                info.raw_content = path.read_text(errors="replace")
            except OSError:
                pass
            sessions.append(info)
        if len(sessions) >= top_n:
            break
    return sessions


def partition_by_cwd(sessions: list[SessionInfo], cwd: str) -> list[SessionInfo]:
    """Sessions in `cwd` first (preserving order), then the rest."""
    here = [s for s in sessions if s.cwd == cwd]
    other = [s for s in sessions if s.cwd != cwd]
    return here + other


def section_header(idx: int, sessions: list[SessionInfo], current_cwd: str,
                   in_search: bool) -> str | None:
    """Header text to draw above `sessions[idx]`, or None."""
    if in_search or not sessions or not current_cwd:
        return None
    curr = sessions[idx]
    prev = sessions[idx - 1] if idx > 0 else None
    if idx == 0:
        return "── In this directory ──" if curr.cwd == current_cwd \
            else "── Other directories ──"
    if prev and prev.cwd == current_cwd and curr.cwd != current_cwd:
        return "── Other directories ──"
    return None


def search_sessions(sessions: list[SessionInfo], query: str) -> list[SessionInfo]:
    """Filter sessions whose raw_content contains query (case-insensitive)."""
    q = query.lower()
    return [s for s in sessions if q in s.raw_content.lower()]


def _snippet_window(display_text: str, query_lower: str, width: int) -> str:
    """Extract a ~width-char window around the first match in display_text."""
    dt_lower = display_text.lower()
    match_pos = dt_lower.find(query_lower)
    if match_pos == -1:
        return truncate(display_text, width)

    half = (width - len(query_lower)) // 2
    start = max(0, match_pos - half)
    end = min(len(display_text), start + width)
    if end - start < width:
        start = max(0, end - width)

    snippet = display_text[start:end]
    if start > 0:
        snippet = "..." + snippet[3:]
    if end < len(display_text):
        snippet = snippet[:-3] + "..."
    return snippet


def extract_match_snippets(raw_content: str, query: str, width: int = 120,
                           max_matches: int = 10) -> list[str]:
    """Extract readable snippets around all matches of query in raw_content."""
    q_lower = query.lower()
    rc_lower = raw_content.lower()
    snippets: list[str] = []
    seen_lines: set[int] = set()  # deduplicate by JSONL line start
    pos = 0

    while len(snippets) < max_matches:
        pos = rc_lower.find(q_lower, pos)
        if pos == -1:
            break

        line_start = raw_content.rfind("\n", 0, pos) + 1
        if line_start in seen_lines:
            pos += len(q_lower)
            continue
        seen_lines.add(line_start)

        line_end = raw_content.find("\n", pos)
        if line_end == -1:
            line_end = len(raw_content)
        line = raw_content[line_start:line_end].strip()

        # Try to extract readable text from JSON
        display_text = line
        try:
            obj = json.loads(line)
            msg_content = obj.get("message", {}).get("content", "")
            extracted = extract_text(msg_content)
            if extracted:
                display_text = extracted
        except (json.JSONDecodeError, AttributeError):
            pass

        # Split on newlines, find the line containing the match, snippet that
        display_lines = display_text.split("\n")
        best_line = None
        for dl in display_lines:
            if q_lower in dl.lower():
                best_line = dl.strip()
                break
        if best_line:
            display_text = " ".join(best_line.split())
        else:
            display_text = " ".join(display_text.split())

        snippet = _snippet_window(display_text, q_lower, width)
        if snippet:
            snippets.append(snippet)

        pos = line_end + 1

    return snippets


# ── Curses TUI ───────────────────────────────────────────────────────────────


def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)     # index number
    curses.init_pair(2, curses.COLOR_GREEN, -1)     # cwd path
    curses.init_pair(3, curses.COLOR_YELLOW, -1)    # message labels
    curses.init_pair(4, curses.COLOR_WHITE, -1)     # normal text
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)   # selected row
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)   # assistant text
    curses.init_pair(7, curses.COLOR_BLUE, -1)      # agent text
    curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_WHITE)  # header bar
    curses.init_pair(9, curses.COLOR_BLUE, -1)      # named session title


C_IDX = 1
C_PATH = 2
C_LABEL = 3
C_TEXT = 4
C_SELECTED = 5
C_ASSISTANT = 6
C_AGENT = 7
C_HEADER = 8
C_TITLE = 9


def safe_addstr(win, y, x, text, attr=0):
    """addstr that won't crash at screen edges."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    max_len = w - x - 1
    if max_len <= 0:
        return
    try:
        win.addnstr(y, x, text, max_len, attr)
    except curses.error:
        pass


TAB_LABELS = (" New session ", " Resume ", " Settings ")


def draw_tab_bar(stdscr, active_tab: int, help_text: str = ""):
    h, w = stdscr.getmaxyx()
    safe_addstr(stdscr, 0, 0, " " * w, curses.color_pair(C_HEADER))
    x = 2
    for i, label in enumerate(TAB_LABELS):
        if i == active_tab:
            attr = curses.color_pair(C_HEADER) | curses.A_BOLD | curses.A_REVERSE
        else:
            attr = curses.color_pair(C_HEADER)
        safe_addstr(stdscr, 0, x, label, attr)
        x += len(label) + 1
    if not help_text:
        help_text = " Tab:switch  q:quit "
    safe_addstr(stdscr, 0, max(x + 2, w - len(help_text) - 2),
                help_text, curses.color_pair(C_HEADER))


def draw_new_tab_view(stdscr, entries: list[CwdEntry], cursor: int, scroll: int,
                      current_cwd: str):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    draw_tab_bar(stdscr, active_tab=0,
                 help_text=" Tab:switch  Enter:launch  q:quit ")

    safe_addstr(stdscr, 2, 2, "Start a new Claude session in:",
                curses.color_pair(C_LABEL) | curses.A_BOLD)

    if not entries:
        safe_addstr(stdscr, 4, 2, "(no directories found in session history)",
                    curses.A_DIM)
    else:
        y = 4
        for i in range(scroll, len(entries)):
            if y >= h - 1:
                break
            e = entries[i]
            is_sel = i == cursor
            cwd_short = e.cwd.replace(HOME, "~")
            marker = " > " if is_sel else "   "

            attr_marker = curses.color_pair(C_IDX) | curses.A_BOLD if is_sel else 0
            attr_path = curses.color_pair(C_PATH) | curses.A_BOLD
            safe_addstr(stdscr, y, 0, marker, attr_marker)
            safe_addstr(stdscr, y, len(marker), cwd_short, attr_path)

            parts = []
            if e.cwd == current_cwd:
                parts.append("current dir")
            if e.session_count > 0:
                parts.append(f"{e.session_count} session{'s' if e.session_count != 1 else ''}")
            rel = human_relative_time(e.last_used_epoch)
            if rel:
                parts.append(rel)
            stats = "  ·  ".join(parts)
            x_stats = len(marker) + len(cwd_short) + 3
            safe_addstr(stdscr, y, x_stats, stats, curses.A_DIM)
            y += 1

    safe_addstr(stdscr, h - 1, 0, " " * w, curses.A_DIM)
    safe_addstr(stdscr, h - 1, 2,
                f" {len(entries)} dirs  ·  cursor: {cursor + 1}/{max(1, len(entries))}  ·  Enter to launch ",
                curses.A_DIM)

    stdscr.refresh()


def draw_settings_view(stdscr, cfg: Config, cursor: int):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    draw_tab_bar(stdscr, active_tab=2,
                 help_text=" Tab:switch  Space:toggle  q:quit ")

    safe_addstr(stdscr, 2, 2, "Settings", curses.color_pair(C_LABEL) | curses.A_BOLD)

    y = 4
    for i, (key, label, help_text) in enumerate(SETTINGS_ITEMS):
        is_sel = i == cursor
        value = bool(getattr(cfg, key, False))
        marker = " > " if is_sel else "   "
        checkbox = "[x]" if value else "[ ]"

        attr_marker = curses.color_pair(C_IDX) | curses.A_BOLD if is_sel else 0
        attr_check = curses.color_pair(C_PATH) | curses.A_BOLD if value else curses.A_DIM
        attr_label = curses.color_pair(C_TEXT) | curses.A_BOLD

        safe_addstr(stdscr, y, 0, marker, attr_marker)
        safe_addstr(stdscr, y, len(marker), checkbox, attr_check)
        safe_addstr(stdscr, y, len(marker) + 4, label, attr_label)
        y += 1
        # help line
        for line in textwrap.wrap(help_text, width=w - 12):
            safe_addstr(stdscr, y, len(marker) + 4, line, curses.A_DIM)
            y += 1
        y += 1

    cfg_short = str(CONFIG_PATH).replace(HOME, "~")
    safe_addstr(stdscr, h - 2, 2, f"Saved to: {cfg_short}", curses.A_DIM)

    safe_addstr(stdscr, h - 1, 0, " " * w, curses.A_DIM)
    safe_addstr(stdscr, h - 1, 2,
                " Space/Enter to toggle  ·  Tab to switch ",
                curses.A_DIM)

    stdscr.refresh()


def draw_list_view(stdscr, sessions: list[SessionInfo], cursor: int, scroll: int,
                   search_mode: bool = False, search_query: str = "",
                   active_query: str = "", match_snippets: dict | None = None,
                   current_cwd: str = ""):
    h, w = stdscr.getmaxyx()
    stdscr.erase()

    if active_query:
        help_text = f" Esc:clear  /:search  Enter:expand "
    else:
        help_text = " Tab:switch  /:search  Enter:expand  q:quit "
    draw_tab_bar(stdscr, active_tab=1, help_text=help_text)

    if active_query:
        sub = f'Search: "{active_query}" ({len(sessions)} matches)'
        safe_addstr(stdscr, 1, 2, sub, curses.color_pair(C_LABEL) | curses.A_BOLD)

    has_snippets = match_snippets is not None
    in_search = bool(active_query)
    y = 2

    if not sessions and active_query:
        safe_addstr(stdscr, 2, 2, f'No sessions match "{active_query}"', curses.color_pair(C_LABEL))
        safe_addstr(stdscr, 3, 2, "Press Esc to clear search", curses.A_DIM)
    else:
        for i in range(scroll, len(sessions)):
            if y >= h - 1:
                break

            header = section_header(i, sessions, current_cwd, in_search)
            if header:
                if y > 2:
                    y += 1  # blank spacer between groups
                    if y >= h - 1:
                        break
                safe_addstr(stdscr, y, 2, header,
                            curses.color_pair(C_LABEL) | curses.A_BOLD)
                y += 1
                if y >= h - 1:
                    break

            s = sessions[i]
            is_sel = i == cursor
            cwd_short = s.cwd.replace(HOME, "~")
            ts = format_ts(s.timestamp_last)

            marker = " > " if is_sel else "   "

            attr_marker = curses.color_pair(C_IDX) | curses.A_BOLD
            attr_path = curses.color_pair(C_PATH) | curses.A_BOLD
            attr_dim = curses.A_DIM

            # Optional header: session name (if user-set), in blue
            if s.name:
                attr_name = curses.color_pair(C_TITLE) | curses.A_BOLD
                name_width = max(10, w - len(marker) - 2)
                safe_addstr(stdscr, y, 0, marker, attr_marker if is_sel else 0)
                safe_addstr(stdscr, y, len(marker), truncate(s.name, name_width), attr_name)
                y += 1
                cwd_marker = "   "  # already shown the row marker
            else:
                cwd_marker = marker

            # Line 1: marker (or indent) + cwd + timestamp
            safe_addstr(stdscr, y, 0, cwd_marker,
                        attr_marker if (is_sel and not s.name) else 0)
            safe_addstr(stdscr, y, len(cwd_marker), cwd_short, attr_path)
            safe_addstr(stdscr, y, len(cwd_marker) + len(cwd_short) + 2, ts, attr_dim)
            y += 1

            # Line 2: stats
            if s.duration_min >= 60:
                dur = f"{s.duration_min // 60}h{s.duration_min % 60:02d}m"
            else:
                dur = f"{s.duration_min}m"
            stats = f"{dur}  you:{s.user_msgs}  claude:{s.assistant_msgs}  tools:{s.tool_uses}"
            if s.agent_msgs > 0:
                stats += f"  agents:{s.agent_msgs}"
            safe_addstr(stdscr, y, 5, stats, attr_dim)
            y += 1

            # Line 3: first message
            safe_addstr(stdscr, y, 5, "first:", curses.color_pair(C_LABEL))
            safe_addstr(stdscr, y, 11, " " + truncate(s.first_user_msg, w - 13), curses.color_pair(C_TEXT))
            y += 1

            # Line 4: last message (if different)
            if s.first_user_msg != s.last_user_msg:
                safe_addstr(stdscr, y, 5, " last:", curses.color_pair(C_LABEL))
                safe_addstr(stdscr, y, 11, " " + truncate(s.last_user_msg, w - 13), curses.color_pair(C_TEXT))
            y += 1

            # Match snippets (search mode only)
            if has_snippets and s.session_id in match_snippets:
                for snippet in match_snippets[s.session_id]:
                    if y >= h - 1:
                        break
                    safe_addstr(stdscr, y, 5, "match:", curses.color_pair(C_LABEL))
                    safe_addstr(stdscr, y, 11, " " + truncate(snippet, w - 13),
                                curses.color_pair(C_TEXT) | curses.A_BOLD)
                    y += 1

            # separator
            y += 1

    # Footer / search input
    safe_addstr(stdscr, h - 1, 0, " " * w, curses.color_pair(C_HEADER) if search_mode else curses.A_DIM)
    if search_mode:
        safe_addstr(stdscr, h - 1, 2, f"/ {search_query}_", curses.color_pair(C_HEADER) | curses.A_BOLD)
    else:
        n = len(sessions)
        if n > 0:
            footer = f" {n} sessions | cursor: {cursor + 1}/{n} "
        else:
            footer = f" 0 sessions "
        safe_addstr(stdscr, h - 1, 2, footer, curses.A_DIM)

    stdscr.refresh()


def wrap_text(text: str, width: int) -> list[str]:
    """Wrap text to fit width, preserving existing line breaks."""
    result = []
    for paragraph in text.split("\n"):
        paragraph = paragraph.rstrip()
        if not paragraph:
            result.append("")
            continue
        result.extend(textwrap.wrap(paragraph, width=width) or [""])
    return result


def draw_detail_view(stdscr, session: SessionInfo, scroll: int) -> list[str]:
    """Draw expanded session conversation. Returns the rendered lines for scroll calc."""
    h, w = stdscr.getmaxyx()
    stdscr.erase()

    cwd_short = session.cwd.replace(HOME, "~")
    ts = format_ts(session.timestamp_last)

    # Header
    safe_addstr(stdscr, 0, 0, " " * w, curses.color_pair(C_HEADER) | curses.A_BOLD)
    safe_addstr(stdscr, 0, 2, f" {cwd_short} ", curses.color_pair(C_HEADER) | curses.A_BOLD)
    help_text = " Esc:back  Enter:resume  j/k:scroll "
    safe_addstr(stdscr, 0, max(2, w - len(help_text) - 2), help_text, curses.color_pair(C_HEADER))

    # Sub-header: session info
    safe_addstr(stdscr, 1, 2, f"Session: {session.session_id}", curses.A_DIM)
    safe_addstr(stdscr, 1, 2 + len(f"Session: {session.session_id}") + 3, ts, curses.A_DIM)

    # Build rendered lines: list of (text, attr)
    content_width = w - 6
    rendered: list[tuple[str, int]] = []

    for msg in session.messages:
        if msg.role == "user":
            label = "YOU"
            label_attr = curses.color_pair(C_ASSISTANT) | curses.A_BOLD
            text_attr = curses.color_pair(C_ASSISTANT)
        elif msg.is_agent or msg.role == "agent":
            aid = msg.agent_id[:12] if msg.agent_id else "agent"
            label = f"AGENT({aid})"
            label_attr = curses.A_DIM
            text_attr = curses.A_DIM
        else:
            label = "CLAUDE"
            label_attr = curses.A_DIM
            text_attr = curses.A_DIM

        ts_short = format_ts(msg.timestamp).split(" ")[-1] if msg.timestamp else ""
        rendered.append((f"  {label}  {ts_short}", label_attr))

        # Wrap message text, cap at ~20 lines per message for readability
        lines = wrap_text(msg.text, content_width)
        if len(lines) > 20:
            lines = lines[:19] + [f"  ... ({len(lines) - 19} more lines)"]
        for ln in lines:
            rendered.append((f"    {ln}", text_attr))

        rendered.append(("", 0))  # blank separator

    # Draw with scroll
    y = 3
    total_lines = len(rendered)
    for i in range(scroll, total_lines):
        if y >= h - 1:
            break
        text, attr = rendered[i]
        safe_addstr(stdscr, y, 0, text, attr)
        y += 1

    # Footer
    safe_addstr(stdscr, h - 1, 0, " " * w, curses.A_DIM)
    msgs_count = len(session.messages)
    pct = int(scroll / max(1, total_lines - (h - 4)) * 100) if total_lines > h - 4 else 100
    footer = f" {msgs_count} messages | scroll: {min(pct, 100)}% | Enter to resume "
    safe_addstr(stdscr, h - 1, 2, footer, curses.A_DIM)

    stdscr.refresh()
    return rendered


def curses_main(stdscr, sessions: list[SessionInfo],
                cwd_entries: list[CwdEntry], current_cwd: str,
                cfg: Config,
                ) -> tuple[str, object] | None:
    """Returns ("new", cwd_str) | ("resume", SessionInfo) | None."""
    curses.curs_set(0)
    init_colors()
    stdscr.timeout(-1)

    active_tab = 0 if cwd_entries else 1  # default to New if we have cwds

    # New tab state
    new_cursor = 0
    new_scroll = 0

    # Settings tab state
    settings_cursor = 0

    # Resume tab state
    cursor = 0
    list_scroll = 0
    mode = "list"  # "list" or "detail"
    detail_scroll = 0
    detail_rendered: list = []

    # Search state (resume tab)
    search_mode = False
    search_query = ""
    active_query = ""
    all_sessions = sessions
    filtered_sessions = None
    match_snippets: dict[str, list[str]] = {}

    while True:
        h, w = stdscr.getmaxyx()
        display_sessions = filtered_sessions if filtered_sessions is not None else all_sessions
        has_snippets = filtered_sessions is not None
        ITEM_HEIGHT = 5  # base height without snippets

        # ── New tab ──────────────────────────────────────────────────────
        if active_tab == 0:
            usable = h - 5  # tab bar + heading + footer + margins
            if new_cursor < new_scroll:
                new_scroll = new_cursor
            if new_cursor >= new_scroll + usable:
                new_scroll = new_cursor - usable + 1

            draw_new_tab_view(stdscr, cwd_entries, new_cursor, new_scroll, current_cwd)

            key = stdscr.getch()
            if key in (3, 4):
                return None
            if key == 9:  # Tab
                active_tab = (active_tab + 1) % len(TAB_LABELS)
                continue
            if key in (ord("q"), ord("Q")):
                return None
            if key in (curses.KEY_UP, ord("k")):
                new_cursor = max(0, new_cursor - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                new_cursor = min(max(0, len(cwd_entries) - 1), new_cursor + 1)
            elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):
                if cwd_entries:
                    return ("new", cwd_entries[new_cursor].cwd)
            elif key == curses.KEY_RESIZE:
                pass
            continue

        # ── Settings tab ─────────────────────────────────────────────────
        if active_tab == 2:
            draw_settings_view(stdscr, cfg, settings_cursor)
            key = stdscr.getch()
            if key in (3, 4):
                return None
            if key == 9:  # Tab
                active_tab = (active_tab + 1) % len(TAB_LABELS)
                continue
            if key in (ord("q"), ord("Q")):
                return None
            if key in (curses.KEY_UP, ord("k")):
                settings_cursor = max(0, settings_cursor - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                settings_cursor = min(max(0, len(SETTINGS_ITEMS) - 1),
                                      settings_cursor + 1)
            elif key in (ord(" "), ord("\n"), curses.KEY_ENTER, 10, 13):
                if SETTINGS_ITEMS:
                    field_key = SETTINGS_ITEMS[settings_cursor][0]
                    setattr(cfg, field_key, not getattr(cfg, field_key))
                    save_config(cfg)
            elif key == curses.KEY_RESIZE:
                pass
            continue

        # ── Resume tab ───────────────────────────────────────────────────
        if mode == "list":
            # Compute actual height of each session item
            in_search_now = bool(active_query)
            def item_height(idx):
                s = display_sessions[idx]
                lines = 4  # marker+path, stats, first msg, separator
                if s.first_user_msg != s.last_user_msg:
                    lines += 1
                if s.name:
                    lines += 1  # optional name header
                if has_snippets and s.session_id in match_snippets:
                    lines += len(match_snippets[s.session_id])
                # section header above this item
                if section_header(idx, display_sessions, current_cwd, in_search_now):
                    lines += 1
                    if idx > 0:
                        lines += 1  # blank spacer between groups
                return lines

            # Scroll up: ensure cursor item is visible
            if cursor < list_scroll:
                list_scroll = cursor

            # Scroll down: ensure cursor item fits on screen
            usable = h - 3  # header(1) + blank(1) + footer(1)
            # Sum heights from list_scroll to cursor; if exceeds usable, bump scroll
            while list_scroll < cursor:
                total = sum(item_height(i) for i in range(list_scroll, cursor + 1))
                if total <= usable:
                    break
                list_scroll += 1

            draw_list_view(stdscr, display_sessions, cursor, list_scroll,
                           search_mode=search_mode, search_query=search_query,
                           active_query=active_query,
                           match_snippets=match_snippets if has_snippets else None,
                           current_cwd=current_cwd)
        else:
            detail_rendered_result = draw_detail_view(stdscr, display_sessions[cursor], detail_scroll)

        # Use get_wch in search mode for Unicode support, getch otherwise
        if mode == "list" and search_mode:
            try:
                wch = stdscr.get_wch()
            except curses.error:
                continue
            # Normalize to (key_int, key_char) for uniform handling
            if isinstance(wch, int):
                key = wch
            else:
                key = ord(wch) if len(wch) == 1 else -1

            # Ctrl+C / Ctrl+D
            if key in (3, 4):
                return None
            if key == 27:  # Esc — cancel input
                # Peek for escape sequence (alt/meta key combos)
                stdscr.nodelay(True)
                next_ch = stdscr.getch()
                stdscr.nodelay(False)
                if next_ch == -1:
                    # Real Esc press (no following chars)
                    search_mode = False
                    search_query = ""
                    curses.curs_set(0)
                # else: ignore escape sequence
            elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):
                search_mode = False
                curses.curs_set(0)
                if search_query.strip():
                    active_query = search_query.strip()
                    filtered_sessions = search_sessions(all_sessions, active_query)
                    match_snippets = {}
                    for s in filtered_sessions:
                        match_snippets[s.session_id] = extract_match_snippets(
                            s.raw_content, active_query)
                else:
                    active_query = ""
                    filtered_sessions = None
                    match_snippets = {}
                cursor = 0
                list_scroll = 0
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                search_query = search_query[:-1]
            elif isinstance(wch, str) and wch.isprintable():
                search_query += wch
            continue

        key = stdscr.getch()

        # Ctrl+C (3) and Ctrl+D (4) always quit
        if key in (3, 4):
            return None

        if mode == "list":
            if key == 9:  # Tab
                active_tab = (active_tab + 1) % len(TAB_LABELS)
                continue
            if key in (ord("q"), ord("Q")):
                return None
            elif key == ord("/"):
                search_mode = True
                search_query = ""
                curses.curs_set(1)
            elif key == 27:  # Esc — clear search filter
                if filtered_sessions is not None:
                    active_query = ""
                    filtered_sessions = None
                    match_snippets = {}
                    cursor = 0
                    list_scroll = 0
            elif key in (curses.KEY_UP, ord("k")):
                if cursor > 0:
                    cursor -= 1
            elif key in (curses.KEY_DOWN, ord("j")):
                if cursor < len(display_sessions) - 1:
                    cursor += 1
            elif key in (curses.KEY_RIGHT, ord("\n"), curses.KEY_ENTER, 10, 13):
                if display_sessions:
                    load_messages(display_sessions[cursor])
                    mode = "detail"
                    detail_scroll = 0
            elif key == curses.KEY_RESIZE:
                pass  # redraw on next loop

        elif mode == "detail":
            max_scroll = max(0, len(detail_rendered_result) - (h - 4))
            if key in (27, curses.KEY_LEFT):  # Esc or Left
                mode = "list"
            elif key in (ord("q"), ord("Q")):
                return None
            elif key in (curses.KEY_UP, ord("k")):
                detail_scroll = max(0, detail_scroll - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                detail_scroll = min(max_scroll, detail_scroll + 1)
            elif key in (curses.KEY_PPAGE,):  # Page Up
                detail_scroll = max(0, detail_scroll - (h - 4))
            elif key in (curses.KEY_NPAGE,):  # Page Down
                detail_scroll = min(max_scroll, detail_scroll + (h - 4))
            elif key in (ord("g"),):  # top
                detail_scroll = 0
            elif key in (ord("G"),):  # bottom
                detail_scroll = max_scroll
            elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):
                return ("resume", display_sessions[cursor])
            elif key == curses.KEY_RESIZE:
                pass


def _exec_claude(binary: str, cwd: str, extra_args: list[str], skip_perms: bool):
    cwd_short = cwd.replace(HOME, "~")
    argv = [binary]
    if skip_perms:
        argv.append("--dangerously-skip-permissions")
    argv.extend(extra_args)

    print(f"\033[1mIn: {cwd_short}\033[0m")
    print(f"  {' '.join(argv)}\n")

    os.environ.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
    os.chdir(cwd)
    os.execvp(binary, argv)


def main():
    locale.setlocale(locale.LC_ALL, "")

    parser = argparse.ArgumentParser(
        description="The entry point to Claude Code — pick any directory, resume any session.",
    )
    parser.add_argument(
        "--version", "-V", action="version", version=f"polyclaude {__version__}",
    )
    parser.add_argument(
        "count", nargs="?", type=int, default=20,
        help="number of recent sessions to show (default: 20)",
    )
    parser.add_argument(
        "--binary", "-b",
        default=os.environ.get("CLAUDE_BINARY", "claude"),
        help="claude binary to use (default: $CLAUDE_BINARY or 'claude')",
    )
    parser.add_argument(
        "--unsafe", action="store_true",
        help="force --dangerously-skip-permissions ON for this run (overrides config)",
    )
    parser.add_argument(
        "--cwd-limit", type=int, default=10,
        help="max cwds to list on the New tab (default: 10)",
    )
    args = parser.parse_args()

    current_cwd = os.getcwd()
    cwd_entries = collect_recent_cwds(args.cwd_limit, current_cwd)
    sessions = find_sessions(args.count)
    sessions = partition_by_cwd(sessions, current_cwd)
    cfg = load_config()

    if not cwd_entries and not sessions:
        print("No sessions or directories found in ~/.claude/projects.")
        # Still allow launching in current dir
        cwd_entries = [CwdEntry(current_cwd, 0, 0.0)]

    action = curses.wrapper(
        lambda stdscr: curses_main(stdscr, sessions, cwd_entries, current_cwd, cfg)
    )

    if action is None:
        sys.exit(0)

    skip_perms = cfg.skip_permissions or args.unsafe
    kind, payload = action

    if kind == "new":
        _exec_claude(args.binary, cwd=payload, extra_args=[], skip_perms=skip_perms)
    elif kind == "resume":
        session = payload  # type: SessionInfo
        _exec_claude(args.binary, cwd=session.cwd,
                     extra_args=["--resume", session.session_id],
                     skip_perms=skip_perms)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
