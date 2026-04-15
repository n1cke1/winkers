# Architectural Context — Winkers

This project uses Winkers MCP server for function-level dependency tracking.
The server starts automatically via `.mcp.json`. Seven tools available.

## Workflow

1. **`orient`** with `include: ["map", "conventions", "rules_list"]` — zones,
   hotspots, data flow, zone intents, and coding rules with `title` +
   `wrong_approach` one-liner per rule. **First call.**

2. **`before_create`** with `intent: "<what you want>"` — matches, migration
   cost, affected callers (expressions + risk), `similar_logic` warnings for
   duplicated logic, or safe alternatives. **Call before writing any code.**

3. Write / edit code.

4. **`impact_check`** with `file_path: "<path>"` — graph update + duplicate
   detection + broken import check. Auto via hook in Claude Code; call
   explicitly in other agents after each Write/Edit/Delete.

## On demand

| Tool | When |
|------|------|
| `scope` with `file` or `function` | coupling, caller expressions, `impact` (risk, safe+dangerous ops, classified callers), `similar_logic` |
| `rule_read` with `category` | full rule text when the one-liner isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper inventory |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

## Key concepts

| Term | Meaning |
|------|---------|
| **locked** | Function has callers; changing params/return breaks them |
| **free** | No callers — modify freely |
| **value_locked** | Module-level literal set; removing a value breaks callers passing it as a literal |
| **risk_level** | `low`/`medium`/`high`/`critical` per function from `scope.impact` / `hotspots`; heed `dangerous_operations` before editing |
| **secondary_intents** | Inline sub-task tags; `similar_logic` flags duplicated logic — extract rather than duplicate |
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
