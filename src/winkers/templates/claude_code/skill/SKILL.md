---
name: skill
description: >
  Architectural context for code changes. Queries a function-level
  dependency graph to show locked functions (have callers), free functions,
  zones, conventions, and startup chain. Use before modifying code.
---

# Winkers — Architectural Context

## When to use

Before writing or modifying code, get architectural context to understand
what your changes will affect. Skip for trivial edits (typos, comments).

## Workflow

### 1. Understand structure
```
orient with include: ["map", "conventions"]
```
Zones, hotspots, data flow, zone intents, domain context. Call first.

### 2. Get constraints for your files
```
scope with file: "<path>"
```
For each file you'll modify: locked/free functions, callers, related rules,
startup chain warnings.

### 3. Check coding rules
```
orient with include: ["rules_list"]
```
Available categories. Then:
```
rule_read with category: "<name>"
```
Detailed rules with wrong_approach for that category.

### 4. Before creating new code
```
before_create with intent: "<what you want to create>"
```
Search existing functions. Reuse before writing new code.

### 5. After writing code
```
after_create with file_path: "<path>"
```
Updates graph, checks impact, coherence. Then:
```
scope with function: "<name>"
```
Verify callers are not broken. Check:
- `safe_changes`: modify body, add optional params with defaults
- `breaking_changes`: change param types/order, remove params, change return type

### 6. When task is complete
```
session_done (no args)
```
PASS/FAIL audit. Do not finish until PASS.

## Key concepts

- **locked**: function has callers depending on its signature. Do NOT change
  parameter types, parameter order, or return type without updating all callers.
- **free**: no callers anywhere in the project. Modify freely.
- **startup_chain**: file is in the startup import chain — changes can
  prevent the application from starting.
- **hotspots**: top functions by caller count — highest-risk to modify.

## Example

**Task**: "add batch price update for wholesale orders"

```
orient(include: ["map","conventions"])
→ zones: modules (pricing.py, inventory.py), api (prices.py)
→ hotspot: modules/pricing.py::calculate_price (7 callers)
→ data_flow: User -> API -> pricing -> DB

scope(file: "modules/pricing.py")
→ calculate_price LOCKED (7 callers), callers expect (item_id, qty)->float
→ apply_discount LOCKED (1 caller)
→ related_rules: [numeric] "Use Decimal for money calculations"

before_create(intent: "batch price calculation") → no match, create new

Decision: create batch_calculate_prices(items: list) that calls
calculate_price in a loop. Does not touch calculate_price signature.

[write code]

after_create(file_path: "modules/pricing.py") → 1 function added, ok
scope(function: "calculate_price") → callers unchanged [ok]
session_done() → PASS
```
