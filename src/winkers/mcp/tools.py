"""MCP tool definitions: map, full_map, scope."""

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
                name="map",
                description=(
                    "IMPORTANT: Call this FIRST before any code changes."
                    " Returns critical project constraints, conventions,"
                    " and before_writing_code checklist that you MUST follow."
                    " Ignoring these causes architectural damage."
                    " Also returns: zones with intents, hotspots."
                    " Use zone/file filters to narrow down."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "zone": {"type": "string", "description": "Filter by zone name"},
                        "file": {"type": "string", "description": "Filter by file path"},
                    },
                },
            ),
            Tool(
                name="functions_graph",
                description=(
                    "Indexed function graph: every function with a numeric ID,"
                    " callers as index references, and complexity."
                    " Use index numbers to quickly identify dependencies."
                    " High callers count = many dependents, change carefully."
                    " Use before calling scope on specific functions."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="hotspots",
                description=(
                    "Critical dependency graph: functions with 5+ callers and"
                    " WHO calls them. These are high-impact functions —"
                    " changing their signature or behavior affects many callers."
                    " ALWAYS check this before modifying a frequently-called function."
                    " Each entry shows the function, its callers list, and the"
                    " call expressions so you understand HOW it's being used."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "min_callers": {
                            "type": "integer",
                            "description": "Minimum callers threshold (default: 5)",
                        },
                    },
                },
            ),
            Tool(
                name="scope",
                description=(
                    "Full context for a function or file: callers, callees,"
                    " related project constraints, recent git changes."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "function": {"type": "string", "description": "Function ID or name"},
                        "file": {"type": "string", "description": "File path"},
                    },
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

        if name == "map":
            result = _tool_map(graph, arguments, root)
        elif name == "functions_graph":
            result = _tool_functions_graph(graph)
        elif name == "hotspots":
            result = _tool_hotspots(graph, arguments)
        elif name == "scope":
            result = _tool_scope(graph, arguments, root)
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
    """Load semantic layer if available."""
    from winkers.semantic import SemanticStore
    store = SemanticStore(root)
    return store.load()


def _tool_map(graph: Graph, args: dict, root: Path | None = None) -> dict:
    zone_filter = args.get("zone")

    zones: dict[str, list[str]] = {}
    for f in graph.files.values():
        z = f.zone or _infer_zone(f.path)
        zones.setdefault(z, []).append(f.path)

    # Filter by zone
    if zone_filter:
        zones = {z: files for z, files in zones.items() if z == zone_filter}

    hotspots = _get_hotspots(graph, top=5)
    semantic = _load_semantic(root) if root else None

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
        if semantic and z in semantic.zone_intents:
            intent = semantic.zone_intents[z]
            entry["intent"] = {
                "why": intent.why,
                "wrong_approach": intent.wrong_approach,
            }
        zone_list.append(entry)

    result: dict[str, Any] = {
        "project": ".",
        "languages": graph.meta.get("languages", []),
        "total_files": len(graph.files),
        "total_functions": len(graph.functions),
        "zones": zone_list,
        "hotspots": hotspots,
    }
    if semantic:
        if semantic.data_flow:
            result["data_flow"] = semantic.data_flow
        if semantic.domain_context:
            result["domain_context"] = semantic.domain_context
        if semantic.monster_files:
            result["monster_files"] = {
                f: {"sections": [s.model_dump() for s in m.sections],
                    "where_to_add": m.where_to_add}
                for f, m in semantic.monster_files.items()
            }
        if semantic.conventions:
            result["conventions"] = [
                {"rule": c.rule, "wrong_approach": c.wrong_approach}
                for c in semantic.conventions
            ]
        if semantic.constraints:
            result["constraints"] = [
                {"id": c.id, "name": c.name, "why": c.why,
                 "severity": c.severity, "affects": c.affects}
                for c in semantic.constraints
            ]
        if semantic.new_feature_checklist:
            result["before_writing_code"] = semantic.new_feature_checklist
    return result


def _tool_functions_graph(graph: Graph) -> dict:
    """Indexed function graph: numeric IDs, callers as index references."""
    # Build index: fn_id → numeric index
    fn_ids = sorted(graph.functions.keys())
    id_to_idx: dict[str, int] = {fid: i + 1 for i, fid in enumerate(fn_ids)}

    # Build caller lookup
    caller_map: dict[str, list[str]] = {}
    for edge in graph.call_edges:
        caller_map.setdefault(edge.target_fn, []).append(edge.source_fn)

    functions: dict[str, dict] = {}
    for fid in fn_ids:
        fn = graph.functions[fid]
        idx = id_to_idx[fid]
        callers = caller_map.get(fid, [])
        caller_indices = sorted(
            id_to_idx[c] for c in callers if c in id_to_idx
        )
        entry: dict[str, Any] = {
            "id": fn.id,
            "name": fn.name,
            "file": fn.file,
        }
        if caller_indices:
            entry["callers"] = caller_indices
        if fn.complexity and fn.complexity > 1:
            entry["cx"] = fn.complexity
        functions[str(idx)] = entry

    return {
        "total": len(functions),
        "functions": functions,
    }


def _tool_hotspots(graph: Graph, args: dict) -> dict:
    """Functions with many callers + who calls them."""
    min_callers = args.get("min_callers", 5)

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
    return {
        "min_callers": min_callers,
        "count": len(hotspots),
        "hotspots": hotspots,
    }



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
                {
                    "fn": e.target_fn,
                    "expression": e.call_site.expression,
                }
                for e in callee_edges
            ],
            "constraints": _build_constraints(fn, caller_edges),
            "related_constraints": _related_constraints(fn, root),
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


def _file_imports_from(path: str, graph: Graph) -> list[str]:
    return [e.target_file for e in graph.import_edges if e.source_file == path]


def _file_imported_by(path: str, graph: Graph) -> list[str]:
    return [e.source_file for e in graph.import_edges if e.target_file == path]


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
    """Return recent commits from pre-collected graph data."""
    file_node = graph.files.get(fn.file)
    if not file_node or not file_node.recent_commits:
        return []
    return file_node.recent_commits


def _related_constraints(fn: Any, root: Path | None) -> list[dict]:
    """Find semantic constraints that affect this function's zone or file."""
    if root is None:
        return []
    semantic = _load_semantic(root)
    if semantic is None:
        return []
    zone = _infer_zone(fn.file)
    return [
        {"id": c.id, "name": c.name, "why": c.why, "severity": c.severity}
        for c in semantic.constraints
        if zone in c.affects or fn.file in c.affects
    ]


def _build_constraints(fn: Any, caller_edges: list) -> dict:
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
