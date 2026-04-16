<!-- winkers-snippet-version: 0.8.4 -->
## Architectural context (Winkers)

[Winkers](https://github.com/n1cke1/winkers) MCP: function-level dependency graph, zones, rules. Use before non-trivial edits.

### Workflow

1. `orient` with `include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, rules (`title` + `wrong_approach` one-liner). **First call.**
2. `browse` with `zone` or `file` — mid-level inventory: function list with LLM intents (`"file::fn (callers) — intent"`). With `file=`, caller call-sites are inlined under each fn (`"  ← caller_file:line  expression"`) so you see who invokes what before editing. Use to pick a target before deep-dive.
3. `before_create` with `intent: "<what you want>"` — matches, affected callers (expressions + risk). **Prefer explicit targets** in the intent: `fn_name()` / `Class.method()` / path / `file.py::fn`. **One call per concrete change**, not one per feature — batched intents dilute signal. **Before writing any code.**
4. Write / edit code.
5. `impact_check` with `file_path: "<path>"` — graph update + duplicate + broken-import check. Auto via hook in Claude Code.

### On demand

| Tool | When |
|------|------|
| `browse` with `zone` / `file` / `min_callers` / `limit` / `offset` | list functions + intents, paginated |
| `scope` with `file` or `function` | coupling, caller expressions, `impact` (risk / safe+dangerous ops), `similar_logic` |
| `rule_read` with `category` | full rule text when the one-liner isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper call-graph / endpoints / risk-ranked fns |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

### Key concepts

- **locked** — has callers; don't change signature without updating them.
- **free** — no callers; modify freely.
- **value_locked** — module-level literal set; removing a value breaks callers passing it as a literal.
- **risk_level** — `low`/`medium`/`high`/`critical` per function from `scope.impact` / `hotspots`; heed `dangerous_operations` before editing.
- **secondary_intents** — inline sub-task tags; `similar_logic` flags duplicated logic — extract rather than duplicate.
- **direct_caller_files** vs **migration_cost** (`before_create.files`) — `direct_caller_files` = files that actually *call* your target fn (tight surface). `migration_cost` = raw import-edge count (loose upper bound). Prefer `direct_caller_files` on fn-level intents.
- **route / http_method** — HTTP-handler marker (Flask / FastAPI / Django / aiohttp). Inlined in `scope`, `browse` (`[METHOD /path]`), `hotspots`, `before_create.affected_fns`.
