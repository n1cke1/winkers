# Architectural Context — Winkers

This project uses Winkers MCP server for function-level dependency tracking.
The server starts automatically via `.mcp.json`. Seven tools available.

## Workflow

1. **`orient`** with `include: ["map", "conventions", "rules_list"]` — zones,
   hotspots, data flow, zone intents, and coding rules with `title` +
   `wrong_approach` one-liner per rule. **First call.**

2. **`before_create`** with `intent: "<what you want>"` — classifies intent,
   resolves targets from graph, returns matches, migration cost, affected
   callers with expressions + `risk_level` / `dangerous_operations`,
   `similar_logic` warnings for duplicated `secondary_intents`, or safe
   alternatives. **Call before writing any code.**

3. Write / edit code.

4. **`impact_check`** with `file_path: "<path>"` — graph update + duplicate
   detection + broken import check. Auto via hook in Claude Code; call
   explicitly in other agents after each Write/Edit/Delete.

## On demand

| Tool | When |
|------|------|
| `scope` with `file` or `function` | drill into coupling, caller expressions, pre-computed `impact` (risk, safe+dangerous ops, classified callers, action plan), `similar_logic` (shared `secondary_intents`) |
| `rule_read` with `category` | full rule text when the one-liner from step 1 isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper inventory; `hotspots` entries include `risk_level` when impact.json exists |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

## Key concepts

| Term | Meaning |
|------|---------|
| **locked** | Function has callers; changing params/return breaks them |
| **free** | No callers — modify freely |
| **value_locked** | Module-level literal collection (`{"draft", "sent", ...}`); removing a value silently breaks callers passing it as a literal |
| **impact / risk_level** | Per-function LLM-assessed risk (`low`/`medium`/`high`/`critical`) + `safe_operations` / `dangerous_operations` / classified callers + `action_plan`. Pre-computed at `winkers init` when an LLM provider is configured; surfaced in `scope(function=).impact` and in `hotspots`. |
| **secondary_intents** | Inline sub-task tags (e.g. `"email validation"`). `scope.similar_logic` and `before_create.similar_logic` group functions sharing tags — consider extracting instead of duplicating. |
| **startup_chain** | File is in the startup import chain; changes can prevent app start |

## Example

```
orient(include: ["map","conventions","rules_list"])
  → zones: modules, api | hotspot: calculate_price (7 callers, risk: high)
  → rule #4 "Decimal precision": "Converting Decimal to float mid-pipeline..."

scope(function: "calculate_price")
  → impact: risk=high (0.78); dangerous=[change return type]
  → similar_logic: "price computation" also in get_price, reserve_items

before_create(intent: "batch price calculation")
  → intent_type: create, no match → zone_conventions
  → resolved_targets: modules/pricing.py

[write batch_calculate_prices calling calculate_price in a loop]

impact_check(file_path: "modules/pricing.py") → 1 function added, no broken callers
```
