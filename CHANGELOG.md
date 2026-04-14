# Changelog

## 0.8.1 (unreleased)

### Fixes

- **Hook path auto-update** — `winkers init` now updates stale hook paths (e.g. Linux→Windows) instead of skipping or duplicating.
- **Autocommit marker mismatch** — hook check looked for "auto-commit" but command was "autocommit"; caused duplicate hooks on every init.
- **Intent provider defaults** — `auto` mode now uses Claude API (Haiku) if key available; Ollama only when explicitly set via `--ollama` or config.toml. Prevents slow Ollama detection.
- **No surprise intents in after_create** — incremental intent generation only runs if provider was explicitly configured, not on "auto"/"none".
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
