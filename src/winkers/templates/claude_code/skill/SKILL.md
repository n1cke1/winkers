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

1. `orient include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, zone intents, and coding rules with `title` + `wrong_approach` one-liner per rule. **First call.**
2. `before_create intent: "<goal>"` — classifies intent, resolves targets from graph, returns matches, migration cost, affected callers with expressions + `risk_level` / `dangerous_operations`, `similar_logic` warnings for duplicated `secondary_intents`, or safe alternatives. **Call before writing any code.**
3. Write / edit code.
4. `impact_check file_path: "<path>"` — graph update + duplicate detection + broken import check. Auto via hook in Claude Code; call explicitly in other agents.

## On demand

| Tool | When |
|------|------|
| `scope` with `file` or `function` | drill into coupling, caller expressions, pre-computed `impact` (risk, safe+dangerous ops, classified callers, action plan), `similar_logic` (shared `secondary_intents`) |
| `rule_read` with `category` | full rule text when the one-liner from step 1 isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper inventory; `hotspots` entries carry `risk_level` when impact.json exists |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

## Key concepts

- **locked** — has callers. Don't change param types/order/return without updating all callers.
- **free** — no callers; modify freely.
- **value_locked** — module-level literal collection (`{"draft", "sent", ...}`) read by code and tested by callers as literals. Removing a value silently breaks them; `scope` / `before_create` / `impact_check` warn.
- **impact / risk_level** — per-function LLM-assessed risk (`low`/`medium`/`high`/`critical`) + `safe_operations` / `dangerous_operations` / classified callers (`dependency_type`, `coupling`, `update_effort`) + `action_plan`. Pre-computed at `winkers init` when a Claude API key is set; surfaced in `scope(function=).impact` and in `orient(["hotspots"])`. No risk field → impact.json not populated, fall back to `callers_count` / complexity.
- **secondary_intents** — inline sub-task tags (e.g. `"email validation"`, `"password hashing"`). `scope.similar_logic` groups other functions sharing tags; `before_create` (change) surfaces `similar_logic` when the target shares tags — consider extracting shared logic instead of duplicating.
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
