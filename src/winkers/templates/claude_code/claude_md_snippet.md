<!-- winkers-snippet-version: 0.8.1 -->
## Architectural context (Winkers)

[Winkers](https://github.com/n1cke1/winkers) MCP: function-level dependency graph, zones, rules. Use before non-trivial edits.

### Workflow

1. `orient` with `include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, zone intents, and coding rules with `title` + `wrong_approach` one-liner per rule. **First call.**
2. `before_create` with `intent: "<what you want to do>"` — classifies intent, resolves targets from graph, returns matches, migration cost, affected callers with expressions, or safe alternatives. **Call before writing any code.**
3. Write / edit code.
4. `impact_check` with `file_path: "<path>"` — graph update + duplicate detection + broken import check. Auto via hook in Claude Code; call explicitly in other agents.

### On demand

| Tool | When |
|------|------|
| `scope` with `file` or `function` | drill into coupling or caller expressions |
| `rule_read` with `category` | full rule text when the one-liner from step 1 isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper inventory |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

### Key concepts

- **locked** — has callers; don't change signature without updating them.
- **free** — no callers; modify freely.
- **value_locked** — a module-level collection of literal values (`{"draft", "sent", ...}`) consumed by some function and tested by callers with literals. Removing a value silently breaks those callers — `scope`, `before_create`, and `impact_check` surface the warning.
