<!-- winkers-snippet-version: 0.8.0 -->
## Architectural context (Winkers)

This project uses [Winkers](https://github.com/n1cke1/winkers) for
architectural context: dependency graph, semantic zones, coding conventions,
data flow, and UI mapping. MCP server `winkers` is connected.

### Before modifying code

1. `orient` with `include: ["map", "conventions"]` — project structure, zones, hotspots, data flow, zone intents. **Call first.**
2. `scope` with `file: "<path>"` — locked/free functions, callers, related rules for that file.
3. `orient` with `include: ["rules_list"]` — available coding rule categories; then `rule_read` with `category: "<name>"` for details.

### Before creating new code

- `before_create` with `intent: "<what you want to create>"` — searches for existing implementations. **Call before writing any new function, class, or module.**

### After modifying code

- `after_create` with `file_path: "<path>"` — updates graph, checks impact, coherence, duplicates. **Call after every file write.**

### When task is complete

- `session_done` (no args) — session audit. Returns PASS or FAIL. **Do not consider your task finished until this returns PASS.**

### Key concepts

- **locked** = function has callers depending on its signature. Do not change
  param types, order, or return type without updating all callers.
- **free** = no callers. Modify freely.

### Other tools

- `orient` with `include: ["functions_graph"]` — full indexed function list with caller counts.
- `orient` with `include: ["routes"]` — HTTP endpoints (Flask/FastAPI): method, path, handler, callees.
- `orient` with `include: ["ui_map"]` — Flask route→template links with UI elements (panels, tables, forms, headings).
- `orient` with `include: ["hotspots"]` — functions with many callers; high-impact changes.
- `convention_read` with `target: "<zone>"` — zone intent details (e.g. "app.py", "data_flow", "checklist").
