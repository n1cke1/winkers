# Architectural Context — Winkers

This project uses Winkers MCP server for function-level dependency tracking.
The server starts automatically via `.mcp.json`. Four tools available:

## Workflow for code changes

Before modifying any code:

1. **`orient(["map","conventions"])`** — project structure, zones, hotspots,
   data flow, zone intents, domain context. **Call first.**

2. **`scope(file="<path>")`** — for each file you'll touch:
   locked/free functions, callers, related rules, startup chain warnings.

3. **`orient(["rules_list"])`** — available coding rule categories.
   Then **`rule_read("<category>")`** for details with wrong_approach.

After writing code:

4. **`scope(function="<name>")`** — verify callers are not broken by your change.

## Key concepts

| Term | Meaning |
|------|---------|
| **locked** | Function has callers depending on its signature |
| **free** | No callers — safe to modify freely |
| **startup_chain** | File is in the startup import chain — changes can prevent app start |

## Other tools

- `orient(["functions_graph"])` — full indexed function list with caller counts.
- `orient(["hotspots"])` — functions with many callers; high-impact to change.
- `orient(["routes"])` — HTTP endpoints: method, path, handler, callees.
- `orient(["ui_map"])` — route-to-template links with UI elements.
- `convention_read("<zone>")` — zone intent details, data_flow, checklist.

## Example

```
User: add batch discount feature

Agent:
  orient(["map","conventions"]) → zones: modules, api. hotspot: calculate_price (7 callers)
  scope(file="modules/pricing.py") → calculate_price LOCKED, callers expect (item_id, qty)->float

  Decision: new batch_calculate_prices() calls calculate_price in loop.
  Does not change calculate_price signature.

  [writes code]

  scope(function="calculate_price") → callers unchanged [ok]
```
