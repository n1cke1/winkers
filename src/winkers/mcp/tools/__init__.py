"""MCP tool definitions: orient, scope, convention_read, rule_read."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.types import TextContent, Tool

from winkers.mcp.tools._common import (  # noqa: F401  (re-exports for external callers)
    MAX_ORIENT_TOKENS,
    _attach_route,
    _build_callers_constraint,
    _coerce_include,
    _estimate_tokens,
    _file_fn_entry,
    _find_function,
    _get_hotspots,
    _impact_section_for_fn,
    _load_impact,
    _load_rules,
    _load_semantic,
    _log_call,
    _one_liner,
    _recent_changes_from_graph,
    _related_rules,
    _route_marker,
    _semantic_context_for_fn,
    _signature,
    _similar_logic_for_fn,
    _try_compact,
    _value_locked_for_file,
    _zone_imported_by,
    _zone_imports_from,
)
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


# ---------------------------------------------------------------------------
# Re-exports — extracted tool modules.
# ---------------------------------------------------------------------------

from winkers.mcp.tools.before_create import (  # noqa: E402, F401
    _before_create_change,
    _before_create_unknown,
    _duplication_warning,
    _files_block,
    _functions_block,
    _tool_before_create,
    _value_changes_block,
)
from winkers.mcp.tools.browse import _tool_browse  # noqa: E402, F401
from winkers.mcp.tools.convention_read import _tool_convention_read  # noqa: E402, F401
from winkers.mcp.tools.find_work_area import (  # noqa: E402, F401
    _find_work_area_advice,
    _tool_find_work_area,
)
from winkers.mcp.tools.impact_check import (  # noqa: E402, F401
    _coherence_check,
    _generate_incremental_intents,
    _tool_impact_check,
)
from winkers.mcp.tools.orient import (  # noqa: E402, F401
    _section_conventions,
    _section_functions_graph,
    _section_hotspots,
    _section_map,
    _section_routes,
    _section_rules_list,
    _section_ui_map,
    _session_status,
    _tool_orient,
    _validate_task,
)
from winkers.mcp.tools.rule_read import _tool_rule_read  # noqa: E402, F401
from winkers.mcp.tools.scope import _tool_scope  # noqa: E402, F401
from winkers.mcp.tools.session_done import (  # noqa: E402, F401
    _broken_caller_details,
    _check_complexity_delta,
    _extract_sync_files,
    _tool_session_done,
)
