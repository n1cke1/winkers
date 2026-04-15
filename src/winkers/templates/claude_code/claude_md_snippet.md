<!-- winkers-snippet-version: 0.8.1 -->
## Architectural context (Winkers)

[Winkers](https://github.com/n1cke1/winkers) MCP: function-level dependency graph, zones, rules. Use before non-trivial edits.

### Workflow

1. `orient` with `include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, zone intents, and coding rules with `title` + `wrong_approach` one-liner per rule. **First call.**
2. `before_create` with `intent: "<what you want to do>"` — classifies intent, resolves targets from graph, returns matches, migration cost, affected callers with expressions + `risk_level` / `dangerous_operations`, or safe alternatives. **Call before writing any code.**
3. Write / edit code.
4. `impact_check` with `file_path: "<path>"` — graph update + duplicate detection + broken import check. Auto via hook in Claude Code; call explicitly in other agents.

### On demand

| Tool | When |
|------|------|
| `scope` with `file` or `function` | drill into coupling, caller expressions, pre-computed `impact` (risk / safe+dangerous ops / classified callers / action plan), `similar_logic` (shared `secondary_intents`) |
| `rule_read` with `category` | full rule text when the one-liner from step 1 isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper inventory; `hotspots` entries include `risk_level` when impact.json exists |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

### Key concepts

- **locked** — has callers; don't change signature without updating them.
- **free** — no callers; modify freely.
- **value_locked** — a module-level collection of literal values (`{"draft", "sent", ...}`) consumed by some function and tested by callers with literals. Removing a value silently breaks those callers — `scope`, `before_create`, and `impact_check` surface the warning.
- **impact / risk_level** — per-function LLM-assessed risk (`low`/`medium`/`high`/`critical`) + `safe_operations` / `dangerous_operations` / caller classifications + `action_plan`. Pre-computed at `winkers init` time when a Claude API key is set; surfaced in `scope(function=)` and in `orient hotspots`. No risk field → impact.json not populated, fall back to `callers_count` / `complexity` signals.
- **secondary_intents** — inline sub-task tags (e.g. `"email validation"`, `"password hashing"`). `scope.similar_logic` groups other functions sharing tags with the target; `before_create` (change) adds a `similar_logic` warning when the target shares tags with N≥1 other functions — consider extracting instead of duplicating.
