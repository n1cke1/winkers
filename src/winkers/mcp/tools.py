"""MCP tool definitions: orient, scope, convention_read, rule_read."""

from __future__ import annotations

from collections.abc import Callable
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
                    "IMPORTANT: Call this FIRST. Specify what you need via 'include'."
                    " 'map' = project structure, zones, hotspots, data flow."
                    " 'conventions' = domain context, zone intents, business logic."
                    " 'rules_list' = coding rules grouped by category."
                    " 'functions_graph' = indexed call graph."
                    " 'hotspots' = high-impact functions."
                    " 'routes' = HTTP endpoints."
                    " 'ui_map' = route→template links with UI elements (panels, tables, forms)."
                    " Combine: include=['map','conventions']."
                    " Then use convention_read/rule_read for details."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "include": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "What to include: 'map', 'conventions', 'rules_list',"
                                " 'functions_graph', 'hotspots', 'routes', 'ui_map'"
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


def _load_rules(root: Path):
    from winkers.conventions import RulesStore
    return RulesStore(root).load()


def _tool_orient(graph: Graph, args: dict, root: Path) -> dict:
    include = args.get("include", [])

    zone = args.get("zone")
    min_callers = args.get("min_callers", 10)
    result: dict[str, Any] = {}

    if "map" in include:
        result["map"] = _section_map(graph, zone, root)
    if "functions_graph" in include:
        result["functions_graph"] = _section_functions_graph(graph, zone)
    if "conventions" in include:
        result["conventions"] = _section_conventions(root)
    if "rules_list" in include:
        result["rules_list"] = _section_rules_list(root)
    if "hotspots" in include:
        result["hotspots"] = _section_hotspots(graph, min_callers)
    if "routes" in include:
        result["routes"] = _section_routes(graph, zone)
    if "ui_map" in include:
        result["ui_map"] = _section_ui_map(graph, zone)

    if not result:
        result["error"] = (
            "No valid include values. Use: map, conventions, rules_list,"
            " functions_graph, hotspots, routes, ui_map"
        )
    return result


def _section_map(graph: Graph, zone_filter: str | None, root: Path) -> dict:
    zones: dict[str, list[str]] = {}
    for f in graph.files.values():
        z = f.zone or _infer_zone(f.path)
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
                  if _infer_zone(graph.functions[fid].file) == zone_filter]

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
        by_category.setdefault(r.category, []).append({
            "id": r.id,
            "title": r.title,
            "related": r.related,
        })

    return {
        "total": len(rules_file.rules),
        "categories": {
            cat: rules for cat, rules in sorted(by_category.items())
        },
    }


def _section_hotspots(graph: Graph, min_callers: int) -> dict:
    hotspots = []
    for fn_id, fn in graph.functions.items():
        caller_edges = graph.callers(fn_id)
        if len(caller_edges) < min_callers:
            continue
        hotspots.append({
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
        })
    hotspots.sort(key=lambda h: h["callers_count"], reverse=True)
    return {"min_callers": min_callers, "count": len(hotspots), "hotspots": hotspots}


def _section_routes(graph: Graph, zone_filter: str | None) -> dict:
    routes = []
    for fn in graph.functions.values():
        if not fn.route:
            continue
        if zone_filter and _infer_zone(fn.file) != zone_filter:
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
            if _infer_zone(data.get("file", "")) == zone_filter
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

        return {
            "function": {
                "id": fn.id,
                "file": fn.file,
                "line_start": fn.line_start,
                "line_end": fn.line_end,
                "signature": _signature(fn),
                "docstring": fn.docstring,
                "complexity": fn.complexity,
                "is_async": fn.is_async,
                "locked": graph.is_locked(fn.id),
            },
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
            "related_rules": _related_rules(fn, root),
            "recent_changes": _recent_changes_from_graph(fn, graph),
        }

    if file_path:
        file_node = graph.files.get(file_path)
        if not file_node:
            return {"error": f"File not found: {file_path}"}
        return {
            "file": file_path,
            "language": file_node.language,
            "loc": file_node.lines_of_code,
            "imports": file_node.imports,
            "functions": [
                {
                    "id": fid,
                    "name": graph.functions[fid].name,
                    "locked": graph.is_locked(fid),
                    "callers": len(graph.callers(fid)),
                }
                for fid in file_node.function_ids
                if fid in graph.functions
            ],
        }

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


def _infer_zone(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else "root"


def _zone_imports_from(zone: str, zones: dict[str, list[str]], graph: Graph) -> list[str]:
    zone_files = set(zones.get(zone, []))
    imported_zones: set[str] = set()
    for edge in graph.import_edges:
        if edge.source_file in zone_files:
            target_zone = _infer_zone(edge.target_file)
            if target_zone != zone:
                imported_zones.add(target_zone)
    return sorted(imported_zones)


def _zone_imported_by(zone: str, zones: dict[str, list[str]], graph: Graph) -> list[str]:
    zone_files = set(zones.get(zone, []))
    importing_zones: set[str] = set()
    for edge in graph.import_edges:
        if edge.target_file in zone_files:
            source_zone = _infer_zone(edge.source_file)
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


def _related_rules(fn: Any, root: Path | None) -> list[dict]:
    """Find rules from rules.json that affect this function's zone or file."""
    if root is None:
        return []
    rules_file = _load_rules(root)
    zone = _infer_zone(fn.file)
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
