"""MCP tool: scope — deep context for a function or file (callers, callees, rules, impact)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from winkers.mcp.tools._common import (
    _attach_route,
    _build_callers_constraint,
    _file_fn_entry,
    _find_function,
    _impact_section_for_fn,
    _recent_changes_from_graph,
    _related_rules,
    _semantic_context_for_fn,
    _signature,
    _similar_logic_for_fn,
    _value_locked_for_file,
)
from winkers.models import Graph


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
        _attach_route(function_entry, fn)
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
