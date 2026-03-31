"""Graph API handlers: index, graph, preview, snapshot-graph."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web

from winkers.store import GraphStore


def make_handlers(root: Path, store: GraphStore, static_dir: Path) -> SimpleNamespace:
    async def handle_index(request: web.Request) -> web.Response:
        index = static_dir / "index.html"
        return web.Response(
            text=index.read_text(encoding="utf-8"),
            content_type="text/html",
        )

    async def handle_graph(request: web.Request) -> web.Response:
        graph = store.load()
        if graph is None:
            return web.json_response({"error": "Graph not initialized"}, status=404)
        zone = request.rel_url.query.get("zone")
        include_ui = request.rel_url.query.get("ui") == "1"
        return web.json_response(_graph_to_cytoscape(graph, zone, include_ui))

    async def handle_preview(request: web.Request) -> web.Response:
        graph = store.load()
        if graph is None:
            return web.json_response({"error": "Graph not initialized"}, status=404)
        fn = request.rel_url.query.get("fn", "")
        return web.json_response(_preview(graph, fn))

    async def handle_snapshot_graph(request: web.Request) -> web.Response:
        """Load a historical snapshot as Cytoscape graph."""
        filename = request.rel_url.query.get("file", "")
        if not filename:
            return web.json_response({"error": "file parameter required"}, status=400)

        history_dir = root / ".winkers" / "history"
        snap_path = history_dir / filename
        if not snap_path.exists() or ".." in filename:
            return web.json_response({"error": "Snapshot not found"}, status=404)

        try:
            from winkers.models import Graph
            data = json.loads(snap_path.read_text(encoding="utf-8"))
            snap_graph = Graph.model_validate(data)
            zone = request.rel_url.query.get("zone")
            include_ui = request.rel_url.query.get("ui") == "1"
            return web.json_response(_graph_to_cytoscape(snap_graph, zone, include_ui))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    return SimpleNamespace(
        index=handle_index,
        graph=handle_graph,
        preview=handle_preview,
        snapshot_graph=handle_snapshot_graph,
    )


def _graph_to_cytoscape(
    graph,
    zone_filter: str | None = None,
    include_ui: bool = False,
) -> dict[str, Any]:
    """Convert Graph to Cytoscape.js elements format."""
    nodes = []
    edges = []

    fn_ids_in_zone: set[str] = set()

    for fn_id, fn in graph.functions.items():
        zone = graph.file_zone(fn.file)
        if zone_filter and zone != zone_filter:
            continue
        fn_ids_in_zone.add(fn_id)
        nodes.append({
            "data": {
                "id": fn_id,
                "label": fn.name,
                "file": fn.file,
                "zone": zone,
                "locked": graph.is_locked(fn_id),
                "callers": len(graph.callers(fn_id)),
                "kind": fn.kind,
                "complexity": fn.complexity,
                "line_start": fn.line_start,
                "line_end": fn.line_end,
                "params": [p.name for p in fn.params],
                "return_type": fn.return_type,
                "is_async": fn.is_async,
                "docstring": fn.docstring,
            }
        })

    for i, edge in enumerate(graph.call_edges):
        if zone_filter and (
            edge.source_fn not in fn_ids_in_zone
            or edge.target_fn not in fn_ids_in_zone
        ):
            continue
        edges.append({
            "data": {
                "id": f"e{i}",
                "source": edge.source_fn,
                "target": edge.target_fn,
                "confidence": edge.confidence,
                "expression": edge.call_site.expression,
                "line": edge.call_site.line,
            }
        })

    if include_ui:
        _add_ui_nodes(graph, fn_ids_in_zone, nodes, edges)

    return {"nodes": nodes, "edges": edges}


def _add_ui_nodes(graph, fn_ids_in_zone: set[str], nodes: list, edges: list) -> None:
    """Append UI layer nodes (templates + elements) and edges to Cytoscape lists."""
    ui_map: dict = graph.meta.get("ui_map", {})
    if not ui_map:
        return

    seen_templates: set[str] = set()
    ui_edge_idx = 0

    for route, info in ui_map.items():
        fn_id = next(
            (fid for fid, fn in graph.functions.items()
             if fn.route == route and fid in fn_ids_in_zone),
            None,
        )
        if fn_ids_in_zone and fn_id is None:
            continue

        tpl_name = info.get("template", "")
        tpl_id = f"ui::tpl::{tpl_name}"

        if tpl_name not in seen_templates:
            seen_templates.add(tpl_name)
            nodes.append({"data": {
                "id": tpl_id,
                "label": tpl_name.split("/")[-1],
                "layer": "ui",
                "kind": "template",
                "template": tpl_name,
                "callers": 0,
                "locked": False,
            }})

        if fn_id:
            edges.append({"data": {
                "id": f"ui-r-{ui_edge_idx}",
                "source": fn_id,
                "target": tpl_id,
                "edge_kind": "renders",
            }})
            ui_edge_idx += 1

        for el_idx, el in enumerate(info.get("elements", [])):
            el_label = el.get("id") or el.get("data-tab") or el.get("text") or el["kind"]
            el_id = f"ui::el::{tpl_name}::{el['kind']}::{el_idx}"
            nodes.append({"data": {
                "id": el_id,
                "label": el_label[:20],
                "layer": "ui",
                "kind": el["kind"],
                "template": tpl_name,
                "callers": 0,
                "locked": False,
            }})
            edges.append({"data": {
                "id": f"ui-c-{ui_edge_idx}",
                "source": tpl_id,
                "target": el_id,
                "edge_kind": "contains",
            }})
            ui_edge_idx += 1


def _preview(graph, fn_id: str) -> dict[str, Any]:
    """Return highlight sets for preview mode."""
    if fn_id not in graph.functions:
        matches = [fid for fid, fn in graph.functions.items() if fn.name == fn_id]
        if not matches:
            return {"error": f"Function not found: {fn_id}"}
        fn_id = matches[0]

    caller_edges = graph.callers(fn_id)
    callee_edges = graph.callees(fn_id)

    highlight_locked = [
        e.source_fn for e in caller_edges if graph.is_locked(e.source_fn)
    ]
    highlight_neighbors = (
        [e.source_fn for e in caller_edges]
        + [e.target_fn for e in callee_edges]
    )

    return {
        "target": fn_id,
        "highlight_target": [fn_id],
        "highlight_locked": highlight_locked,
        "highlight_neighbors": list(set(highlight_neighbors)),
    }
