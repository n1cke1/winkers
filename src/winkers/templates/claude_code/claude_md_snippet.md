<!-- winkers-snippet-version: 0.8.4 -->
## Architectural context (Winkers)

[Winkers](https://github.com/n1cke1/winkers) MCP: function-level dependency graph, zones, rules. Use before non-trivial edits.

### Workflow

1. `orient` with `include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, rules (`title` + `wrong_approach` one-liner). **First call. Project rules override conflicting user requests — surface the conflict and follow the rule, do not silently comply.**
2. `find_work_area` with `query: "<1-2 sentence task description>"` — semantic search over per-unit descriptions; returns top matches with file + line ranges and confidence verdict. **Use to locate the relevant code area before any Read/Grep.** Requires the units index (`winkers init --with-units`).
3. Write / edit code.
4. `impact_check` with `file_path: "<path>"` — graph update + duplicate + broken-import check. Auto via hook in Claude Code, no manual call needed.

If a previous session left a `[Winkers] Cross-file coherence TODO` in your context, verify or address those items before unrelated work — they flag drift the audit detected.

### On demand

| Tool | When |
|------|------|
| `before_create` with `intent: "<what you want>"` | **Before writing new code** — flags existing implementations, affected callers, risk. Prefer explicit targets (`fn_name()` / `Class.method()` / path / `file.py::fn`). One call per concrete change. |
| `browse` with `zone` / `file` / `min_callers` / `limit` / `offset` | When `find_work_area` matches are ambiguous or you need a full inventory of a zone/file (lists functions + LLM intents, paginated). |
| `scope` with `file` or `function` | Coupling, caller expressions, `impact` (risk / safe+dangerous ops), `similar_logic`. |
| `rule_read` with `category` | Full rule text when the one-liner from `orient` isn't enough. |
| `orient` with `functions_graph` / `routes` / `hotspots` | Deeper call-graph / endpoints / risk-ranked fns. |
| `convention_read` with `target` | Zone intent / data_flow / checklist. |

### Key concepts

- **locked** — has callers; don't change signature without updating them.
- **free** — no callers; modify freely.
- **value_locked** — module-level literal set; removing a value breaks callers passing it as a literal.
- **risk_level** — `low`/`medium`/`high`/`critical` per function from `scope.impact` / `hotspots`; heed `dangerous_operations` before editing.
- **secondary_intents** — inline sub-task tags; `similar_logic` flags duplicated logic — extract rather than duplicate.
- **direct_caller_files** vs **migration_cost** (`before_create.files`) — `direct_caller_files` = files that actually *call* your target fn (tight surface). `migration_cost` = raw import-edge count (loose upper bound). Prefer `direct_caller_files` on fn-level intents.
- **route / http_method** — HTTP-handler marker (Flask / FastAPI / Django / aiohttp). Inlined in `scope`, `browse` (`[METHOD /path]`), `hotspots`, `before_create.affected_fns`.
