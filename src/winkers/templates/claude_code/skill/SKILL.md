---
name: winkers
description: >
  Architectural context for code changes. Builds and queries a
  function-level dependency graph to show which functions are locked
  (have callers depending on their signature) and which are free.
  Use before modifying code to understand impact.
triggers:
  - modify
  - refactor
  - add feature
  - change
  - update
  - fix bug
  - implement
  - create new
  - move
  - rename
  - delete
---

# Winkers — Architectural Context

## When to use

Before writing or modifying code, get architectural context to understand
what your changes will affect. Skip for trivial edits (typos, comments).

## Workflow

### 1. Understand structure
```
map(detail="zones")
```
See top-level zones, their file/function counts, and hotspots (most-called
functions). Use this to identify which zone your task lives in.

### 2. Drill into the zone
```
map(detail="files", zone="<zone>")
```
See files with their functions listed as locked/free, caller counts,
and signatures. Identify which files you'll need to modify.

### 3. Get constraints
```
scope(file="<path>")
```
For each file you'll modify: locked functions with their callers,
what's safe to change vs what breaks callers.

Or for a specific function:
```
scope(function="<file>::<name>")
```
Returns callers, callees, and explicit constraints:
- `safe_changes`: modify body, add optional params with defaults
- `breaking_changes`: change param types/order, remove params, change return type

### 4. See implementation (when needed)
```
inspect(function="<file>::<name>")
```
Source code of the function.

```
inspect(function="<file>::<name>", include_callers_code=true)
```
Adds ±2 lines around each call site — see exactly how callers use the function.

### 5. After writing code
```
analyze(files=["<changed_file>", ...])
```
Re-parses changed files, detects violations:
- `signature_changed`: locked function signature changed while callers exist
- `function_removed`: locked function removed

Zero violations = safe to commit.

## Key concepts

- **locked**: function has callers depending on its signature. Do NOT change
  parameter types, parameter order, or return type without updating all callers.
- **free**: no callers anywhere in the project. Modify freely.
- **confidence**: how certain the resolver is about each call edge.
  1.0 = direct import, 0.9 = module/relative import, 0.5 = name-only match.
  Low confidence edges deserve extra scrutiny.
- **hotspots**: top functions by caller count — highest-risk to modify.

## Example

**Task**: "add batch price update for wholesale orders"

```
map(detail="zones")
→ zones: modules (pricing.py, inventory.py), api (prices.py)
→ hotspot: modules/pricing.py::calculate_price (7 callers)

map(detail="files", zone="modules")
→ pricing.py: calculate_price LOCKED (7 callers), apply_discount LOCKED (1 caller)
→ inventory.py: check_stock FREE, reserve_items FREE

scope(function="modules/pricing.py::calculate_price")
→ callers_expect: (item_id: int, qty: int) -> float
→ safe_changes: modify body, add optional params with defaults
→ breaking_changes: change param types, remove params, change return type

Decision: create batch_calculate_prices(items: list) that calls calculate_price
in a loop. Does not touch calculate_price signature.

[write code]

analyze(files=["modules/pricing.py"])
→ violations: []  [ok]
```
