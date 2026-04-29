# winkers issue: impact_check VALUE_LOCKED is blind to string-literal usage → trap-task false safety

**Severity:** P0 (false-negative risk gate; lets agent merge enum values that hundreds of literal call-sites depend on)
**Found:** 2026-04-28, winkers 0.8.4 @ commit 1e132db (latest main as of 2026-04-26), invoicekit task I5 "Simplify invoice statuses"
**Status:** Reproducible — same failure mode as historical I5 S-WNK runs (7/8 regressed pre-update; today's post-update run also catastrophic)

## Summary

`PostToolUse` impact_check correctly fires on every Edit, correctly detects which constants/enum values were removed, and emits `VALUE_LOCKED` warnings — but the **`N caller literal use(s) at risk`** count it reports is derived from the call-graph only and never grep/AST-scans the codebase for **string-literal references** to the removed values.

Result: when an agent removes status enum values like `{"sent", "paid", "viewed", ...}`, impact_check tells it "0 caller literal use(s) at risk", even though tests, repos, services, routes, and templates have **dozens of literal references** like `Invoice.status == "sent"`, `if invoice.status == "paid":`, `assert response.status == "void"`, etc. The agent reads "0 at risk", continues the cascade, and crashes 217 tests.

## Reproduction

Task: I5 (TRAP) — "Simplify invoice statuses from 6 to 3" against invoicekit.

```bash
cd C:/Development/MCP graph bench/graph-mcp-bench
python -m bench.cli run -p invoicekit -t I5 -c S-WNK --source ../invoicekit
```

Result on winkers 0.8.4 @ 1e132db (Sonnet 4.6):
- Tests: **15 pass / 217 broken** (baseline 232)
- Cost: $1.75, 51 turns, 476s
- Run dir: `results/20260428_054159_I5_S-WNK/`
- Identical failure mode to historical run `20260415_230156_I5_S-WNK` (also 15 pass)

### What the agent did (from diff.patch)

`app/domain/status.py`:
```python
-VALID_STATUSES = {"draft", "sent", "viewed", "partially_paid", "paid", "void"}
+VALID_STATUSES = {"draft", "active", "closed"}
```
Plus removed `is_voidable`, `VOIDABLE_STATUSES`, replaced `"sent"→"active"`, `"paid"→"closed"` in repos and services.

### What impact_check reported (from `claude_debug.log`)

After the Edit on `app/domain/status.py`:
```
[Winkers] Graph updated: app/domain/status.py
  VALUE_LOCKED: VALID_STATUSES removed ['paid', 'partially_paid', 'sent', 'viewed', 'void']
    — 0 caller literal use(s) at risk
  VALUE_LOCKED: IMMUTABLE_STATUSES removed ['paid', 'void']
    — 0 caller literal use(s) at risk
  SESSION: 2 warning(s) pending. Call session_done() for an optional final audit.
```

Subsequent Edits accumulated warnings: 3 → 4 → 5 → 6 → 7 → 8. Each carried `0 caller literal use(s) at risk` for the affected symbols. Agent ignored the `SESSION: N warning(s) pending` line and continued.

### What was actually at risk

Quick grep on the source project shows literal references the impact-check missed:

```bash
$ grep -rIn '"sent"\|"viewed"\|"partially_paid"\|"paid"\|"void"' invoicekit/ | wc -l
~50 hits across tests/, app/services/, app/repos/, app/api/routes/
```

These break the moment the values are removed from `VALID_STATUSES`. The graph counted **0**.

## Root cause (hypothesis, not verified in code)

Winkers builds the graph from tree-sitter call edges:

```
caller_fn  ──call──▶  callee_fn
```

When a constant like `VALID_STATUSES = {"sent", ...}` is removed, the graph search for "callers" looks for **functions that import this module and reference the symbol**, not for **expressions/conditionals/comparisons containing the literal value** "sent" anywhere in the codebase.

So the relevant linkage — "tests assert `invoice.status == 'sent'`" — is invisible to the call-graph based impact analysis. The "literal use(s)" count appears to be reading a separate `hardcoded_artifacts` field that's still scoped to import-graph context, not full-codebase grep.

The structural assumption "if no function imports this symbol, no caller is at risk" breaks whenever:
- enum values exist as bare strings in DB queries (SQLAlchemy: `Invoice.status == "sent"`)
- enum values appear in test assertions (`assert resp.json()["status"] == "paid"`)
- enum values are passed as request payloads in routes (FastAPI: `data: dict` with status field)
- enum values populate fixture/seed data
- enum values appear in templates / frontend payloads

For an enum-merge trap (I5), almost ALL the risk lives in literal references that aren't in the call graph at all.

## Impact on bench

I5 is the canonical "destructive refactor trap" test in the suite. The whole point is to detect whether the graph-MCP can warn the agent before it merges enum values that hundreds of call-sites depend on. Today:

- Agent calls `before_create` → returns the standard scope/migration_cost (graph-callers based) → looks small
- Agent edits `status.py` → impact_check VALUE_LOCKED fires with "0 at risk"
- Agent reasons: "warned but no callers at risk → safe" → continues cascade
- 217 tests break

This is **why I5 fails 7/8 historical S-WNK runs**. Without literal-use tracking, no amount of CLAUDE.md nudging / find_work_area / ONNX upgrade helps — the gate that's *supposed* to fire correctly says "0 at risk" and effectively endorses the trap.

## Suggested fixes

### A. Cheap: add string-literal grep to VALUE_LOCKED

When emitting `VALUE_LOCKED: <CONST> removed <values>`, after computing the call-graph-based risk count, **also** run a literal grep across the repo for each removed value and surface the hit count separately:

```
VALUE_LOCKED: VALID_STATUSES removed ['paid', 'sent', 'void']
  — 0 caller literal use(s) at risk        # graph callers (current)
  — 47 string-literal occurrences in 18 files: tests/test_invoice.py:42 "sent",
    tests/test_status.py:11 "paid", app/repos/invoice.py:54 "sent", ...
```

Implementation sketch in `winkers/impact/value_locked.py` (or wherever VALUE_LOCKED is computed):
- `for value in removed_values: count = count_literal_occurrences(repo_root, value)`
- `count_literal_occurrences` = quick `ripgrep -F -c '"' + value + '"'` or AST visitor over `ast.Constant(value=str)`.
- Cache results by repo content_hash so subsequent edits don't re-scan.

This is **the single highest-leverage fix** — would change the I5 failure mode from "agent sees 0, continues" to "agent sees 47, pauses".

### B. Medium: AST-level "expression-uses" pass

Build a one-time index `value_literal_usages.json`:

```json
{
  "sent": [
    {"file": "tests/test_invoice.py", "line": 42, "context": "assert resp.json()[\"status\"] == \"sent\""},
    {"file": "app/repos/invoice.py", "line": 54, "context": "Invoice.status == \"sent\""},
    ...
  ]
}
```

Built during `winkers init` via AST visitor. impact_check can then surface:
```
VALUE_LOCKED: VALID_STATUSES removed ['sent', 'paid']
  — 23 literal usages in tests/, 18 in app/repos, 6 in app/api
  Top 3:
    tests/test_invoice.py:42  assert ... == "sent"
    app/repos/invoice.py:54   Invoice.status == "sent"
    app/api/routes/invoices.py:88  if data["status"] == "paid"
```

### C. Hard: PreToolUse block on accumulated warnings

When `SESSION: N warning(s) pending` reaches a threshold (e.g. N≥3) AND any are VALUE_LOCKED with high-confidence risk, **block** subsequent Edit/Write tools until the agent calls `session_done` or explicitly acknowledges. This makes the gate enforcing rather than advisory.

Memory's `feedback_trap_prompt_neutrality.md` says don't bake task-specific rules into prompts. This proposal is structural (any task with mass-removal triggers it), not trap-specific, so it should be neutral.

## Reproduction artefacts

- `results/20260428_054159_I5_S-WNK/diff.patch` — what the agent changed
- `results/20260428_054159_I5_S-WNK/claude_debug.log` — full hook lifecycle including all VALUE_LOCKED warnings
- `results/20260428_054159_I5_S-WNK/claude_output.json` — agent's reasoning + tool calls
- `results/20260428_054159_I5_S-WNK/claude_settings.json` — installed hook config (verifies post-write hook was registered)
- `results/20260415_230156_I5_S-WNK/` — historical pre-update catastrophe (same 15-pass result, identical failure mode)

## Context: what wasn't the cause

These were ruled out before filing:
- **Hooks not firing**: confirmed PostToolUse fired on every Edit (148 hits in debug log)
- **Stale impact.json from 0.8.2 schema**: source impact.json was fully regenerated 2026-04-27 with current 0.8.4 schema (478/496 fns analyzed, full-string dangerous_ops at maxlen=100)
- **find_work_area dormancy** (0.8.3-): resolved upstream by ONNX-INT8 + eager preload (commit 1e132db). Agent uses fwa now (1× on this run, query "invoice status system with ... reduce to draft, active, closed") but its result didn't change the framing — task was already locked into trap.
- **Adapter patches**: bench `_patch_claude_md` strips "safe alternatives" mention but that doesn't gate VALUE_LOCKED behavior.

The bug is in **how VALUE_LOCKED computes "at risk" callers** — the metric is graph-edge based when it needs to be (graph-edge | literal-occurrence)-based.

---

## Code-verified analysis (added after reading `src/winkers/value_locked.py`)

The bug-report hypothesis ("hardcoded_artifacts field scoped to import-graph context") is close but slightly miscaptioned. Actual mechanics in `src/winkers/value_locked.py`:

**Pass 1 — collection discovery (lines 95–128):**
- Finds module-level `NAME = {...}` / `frozenset({...})` / `set({...})` literal collections, capped at 64 values.
- Then in `_find_referencing_fns` (lines 154–177): for each collection, picks "referencing functions" *only from the same file* — `for fid in file_node.function_ids` where `file_node` is the file where the collection lives. **Cross-file consumers are not in the set at all.**

**Pass 2 — literal-use counting (lines 181–218):**
- Walks `graph.call_edges`. For each edge whose **target is one of those same-file consumer functions**, extracts string literals from the call-site expression text and checks against the collection's values.
- The `literal_uses[v]` counter increments only on call-sites like `validate_status("sent")`.

**Diff (`diff_collections`, lines 244–284):**
- `affected_literal_uses = sum(before_col.literal_uses.get(v, 0) for v in removed)` — i.e. the "N caller literal use(s) at risk" reported on `VALUE_LOCKED` is *only* call-site literals to same-file consumer functions.

So the gate is fundamentally blind to:

1. **Bare comparisons:** `Invoice.status == "sent"`, `if status == "paid":`, `assert resp.json()["status"] == "void"`.
2. **Membership tests outside same-file consumers:** `if status in {"sent","paid"}` in a service module.
3. **Subscript/dict literals:** `data["status"]` matched against "sent" via comparison, fixtures `Invoice(status="paid")` (no consumer call).
4. **Pattern-match arms / case statements.**
5. **Cross-file consumers entirely** (compounding the issue — even if a service in `app/services/invoice.py` does `if status not in VALID_STATUSES`, that consumer fn never enters `referenced_by_fns`, so its callers' literals are never walked either).
6. **Non-Python literal references:** SQL fixtures, JSON seed data, HTML templates, JS payloads.

For an enum-merge trap like I5, virtually all the risk lives in (1)–(4) and (6). The reported "0 at risk" is structurally correct for the metric being computed; the metric is the wrong metric.

### Two distinct gaps, ranked

| # | Gap | Where in code | Severity for I5 |
|---|-----|---------------|-----------------|
| 1 | Literal-use counter only sees call-site args of consumer fns; misses comparisons/membership/subscripts | `_count_caller_uses`, line 181+ | **P0** — explains the 0 |
| 2 | Consumer detection is same-file only — cross-module consumers absent | `_find_referencing_fns`, line 154+ | P1 — compounds (1), but (1) is the proximate cause of the false safety |

Gap 2 is independent and worth a separate ticket; closing 1 fixes the symptom even with 2 still open.

### Recommended path (refines suggestions A/B/C from above)

**Path 1 — ship now (≤ 1 day): scoped grep for removed values.**
- In `diff_collections`, after computing `removed`, run a string-literal-aware scan of the repo for each removed value.
- Don't raw-grep — that hits comments, identifiers, English prose. Limit to:
  - quoted-string occurrences `r'(["\'])' + re.escape(value) + r'\1'`,
  - in source files only (extension allowlist: `.py`, `.sql`, `.json`, `.yaml`, `.yml`, `.html`, `.jinja`, `.j2`),
  - excluding `.git/`, `node_modules/`, `__pycache__/`.
- Surface as a **separate field** in the `VALUE_LOCKED` warning so the existing call-site semantic stays intact:
  ```
  VALUE_LOCKED: VALID_STATUSES removed ['paid', 'sent', 'void']
    — 0 caller literal use(s) at risk         (call-site, current metric)
    — 47 string-literal occurrences in 18 files (repo-wide):
        tests/test_invoice.py:42, app/repos/invoice.py:54, ...
  ```
- Cache by repo content-hash so multi-Edit sessions don't repeatedly rescan.
- Acceptance: I5 run shows non-zero "string-literal occurrences" → agent must address before continuing. Re-run benchmark; expect 7/8 → 1/8 catastrophic regress.

**Path 2 — proper structural fix (week+): AST expression-uses index.**
- Build during `winkers init` an inverted index `value_literal_index.json`:
  ```
  { "sent": [{"file": ..., "line": ..., "kind": "comparison|call_arg|dict_value|subscript|match", "context": "..."}] }
  ```
- Tree-sitter pass per `.py` file collecting `string` literal nodes whose syntactic context is one of:
  - `comparison_operator` rhs/lhs,
  - `argument_list` of `call`,
  - `dictionary` value,
  - `subscript` index,
  - `match_pattern` / `case_clause`.
- Cache by file mtime+hash, same as `descriptions/` cache.
- `diff_collections` consults the index instead of `literal_uses` for the removed values.
- This **replaces** the call-site-only metric for Python and renders Path 1 redundant for `.py`. Path 1 stays as the fallback for non-Python files.

**Path 3 — UX hardening, after Path 1.**
- Bug report's option C (PreToolUse block on accumulated warnings) is independent of accuracy. **Do not ship it before Path 1** — blocking on a structurally noisy gate is worse than letting it through. After Path 1, accumulated `VALUE_LOCKED` with non-zero literal-occurrence count is a strong signal to block-or-confirm.
- Also worth: in the warning text itself, make the implication explicit. Today the agent reads "0 caller literal use(s) at risk" as "0 risk." Even before the fix, changing the wording to "0 *call-site* literal uses (does not include comparisons/membership tests)" reduces misinterpretation. Sub-day change, no risk.

**Path 4 — fix Gap 2 (cross-file consumers).**
- Standalone ticket. Walk `graph.call_edges` / import edges to populate `referenced_by_fns` across files, not just `file_node.function_ids`.
- Lower priority once Path 1/2 lands (the literal-occurrence count subsumes the missing consumer signal for the specific I5-style trap), but worth fixing because `referenced_by_fns` is also surfaced elsewhere as a "who-uses-this" signal.

### Risks / things to watch

- **False positives in Path 1:** common-English values like "void", "draft", "active" will hit prose, comments, unrelated domains. Mitigations: word-boundary regex, source-file allowlist, file-count threshold (only warn when ≥2 distinct files hit). Show top-3 hits inline so the agent can judge whether they're real.
- **Performance on large repos:** Path 1's grep is O(repo_size × removed_values). Cache aggressively. For repos > N files, run once per impact_check invocation, not per removed value (single pass collecting all matches).
- **Path 2 build time:** AST visitor on init is non-trivial for large repos. Probably fine — winkers already does heavier work in init (descriptions, embeddings).
- **Backward-compat of warning format:** the current `claude_code/skill` template parses `VALUE_LOCKED:` lines. Adding a new line under it should be additive; verify the template still renders.

### Concrete first commits to propose

1. `value_locked.py` — add `_count_repo_string_literals(removed: list[str], root: Path) -> dict[str, list[tuple[str,int,str]]]` using ripgrep-or-pure-Python source scan with the allowlist above.
2. `diff_collections` — populate new field `string_literal_hits` on each change record.
3. Hook output formatter — render the new line under existing `VALUE_LOCKED`.
4. Word-clarification in the existing line: `caller literal use(s)` → `call-site literal use(s)`.
5. Tests: synthesize a fixture project with `VALID_STATUSES = {"sent","paid"}` plus `assert x == "sent"` in tests, confirm `string_literal_hits["sent"]` is non-zero.
6. Re-run I5 benchmark, capture new failure rate.
