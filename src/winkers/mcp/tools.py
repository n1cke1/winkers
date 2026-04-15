"""MCP tool definitions: orient, scope, convention_read, rule_read."""

from __future__ import annotations

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
                    " Pass `include` as an array of section names,"
                    " e.g. include=['map','rules_list']."
                    " Do NOT serialize as a JSON-encoded string."
                    " 'map' = project structure, zones, hotspots, data flow."
                    " 'conventions' = domain context, zone intents, business logic."
                    " 'rules_list' = coding rules grouped by category."
                    " 'functions_graph' = indexed call graph."
                    " 'hotspots' = high-impact functions."
                    " 'routes' = HTTP endpoints."
                    " 'ui_map' = route→template links with UI elements."
                    " Then use convention_read/rule_read for details."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
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
                    },
                    "required": ["include"],
                },
            ),
            Tool(
                name="scope",
                description=(
                    "Full context for a function or file: callers, callees,"
                    " related rules, recent git changes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "function": {"type": "string", "description": "Function ID or name"},
                        "file": {"type": "string", "description": "File path"},
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
                    "CALL THIS BEFORE writing any new function, class, or module."
                    " Searches the project graph for existing implementations matching"
                    " your intent. Returns reusable code with import paths and pipeline"
                    " context (upstream callers + downstream callees), or conventions"
                    " for writing new code in the target zone."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "description": (
                                "What you want to create, in natural language."
                                " Examples: 'validate email', 'calculate price',"
                                " 'parse CSV config', 'send notification'"
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
        elif name == "impact_check":
            result = _tool_impact_check(graph, arguments, root, get_graph)
        elif name == "session_done":
            result = _tool_session_done(graph, root)
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


MAX_ORIENT_TOKENS = 2000

# Priority order: most important sections first for truncation.
_SECTION_PRIORITY = [
    "map", "conventions", "rules_list", "hotspots",
    "routes", "ui_map", "functions_graph",
]


def _estimate_tokens(data: Any) -> int:
    """Rough token count: ~4 chars per token in JSON output."""
    import json
    return len(json.dumps(data, default=str)) // 4


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
    include = _coerce_include(args.get("include", []))
    zone = args.get("zone")
    min_callers = args.get("min_callers", 10)
    max_tokens = args.get("max_tokens", MAX_ORIENT_TOKENS)

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

    for section in ordered:
        build = builders.get(section)
        if build is None:
            continue
        data = build()
        section_tokens = _estimate_tokens(data)
        if used_tokens + section_tokens > max_tokens and result:
            skipped.append(section)
            continue
        result[section] = data
        used_tokens += section_tokens

    if skipped:
        result["_truncated"] = True
        result["_hint"] = (
            f"Response truncated at ~{max_tokens} token budget. "
            f"Skipped: {', '.join(skipped)}. "
            "Call orient() with fewer includes or filter by zone."
        )

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

    return result


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
    return entry


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


def _tool_scope(graph: Graph, args: dict, root: Path | None = None) -> dict:
    fn_name = args.get("function")
    file_path = args.get("file")

    if fn_name:
        fn = _find_function(fn_name, graph)
        if fn is None:
            return {"error": f"Function not found: {fn_name}"}

        caller_edges = graph.callers(fn.id)
        callee_edges = graph.callees(fn.id)

        function_entry: dict[str, Any] = {
            "id": fn.id,
            "file": fn.file,
            "line_start": fn.line_start,
            "line_end": fn.line_end,
            "signature": _signature(fn),
            "docstring": fn.docstring,
            "complexity": fn.complexity,
            "is_async": fn.is_async,
            "locked": graph.is_locked(fn.id),
        }
        if fn.intent:
            function_entry["intent"] = fn.intent
        result = {
            "function": function_entry,
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
            "callees": [
                {"fn": e.target_fn, "expression": e.call_site.expression}
                for e in callee_edges
            ],
            "callers_constraint": _build_callers_constraint(fn, caller_edges),
            "related_rules": _related_rules(fn, graph, root),
            "recent_changes": _recent_changes_from_graph(fn, graph),
        }

        semantic_ctx = _semantic_context_for_fn(fn, graph, root)
        if semantic_ctx:
            result["semantic"] = semantic_ctx

        impact_section = _impact_section_for_fn(fn, root)
        if impact_section:
            result["impact"] = impact_section

        similar = _similar_logic_for_fn(fn, graph)
        if similar:
            result["similar_logic"] = similar

        return result

    if file_path:
        file_node = graph.files.get(file_path)
        if not file_node:
            return {"error": f"File not found: {file_path}"}
        incoming = graph.imported_by_file(file_path)
        file_result: dict[str, Any] = {
            "file": file_path,
            "language": file_node.language,
            "loc": file_node.lines_of_code,
            "imports": file_node.imports,
            "functions": [
                _file_fn_entry(graph, fid)
                for fid in file_node.function_ids
                if fid in graph.functions
            ],
            "sibling_imports": graph.sibling_imports_count(file_path),
            "imported_by": sorted({e.source_file for e in incoming}),
            "migration_cost": len(incoming),
        }
        value_locked_section = _value_locked_for_file(graph, file_path)
        if value_locked_section:
            file_result["value_locked_collections"] = value_locked_section
        if root:
            from winkers.protect import load_startup_chain
            if file_path in load_startup_chain(root):
                file_result["startup_chain"] = True
                file_result["warning"] = (
                    "This file is in the startup chain. "
                    "Changes here can prevent the application from starting."
                )
        return file_result

    return {"error": "Provide 'function' or 'file' argument"}


def _tool_convention_read(args: dict, root: Path) -> dict:
    target = args.get("target", "")
    semantic = _load_semantic(root)

    if semantic is None:
        return {"error": "No semantic.json found. Run winkers init."}

    # Aspect names
    if target == "data_flow":
        return {"data_flow": semantic.data_flow or "Not available."}
    if target == "domain_context":
        return {"domain_context": semantic.domain_context or "Not available."}
    if target == "checklist":
        return {"checklist": semantic.new_feature_checklist}
    if target == "constraints":
        return {"constraints": semantic.constraints}

    # Zone name
    if target in semantic.zone_intents:
        intent = semantic.zone_intents[target]
        return {
            "zone": target,
            "why": intent.why,
            "wrong_approach": intent.wrong_approach,
        }

    # File path (monster file)
    if target in semantic.monster_files:
        mf = semantic.monster_files[target]
        return {
            "file": target,
            "sections": [s.model_dump() for s in mf.sections],
            "where_to_add": mf.where_to_add,
        }

    return {
        "error": f"Target '{target}' not found.",
        "available_zones": list(semantic.zone_intents.keys()),
        "available_files": list(semantic.monster_files.keys()),
        "aspects": ["data_flow", "domain_context", "checklist", "constraints"],
    }


def _tool_rule_read(args: dict, root: Path) -> dict:
    category = args.get("category", "")
    rules_file = _load_rules(root)

    matches = [r for r in rules_file.rules if r.category == category]
    if not matches:
        available = sorted({r.category for r in rules_file.rules})
        return {"error": f"No rules for category '{category}'.", "available": available}

    return {
        "category": category,
        "rules": [
            {
                "id": r.id,
                "title": r.title,
                "content": r.content,
                "wrong_approach": r.wrong_approach,
                "affects": r.affects,
                "related": r.related,
            }
            for r in matches
        ],
    }


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
    value_changes = diff_collections(old_value_locked, graph.value_locked_collections)

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
    """Session audit: PASS if no unresolved issues, FAIL otherwise."""
    from winkers.session.state import SessionStore

    session_store = SessionStore(root)
    session = session_store.load_or_create()

    session.session_done_calls += 1
    is_first_call = session.session_done_calls == 1

    # Collect issues
    issues: list[dict] = []
    recommendations: list[dict] = []

    if is_first_call:
        # 1. Unresolved broken callers
        for w in session.pending_warnings():
            if w.kind == "broken_caller":
                callers_info = _broken_caller_details(w.target, graph)
                issues.append({
                    "kind": "broken_caller",
                    "detail": w.detail,
                    "call_sites": callers_info,
                })

        # 2. Coherence sync_with not modified
        modified_files = set(session.files_modified())
        for w in session.pending_warnings():
            if w.kind != "coherence":
                continue
            if w.fix_approach == "sync":
                # Extract sync_with files from the warning detail
                sync_files = _extract_sync_files(w, root)
                unmodified = [f for f in sync_files if f not in modified_files]
                if unmodified:
                    issues.append({
                        "kind": "coherence_sync",
                        "detail": w.detail,
                        "unmodified_files": unmodified,
                    })
            else:
                # derived/refactor: don't block, but recommend
                recommendations.append({
                    "kind": f"coherence_{w.fix_approach or 'derived'}",
                    "detail": w.detail,
                })

        # 3. Complexity delta check
        cx_issue = _check_complexity_delta(graph, session)
        if cx_issue:
            issues.append(cx_issue)

    # Save updated session state
    session_store.save(session)

    # Build response
    if not is_first_call:
        # Anti-loop: PASS with remaining warnings on second+ call
        result: dict = {
            "status": "PASS",
            "note": "Session audit passed (repeat call — remaining warnings logged).",
            "session": session.summary(),
        }
        pending = session.pending_warnings()
        if pending:
            result["remaining_warnings"] = [w.detail for w in pending[:5]]
        return result

    if issues:
        result = {
            "status": "FAIL",
            "issues": issues,
            "session": session.summary(),
            "hint": "Fix the issues above and call session_done() again.",
        }
        if recommendations:
            result["recommendations"] = recommendations
        return result

    result = {
        "status": "PASS",
        "session": session.summary(),
    }
    if recommendations:
        result["recommendations"] = recommendations
        result["note"] = "PASS — but consider these improvements."
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

    response: dict = {
        "intent_type": "change",
        "intent": intent,
        "resolved_targets": {
            "files": file_paths,
            "functions": [f for f in explicit_fns if f in graph.functions],
        },
    }

    if file_paths:
        response["files"] = _files_block(graph, file_paths)

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
            "safe_alternative": _value_safe_alternative(total_uses),
        }
    return block or None


def _value_safe_alternative(total_uses: int) -> str:
    if total_uses == 0:
        return (
            "no caller uses these values as literals — safe to change, but "
            "verify no string-formatting / dict-key paths read them."
        )
    return (
        f"add new values alongside existing; map old to new via a function. "
        f"Do not remove values that appear as literals in {total_uses} call sites."
    )


def _files_block(graph: Graph, file_paths: list[str]) -> dict:
    resolved_set = set(file_paths)
    cross_imports = 0
    external_importers: set[str] = set()
    migration_cost = 0
    locked_fns = 0

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
        for fid in fnode.function_ids:
            if graph.is_locked(fid):
                locked_fns += 1

    if cross_imports == 0 and migration_cost > 0:
        safe_alternative = (
            "re-export facade: create a new module that re-exports from originals. "
            "Zero existing files changed, zero callers to update."
        )
    elif cross_imports == 0 and migration_cost == 0:
        safe_alternative = (
            "files have no cross-imports and no external callers — merging is safe, "
            "but gives no cohesion benefit either."
        )
    else:
        safe_alternative = (
            f"merge with caller updates: {migration_cost} import statements across "
            f"{len(external_importers)} files must be rewritten."
        )

    return {
        "cross_imports": cross_imports,
        "imported_by": sorted(external_importers),
        "migration_cost": migration_cost,
        "locked_fns": locked_fns,
        "safe_alternative": safe_alternative,
    }


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
    hotspots = _section_hotspots(graph, min_callers=3, root=root)
    top_hotspots = hotspots.get("hotspots", [])[:5]

    zone_intents: dict[str, dict] = {}
    try:
        semantic = _load_semantic(root)
        if semantic is not None:
            for zname, zintent in semantic.zone_intents.items():
                zone_intents[zname] = {
                    "why": zintent.why,
                    "wrong_approach": zintent.wrong_approach,
                }
    except Exception:
        pass

    return {
        "intent_type": "unknown",
        "intent": intent,
        "note": (
            "Could not resolve specific files or functions from the intent. "
            "Returning architectural context to avoid an empty answer."
        ),
        "hotspots": top_hotspots,
        "zone_intents": zone_intents,
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
