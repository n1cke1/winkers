---
name: skill
description: >
  Architectural context for code changes. Function-level dependency graph,
  locked/free functions, zones, rules, module coupling. Use before modifying code.
---

# Winkers — Architectural Context

## When to use

Before non-trivial writes or edits. Skip for typos, comments, one-line tweaks.

## Workflow

1. `orient include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, rules (`title` + `wrong_approach` one-liner). **First call.**
2. `before_create intent: "<goal>"` — matches, migration cost, affected callers (expressions + risk), `similar_logic` warnings, or safe alternatives. **Call before writing any code.**
3. Write / edit code.
4. `impact_check file_path: "<path>"` — graph update + duplicate + broken-import check. Auto via hook in Claude Code.

## On demand

| Tool | When |
|------|------|
| `scope` with `file` or `function` | coupling, caller expressions, `impact` (risk, safe+dangerous ops, classified callers, action plan), `similar_logic` |
| `rule_read` with `category` | full rule text when the one-liner isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper inventory |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

## Key concepts

- **locked** — has callers; don't change param types/order/return without updating all callers.
- **free** — no callers; modify freely.
- **value_locked** — module-level literal set; removing a value breaks callers passing it as a literal.
- **risk_level** — `low`/`medium`/`high`/`critical` per function from `scope.impact` / `hotspots`; heed `dangerous_operations` before editing.
- **secondary_intents** — inline sub-task tags; `similar_logic` flags duplicated logic — extract rather than duplicate.
- **startup_chain** — changing a startup-chain file can prevent app start.
- **hotspots** — functions with many callers; high-risk changes.

## Example

Task: add batch price update for wholesale orders.

```
orient(include: ["map","conventions","rules_list"])
  → zones: modules, api | hotspot: calculate_price (7 callers, risk: high)
  → rule #4 "Decimal precision": "Converting Decimal to float mid-pipeline..."

scope(function: "calculate_price")
  → impact: risk=high (0.78); dangerous=[change return type, remove validation]
  → similar_logic: "price computation" also in get_price, reserve_items

before_create(intent: "batch price calculation")
  → intent_type: create, no match, zone_conventions returned
  → resolved_targets: modules/pricing.py

[write batch_calculate_prices calling calculate_price in a loop]

(post-write hook) impact_check: 1 function added, no broken callers
```
