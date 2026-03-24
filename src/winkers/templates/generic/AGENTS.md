# Architectural Context — Winkers

This project uses Winkers MCP server for function-level dependency tracking.
Run `winkers serve` to start the server, then use these tools:

## Workflow for code changes

Before modifying any code:

1. **`map(detail="zones")`** — understand project structure, identify
   relevant zones and hotspot functions (most callers = highest risk).

2. **`map(detail="files", zone="<zone>")`** — list files with their
   functions marked as locked (has callers) or free (no callers).

3. **`scope(file="<path>")`** — for each file you'll touch, get:
   - which functions are locked and who calls them
   - what callers expect (signature)
   - safe changes vs breaking changes

4. **`inspect(function="<id>")`** — source code when needed.
   Add `include_callers_code=true` to see call sites.

After writing code:

5. **`analyze(files=[...])`** — re-parse changed files and check for:
   - `signature_changed`: locked function signature differs from what callers expect
   - `function_removed`: locked function deleted while callers exist

   Fix all violations before committing.

## Key concepts

| Term | Meaning |
|------|---------|
| **locked** | Function has callers depending on its signature |
| **free** | No callers — safe to modify freely |
| **confidence** | How certain the resolver is (1.0=direct import, 0.5=name guess) |

## Example

```
User: add batch discount feature

Agent:
  map(zones) → business_logic has pricing.py
  map(files, zone=business_logic) → calculate_price LOCKED (5 callers)
  scope(file=pricing.py) → callers expect (item_id:int, qty:int)->float

  Decision: new batch_apply_discounts() calls calculate_price in loop.
  Does not change calculate_price signature.

  [writes code]

  analyze(files=["pricing.py"]) → violations: []  [ok]
```
