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
orient(["map","conventions"])
```
Zones, hotspots, data flow, zone intents, domain context. Call first.

### 2. Get constraints for your files
```
scope(file="<path>")
```
For each file you'll modify: locked/free functions, callers, related rules,
startup chain warnings.

### 3. Check coding rules
```
orient(["rules_list"])
```
Available categories. Then:
```
rule_read("<category>")
```
Detailed rules with wrong_approach for that category.

### 4. After writing code
```
scope(function="<name>")
```
Verify callers are not broken. Check:
- `safe_changes`: modify body, add optional params with defaults
- `breaking_changes`: change param types/order, remove params, change return type

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
orient(["map","conventions"])
→ zones: modules (pricing.py, inventory.py), api (prices.py)
→ hotspot: modules/pricing.py::calculate_price (7 callers)
→ data_flow: User -> API -> pricing -> DB

scope(file="modules/pricing.py")
→ calculate_price LOCKED (7 callers), callers expect (item_id, qty)->float
→ apply_discount LOCKED (1 caller)
→ related_rules: [numeric] "Use Decimal for money calculations"

Decision: create batch_calculate_prices(items: list) that calls
calculate_price in a loop. Does not touch calculate_price signature.

[write code]

scope(function="calculate_price") → callers unchanged [ok]
```
