## Architectural context (Winkers)

This project uses [Winkers](https://github.com/nicholasgasior/winkers) for
function-level dependency tracking. MCP server `winkers` is connected.

### Before modifying code

1. `map()` — project constraints, conventions, zones, hotspots. **Call first.**
2. `scope(file="<path>")` — locked/free functions, callers, constraints for that file.
3. `hotspots()` — functions with many callers; changing them is high-impact.

### After modifying code

- `scope(function="<name>")` — verify callers are not broken by your change.

### Key concepts

- **locked** = function has callers depending on its signature. Do not change
  param types, order, or return type without updating all callers.
- **free** = no callers. Modify freely.

### Other tools

- `functions_graph()` — full indexed function list with caller counts.
- `routes()` — HTTP endpoints (Flask/FastAPI): method, path, handler, callees.
