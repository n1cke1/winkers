<!-- winkers-snippet-version: 0.9.0 -->
## Architectural context (Winkers)

[Winkers](https://github.com/n1cke1/winkers) MCP: function-level dependency graph, zones, rules. Use before non-trivial edits.

### Workflow

1. `orient` with `task: "<what you were asked to do>"`, `include: ["map", "conventions", "rules_list"]` — zones, hotspots, data flow, zone intents, coding rules, **plus** `semantic_matches` (top-K relevant units ranked by embedding similarity against your task). `task` is **mandatory**. **First call.** Bundles the per-unit semantic search that was previously a separate `find_work_area` step.
2. `browse` with `zone` or `file` — mid-level inventory: function list with LLM intents (`"file::fn (callers) — intent"`). With `file=`, caller call-sites are inlined under each fn (`"  ← caller_file:line  expression"`) so you see who invokes what before editing. Use to pick a target before deep-dive.
3. `before_create` with `intent: "<what you're about to change>"` — matches, affected callers (expressions + risk), `similar_logic`. **Prefer explicit targets** (`fn_name()` / `Class.method()` / `Class.attribute` / path / `file.py::fn`). **One call per concrete change** — batched intents dilute signal. **Before writing any code.**
4. Write / edit code.
5. `impact_check` with `file_path: "<path>"` — graph update + duplicate detection + broken import check. Auto via hook in Claude Code; call explicitly in other agents.

### Task / intent formation rules

`task` (orient) and `intent` (before_create) are **two distinct inputs**:
- **`task`** — task-level, broad: what was assigned to you. Set once per session.
- **`intent`** — change-level, narrow: what you're about to modify right now. Set per concrete change.

Both follow the same structural rules. A useful task/intent is:

| Component | Required? | Example |
|-----------|-----------|---------|
| Verb-first | required | `create` / `change` / `fix` / `add` / `refactor` / `extract` / `remove` / `rename` / `audit` / `simplify` |
| Target if applicable | for `intent`: strongly preferred; for `task`: optional | `Class.method()`, `Class.attribute`, `file.py::fn`, path |
| Goal in one phrase | required | what should become, not how to get there |
| One concern | required | no `and` / `&` / multi-task lists |

**✅ Good:**
- `simplify invoice statuses from 6 to 3`
- `fix Client.invoices relationship cascade`
- `add soft-delete to all financial repos`
- `extract date utilities from app/services/billing.py`
- `audit soft-delete consistency across repos`

**❌ Bad:**
- `improve invoice handling` — no concrete verb, no scope
- `invoices` / `statuses` — bare noun
- `fix bug X and add feature Y` — multi-task, dilutes intent fulfillment audit
- `rewrite using Pydantic v2` — implementation-first, goal lost
- `make it better` / `refactor everything` — no target, no verb specificity

`orient` returns `task_warnings` (non-blocking) when the task is structurally weak: < 3 words, multi-task markers, or no semantic_matches scoring above 0.5. Treat warnings as guidance, not gates.

### On demand

| Tool | When |
|------|------|
| `browse` with `zone` / `file` / `min_callers` / `limit` / `offset` | list functions + intents, paginated |
| `scope` with `file` or `function` | coupling, caller expressions, `impact` (risk, safe+dangerous ops), `similar_logic` |
| `rule_read` with `category` | full rule text when the one-liner isn't enough |
| `orient` with `functions_graph` / `routes` / `hotspots` | deeper call-graph / endpoints / risk-ranked fns |
| `convention_read` with `target` | zone intent / data_flow / checklist |
| `session_done` | optional cross-file audit |

### Key concepts

- **locked** — has callers; don't change signature without updating them.
- **free** — no callers; modify freely.
- **value_locked** — module-level literal set; removing a value breaks callers passing it as a literal.
- **risk_level** — `low`/`medium`/`high`/`critical` per function from `scope.impact` / `hotspots`; heed `dangerous_operations` before editing.
- **secondary_intents** — inline sub-task tags; `similar_logic` flags duplicated logic — extract rather than duplicate.
- **direct_caller_files** vs **migration_cost** (`before_create.files`) — `direct_caller_files` = files that actually *call* the fn being changed (tight surface). `migration_cost` = raw import-edge count (loose upper bound). Prefer `direct_caller_files` on fn-level intents.
- **route / http_method** — HTTP-handler marker (Flask / FastAPI / Django / aiohttp). Inlined in `scope`, `browse` (`[METHOD /path]`), `hotspots`, `before_create.affected_fns`.
