# Changelog

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
