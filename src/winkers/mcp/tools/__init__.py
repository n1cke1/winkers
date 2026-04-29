"""MCP tool definitions: orient, scope, convention_read, rule_read."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from winkers.models import Graph


def register_tools(
    server: Server,
    root: Path,
    get_graph: Callable[[], Graph | None],
) -> None:

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="orient",
                description=(
                    "IMPORTANT: Call this FIRST."
                    " Provide your task in `task` (mandatory): a one-sentence"
                    " description of what was assigned to you (verb + scope)."
                    " orient returns the standard project context PLUS"
                    " `semantic_matches` — top-K relevant units (functions,"
                    " UI sections, couplings) ranked by embedding similarity"
                    " against your task. Replaces the prior orient + "
                    "find_work_area two-step."
                    " Pass `include` as an array of section names,"
                    " e.g. include=['map','rules_list']."
                    " Do NOT serialize as a JSON-encoded string."
                    " 'map' = project structure, zones, hotspots, data flow."
                    " 'conventions' = domain context, zone intents, business logic."
                    " 'rules_list' = coding rules grouped by category."
                    " 'functions_graph' = indexed call graph."
                    " 'hotspots' = high-impact functions."
                    " 'routes' = HTTP endpoints (Flask/FastAPI/Django/aiohttp)."
                    " Route info is also inlined per-fn in scope/browse/"
                    "hotspots/before_create — this section is rarely needed."
                    " 'ui_map' = route→template links with UI elements."
                    " Then use convention_read/rule_read for details."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "What you were asked to do — verb + scope, one"
                                " sentence. Examples: 'simplify invoice statuses"
                                " from 6 to 3', 'fix Client.invoices relationship"
                                " cascade', 'audit soft-delete consistency across"
                                " repos'. If exploratory, paste the task verbatim"
                                " ('explore project structure' is OK). Used for"
                                " semantic_matches AND registered for post-session"
                                " task fulfillment audit."
                            ),
                        },
                        "include": {
                            "oneOf": [
                                {"type": "array", "items": {"type": "string"}},
                                {"type": "string"},
                            ],
                            "description": (
                                "Array of section names, e.g. ['map','rules_list']."
                                " A single section name string is also accepted."
                                " Valid names: 'map', 'conventions', 'rules_list',"
                                " 'functions_graph', 'hotspots', 'routes', 'ui_map'."
                            ),
                        },
                        "zone": {
                            "type": "string",
                            "description": "Filter map/functions_graph by zone name",
                        },
                        "min_callers": {
                            "type": "integer",
                            "description": "Min callers for hotspots (default 10)",
                        },
                        "k": {
                            "type": "integer",
                            "description": (
                                "Top-K semantic_matches to return (default 5)."
                            ),
                        },
                    },
                    "required": ["task", "include"],
                },
            ),
            Tool(
                name="scope",
                description=(
                    "Deep context for one function or file."
                    " function=: callers + callees with call-site expressions,"
                    " `route`/`http_method` when the fn is an HTTP handler,"
                    " pre-computed `impact` (risk_level, safe/dangerous_operations,"
                    " caller_classifications, action_plan), `similar_logic`"
                    " (functions sharing secondary_intents), related rules,"
                    " recent git changes."
                    " file=: per-fn entries (with route when applicable),"
                    " imports, `migration_cost`, `value_locked_collections`,"
                    " startup_chain warning."
                    " Accepts `file::fn` or `file::Class.method` ids."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "function": {
                            "type": "string",
                            "description": "Function id: `file::fn` or `file::Class.method`",
                        },
                        "file": {"type": "string", "description": "Relative file path"},
                    },
                },
            ),
            Tool(
                name="convention_read",
                description=(
                    "Read detailed convention for a zone, file, or aspect."
                    " target = zone name as listed in conventions (e.g. 'app.py', 'old/'),"
                    " or 'data_flow' / 'domain_context' / 'checklist'."
                    " Use orient(include=['conventions']) first to see available zone names."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "Zone name, file path, or aspect name",
                        },
                    },
                    "required": ["target"],
                },
            ),
            Tool(
                name="rule_read",
                description=(
                    "Read all coding rules for a category."
                    " Returns list of rules with content, wrong_approach, and related categories."
                    " Use orient(include=['rules_list']) to see available categories."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Rule category name",
                        },
                    },
                    "required": ["category"],
                },
            ),
            Tool(
                name="before_create",
                description=(
                    "CALL THIS BEFORE writing, editing, or deleting code."
                    " For creates: searches the graph for existing implementations"
                    " matching your intent (reuse over duplicate)."
                    " For changes: returns affected files/functions, caller impact,"
                    " migration cost, risk level, and similar_logic warnings."
                    " PREFER explicit targets in your intent: `fn_name()` for"
                    " functions, `Class.method()` for methods, relative file"
                    " paths (`app/repos/invoice.py`), or `file.py::fn` notation."
                    " Explicit targets give a precise response; plain language"
                    " falls back to fuzzy matching with a tests/ filter."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": (
                                "What you want to do, in natural language."
                                " Use explicit markers for precision —"
                                " examples: 'refactor calculate_price() in"
                                " modules/pricing.py to round half-even',"
                                " 'fix InvoiceRepo.get_with_items() selectinload',"
                                " 'rename modules/pricing.py::calc_tax to"
                                " apply_tax'. Creates may be plain:"
                                " 'add batch discount feature'."
                            ),
                        },
                        "zone": {
                            "type": "string",
                            "description": "Zone to search in. Empty = search all zones.",
                        },
                    },
                    "required": ["intent"],
                },
            ),
            Tool(
                name="browse",
                description=(
                    "Mid-level function inventory between orient and scope."
                    " Entry format: 'file::fn (callers) [METHOD /path]? — intent?'."
                    " With `file=`, caller call-sites are inlined under each fn"
                    " as '  ← caller_file:line  expression' — one-shot"
                    " 'who calls what I'm about to edit' view."
                    " When `zone=` yields 0 fns but matches real files, the"
                    " response surfaces `files_in_zone` so you can drill down"
                    " with browse(file=…). Paginated via limit/offset."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "zone": {
                            "type": "string",
                            "description": "Filter by zone name (see orient.map.zones).",
                        },
                        "file": {
                            "type": "string",
                            "description": (
                                "Filter by exact file path."
                                " Supersedes zone if both given."
                            ),
                        },
                        "min_callers": {
                            "oneOf": [
                                {"type": "integer", "minimum": 0},
                                {"type": "string", "pattern": "^\\d+$"},
                            ],
                            "description": "Hide fns with fewer callers (default 0).",
                        },
                        "limit": {
                            "oneOf": [
                                {"type": "integer", "minimum": 1},
                                {"type": "string", "pattern": "^\\d+$"},
                            ],
                            "description": "Page size (default 50, max 100).",
                        },
                        "offset": {
                            "oneOf": [
                                {"type": "integer", "minimum": 0},
                                {"type": "string", "pattern": "^\\d+$"},
                            ],
                            "description": "Starting index into sorted results (default 0).",
                        },
                    },
                },
            ),
            Tool(
                name="impact_check",
                description=(
                    "Call after writing, editing, or deleting code. Updates the project"
                    " graph and checks for issues. Returns impact analysis for changed"
                    " functions, coherence checklist, and session status summary."
                    " In Claude Code this runs automatically via the post-write hook;"
                    " call explicitly for re-check or files you didn't edit directly."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative path to the modified file",
                        },
                    },
                    "required": ["file_path"],
                },
            ),
            Tool(
                name="find_work_area",
                description=(
                    "DEPRECATED — use orient(task=...) instead. orient now"
                    " always returns semantic_matches against the registered"
                    " task. find_work_area is kept as an alias for one minor"
                    " for existing scripts/agents and will be removed."
                    " Locate where in the codebase to make a change."
                    " Describe the task in 1-2 sentences in any language —"
                    " plain prose, mixing Russian and English domain terms"
                    " is fine."
                    " Returns top-K relevant function_units and"
                    " traceability_units (UI sections, cross-file couplings)"
                    " with confidence verdict."
                    " On verdict='OK': top match is the place to start."
                    " On verdict='NO_CLEAR_MATCH': no existing unit fits well"
                    " — likely a new feature, or the query uses domain"
                    " vocabulary missing from the index."
                    " Requires `winkers init --with-units` to have run; falls"
                    " back to an error message otherwise."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "What you want to do, in natural language."
                                " Examples: 'добавить переключатель темы',"
                                " 'fix negative condensate in SLP loop',"
                                " 'where does the IDX dict live'."
                            ),
                        },
                        "k": {
                            "type": "integer",
                            "description": "Top-K matches to return (default 5)",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="session_done",
                description=(
                    "Optional final audit when your task is complete. Returns PASS or"
                    " FAIL across all writes in the session. Useful for cross-file"
                    " coherence review after a series of edits."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        import json

        _log_call(root, name, arguments)
        graph = get_graph()

        if graph is None:
            return [TextContent(
                type="text",
                text='{"error": "Graph not initialized. Run winkers init first."}',
            )]

        if name == "orient":
            result = _tool_orient(graph, arguments, root)
        elif name == "scope":
            result = _tool_scope(graph, arguments, root)
        elif name == "convention_read":
            result = _tool_convention_read(arguments, root)
        elif name == "rule_read":
            result = _tool_rule_read(arguments, root)
        elif name == "before_create":
            result = _tool_before_create(graph, arguments, root)
        elif name == "browse":
            result = _tool_browse(graph, arguments)
        elif name == "impact_check":
            result = _tool_impact_check(graph, arguments, root, get_graph)
        elif name == "session_done":
            result = _tool_session_done(graph, root)
        elif name == "find_work_area":
            result = _tool_find_work_area(graph, arguments, root)
        else:
            result = {"error": f"Unknown tool: {name}"}

        return [TextContent(type="text", text=json.dumps(result, indent=2))]


def _log_call(root: Path, tool: str, args: dict) -> None:
    """Append MCP tool call to .winkers/mcp.log."""
    import datetime
    try:
        log_path = root / ".winkers" / "mcp.log"
        log_path.parent.mkdir(exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        args_str = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}  {tool}({args_str})\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _load_semantic(root: Path):
    from winkers.semantic import SemanticStore
    return SemanticStore(root).load()


def _load_impact(root: Path):
    """Load impact.json once per tool call; returns None if missing/empty."""
    from winkers.impact import ImpactStore
    impact = ImpactStore(root).load()
    if not impact.functions:
        return None
    return impact


def _load_rules(root: Path):
    from winkers.conventions import RulesStore
    return RulesStore(root).load()


MAX_ORIENT_TOKENS = 2500

# Priority order: most important sections first for truncation.
_SECTION_PRIORITY = [
    "map", "conventions", "rules_list", "hotspots",
    "routes", "ui_map", "functions_graph",
]

# Sections that have a compact fallback + the minimum tokens to reserve
# for each when it's been explicitly requested. Prevents earlier sections
# from starving the compact-capable ones; observed on large projects where
# map + conventions ate the entire budget and rules_list silently vanished.
_COMPACT_RESERVE: dict[str, int] = {
    "rules_list": 200,
}


def _estimate_tokens(data: Any) -> int:
    """Rough token count: ~4 chars per token in JSON output."""
    import json
    return len(json.dumps(data, default=str)) // 4


def _try_compact(section: str, data: Any) -> Any | None:
    """Return a smaller variant of `data` when budget is tight, or None.

    Right now we only know how to shrink `rules_list` (drop wrong_approach
    and related fields — agents can still see the categories and rule titles,
    and fetch details via rule_read). Other sections return None, which the
    caller treats as "skip the section entirely". Also returns None when the
    compact form would be empty (nothing useful to surface → prefer skip so
    agents see it in `_skipped` rather than getting a silent empty dict).
    """
    if section != "rules_list":
        return None
    if not isinstance(data, dict):
        return None
    categories = data.get("categories")
    if not isinstance(categories, dict) or not categories:
        return None
    compact_categories: dict[str, list[dict]] = {}
    for cat, rules in categories.items():
        compact_categories[cat] = [
            {k: v for k, v in r.items() if k in ("id", "title")}
            for r in rules
        ]
    if not compact_categories or not any(compact_categories.values()):
        return None
    return {
        "total": data.get("total"),
        "categories": compact_categories,
        "_compacted": "titles only — call rule_read(category) for wrong_approach",
    }


def _coerce_include(value: Any) -> list[str]:
    """Accept array, JSON-encoded array string, or single section name.

    Claude Sonnet sometimes serialises array arguments as a JSON-encoded
    string (`'["map","rules_list"]'`). Haiku sometimes sends a single
    section name string (`'map'`) when the caller means a one-element
    array. Normalise both to a plain list of names.
    """
    import json as _json

    if isinstance(value, list):
        return [str(s) for s in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = _json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(s) for s in parsed]
            except _json.JSONDecodeError:
                pass
        return [stripped] if stripped else []
    return []


def _tool_orient(graph: Graph, args: dict, root: Path) -> dict:
    task = (args.get("task") or "").strip()
    if not task:
        return {
            "error": (
                "task required: provide a one-sentence task description"
                " (verb + scope, e.g. 'simplify invoice statuses from 6 to 3')."
            )
        }

    include = _coerce_include(args.get("include", []))
    zone = args.get("zone")
    min_callers = args.get("min_callers", 10)
    max_tokens = args.get("max_tokens", MAX_ORIENT_TOKENS)
    k = int(args.get("k", 5)) if args.get("k") else 5

    builders: dict[str, Any] = {
        "map": lambda: _section_map(graph, zone, root),
        "functions_graph": lambda: _section_functions_graph(graph, zone),
        "conventions": lambda: _section_conventions(root),
        "rules_list": lambda: _section_rules_list(root),
        "hotspots": lambda: _section_hotspots(graph, min_callers, root),
        "routes": lambda: _section_routes(graph, zone),
        "ui_map": lambda: _section_ui_map(graph, zone),
    }

    # Process sections in priority order (only those requested).
    ordered = [s for s in _SECTION_PRIORITY if s in include]

    result: dict[str, Any] = {}
    used_tokens = 0
    skipped: list[str] = []
    compacted: list[str] = []

    # Reserve a floor for compact-capable sections that are still pending,
    # so earlier sections don't consume the entire budget.
    pending_compact = [s for s in ordered if s in _COMPACT_RESERVE]

    for section in ordered:
        build = builders.get(section)
        if build is None:
            continue

        # Effective ceiling for THIS section: full budget minus whatever we
        # still need to reserve for yet-to-process compact-capable sections.
        if section in pending_compact:
            pending_compact.remove(section)
        reserved = sum(_COMPACT_RESERVE[s] for s in pending_compact)
        effective_max = max_tokens - reserved

        data = build()
        section_tokens = _estimate_tokens(data)

        if used_tokens + section_tokens > effective_max and result:
            # Try a compact variant before giving up on the section entirely.
            compact = _try_compact(section, data)
            if compact is not None:
                compact_tokens = _estimate_tokens(compact)
                # Compact gets the FULL budget (not effective_max) — this is
                # the fallback path, no further reservation needed.
                if used_tokens + compact_tokens <= max_tokens:
                    result[section] = compact
                    used_tokens += compact_tokens
                    compacted.append(section)
                    continue
            skipped.append(section)
            continue
        result[section] = data
        used_tokens += section_tokens

    if skipped or compacted:
        result["_truncated"] = True
        hint_parts = [f"Response constrained to ~{max_tokens} token budget."]
        if skipped:
            hint_parts.append(f"Skipped: {', '.join(skipped)}.")
        if compacted:
            hint_parts.append(
                f"Compacted (titles only): {', '.join(compacted)} — "
                "use rule_read(category) for wrong_approach."
            )
        hint_parts.append("Call orient() with fewer includes or filter by zone.")
        result["_hint"] = " ".join(hint_parts)

    if not result:
        result["error"] = (
            "No valid include values. Use: map, conventions, rules_list,"
            " functions_graph, hotspots, routes, ui_map"
        )
        return result

    # Show session status if active
    session_info = _session_status(root)
    if session_info:
        result["session"] = session_info

    # Always compute semantic_matches against the registered task — this is
    # the merged orient + find_work_area pathway (Wave 2 of the redesign).
    fwa = _tool_find_work_area(graph, {"query": task, "k": k}, root)
    if "matches" in fwa:
        result["semantic_matches"] = fwa.get("matches", [])
        result["semantic_verdict"] = fwa.get("verdict", "EMPTY")
        result["semantic_top_score"] = fwa.get("max_score", 0.0)
    else:
        # Index missing / warming / empty — surface a hint but don't fail
        # the whole orient call (the session-context sections are still
        # useful even without semantic ranking).
        result["semantic_matches"] = []
        hint = fwa.get("hint") or fwa.get("error") or "semantic index unavailable"
        result["semantic_hint"] = hint

    task_warnings = _validate_task(task, result.get("semantic_matches", []))
    if task_warnings:
        result["task_warnings"] = task_warnings

    return result


# Soft-validation thresholds for the orient(task) parameter.
# These are diagnostics, not gates — the agent always gets results; warnings
# are surface-level guidance per the "Intent formation rules" concept (Wave 2).
_TASK_MIN_WORDS = 3
_TASK_MULTI_RE = re.compile(r"\band\b|&", re.IGNORECASE)
_TASK_VERB_RE = re.compile(
    r"\b(create|change|fix|add|refactor|extract|remove|rename|audit"
    r"|simplify|implement|update|build|delete|move)\b",
    re.IGNORECASE,
)
_TASK_MIN_GOOD_SCORE = 0.5


def _validate_task(task: str, matches: list[dict]) -> list[str]:
    """Return human-readable warnings when the task is structurally weak.

    Three checks today (CONCEPT.md §6 "Intent formation rules"):
      * too short  → likely missing verb or scope
      * multi-task → 'and'/'&' plus ≥2 verbs dilute the audit signal
      * no matches → no semantic_matches scored above threshold
    """
    warnings: list[str] = []
    words = [w for w in task.split() if w]

    if len(words) < _TASK_MIN_WORDS:
        warnings.append(
            "task is very short — recommend adding a verb + scope"
            " (e.g. 'fix Class.method', 'audit X consistency')."
        )

    if _TASK_MULTI_RE.search(task):
        verbs = _TASK_VERB_RE.findall(task)
        if len(verbs) >= 2:
            warnings.append(
                "task looks multi-task (has 'and/&' plus multiple verbs)"
                " — split into separate orient/before_create cycles for"
                " cleaner audit and risk gates."
            )

    if matches:
        top_score = max((m.get("score", 0.0) for m in matches), default=0.0)
        if top_score < _TASK_MIN_GOOD_SCORE:
            warnings.append(
                f"task didn't match any indexed area well"
                f" (top score {top_score:.2f} < {_TASK_MIN_GOOD_SCORE})"
                " — rephrase with a named target or domain term."
            )

    return warnings


def _section_map(graph: Graph, zone_filter: str | None, root: Path) -> dict:
    from winkers.protect import load_startup_chain
    startup_chain = load_startup_chain(root)

    zones: dict[str, list[str]] = {}
    for f in graph.files.values():
        z = f.zone or "unknown"
        zones.setdefault(z, []).append(f.path)

    if zone_filter:
        zones = {z: files for z, files in zones.items() if z == zone_filter}

    semantic = _load_semantic(root)

    zone_list = []
    for z, files in sorted(zones.items()):
        entry: dict[str, Any] = {
            "name": z,
            "files": len(files),
            "functions": sum(
                len(graph.files[f].function_ids) for f in files if f in graph.files
            ),
            "imports_from": _zone_imports_from(z, zones, graph),
            "imported_by": _zone_imported_by(z, zones, graph),
        }
        route_count = sum(
            1 for f in files
            for fn_id in (graph.files[f].function_ids if f in graph.files else [])
            if graph.functions.get(fn_id) and graph.functions[fn_id].route
        )
        if route_count:
            entry["routes_count"] = route_count
        protected_count = sum(1 for f in files if f in startup_chain)
        if protected_count:
            entry["startup_chain"] = protected_count
        if semantic and z in semantic.zone_intents:
            intent = semantic.zone_intents[z]
            entry["intent"] = {"why": intent.why, "wrong_approach": intent.wrong_approach}
        zone_list.append(entry)

    result: dict[str, Any] = {
        "total_files": len(graph.files),
        "total_functions": len(graph.functions),
        "languages": graph.meta.get("languages", []),
        "zones": zone_list,
        "hotspots_top5": _get_hotspots(graph, top=5),
    }
    if semantic and semantic.data_flow:
        result["data_flow"] = semantic.data_flow
    if semantic and semantic.data_flow_targets:
        result["data_flow_targets"] = semantic.data_flow_targets
    return result


def _section_functions_graph(graph: Graph, zone_filter: str | None) -> dict:
    fn_ids = sorted(graph.functions.keys())
    if zone_filter:
        fn_ids = [fid for fid in fn_ids
                  if graph.file_zone(graph.functions[fid].file) == zone_filter]

    id_to_idx: dict[str, int] = {fid: i + 1 for i, fid in enumerate(fn_ids)}
    caller_map: dict[str, list[str]] = {}
    for edge in graph.call_edges:
        caller_map.setdefault(edge.target_fn, []).append(edge.source_fn)

    functions: dict[str, dict] = {}
    for fid in fn_ids:
        fn = graph.functions[fid]
        idx = id_to_idx[fid]
        caller_indices = sorted(
            id_to_idx[c] for c in caller_map.get(fid, []) if c in id_to_idx
        )
        entry: dict[str, Any] = {"id": fn.id, "name": fn.name, "file": fn.file}
        if caller_indices:
            entry["callers"] = caller_indices
        if fn.complexity and fn.complexity > 1:
            entry["cx"] = fn.complexity
        functions[str(idx)] = entry

    return {"total": len(functions), "functions": functions}


def _section_conventions(root: Path) -> dict:
    semantic = _load_semantic(root)
    if semantic is None:
        return {"note": "No semantic.json found. Run winkers init."}

    result: dict[str, Any] = {}
    if semantic.domain_context:
        result["domain_context"] = semantic.domain_context
    if semantic.zone_intents:
        result["zone_intents"] = {
            z: {"why": i.why, "wrong_approach": i.wrong_approach}
            for z, i in semantic.zone_intents.items()
        }
    if semantic.monster_files:
        result["monster_files"] = {
            f: {"sections": [s.model_dump() for s in m.sections],
                "where_to_add": m.where_to_add}
            for f, m in semantic.monster_files.items()
        }
    if semantic.constraints:
        result["project_constraints"] = semantic.constraints
    if semantic.new_feature_checklist:
        result["before_writing_code"] = semantic.new_feature_checklist
    return result


def _section_rules_list(root: Path) -> dict:
    rules_file = _load_rules(root)
    if not rules_file.rules:
        return {"note": "No rules yet. Run winkers init or winkers conventions add."}

    by_category: dict[str, list[dict]] = {}
    for r in rules_file.rules:
        entry: dict = {
            "id": r.id,
            "title": r.title,
        }
        snippet = _one_liner(r.wrong_approach)
        if snippet:
            entry["wrong_approach"] = snippet
        if r.related:
            entry["related"] = r.related
        by_category.setdefault(r.category, []).append(entry)

    return {
        "total": len(rules_file.rules),
        "categories": {
            cat: rules for cat, rules in sorted(by_category.items())
        },
    }


def _one_liner(text: str, limit: int = 140) -> str:
    """Collapse text to a single-line snippet truncated to `limit` chars."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _impact_section_for_fn(fn, root: Path | None) -> dict | None:
    """Shape an impact section for scope(function=) from impact.json."""
    if root is None:
        return None
    impact = _load_impact(root)
    if impact is None:
        return None
    report = impact.functions.get(fn.id)
    if report is None:
        return None
    return {
        "risk_level": report.risk_level,
        "risk_score": report.risk_score,
        "summary": report.summary,
        "safe_operations": list(report.safe_operations),
        "dangerous_operations": list(report.dangerous_operations),
        "caller_classifications": [
            {
                "caller": cc.caller,
                "dependency_type": cc.dependency_type,
                "coupling": cc.coupling,
                "update_effort": cc.update_effort,
                "note": cc.note,
            }
            for cc in report.caller_classifications
        ],
        "action_plan": report.action_plan,
    }


def _similar_logic_for_fn(fn, graph: Graph) -> list[dict]:
    """Group other functions by shared secondary_intents with `fn`."""
    if not fn.secondary_intents:
        return []
    out: list[dict] = []
    for tag in fn.secondary_intents:
        others: list[str] = []
        for other in graph.functions.values():
            if other.id == fn.id:
                continue
            if tag in (other.secondary_intents or []):
                others.append(other.id)
        if not others:
            continue
        entry: dict = {"intent": tag, "also_in": others[:10]}
        if len(others) >= 2:
            entry["suggestion"] = f"consider extracting shared {tag} logic"
        out.append(entry)
    return out


def _value_locked_for_file(graph: Graph, file_path: str) -> list[dict]:
    """Compact value_locked_collections block for scope(file=) response."""
    out: list[dict] = []
    for c in graph.value_locked_collections:
        if c.file != file_path:
            continue
        entry: dict = {
            "name": c.name,
            "kind": c.kind,
            "values": list(c.values),
            "total_literal_uses": sum(c.literal_uses.values()),
        }
        if c.literal_uses:
            entry["literal_uses"] = dict(c.literal_uses)
        if c.files_with_uses:
            entry["files_with_uses"] = list(c.files_with_uses)
        out.append(entry)
    return out


def _file_fn_entry(graph: Graph, fid: str) -> dict:
    """One row inside scope(file=) functions[]. Includes intent if present."""
    fn = graph.functions[fid]
    entry: dict = {
        "id": fid,
        "name": fn.name,
        "locked": graph.is_locked(fid),
        "callers": len(graph.callers(fid)),
    }
    if fn.intent:
        entry["intent"] = fn.intent
    _attach_route(entry, fn)
    return entry


def _attach_route(entry: dict, fn) -> None:
    """Add route/http_method/template to a per-fn dict when fn is a handler."""
    if fn.route:
        entry["route"] = fn.route
        if fn.http_method:
            entry["http_method"] = fn.http_method
        if fn.template:
            entry["template"] = fn.template


def _route_marker(fn) -> str:
    """Compact '[METHOD /path]' marker for inline use in browse strings."""
    if not fn.route:
        return ""
    method = fn.http_method or "GET"
    return f"[{method} {fn.route}]"


def _section_hotspots(graph: Graph, min_callers: int, root: Path | None = None) -> dict:
    hotspots = []
    impact = _load_impact(root) if root is not None else None
    for fn_id, fn in graph.functions.items():
        caller_edges = graph.callers(fn_id)
        if len(caller_edges) < min_callers:
            continue
        entry: dict = {
            "function": fn_id,
            "file": fn.file,
            "signature": _signature(fn),
            "callers_count": len(caller_edges),
            "callers": [
                {
                    "fn": e.source_fn,
                    "file": e.call_site.file,
                    "line": e.call_site.line,
                    "expression": e.call_site.expression,
                    "confidence": e.confidence,
                }
                for e in caller_edges
            ],
        }
        if fn.intent:
            entry["intent"] = fn.intent
        _attach_route(entry, fn)
        if impact is not None:
            report = impact.functions.get(fn_id)
            if report is not None:
                entry["risk_level"] = report.risk_level
                entry["risk_score"] = report.risk_score
        hotspots.append(entry)
    hotspots.sort(key=lambda h: h["callers_count"], reverse=True)
    return {"min_callers": min_callers, "count": len(hotspots), "hotspots": hotspots}


def _section_routes(graph: Graph, zone_filter: str | None) -> dict:
    routes = []
    for fn in graph.functions.values():
        if not fn.route:
            continue
        if zone_filter and graph.file_zone(fn.file) != zone_filter:
            continue
        callees = [e.target_fn.split("::")[-1] for e in graph.callees(fn.id)]
        entry: dict = {
            "method": fn.http_method or "GET",
            "path": fn.route,
            "handler": fn.name,
            "file": fn.file,
            "calls": callees[:8],
        }
        if fn.template:
            entry["template"] = fn.template
        routes.append(entry)
    routes.sort(key=lambda r: (r["file"], r["path"]))
    if not routes:
        return {"count": 0, "routes": [],
                "note": "No routes found. Project may not use decorators."}
    return {"count": len(routes), "routes": routes}


def _section_ui_map(graph: Graph, zone_filter: str | None) -> dict:
    raw: dict = graph.meta.get("ui_map", {})
    if not raw:
        return {"count": 0, "routes": {},
                "note": "No UI map. No templates found or project has no Flask routes."}
    if zone_filter:
        raw = {
            path: data for path, data in raw.items()
            if graph.file_zone(data.get("file", "")) == zone_filter
        }
    return {"count": len(raw), "routes": raw}


def _tool_impact_check(
    graph: Graph, args: dict, root: Path,
    get_graph: Callable[[], Graph | None],
) -> dict:
    from datetime import datetime

    from winkers.detection.impact import compute_diff, format_impact, snapshot_signatures
    from winkers.session.state import SessionStore, Warning, WriteEvent
    from winkers.store import GraphStore

    file_path = args.get("file_path", "")
    if not file_path:
        return {"error": "Provide 'file_path' — relative path to the modified file."}

    # Normalize path separators
    file_path = file_path.replace("\\", "/")

    # 1. Snapshot old signatures + value_locked collections before update
    old_sigs = snapshot_signatures(graph, [file_path])
    old_value_locked = [c.model_copy(deep=True) for c in graph.value_locked_collections]

    # 2. Incremental graph update
    store = GraphStore(root)
    store.update_files(graph, [file_path])
    store.save(graph)

    # Invalidate search token cache for updated file
    from winkers.search import invalidate_token_cache
    file_fn_ids = [
        fid for fid, fn in graph.functions.items() if fn.file == file_path
    ]
    invalidate_token_cache(file_fn_ids)

    # 3. Incremental intent for new/modified functions
    _generate_incremental_intents(graph, root, [file_path])

    # 4. Impact analysis
    diff = compute_diff(old_sigs, graph, [file_path])
    impact = format_impact(diff)

    # 4b. Value-domain change detection
    from winkers.value_locked import diff_collections
    value_changes = diff_collections(
        old_value_locked,
        graph.value_locked_collections,
        root=root,
    )

    # 5. Coherence check
    coherence = _coherence_check(file_path, root)

    # 6. Session state update
    session_store = SessionStore(root)
    session = session_store.load_or_create()

    event = WriteEvent(
        timestamp=datetime.now(UTC).isoformat(),
        file_path=file_path,
        functions_added=[fn.name for fn in diff.added],
        functions_modified=[sc.fn.name for sc in diff.signature_changed],
        functions_removed=diff.removed,
        signature_changes=[
            {"fn_id": sc.fn_id, "old_sig": sc.old_signature, "new_sig": sc.new_signature}
            for sc in diff.signature_changed
        ],
    )
    session.add_write(event)

    # Add warnings for broken callers
    for sc in diff.signature_changed:
        if sc.callers:
            session.add_warning(Warning(
                kind="broken_caller",
                severity="error" if len(sc.callers) > 0 else "warning",
                target=sc.fn_id,
                detail=(
                    f"{sc.fn.name}() signature changed: {sc.old_signature} → {sc.new_signature}. "
                    f"{len(sc.callers)} caller(s) may need updating."
                ),
            ))

    # Add warnings for coherence rules
    for rule in coherence:
        session.add_warning(Warning(
            kind="coherence",
            severity="warning",
            target=file_path,
            detail=f"Rule #{rule['id']} \"{rule['title']}\": check {', '.join(rule['sync_with'])}",
            fix_approach=rule.get("fix_approach", "sync"),
        ))

    # Add warnings for value-domain shrinkage
    for vc in value_changes:
        if not vc["removed"]:
            continue
        session.add_warning(Warning(
            kind="value_locked",
            severity="error" if vc["affected_literal_uses"] > 0 else "warning",
            target=f"{vc['file']}::{vc['name']}",
            detail=(
                f"{vc['name']}: removed {vc['removed']!r}; "
                f"{vc['affected_literal_uses']} caller literal use(s) at risk "
                f"in {len(vc['files_at_risk'])} file(s)."
            ),
        ))

    session_store.save(session)

    # Build response
    result: dict = {"file": file_path}

    if impact:
        result["impact"] = impact

    if value_changes:
        result["value_changes"] = value_changes

    if coherence:
        result["coherence"] = coherence

    result["session"] = session.summary()

    if session.pending_warnings():
        result["session"]["pending"] = [
            w.detail for w in session.pending_warnings()[:5]
        ]
        result["session"]["hint"] = "Call session_done() for an optional final audit."

    return result


def _coherence_check(file_path: str, root: Path) -> list[dict]:
    """Find coherence rules where 'affects' matches the modified file."""
    from winkers.conventions import RulesStore

    rules_file = RulesStore(root).load()
    matches: list[dict] = []

    for r in rules_file.rules:
        if r.category != "coherence":
            continue
        # Check if file_path matches any entry in r.affects
        if not any(file_path == a or file_path.endswith(a) for a in r.affects):
            continue

        entry: dict = {
            "id": r.id,
            "title": r.title,
            "content": r.content,
            "sync_with": r.sync_with,
            "fix_approach": r.fix_approach or "sync",
        }
        if r.wrong_approach:
            entry["wrong_approach"] = r.wrong_approach
        matches.append(entry)

    return matches


def _session_status(root: Path) -> dict | None:
    """Return brief session status if an active session exists."""
    from winkers.session.state import SessionStore

    session_store = SessionStore(root)
    session = session_store.load()
    if session is None:
        return None

    pending = session.pending_warnings()
    info: dict = {
        "writes": len(session.writes),
        "warnings": len(session.warnings),
        "warnings_pending": len(pending),
    }
    if pending:
        info["pending"] = [w.detail for w in pending[:3]]
    return info


def _tool_session_done(graph: Graph, root: Path) -> dict:
    """Session audit — Wave 6 three-tier verdict (PASS / WARN / FAIL).

    Criteria (CONCEPT.md §5):

    FAIL — high precision, structural breakage:
      - Unresolved `broken_caller` warnings (signature changed but callers
        not updated).
      - `coherence` rule with `fix_approach=sync` whose sync_with files
        were not touched.
      - Complexity-delta regression beyond budget.

    WARN — soft signals that don't block:
      - Writes happened but no `before_create` was registered for the
        session (terra incognita choice — surface but don't fail).
      - `value_locked` warnings still present (literal_hits surfaced
        by post_write but neither resolved nor blocking).
      - `coherence` rules with `fix_approach=derived|refactor`.

    PASS — none of the above.

    Anti-loop: on the second+ call we still report the same status —
    Wave 6 dropped the prior "always PASS on repeat" behaviour because
    the Stop hook no longer forces continuation; agents calling
    session_done() repeatedly just get the current verdict.
    """
    from winkers.session.state import SessionStore

    session_store = SessionStore(root)
    session = session_store.load_or_create()

    session.session_done_calls += 1

    issues: list[dict] = []        # FAIL-level
    warnings_list: list[dict] = []  # WARN-level
    recommendations: list[dict] = []

    # FAIL — broken callers
    for w in session.pending_warnings():
        if w.kind == "broken_caller":
            callers_info = _broken_caller_details(w.target, graph)
            issues.append({
                "kind": "broken_caller",
                "detail": w.detail,
                "call_sites": callers_info,
            })

    # FAIL / recommendation — coherence sync_with vs derived
    modified_files = set(session.files_modified())
    for w in session.pending_warnings():
        if w.kind != "coherence":
            continue
        if w.fix_approach == "sync":
            sync_files = _extract_sync_files(w, root)
            unmodified = [f for f in sync_files if f not in modified_files]
            if unmodified:
                issues.append({
                    "kind": "coherence_sync",
                    "detail": w.detail,
                    "unmodified_files": unmodified,
                })
        else:
            recommendations.append({
                "kind": f"coherence_{w.fix_approach or 'derived'}",
                "detail": w.detail,
            })

    # FAIL — complexity-delta regression
    cx_issue = _check_complexity_delta(graph, session)
    if cx_issue:
        issues.append(cx_issue)

    # WARN — value_locked still pending
    for w in session.pending_warnings():
        if w.kind == "value_locked":
            warnings_list.append({
                "kind": "value_locked",
                "severity": w.severity,
                "detail": w.detail,
            })

    # WARN — writes happened but no before_create registered
    if (
        len(session.writes) > 0
        and session.before_create_calls == 0
    ):
        warnings_list.append({
            "kind": "no_intent_registered",
            "detail": (
                f"{len(session.writes)} write(s) without a single"
                " before_create call — terra incognita work, no audit"
                " axis to verify intent fulfillment."
            ),
        })

    session_store.save(session)

    if issues:
        status = "FAIL"
    elif warnings_list:
        status = "WARN"
    else:
        status = "PASS"

    result: dict = {
        "status": status,
        "session": session.summary(),
    }
    if issues:
        result["issues"] = issues
        result["hint"] = (
            "Resolve the issues above. Stop hook will not block; the"
            " status lands in audit.json and the next session's"
            " prompt enrichment will surface it."
        )
    if warnings_list:
        result["warnings"] = warnings_list
    if recommendations:
        result["recommendations"] = recommendations
    return result


def _generate_incremental_intents(
    graph: Graph, root: Path, files: list[str],
) -> None:
    """Generate intents for new/modified functions (non-blocking).

    Only runs if intent provider was explicitly configured (not "auto").
    This prevents surprise API calls during impact_check.
    """
    try:
        from winkers.intent.provider import NoneProvider, auto_detect, load_config

        config = load_config(root)
        # Only generate if user explicitly chose a provider
        if config.provider in ("auto", "none"):
            return

        provider = auto_detect(config)
        if isinstance(provider, NoneProvider):
            return

        for file_path in files:
            file_node = graph.files.get(file_path)
            if not file_node:
                continue
            src_path = root / file_path
            if not src_path.exists():
                continue
            source = src_path.read_text(encoding="utf-8")

            for fn_id in file_node.function_ids:
                fn = graph.functions.get(fn_id)
                if fn is None or fn.intent:
                    continue
                intent = provider.generate(fn, source)
                if intent:
                    fn.intent = intent
    except Exception:
        pass  # Non-blocking: don't fail impact_check on intent errors


def _broken_caller_details(fn_id: str, graph: Graph) -> list[dict]:
    """Get call site details for a broken caller warning."""
    callers = graph.callers(fn_id)
    return [
        {
            "fn": e.source_fn,
            "file": e.call_site.file,
            "line": e.call_site.line,
            "expression": e.call_site.expression,
        }
        for e in callers
    ]


def _extract_sync_files(warning, root: Path) -> list[str]:
    """Extract sync_with file list from a coherence warning."""
    from winkers.conventions import RulesStore

    rules_file = RulesStore(root).load()
    # Match by rule id in the warning detail (e.g. "Rule #14")
    import re
    match = re.search(r"Rule #(\d+)", warning.detail)
    if match:
        rule_id = int(match.group(1))
        for r in rules_file.rules:
            if r.id == rule_id:
                return r.sync_with
    return []


def _check_complexity_delta(graph: Graph, session) -> dict | None:
    """Check if total complexity grew too much during this session."""
    if not session.graph_snapshot_at_start:
        return None

    # Compare complexity of modified files
    modified_files = set(session.files_modified())
    if not modified_files:
        return None

    # Sum current complexity of modified files
    modified_fns = [
        fn for fn in graph.functions.values()
        if fn.file in modified_files
    ]
    if not modified_fns:
        return None

    new_cx = sum(fn.complexity or 0 for fn in modified_fns)

    # Flag if average complexity is very high per function
    avg_cx = new_cx / len(modified_fns)
    if avg_cx > 15:
        return {
            "kind": "debt_regression",
            "detail": (
                f"Average complexity in modified files is {avg_cx:.0f} "
                f"(threshold: 15). Consider simplifying."
            ),
        }
    return None


_AFFECTED_FNS_LIMIT = 5  # cap for zone-expanded function lists

# Keywords that suggest value-domain shrinking (vs additive change). Matched
# stem-wise via the same stemmer search.py uses.
_VALUE_REMOVAL_KEYWORDS = frozenset({
    "simplify", "reduce", "remove", "delete", "consolidate", "shrink",
    "drop", "prune", "collapse", "merge",
})


def _tool_before_create(graph: Graph, args: dict, root: Path) -> dict:
    from winkers.search import format_before_create_response, search_functions
    from winkers.target_resolution import categorize_intent, resolve_targets

    intent = args.get("intent", "")
    zone = args.get("zone", "")

    if not intent:
        return {"error": "Provide 'intent' — what you want to create or change."}

    category = categorize_intent(intent)
    targets = resolve_targets(intent, graph)

    if category == "change":
        if targets.functions or targets.paths:
            return _before_create_change(
                graph, intent, targets, explicit_fns=targets.functions, root=root,
            )
        # No explicit targets — fall back to FTS5 to derive function targets.
        fallback_matches = search_functions(graph, intent, zone=zone)
        if fallback_matches:
            from winkers.target_resolution import ResolvedTargets

            derived = ResolvedTargets()
            for m in fallback_matches:
                if m.fn.id not in derived.functions:
                    derived.functions.append(m.fn.id)
            return _before_create_change(
                graph, intent, derived, explicit_fns=derived.functions, root=root,
            )
        return _before_create_unknown(graph, intent, root)

    if category == "unknown" and targets.is_empty():
        # No keywords, no named targets — give architectural context.
        fallback_matches = search_functions(graph, intent, zone=zone)
        if not fallback_matches:
            return _before_create_unknown(graph, intent, root)
        response = format_before_create_response(
            graph, intent, fallback_matches, zone=zone, root=root,
        )
        response["intent_type"] = "create"
        return response

    # Default: create / fallback.
    matches = search_functions(graph, intent, zone=zone)
    response = format_before_create_response(graph, intent, matches, zone=zone, root=root)
    response["intent_type"] = "create"
    return response


def _before_create_change(
    graph: Graph,
    intent: str,
    targets,
    explicit_fns: list[str],
    root: Path | None = None,
) -> dict:
    """Adaptive response for `change` intents.

    `explicit_fns` are function ids the user named directly in the intent
    (or FTS5-derived from the intent). They are always shown in full.
    Functions discovered by zone/file expansion are added on top, but only
    if locked, and capped at _AFFECTED_FNS_LIMIT.
    """
    file_paths = sorted(set(targets.paths))
    explicit_set = set(explicit_fns)

    # Files derived from explicit functions — used only for the `files` block
    # (import/migration metrics). Not surfaced in resolved_targets (user didn't
    # name them) and not fed into zone expansion (would inflate `functions`).
    derived_files = {
        graph.functions[fid].file
        for fid in explicit_fns
        if fid in graph.functions
    }
    analysis_paths = sorted(set(file_paths) | derived_files)

    response: dict = {
        "intent_type": "change",
        "intent": intent,
        "resolved_targets": {
            "files": file_paths,
            "functions": [f for f in explicit_fns if f in graph.functions],
        },
    }

    if analysis_paths:
        response["files"] = _files_block(
            graph, analysis_paths, explicit_fns=explicit_fns,
        )

    fn_block = _functions_block(graph, file_paths, explicit_set, root=root)
    if fn_block is not None:
        response["functions"] = fn_block

    value_block = _value_changes_block(graph, intent, file_paths, explicit_fns)
    if value_block:
        response["value_changes"] = value_block

    duplication_block = _duplication_warning(graph, explicit_fns)
    if duplication_block:
        response["similar_logic"] = duplication_block

    return response


def _duplication_warning(graph: Graph, explicit_fns: list[str]) -> list[dict]:
    """If an explicit target function shares secondary_intents with others,
    surface them so the caller doesn't inadvertently extend duplicated logic."""
    seen_tags: set[str] = set()
    out: list[dict] = []
    for fid in explicit_fns:
        fn = graph.functions.get(fid)
        if fn is None or not fn.secondary_intents:
            continue
        for tag in fn.secondary_intents:
            if tag in seen_tags:
                continue
            others = [
                other.id for other in graph.functions.values()
                if other.id != fid and tag in (other.secondary_intents or [])
            ]
            if not others:
                continue
            seen_tags.add(tag)
            out.append({
                "intent": tag,
                "source": fid,
                "also_in": others[:10],
                "suggestion": f"'{tag}' appears in other functions too — "
                "consider extracting instead of duplicating.",
            })
    return out


def _value_changes_block(
    graph: Graph,
    intent: str,
    file_paths: list[str],
    explicit_fns: list[str],
) -> dict | None:
    """If intent looks like a value-domain shrink AND it touches a file (or
    referencing function) with a value_locked collection, surface a warning
    block per affected collection.
    """
    if not graph.value_locked_collections:
        return None

    # Only fire on shrink-style intents — additive changes don't break callers.
    import re as _re

    from winkers.search import stem
    intent_stems = {stem(w.lower()) for w in _re.findall(r"[A-Za-z][A-Za-z0-9]*", intent)}
    removal_stems = {stem(w) for w in _VALUE_REMOVAL_KEYWORDS}
    if not (intent_stems & removal_stems):
        return None

    file_set = set(file_paths)
    explicit_set = set(explicit_fns)

    block: dict = {}
    for c in graph.value_locked_collections:
        # Match if collection's file is targeted, or any explicit fn references it.
        relevant = (
            c.file in file_set
            or any(fid in explicit_set for fid in c.referenced_by_fns)
        )
        if not relevant:
            continue
        total_uses = sum(c.literal_uses.values())
        block[c.name] = {
            "value_locked": True,
            "file": c.file,
            "values": list(c.values),
            "total_literal_uses": total_uses,
            "files_at_risk": len(c.files_with_uses),
        }
    return block or None


def _files_block(
    graph: Graph,
    file_paths: list[str],
    explicit_fns: set[str] | None = None,
) -> dict:
    """Per-file coupling and impact metrics for the before_create `files` block.

    Three distinct "how much needs to change" signals:

    - ``importing_files``: unique external files that import from anything in
      the resolved set. Upper bound on "files that reference our module
      surface" — a module-level change touches all of them.
    - ``migration_cost``: raw count of *import edges* from external files.
      Will exceed ``len(importing_files)`` when a single file imports several
      symbols from a target. Kept for backward compat; prefer
      ``direct_caller_files`` for fn-level intents.
    - ``direct_caller_files``: when the intent names specific functions
      (``explicit_fns``), the unique external files that actually *invoke*
      those functions. This is the real hands-on editing surface — only set
      when explicit fns are resolved, otherwise the importer-level bound is
      as tight as we can be without fn info.
    """
    from winkers.target_resolution import is_test_path

    resolved_set = set(file_paths)
    explicit_fns = explicit_fns or set()

    cross_imports = 0
    external_importers: set[str] = set()
    migration_cost = 0
    prod_locked = 0
    test_locked = 0

    for path in file_paths:
        for edge in graph.imports_from_file(path):
            if edge.target_file in resolved_set and edge.target_file != path:
                cross_imports += 1
        for edge in graph.imported_by_file(path):
            if edge.source_file in resolved_set:
                continue
            external_importers.add(edge.source_file)
            migration_cost += 1
        fnode = graph.files.get(path)
        if not fnode:
            continue
        path_is_test = is_test_path(path)
        for fid in fnode.function_ids:
            if not graph.is_locked(fid):
                continue
            if path_is_test:
                test_locked += 1
                continue
            # Prod-file fn: only count as "locked" if it has at least one
            # production caller. Pytest-fixture-like references from tests
            # shouldn't inflate the number.
            if any(not is_test_path(e.call_site.file) for e in graph.callers(fid)):
                prod_locked += 1

    block: dict = {
        "cross_imports": cross_imports,
        "imported_by": sorted(external_importers),
        "importing_files": len(external_importers),
        "migration_cost": migration_cost,
        "locked_fns": prod_locked,
    }
    if test_locked:
        block["locked_test_fns"] = test_locked

    # Direct-caller surface: only meaningful when the intent named specific
    # functions. Collect external files that contain actual call-sites to
    # those functions, not just an import of the module.
    if explicit_fns:
        direct_caller_files: set[str] = set()
        for fid in explicit_fns:
            if fid not in graph.functions:
                continue
            for edge in graph.callers(fid):
                caller_file = edge.call_site.file
                if caller_file in resolved_set:
                    continue
                direct_caller_files.add(caller_file)
        block["direct_caller_files"] = sorted(direct_caller_files)
        block["direct_caller_files_count"] = len(direct_caller_files)

    return block


def _functions_block(
    graph: Graph,
    file_paths: list[str],
    explicit_fns: set[str],
    root: Path | None = None,
) -> dict | None:
    """Build affected_fns list. Explicit fns shown in full; zone-expanded
    fns shown locked-only and capped, with totals so the agent sees scale."""

    # Functions discovered by zone/file expansion (not user-named).
    expanded_locked: list[str] = []
    expanded_total_locked = 0
    expanded_total_callers = 0
    for path in file_paths:
        fnode = graph.files.get(path)
        if not fnode:
            continue
        for fid in fnode.function_ids:
            if fid in explicit_fns:
                continue
            if not graph.is_locked(fid):
                continue
            expanded_total_locked += 1
            expanded_total_callers += len(graph.callers(fid))
            expanded_locked.append(fid)

    # Sort expansion by callers desc so the cap keeps the most-impactful ones.
    expanded_locked.sort(key=lambda fid: len(graph.callers(fid)), reverse=True)
    shown_expanded = expanded_locked[:_AFFECTED_FNS_LIMIT]

    chosen_ids = list(explicit_fns) + [
        fid for fid in shown_expanded if fid not in explicit_fns
    ]
    if not chosen_ids:
        return None

    impact = _load_impact(root) if root is not None else None

    affected: list[dict] = []
    for fid in chosen_ids:
        fn = graph.functions.get(fid)
        if fn is None:
            continue
        caller_edges = graph.callers(fid)
        test_count = sum(
            1 for e in caller_edges
            if "test" in e.call_site.file.lower()
        )
        affected_entry: dict = {
            "name": fn.name,
            "file": fn.file,
            "locked": bool(caller_edges),
            "callers_count": len(caller_edges),
            "test_callers_count": test_count,
            "callers": [
                {
                    "file": e.call_site.file,
                    "line": e.call_site.line,
                    "fn": e.source_fn,
                    "expression": e.call_site.expression,
                }
                for e in caller_edges[:8]
            ],
        }
        if fn.intent:
            affected_entry["intent"] = fn.intent
        _attach_route(affected_entry, fn)
        if impact is not None:
            report = impact.functions.get(fid)
            if report is not None:
                affected_entry["risk_level"] = report.risk_level
                if report.dangerous_operations:
                    affected_entry["dangerous_operations"] = list(report.dangerous_operations)
        affected.append(affected_entry)

    block: dict = {"affected_fns": affected}

    # Truncation counters: only when zone-expansion actually skipped something.
    if expanded_total_locked > len(shown_expanded):
        block["total_locked"] = expanded_total_locked + len(explicit_fns)
        block["total_callers"] = expanded_total_callers + sum(
            len(graph.callers(fid)) for fid in explicit_fns if fid in graph.functions
        )
        block["shown"] = len(affected)

    return block


def _before_create_unknown(graph: Graph, intent: str, root: Path) -> dict:
    """Compact, actionable response when intent can't be resolved.

    Returning architectural context here (hotspots + zone_intents) produced
    ~1.5k tokens of noise that agents took as relevant — strictly worse
    than an empty answer. A short "rewrite your intent like X" is more
    useful and a lot cheaper.
    """
    return {
        "intent_type": "unknown",
        "intent": intent,
        "error": "Could not parse intent into resolvable targets.",
        "hint": (
            "Rewrite the intent with explicit markers:"
            " `fn_name()` for functions (e.g. calculate_price()),"
            " `Class.method()` for methods,"
            " `Class.attribute` (no parens) for SQLAlchemy relationships,"
            " Pydantic / dataclass fields, or other attribute-level targets,"
            " or a relative file path (e.g. app/repos/invoice.py)."
            " Combine for precision: 'refactor calculate_price() in"
            " app/pricing.py'. Multiple attrs in one go are fine:"
            " 'fix Client.invoices, Client.payments, Client.contracts'."
        ),
        "examples": [
            "refactor calculate_price() to round half-even",
            "fix InvoiceRepo.get_with_items() selectinload",
            "fix Client.invoices, Client.payments cascade",
            "add soft_delete() to app/repos/base.py",
            "rename modules/pricing.py::calc_tax to apply_tax",
        ],
        "fallback": (
            "For architectural context call"
            " orient(task='<your task>', include=['map','conventions','hotspots'])."
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_function(name: str, graph: Graph):
    if name in graph.functions:
        return graph.functions[name]
    matches = [fn for fn in graph.functions.values() if fn.name == name]
    return matches[0] if len(matches) == 1 else None


def _signature(fn: Any) -> str:
    params = ", ".join(
        f"{p.name}: {p.type_hint}" if p.type_hint else p.name
        for p in fn.params
    )
    ret = f" -> {fn.return_type}" if fn.return_type else ""
    return f"({params}){ret}"


def _zone_imports_from(zone: str, zones: dict[str, list[str]], graph: Graph) -> list[str]:
    zone_files = set(zones.get(zone, []))
    imported_zones: set[str] = set()
    for edge in graph.import_edges:
        if edge.source_file in zone_files:
            target_zone = graph.file_zone(edge.target_file)
            if target_zone != zone:
                imported_zones.add(target_zone)
    return sorted(imported_zones)


def _zone_imported_by(zone: str, zones: dict[str, list[str]], graph: Graph) -> list[str]:
    zone_files = set(zones.get(zone, []))
    importing_zones: set[str] = set()
    for edge in graph.import_edges:
        if edge.target_file in zone_files:
            source_zone = graph.file_zone(edge.source_file)
            if source_zone != zone:
                importing_zones.add(source_zone)
    return sorted(importing_zones)


def _get_hotspots(graph: Graph, top: int = 5) -> list[dict]:
    scored = [(fid, len(graph.callers(fid))) for fid in graph.functions]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [
        {
            "fn": fid,
            "file": graph.functions[fid].file,
            "call_count": count,
            "locked": graph.is_locked(fid),
        }
        for fid, count in scored[:top]
        if count > 0
    ]


def _recent_changes_from_graph(fn: Any, graph: Graph) -> list[dict]:
    file_node = graph.files.get(fn.file)
    if not file_node or not file_node.recent_commits:
        return []
    return file_node.recent_commits


def _semantic_context_for_fn(fn: Any, graph: Graph, root: Path | None) -> dict:
    """Return zone_intent and data_flow for the function's zone."""
    if root is None:
        return {}
    semantic = _load_semantic(root)
    if semantic is None:
        return {}
    zone = graph.file_zone(fn.file)
    ctx: dict[str, Any] = {}
    if zone in semantic.zone_intents:
        intent = semantic.zone_intents[zone]
        ctx["zone_intent"] = {"why": intent.why, "wrong_approach": intent.wrong_approach}
    if semantic.data_flow:
        ctx["data_flow"] = semantic.data_flow
    if semantic.data_flow_targets:
        ctx["data_flow_targets"] = semantic.data_flow_targets
    return ctx


def _related_rules(fn: Any, graph: Graph, root: Path | None) -> list[dict]:
    """Find rules from rules.json that affect this function's zone or file."""
    if root is None:
        return []
    rules_file = _load_rules(root)
    zone = graph.file_zone(fn.file)
    return [
        {"id": r.id, "category": r.category, "title": r.title}
        for r in rules_file.rules
        if zone in r.affects or fn.file in r.affects
    ]


def _build_callers_constraint(fn: Any, caller_edges: list) -> dict:
    return {
        "callers_expect": _signature(fn),
        "safe_changes": [
            "modify function body",
            "add optional parameters with defaults",
            "add overloaded variant",
        ],
        "breaking_changes": [
            "change existing parameter types",
            "change parameter order",
            "change return type",
            "remove parameters",
        ],
    }


# ---------------------------------------------------------------------------
# Re-exports — extracted tool modules. Imports come AFTER all helpers above
# are defined as module attributes, so sub-modules can do
# `from winkers.mcp.tools import _find_function` without a circular bind.
# ---------------------------------------------------------------------------

from winkers.mcp.tools.browse import _tool_browse  # noqa: E402, F401
from winkers.mcp.tools.convention_read import _tool_convention_read  # noqa: E402, F401
from winkers.mcp.tools.find_work_area import (  # noqa: E402, F401
    _find_work_area_advice,
    _tool_find_work_area,
)
from winkers.mcp.tools.rule_read import _tool_rule_read  # noqa: E402, F401
from winkers.mcp.tools.scope import _tool_scope  # noqa: E402, F401
