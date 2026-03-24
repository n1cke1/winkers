## Architectural context (Winkers)

This project uses [Winkers](https://github.com/winkers/winkers) for
function-level dependency tracking. MCP server `winkers` is connected.

Before modifying code, use:
- `map(detail="zones")` — project structure overview
- `scope(file="<path>")` — locked/free functions and callers
- `analyze(files=[...])` — verify no locked signatures broken after changes

**locked** = has callers depending on signature. Do not change param types,
order, or return type without updating all callers.
