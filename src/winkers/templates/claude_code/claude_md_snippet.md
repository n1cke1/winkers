<!-- winkers-snippet-version: 0.7.5 -->
## Architectural context (Winkers)

This project uses [Winkers](https://github.com/n1cke1/winkers) for
architectural context: dependency graph, semantic zones, coding conventions,
data flow, and UI mapping. MCP server `winkers` is connected.

### Before modifying code

1. `orient(["map","conventions"])` — project structure, zones, hotspots, data flow, zone intents. **Call first.**
2. `scope(file="<path>")` — locked/free functions, callers, related rules for that file.
3. `orient(["rules_list"])` — available coding rule categories; then `rule_read("<category>")` for details.

### After modifying code

- `scope(function="<name>")` — verify callers are not broken by your change.

### Key concepts

- **locked** = function has callers depending on its signature. Do not change
  param types, order, or return type without updating all callers.
- **free** = no callers. Modify freely.

### Other tools

- `orient(["functions_graph"])` — full indexed function list with caller counts.
- `orient(["routes"])` — HTTP endpoints (Flask/FastAPI): method, path, handler, callees.
- `orient(["ui_map"])` — Flask route→template links with UI elements (panels, tables, forms, headings).
- `orient(["hotspots"])` — functions with many callers; high-impact changes.
- `convention_read("<zone>")` — zone intent details (e.g. "app.py", "data_flow", "checklist").
