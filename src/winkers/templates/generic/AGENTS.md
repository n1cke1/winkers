# Architectural Context — Winkers

This project uses Winkers MCP server for function-level dependency tracking.
The server starts automatically via `.mcp.json`. Seven tools available:

## Workflow for code changes

Before modifying any code:

1. **`orient`** with `include: ["map", "conventions"]` — project structure, zones, hotspots,
   data flow, zone intents, domain context. **Call first.**

2. **`scope`** with `file: "<path>"` — for each file you'll touch:
   locked/free functions, callers, related rules, startup chain warnings.

3. **`orient`** with `include: ["rules_list"]` — available coding rule categories.
   Then **`rule_read`** with `category: "<name>"` for details with wrong_approach.

Before creating new code:

4. **`before_create`** with `intent: "<what you want>"` — search existing functions.

After writing code:

5. **`after_create`** with `file_path: "<path>"` — updates graph, checks impact.

6. **`scope`** with `function: "<name>"` — verify callers are not broken by your change.

When task is complete:

7. **`session_done`** (no args) — PASS/FAIL audit. Do not finish until PASS.

## Key concepts

| Term | Meaning |
|------|---------|
| **locked** | Function has callers depending on its signature |
| **free** | No callers — safe to modify freely |
| **startup_chain** | File is in the startup import chain — changes can prevent app start |

## Other tools

- `orient` with `include: ["functions_graph"]` — full indexed function list with caller counts.
- `orient` with `include: ["hotspots"]` — functions with many callers; high-impact to change.
- `orient` with `include: ["routes"]` — HTTP endpoints: method, path, handler, callees.
- `orient` with `include: ["ui_map"]` — route-to-template links with UI elements.
- `convention_read` with `target: "<zone>"` — zone intent details, data_flow, checklist.

## Example

```
User: add batch discount feature

Agent:
  orient(include: ["map","conventions"]) → zones: modules, api. hotspot: calculate_price (7 callers)
  scope(file: "modules/pricing.py") → calculate_price LOCKED, callers expect (item_id, qty)->float
  before_create(intent: "batch discount calculation") → no exact match, conventions shown

  Decision: new batch_calculate_prices() calls calculate_price in loop.
  Does not change calculate_price signature.

  [writes code]

  after_create(file_path: "modules/pricing.py") → 1 function added, no broken callers
  scope(function: "calculate_price") → callers unchanged [ok]
  session_done() → PASS
```
