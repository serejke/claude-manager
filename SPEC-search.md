# Spec: Search sessions by text content

## Summary

Add text search to the claude-manager TUI, allowing users to filter the session list by searching through conversation content. Activated with `/` in list view (vim convention), filters the already-loaded N sessions in-place.

## UX Flow

1. User presses `/` in list view
2. Search input appears at the bottom of the screen: `/ query_`
3. User types a search query and presses **Enter**
4. Session list filters in-place to only show sessions whose JSONL content matches the query (case-insensitive substring match)
5. Filtered sessions show an extra **match:** line below first/last, displaying the matched snippet with surrounding context
6. User navigates filtered results normally (j/k, Enter to expand, Enter to resume)
7. **Esc** clears the search and restores the full session list
8. Standard text editing in search input: Backspace to delete, Esc to cancel mid-typing

### Layout in search mode

```
 > ~/projects/foo   2025-03-24 14:00
     0h45m  you:12  claude:15  tools:8
     first: I want to add a feature...
      last: can you also fix the tests
     match: "...fix the [auth bug] in login..."

   ~/projects/bar   2025-03-22 09:00
     1h20m  you:8  claude:10  tools:5
     first: set up the new API endpoint
     match: "...auth bug was related to..."

 2 of 20 sessions | /auth bug
```

## Search mechanics

### What gets searched

- **Raw substring match** on each line of the JSONL file
- Case-insensitive
- This means it matches user text, assistant text, tool names, file paths, code snippets — anything in the raw data
- No JSON parsing needed for matching; only for extracting the display snippet

### Snippet extraction

- When a raw line matches, extract a ~120-char window around the match for display
- Highlight or bracket the matched term in the snippet (e.g., `[auth bug]`)
- Show the **first** match found in that session file as the `match:` line
- If multiple lines match, just show the first one (keep it simple)

### Scope and performance

- Search only within the **already-loaded N sessions** (same set visible in the list, controlled by the `count` arg, default 20)
- Users who want to search deeper can launch with a larger N: `claude-manager 100`
- **Raw file content is loaded at startup** alongside metadata parsing — stored as a simple string per session
- On Enter, filter is a pure in-memory substring search across cached content — effectively instant

### Data changes

- `SessionInfo` gets a new field: `raw_content: str` — the full file content read at startup
- `SessionInfo` gets a new field: `match_snippet: str` — populated when search matches, cleared when search is cleared
- `parse_session()` (or `find_sessions()`) reads and stores raw file content during initial load

## Keyboard handling

| Key       | Context                  | Action                            |
| --------- | ------------------------ | --------------------------------- |
| `/`       | list view, not searching | Enter search mode, show input     |
| any char  | search input active      | Append to query                   |
| Backspace | search input active      | Delete last char                  |
| Enter     | search input active      | Execute search, filter list       |
| Esc       | search input active      | Cancel search input, restore list |
| Esc       | search results shown     | Clear search, restore full list   |

## Edge cases

- **Empty query on Enter**: restore full list (same as Esc)
- **No matches**: show "No matches" message in the list area, Esc to return
- **Cursor reset**: after filtering, cursor resets to 0 and scroll resets to 0
- **Detail view**: if user is in detail/expanded view, `/` does nothing — search only from list view

## What does NOT change

- No new dependencies (pure stdlib)
- No new CLI flags (search depth = existing `count` arg)
- No background threads
- No persistent index or cache
- Single-file architecture preserved
