# Changelog

## 0.9.0 — surface cleanup

### Breaking — MCP tools

- `find_work_area` is no longer registered as a public MCP tool. Its
  semantic-search functionality moved into `orient(task=…)` — `orient`
  always returns `semantic_matches` against the task it was given, so
  the prior orient + find_work_area two-step is now one call. The
  implementation still exists internally; `_FIND_WORK_AREA_TOOL_NAME`
  remains the dedup key in `SeenUnitsRegistry`. Agents calling
  `find_work_area` directly will get `Unknown tool` — switch to
  `orient(task="…")`.

### Breaking — CLI commands removed

The CLI surface was stripped of commands that duplicated MCP tools or
existed only as one-shot migration helpers:

- `winkers search` → use `orient(task=…)` (also upgrades from the old
  FTS5-only path to BGE-M3 embeddings)
- `winkers impact` → use MCP `scope(function=…)` (returns the same
  `impact` block)
- `winkers dupes` → use MCP `before_create(intent=…)` → `similar_logic`
- `winkers couplings` → no caller; `units.json` is reachable directly
  if needed
- `winkers conventions-migrate` → one-shot Wave 4 migration; everyone
  who needed it has migrated
- `winkers cleanup-legacy` → ditto, one-shot artifact sweep

The `hook session-audit` subcommand (muted since 0.8.1) is gone too.
The `_install_interactive_hooks` cleanup sweep that strips
`session-audit` hooks from old `settings.json` files **stays**, so
upgrading installs scrub themselves.

### Breaking — CLI commands renamed

Single-unit re-author commands and the intent provider eval tool are
not part of the day-to-day workflow; they're useful for prompt iteration
and provider tuning, so they moved under a `debug` group:

| Before                       | After                              |
|------------------------------|------------------------------------|
| `winkers describe-fn`        | `winkers debug describe-fn`        |
| `winkers describe-section`   | `winkers debug describe-section`   |
| `winkers describe-data`      | `winkers debug describe-data`      |
| `winkers intent eval`        | `winkers debug intent-eval`        |

Top-level CLI is now 14 commands + 2 groups (`hook` 6 sub, `debug` 4 sub).

### Refactor — internal layout

Two monster files split into per-concern packages. No behavioural
change; tests + lint clean. Public surface preserved through
re-exports — dashboard handlers, hooks, and tests need no changes.

```
src/winkers/mcp/
  tools.py (2438 LoC monolith)        →  tools/  (8 modules + _common.py)
                                          tools/__init__.py is 149 LoC
                                          (orchestrator + dispatch table)
src/winkers/cli/
  main.py  (4196 LoC monolith)        →  main.py     (~110 LoC orchestrator)
                                          commands/  (one module per command)
                                          init_pipeline/  (5 modules)
                                          hook_group.py + debug_group.py
```

Largest module after the split is `mcp/tools/orient.py` at 510 LoC
(was a 4196-line file). Adding a new MCP tool is one new module + one
entry in `_TOOL_MODULES`; adding a new CLI command is one module +
one `cli.add_command(...)` line in `commands/__init__.py`.

### Embeddings — small ergonomics

- `find_work_area` private helper no longer trips on absent
  `INDEX_FILENAME` — caller (`orient`) surfaces `semantic_hint` instead
  of failing the whole call when the per-unit index hasn't been built.
- Background preload + `wait_for_preload(timeout=15.0)` unchanged from
  0.8.4.

### Documentation

- Updated `claude_md_snippet.md` to the new workflow (orient(task=…)
  step 1, no find_work_area step). Existing CLAUDE.md files refresh on
  the next `winkers init` via `_install_claude_md_snippet`.
- README MCP-tools table reflects the 8 registered tools and adds
  `session_done`. CLI block drops `conventions-migrate` and adds
  `debug describe-fn` / `debug intent-eval`.

## 0.8.4

### Embeddings: ONNX-INT8 BGE-M3 replaces sentence-transformers float32

`find_work_area` and the index builder now run BGE-M3 via ONNX-INT8
(`Xenova/bge-m3`, `sentence_transformers_int8.onnx`, 568 MB on disk)
instead of `sentence_transformers.SentenceTransformer("BAAI/bge-m3")`.
Cold load drops 10–15 s → ~3 s, warm batch encode 5 s → 0.1 s, query
latency 397 ms → 38 ms (10× faster), resident RAM 1.7 GiB → 1.1 GiB.

Quality on a 417-unit codebase / 15 representative queries: top-1 match
73%, top-5 overlap 81%, average top-1 score drift 2.4%. The four
mismatches all sit in "ambiguous" zones (multiple equally relevant
candidates in the same file/domain), and in every case the missed pick
was within the other model's top-3 — so `find_work_area`'s top-K output
to the agent is largely unchanged.

### Dependency rearrangement

- **Core deps**: drop `sentence-transformers`; add `onnxruntime>=1.16`,
  `tokenizers>=0.20`, `huggingface_hub>=0.20`. `torch` and `transformers`
  are no longer transitively pulled into core — pipx/pip installs no
  longer risk the 5 GiB CUDA wheel hop. The `tool.uv.sources`
  `pytorch-cpu` pin still applies, but only when `[legacy]` is asked for.
- **`[legacy]` extra**: `sentence-transformers>=3.0` for users who want
  the float32 stack back. Set `WINKERS_USE_LEGACY_ST=1` at runtime to
  switch `_get_model()` to `SentenceTransformer("BAAI/bge-m3")` —
  vectors remain interchangeable with the old `embeddings.npz` format.
- INT8 vectors have a small drift (~2%) from float32; on upgrade,
  back up the prior `embeddings.npz` and re-embed with `force=True`
  rather than mixing old/new indexes.

## 0.8.1

### `orient.include` — clients that mis-serialise arrays no longer lock agents out

Claude Sonnet occasionally serialises array tool arguments as a JSON-encoded
string (`'["map","rules_list"]'`) instead of a real array, triggered by the
literal phrase "JSON array" in the tool description. Strict jsonschema then
rejected the call and the agent abandoned `orient` after one retry — which
in turn made every later step in the recommended flow miss its context.

- Description reworded without the "JSON array" trigger; includes an
  explicit `Do NOT serialize as a JSON-encoded string` hint and a plain
  Python-style example.
- Input schema switched to `oneOf: [array<string>, string]` so a stringified
  array or a single section name both validate.
- `_tool_orient` now normalises the value: array passes through, JSON-string
  is `json.loads`-decoded, bare string becomes a one-element array.
- Same coercion logic protects against the Haiku-style pattern of sending a
  single section name instead of a one-element array.
- Audit of the other six tools: no other descriptions contain the "JSON
  array / JSON object" wording — only `orient` was affected.

### Pre-computed impact analysis + multi-intent

- **`impact.json`** — new fourth layer alongside graph/semantic/rules. Per-function
  pre-computed risk assessment: `risk_level`, `risk_score`, `summary`,
  `caller_classifications` (dependency_type / coupling / update_effort),
  `safe_operations`, `dangerous_operations`, `action_plan`. Generated by a
  combined LLM call at `winkers init` time, cached by `content_hash` over
  function source + all callers' sources so re-init only touches affected fns.
- **Multi-intent** — `FunctionNode.secondary_intents: list[str]` is populated
  in the same LLM call. Lets Winkers spot duplicated inline logic (e.g.
  "email validation" appearing in three otherwise-unrelated functions) via
  tag overlap instead of embeddings.
- **Combined prompt** — one LLM call per function produces both intent fields
  and the full impact report. Halves API cost and token usage vs. running
  intent and impact as separate passes.
- **CLI**:
  - `winkers init --no-impact / --impact-only / --force-impact` — control the
    LLM pass: skip, only-run, or rebuild ignoring cache.
  - `winkers impact <fn>` — print the impact report for a function.
  - `winkers dupes` — list groups of functions sharing secondary_intents.
- **MCP surfaces**:
  - `scope(function=)` now includes an `impact` section (risk, safe/dangerous
    ops, classified callers, action plan) and a `similar_logic` section
    grouping other functions by shared secondary_intents.
  - `before_create` (`change` intent_type) enriches each `affected_fns[]`
    with `risk_level` + `dangerous_operations` and surfaces a
    `similar_logic` warning when the target shares secondary_intents.
  - `orient(["hotspots"])` entries include `risk_level` and `risk_score`.
- **Dashboard**: `/api/impact` endpoint exposes the compact per-function risk
  map for the heatmap layer (UI layer ships separately).
- **Backends**: Claude API is the default when `ANTHROPIC_API_KEY` is set
  (preferred, reliable structured JSON). Ollama is a supported fallback when
  `winkers init --ollama MODEL` was used — the impact pass talks to it via
  `/api/generate` with `format=json`, and the batch worker retries a
  failed parse up to 3× per function since small local models occasionally
  produce incomplete JSON. Without any LLM provider the pass is skipped.

### value_locked — value-domain breaking change detection

- New marker `value_locked` for module-level collections of literal values
  (`VALID_STATUSES = {"draft", "sent", ...}`). Catches the I5 benchmark
  pattern where `locked` doesn't fire: signature stays `(str) -> bool` but
  removing a value silently breaks every caller passing it as a literal.
- AST-only detection (tree-sitter, Python-only in this MVP). Bounded:
  `set` / `frozenset` literals up to 64 values. `dict`, `Enum`, `Literal`
  type aliases and cross-file value propagation are out of scope (v1.0).
- Surfaces in three tools:
  - `scope(file=)` returns a `value_locked_collections` section per file
    with values, per-value `literal_uses` counts, and `files_with_uses`.
  - `before_create` (`change` intent_type) returns a `value_changes` block
    when the intent contains shrinking keywords (simplify, reduce, remove,
    consolidate, drop, prune…) and touches a file or function tied to a
    value_locked collection. Includes a `safe_alternative` recommendation.
  - `impact_check` and the post-write hook compare value sets before/after
    a write and emit a `value_locked` session warning when a value is
    removed and at least one caller was passing it as a literal.

### Control points refinement (graph-mcp-bench findings)

- **`scope(file=)` module coupling** — response now includes `sibling_imports`, `imported_by`, and `migration_cost`. Lets an agent see the actual cost of moving or merging files instead of just "all locked".
- **`before_create` intent categorization** — keyword-based dispatch (create / change / unknown) + regex target resolution from the graph's name dictionary. No API calls.
  - `change` intents (move / merge / consolidate / split / refactor / simplify / rename / etc.) return an adaptive payload: `files` block (coupling: `cross_imports`, `imported_by`, `migration_cost`, `locked_fns`, `safe_alternative`) when file/zone targets are present, and/or `functions` block (`affected_fns` with callers + call-site expressions, plus truncation counters when zone-expansion caps the list) when function targets are present.
  - unresolved intents return an orient-lite payload (top hotspots + zone intents) instead of "No existing implementations found".
- **`after_create` renamed to `impact_check`** — the old name described *when* to call, not *what* it does. The tool is invoked after writes, edits, *or* deletes. Claude Code users keep the automatic post-write hook; other agents call the MCP tool explicitly.
- **`session_done` muted** — no longer gated by a Stop hook and no longer blocks task completion. Remains available as an optional final audit. Per-file `impact_check` (via hook or tool) now carries the main coherence signal.
- **`orient(["rules_list"])` wrong_approach snippet** — each rule entry now includes a one-line `wrong_approach` excerpt (≤140 chars, whitespace-collapsed) next to `id` + `title`. Lets an agent see the precision / anti-pattern signal without a follow-up `rule_read` call.

### Migration notes

- MCP tool `after_create` is gone. Clients that call it by name will see "Unknown tool" — run `winkers init` in each project to refresh CLAUDE.md / skill / hook registrations.
- `SessionState.after_create_calls` field renamed to `impact_check_calls`. `session.json` is ephemeral (gitignored), no migration needed.
- `winkers init` removes any legacy Stop / session-audit hook from `.claude/settings.json`.

### Fixes (previously unreleased under 0.8.1)

- **Hook path auto-update** — `winkers init` now updates stale hook paths (e.g. Linux→Windows) instead of skipping or duplicating.
- **Autocommit marker mismatch** — hook check looked for "auto-commit" but command was "autocommit"; caused duplicate hooks on every init.
- **Intent provider defaults** — `auto` mode now uses Claude API (Haiku) if key available; Ollama only when explicitly set via `--ollama` or config.toml. Prevents slow Ollama detection.
- **No surprise intents in impact_check** — incremental intent generation only runs if provider was explicitly configured, not on "auto"/"none".
- **ui_map expanded** — buttons, inputs, select, textarea, sub-tabs (data-*sub*), span indicators, extended panel regex (toolbar, strip, bar, overlay, toast, loading).

## 0.8.0

### New features

- **Interactive agent workflow** — Winkers now participates during coding, not just at the start.
- **`before_create(intent)`** MCP tool — searches existing functions before creating new code. Returns matches with pipeline context (upstream/downstream callers).
- **`after_create(file_path)`** MCP tool — incremental graph update, impact analysis (signature changes + broken callers), coherence check, session state tracking.
- **`session_done()`** MCP tool — PASS/FAIL session audit. Blocks task completion if broken callers or unsynced coherence files remain. Anti-loop: FAIL max once.
- **Claude Code hooks** — 4 automatic hooks installed by `winkers init`:
  - `UserPromptSubmit` — detects creation intent, injects `before_create` results.
  - `PreToolUse(Write|Edit)` — AST hash duplicate gate, blocks exact clones.
  - `PostToolUse(Write|Edit)` — auto `after_create` on every file write.
  - `Stop` — session audit gate, forces continuation on first FAIL.
- **LLM intent generation** — per-function one-sentence descriptions via Ollama (gemma3:4b) or Claude API (Haiku). Boosts `before_create` search for cryptic function names.
- **`winkers intent eval`** CLI — test and compare intent prompts (`--sample N --json`, `--prompt "..." --json`, `--compare`).
- **Intent eval skill** — `skills/intent_eval/SKILL.md` for prompt tuning workflow.
- **`.winkers/config.toml`** — configurable intent provider, model, prompt template, temperature.
- **Coherence rules** — `category="coherence"` rules with `sync_with` and `fix_approach` (sync/derived/refactor). Semantic enricher now proposes them automatically.
- **orient() session status** — shows active session warnings when session exists.

### Performance

- **Search token cache** — `before_create` search: 1589ms → 74ms (warm) on 837-function project.
- **Cache invalidation** — automatic on `after_create` for modified files.

### CLI changes

- `winkers init --ollama gemma3:4b` — use specific Ollama model for intent.
- `winkers init --no-llm` — skip LLM intent generation.
- `winkers hook <subcommand>` — hook handlers (prompt-enrich, pre-write, post-write, session-audit).
- `winkers intent eval` — intent generation quality evaluation.
- CLAUDE.md snippet updated to v0.8.0 with full interactive workflow instructions.

## 0.7.6

### New features

- **winkers autocommit** — AI-generated commit messages via Haiku with file/function fallback. Replaces generic `wip: auto-commit` in SessionEnd hook.
- **winkers commits --enrich** — retroactive enrichment of old commits via Haiku + session context matching by timestamp.
- **Expanded doctor** — checks venv isolation (pipx/project/global), CLAUDE.md snippet version and position, SessionEnd hook, rules, protect chain, commit format, git hooksPath, sessions, insights.
- **{datetime} template variable** — `YYYY-MM-DD HH:MM` in commit format templates.
- **.mcp.json absolute path** — MCP server config now uses full project path instead of `.`.

### Fixes

- Conventional commit scope: `feat(TICKET): msg` no longer leaves empty parens after normalization.
- CLAUDE.md snippet inserted after first `# ` heading, not appended to end.
- Doctor warns if Winkers section is positioned near end of CLAUDE.md.

### Updated

- All templates (claude_md_snippet, cursor/winkers.mdc, AGENTS.md, SKILL.md) updated to current API (orient/scope/rule_read/convention_read).
- CLI help text lists all commands including analyze, improve, protect, doctor, hooks.

## 0.7.5

### New features

- **Smart zones** — passthrough directory detection (e.g. `src/winkers/` stripped, zone = `mcp`, `core`, `cli`). Flat projects unchanged.
- **orient() token budget** — default 2000 tokens, priority-ordered truncation with hint. Prevents context overflow.
- **scope() semantic context** — zone_intent and data_flow from semantic.json included in scope response.
- **CLAUDE.md dynamic summary** — auto-generated project context block (data_flow, domain, constraints) written on init.
- **winkers analyze** — send recorded sessions to Haiku for knowledge gap analysis. Accumulates in insights.json.
- **winkers improve** — show/apply insights. `--apply` injects high-priority insights into semantic.json constraints.
- **winkers protect --startup** — detect entry point, trace import chain (2 levels), mark protected files in orient/scope.
- **winkers doctor** — diagnostic: Python, tree-sitter, grammars, git, anthropic, API key, graph, semantic, MCP.
- **winkers hooks** — install prepare-commit-msg hook with configurable template ({message}, {ticket}, {date}, {author}).
- **winkers commits** — normalize commit messages to configured template (dry-run by default).
- **Schema versioning** — `schema_version: "2"` in graph.json and semantic.json meta.

### Changes

- `anthropic` moved to optional `[semantic]` dependency. Core install: `pipx install winkers`.
- `_infer_zone()` removed. All zone lookups use `Graph.file_zone()` from stored FileNode.zone.
- `_related_rules()` now takes `graph` parameter for zone lookup.

## 0.7.4

- Convention rules subsystem (rules.json, detectors, orient/rule_read/convention_read)
- Session recording with approval scoring
- Dashboard session viewer and insights panel
- UI map (Flask routes to templates)
- Type hints in params
- conventions-migrate command
